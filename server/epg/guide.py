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
import contextlib
import gzip
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import IO
from xml.etree import ElementTree as ET

import httpx

from server.logos.iptv_db import normalize

DEFAULT_EPG_URL = "http://epg.it999.ru/edem.xml.gz"
CACHE_TTL_SECONDS = 6 * 3600  # 6 hours
WINDOW_DAYS = 3  # keep programmes within ±3 days of load time
# JSON cache TTL — how long to trust the pre-parsed main-only index on
# disk before we re-parse the source XML. 24h keeps the file fresh
# without paying the XML-parse cost on every restart.
JSON_CACHE_TTL_SECONDS = 24 * 3600

# Technical suffix the EPG source sometimes appends to titles:
# "(SETANTA_SPORT2_HD)._05-00-32_FULL." — usually a recording-filename
# artefact leaking from the provider's pipeline. Strip repeatedly so
# stacked suffixes all go.
_EPG_TECH_SUFFIX_RE = re.compile(
    r"\s*\([A-Z0-9_]+\)\._\d{2}-\d{2}-\d{2}_FULL\.?\s*",
    re.IGNORECASE,
)
# Free-standing "_HH-MM-SS_FULL." without a preceding channel-bracket.
_EPG_TECH_TAIL_RE = re.compile(r"\s*_\d{2}-\d{2}-\d{2}_FULL\.?\s*$", re.IGNORECASE)
# "(CHANNEL_NAME)" marker bare at the end — another leak shape we see.
_EPG_TECH_CHAN_RE = re.compile(r"\s*\([A-Z][A-Z0-9_]{2,}\)\s*$")
# Upper bound for a reasonable programme title. Some EPG entries glue
# the blurb/description into the title field ("NASCAR Cup... — Страна:
# США. Доминик и Брайан..."). Cap at the first sentence boundary after
# this threshold so we get a readable title without losing legitimately
# long episode names.
# Soft limit is intentionally low (80 chars) — for over-long titles we
# want the first sentence boundary EARLY to avoid merging the
# programme's own blurb-bleed into the title ("…— Страна: США.
# Доминик и Брайан..." should cut at the country marker, not deep in
# the bleed). Legitimate titles < _TITLE_HARD_LIMIT aren't touched.
_TITLE_SOFT_LIMIT = 80
_TITLE_HARD_LIMIT = 160


def sanitise_epg_title(title: str) -> str:
    """Strip technical suffixes + over-long blurb bleed from a raw EPG
    programme title. Idempotent, safe to call repeatedly.
    """
    if not title:
        return ""
    s = title.strip()
    prev = ""
    while s and s != prev:
        prev = s
        s = _EPG_TECH_SUFFIX_RE.sub(" ", s)
        s = _EPG_TECH_TAIL_RE.sub("", s)
        s = _EPG_TECH_CHAN_RE.sub("", s)
        s = s.strip().rstrip(".").strip()
    # Collapse runs of whitespace the suffix-strip may have left behind.
    s = re.sub(r"\s{2,}", " ", s)
    # Truncate over-long titles at the first sentence boundary past the
    # soft limit — preserves "Суперкары. 2-й этап. Мельбурн. 4-я гонка"
    # while cropping "НАСКАР. ... — Страна: США. Доминик и Брайан..."
    # at "Страна: США."
    if len(s) > _TITLE_HARD_LIMIT:
        cut = s.find(". ", _TITLE_SOFT_LIMIT)
        if cut == -1:
            cut = s.find(". ", 40)
        s = s[: cut + 1].strip() if cut != -1 else s[:_TITLE_HARD_LIMIT].rstrip(".,; ") + "…"
    return s


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


