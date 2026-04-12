"""XMLTV electronic program guide: download, stream-parse, index, lookup.

The default source is `epg.it999.ru` — a maintained Russian IPTV XMLTV feed
with exact display-names including "HD" suffix variants, so matching against
the user's playlist is a direct normalized-name lookup.

Strategy notes:

- The feed is ~43MB gzipped / ~430MB decompressed. We stream-parse with
  `ElementTree.iterparse` and clear each element after processing so peak
  memory stays under ~60MB.
- Programmes are filtered by time window on ingest (±`WINDOW_DAYS` days
  from the moment of load) — we keep only what's relevant for "now / was /
  will be", which shrinks the working set to a few MB.
- The raw gzipped file is cached on disk for `CACHE_TTL_SECONDS`.
- `load()` is safe to call concurrently; the first caller does the work,
  others wait on the lock.

The `lookup()` method takes a playlist channel name (anything like "Первый
канал HD"), normalizes it the same way we normalize logo keys, and returns
the programme list. Missing → empty list → UI hides the EPG panel.
"""

from __future__ import annotations

import asyncio
import gzip
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO, Optional
from xml.etree import ElementTree as ET

import httpx

from server.logos.iptv_db import normalize


DEFAULT_EPG_URL = "http://epg.it999.ru/edem.xml.gz"
CACHE_TTL_SECONDS = 6 * 3600  # 6 hours
WINDOW_DAYS = 3  # keep programmes within ±3 days of load time


@dataclass(frozen=True, slots=True)
class Programme:
    title: str
    description: str
    start: datetime
    stop: datetime

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "description": self.description,
            "start": self.start.isoformat(),
            "stop": self.stop.isoformat(),
        }


def _parse_xmltv_time(raw: str) -> Optional[datetime]:
    """Parse XMLTV `YYYYMMDDhhmmss ±HHMM` into a timezone-aware datetime.

    XMLTV dates come in a compact numeric form. Examples:

        "20260411160000 +0300"
        "20260411160000"        (no timezone → treat as UTC)
    """
    raw = raw.strip()
    if len(raw) < 14:
        return None

    date_part = raw[:14]
    tz_part = raw[14:].strip()

    try:
        year = int(date_part[0:4])
        month = int(date_part[4:6])
        day = int(date_part[6:8])
        hour = int(date_part[8:10])
        minute = int(date_part[10:12])
        second = int(date_part[12:14])
    except ValueError:
        return None

    tz = timezone.utc
    if tz_part:
        sign = 1 if tz_part[0] == "+" else -1 if tz_part[0] == "-" else 0
        if sign:
            digits = tz_part[1:].replace(":", "").strip()
            if len(digits) >= 4:
                try:
                    tz_hours = int(digits[0:2])
                    tz_minutes = int(digits[2:4])
                    tz = timezone(sign * timedelta(hours=tz_hours, minutes=tz_minutes))
                except ValueError:
                    pass

    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=tz)
    except ValueError:
        return None


class EpgGuide:
    """Downloads an XMLTV guide and serves programmes by channel name."""

    def __init__(
        self,
        cache_dir: Path,
        url: str = DEFAULT_EPG_URL,
        timeout: float = 60.0,
    ) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._url = url
        self._timeout = timeout
        self._index: dict[str, list[Programme]] = {}
        self._loaded = False
        self._loading = False
        self._lock = asyncio.Lock()

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def loading(self) -> bool:
        return self._loading

    @property
    def size(self) -> int:
        return sum(len(v) for v in self._index.values())

    @property
    def channels(self) -> int:
        return len(self._index)

    def _cache_path(self) -> Path:
        return self._cache_dir / "_epg.xml.gz"

    def _is_fresh(self, path: Path) -> bool:
        if not path.exists() or path.stat().st_size == 0:
            return False
        return (time.time() - path.stat().st_mtime) < CACHE_TTL_SECONDS

    async def _download(self) -> Optional[Path]:
        path = self._cache_path()
        if self._is_fresh(path):
            return path

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
            ),
            "Accept": "application/xml, application/gzip, */*",
        }

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers=headers,
            ) as client:
                resp = await client.get(self._url)
                resp.raise_for_status()
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_bytes(resp.content)
                tmp.replace(path)
                return path
        except (httpx.HTTPError, OSError):
            return path if path.exists() else None

    def _open_stream(self, path: Path) -> IO[bytes]:
        if path.suffix == ".gz":
            return gzip.open(path, "rb")
        return path.open("rb")

    def _parse(self, path: Path) -> dict[str, list[Programme]]:
        """Stream-parse the XMLTV file into a normalized-name → programmes map."""
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=WINDOW_DAYS)
        window_end = now + timedelta(days=WINDOW_DAYS)

        # Pass 1 (interleaved in one stream): collect channel display-names.
        # XMLTV spec puts all <channel> elements before <programme> elements,
        # so we build name maps as we encounter channels and use them when
        # programme elements start streaming in.
        channel_display_names: dict[str, list[str]] = {}
        programmes_by_channel: dict[str, list[Programme]] = {}

        with self._open_stream(path) as stream:
            context = ET.iterparse(stream, events=("end",))
            for _event, elem in context:
                tag = elem.tag
                if tag == "channel":
                    xml_id = elem.get("id", "")
                    if xml_id:
                        names = [
                            dn.text.strip()
                            for dn in elem.findall("display-name")
                            if dn.text
                        ]
                        if names:
                            channel_display_names[xml_id] = names
                    elem.clear()

                elif tag == "programme":
                    xml_id = elem.get("channel", "")
                    if xml_id in channel_display_names:
                        start = _parse_xmltv_time(elem.get("start", ""))
                        stop = _parse_xmltv_time(elem.get("stop", ""))
                        if start is not None and stop is not None:
                            # Time-window filter to keep memory small.
                            if stop >= window_start and start <= window_end:
                                title_el = elem.find("title")
                                desc_el = elem.find("desc")
                                title = (
                                    title_el.text.strip()
                                    if title_el is not None and title_el.text
                                    else ""
                                )
                                description = (
                                    desc_el.text.strip()
                                    if desc_el is not None and desc_el.text
                                    else ""
                                )
                                programmes_by_channel.setdefault(xml_id, []).append(
                                    Programme(
                                        title=title,
                                        description=description,
                                        start=start,
                                        stop=stop,
                                    )
                                )
                    elem.clear()

        # Sort each channel's programmes by start time
        for progs in programmes_by_channel.values():
            progs.sort(key=lambda p: p.start)

        # Build normalized-name index — one channel's programmes may be
        # reachable by several normalized name keys if its display-names list
        # multiple variants (ru/en/HD/SD).
        index: dict[str, list[Programme]] = {}
        for xml_id, display_names in channel_display_names.items():
            progs = programmes_by_channel.get(xml_id)
            if not progs:
                continue
            for name in display_names:
                key = normalize(name)
                if key and key not in index:
                    index[key] = progs
        return index

    async def load(self) -> None:
        if self._loaded:
            return

        async with self._lock:
            if self._loaded:
                return
            self._loading = True
            try:
                path = await self._download()
                if path is None:
                    return

                # Parse in a thread so the event loop stays responsive. The
                # parse is CPU-bound (~2-4s) and would otherwise block /api
                # requests while the big XML is walked.
                index = await asyncio.to_thread(self._parse, path)
                self._index = index
                self._loaded = True
            finally:
                self._loading = False

    def lookup(self, channel_name: str) -> list[Programme]:
        """Return all cached programmes for a channel, sorted by start time."""
        key = normalize(channel_name)
        if not key:
            return []
        return self._index.get(key, [])

    def window(
        self,
        channel_name: str,
        past: timedelta = timedelta(hours=2),
        future: timedelta = timedelta(hours=12),
    ) -> tuple[Optional[int], list[Programme]]:
        """Return (current_index, programmes_in_window).

        `current_index` points at the programme currently airing, or None if
        there is no overlap with "now".
        """
        all_progs = self.lookup(channel_name)
        if not all_progs:
            return None, []

        now = datetime.now(timezone.utc)
        window_start = now - past
        window_end = now + future

        visible = [p for p in all_progs if p.stop >= window_start and p.start <= window_end]
        if not visible:
            return None, []

        current_index: Optional[int] = None
        for i, p in enumerate(visible):
            if p.start <= now <= p.stop:
                current_index = i
                break

        return current_index, visible