def _parse_xmltv_time(raw: str) -> datetime | None:
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

    tz = UTC
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

    def _json_cache_path(self) -> Path:
        return self._cache_dir / "_main_epg.json"

    def _is_fresh(self, path: Path) -> bool:
        if not path.exists() or path.stat().st_size == 0:
            return False
        return (time.time() - path.stat().st_mtime) < CACHE_TTL_SECONDS

    def _is_json_fresh(self) -> bool:
        p = self._json_cache_path()
        if not p.exists() or p.stat().st_size == 0:
            return False
        return (time.time() - p.stat().st_mtime) < JSON_CACHE_TTL_SECONDS

    def _save_json(self, index: dict[str, list[Programme]]) -> None:
        """Write the in-memory index to a compact JSON file.

        The JSON is the fast-path cache — next startup can skip the
        450 MB XML parse and restore the index in ~100 ms.
        """
        payload = {key: [p.to_dict() for p in progs] for key, progs in index.items()}
        path = self._json_cache_path()
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink()

    def _load_json(self) -> dict[str, list[Programme]] | None:
        path = self._json_cache_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        out: dict[str, list[Programme]] = {}
        for key, progs_data in data.items():
            progs: list[Programme] = []
            for pd in progs_data:
                try:
                    start = datetime.fromisoformat(str(pd["start"]))
                    stop = datetime.fromisoformat(str(pd["stop"]))
                except (KeyError, ValueError, TypeError):
                    continue
                progs.append(
                    Programme(
                        title=sanitise_epg_title(str(pd.get("title", ""))),
                        description=str(pd.get("description", "")),
                        start=start,
                        stop=stop,
                    )
                )
            if progs:
                out[key] = progs
        return out

    async def _download(self) -> Path | None:
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
        now = datetime.now(UTC)
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
                        names = [dn.text.strip() for dn in elem.findall("display-name") if dn.text]
                        if names:
                            channel_display_names[xml_id] = names
                    elem.clear()

                elif tag == "programme":
                    xml_id = elem.get("channel", "")
                    if xml_id in channel_display_names:
                        start = _parse_xmltv_time(elem.get("start", ""))
                        stop = _parse_xmltv_time(elem.get("stop", ""))
                        if (
                            start is not None
                            and stop is not None
                            and stop >= window_start
                            and start <= window_end
                        ):
                            title_el = elem.find("title")
                            desc_el = elem.find("desc")
                            title = sanitise_epg_title(
                                title_el.text if title_el is not None else ""
                            )
                            description = (
                                desc_el.text.strip() if desc_el is not None and desc_el.text else ""
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

    async def load(self, main_names: set[str] | None = None) -> None:
        """Load the EPG index.

        When ``main_names`` is provided, the index is filtered to just
        those channel display-names (user's "Основное" list). The
        filtered index is persisted to JSON so the next restart can skip
        the XML download + parse entirely and be ready in ~100 ms.

        Call order on each process start:
          1. JSON cache fresh → load from JSON (sync, ~100 ms). Done.
          2. JSON stale/missing → download XML → parse → filter to
             main_names → save JSON → done.
        """
        if self._loaded:
            return

        async with self._lock:
            if self._loaded:
                return
            self._loading = True
            try:
                # Fast path: recent JSON cache covers startup without
                # touching the network or the big XML file.
                if self._is_json_fresh():
                    cached = await asyncio.to_thread(self._load_json)
                    if cached:
                        self._index = cached
                        self._loaded = True
                        return

                path = await self._download()
                if path is None:
                    return

                # Parse in a thread so the event loop stays responsive. The
                # parse is CPU-bound (~2-4s) and would otherwise block /api
                # requests while the big XML is walked.
                index = await asyncio.to_thread(self._parse, path)
                if main_names:
                    norm = {normalize(n) for n in main_names if n}
                    norm.discard("")
                    if norm:
                        index = {k: v for k, v in index.items() if k in norm}
                self._index = index
                self._loaded = True
                # Persist for next startup — non-fatal on failure.
                await asyncio.to_thread(self._save_json, index)
            finally:
                self._loading = False

    def lookup(self, channel_name: str) -> list[Programme]:
        """Return all cached programmes for a channel, sorted by start time."""
        key = normalize(channel_name)
        if not key:
            return []
        return self._index.get(key, [])

    def now_playing(self, channel_name: str) -> Programme | None:
        """Return the programme currently airing for ``channel_name``,
        or ``None`` if the EPG has no overlap with "now".

        Lighter than ``window()`` — no list materialisation, no
        ±timedelta expansion — used by batch endpoints that fetch
        "what's on right now" for many channels per request.
        """
        progs = self.lookup(channel_name)
        if not progs:
            return None
        now = datetime.now(UTC)
        for p in progs:
            if p.start <= now <= p.stop:
                return p
            if p.start > now:
                # Sorted list → future programmes end the search.
                break
        return None

    def window(
        self,
        channel_name: str,
        past: timedelta = timedelta(hours=2),
        future: timedelta = timedelta(hours=12),
    ) -> tuple[int | None, list[Programme]]:
        """Return (current_index, programmes_in_window).

        `current_index` points at the programme currently airing, or None if
        there is no overlap with "now".
        """
        all_progs = self.lookup(channel_name)
        if not all_progs:
            return None, []

        now = datetime.now(UTC)
        window_start = now - past
        window_end = now + future

        visible = [p for p in all_progs if p.stop >= window_start and p.start <= window_end]
        if not visible:
            return None, []

        current_index: int | None = None
        for i, p in enumerate(visible):
            if p.start <= now <= p.stop:
                current_index = i
                break

        return current_index, visible
