"""iptv-org database integration for logo resolution.

Two CSVs power this module:

    data/channels.csv  — id, name, alt_names (;-separated), country, …
    data/logos.csv     — channel, feed, width, height, format, url

Both are cached locally for 7 days. At startup we download them if missing
or stale, then build an in-memory index from every normalized channel name
and alt_name to the best logo URL.

Exact normalized match only — intentionally simple. Given alt_names already
contain transliterations ("Первый канал" / "Pervyy Kanal") most real-world
playlist names land a direct hit without any fuzzy scoring.
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

CHANNELS_URL = "https://raw.githubusercontent.com/iptv-org/database/master/data/channels.csv"
LOGOS_URL = "https://raw.githubusercontent.com/iptv-org/database/master/data/logos.csv"

_CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 days


# Country markers we trim off channel names before normalizing.
_COUNTRY_SUFFIXES = {
    "ru",
    "uk",
    "us",
    "usa",
    "de",
    "ger",
    "es",
    "esp",
    "it",
    "ita",
    "fr",
    "fra",
    "dk",
    "nl",
    "nor",
    "se",
    "fi",
    "pl",
    "cz",
    "sk",
    "by",
    "ua",
    "kz",
    "am",
}

# Markers that don't change channel identity — stripped as plain tokens.
# Kept conservative on purpose: "channel"/"канал" is NOT here because some
# channel names collapse to a single generic word without it (e.g. "Первый
# канал" would become just "первый"), which causes bad matches.
_NOISE_TOKENS = {
    "hd",
    "fhd",
    "uhd",
    "4k",
    "8k",
    "sd",
    "hq",
    "tv",
    "тв",
}

# Timezone/feed markers like "+0", "+1", "+2", "+3", "+4", "-1"
_OFFSET_RE = re.compile(r"[+-]\d+")


def normalize(name: str) -> str:
    """Lowercase a channel name and strip common decoration.

    Keeps Cyrillic characters as-is. The goal is that all of these map to
    `"первый канал"`:

        "Первый канал"
        "Первый канал HD"
        "Первый канал +4 HD"
        "Первый канал +4"
    """
    if not name:
        return ""

    s = name.lower().strip()
    # Strip bracketed/parenthesized hints like (russia) or [backup]
    s = re.sub(r"[\(\[].*?[\)\]]", " ", s)
    # Replace hyphens with spaces — "Россия-1" and "Россия 1" must collide.
    # Done BEFORE offset stripping so "-1" inside "Россия-1" turns into " 1".
    s = s.replace("-", " ")
    # Drop standalone timezone-offset markers only when they are their own
    # "word" (surrounded by non-word chars). This protects legitimate numbers
    # like "Россия 1" from being stripped.
    s = re.sub(r"(?<!\w)[+-]\d+(?!\w)", " ", s)
    # Replace remaining punctuation with space (keep word chars, spaces)
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)

    tokens = [
        tok
        for tok in s.split()
        if tok and tok not in _NOISE_TOKENS and tok not in _COUNTRY_SUFFIXES
    ]
    return " ".join(tokens).strip()


def is_specific_enough(key: str) -> bool:
    """Reject keys that are too generic to safely index (single digit, "3", etc)."""
    if not key or len(key) < 3:
        return False
    stripped = key.replace(" ", "")
    return not stripped.isdigit()


@dataclass(frozen=True, slots=True)
class LogoEntry:
    channel_id: str
    country: str
    url: str
    english_name: str  # canonical `name` column from channels.csv
    all_names: tuple[str, ...]  # english_name + alt_names, for slug guessing


class IptvOrgIndex:
    """Name → logo URL index backed by iptv-org/database CSVs."""

    def __init__(self, cache_dir: Path, timeout: float = 20.0) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._timeout = timeout
        self._index: dict[str, LogoEntry] = {}
        self._lock = asyncio.Lock()
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def size(self) -> int:
        return len(self._index)

    def _cache_path(self, name: str) -> Path:
        return self._cache_dir / f"_iptv_org_{name}"

    def _is_fresh(self, path: Path) -> bool:
        if not path.exists() or path.stat().st_size == 0:
            return False
        return (time.time() - path.stat().st_mtime) < _CACHE_TTL_SECONDS

    async def _fetch_if_stale(self, url: str, cache_name: str) -> str:
        path = self._cache_path(cache_name)
        if self._is_fresh(path):
            return path.read_text(encoding="utf-8")

        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            text = resp.text

        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        return text

    async def load(self) -> None:
        """Download (or read cached) CSVs and build the in-memory index.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._loaded:
            return

        async with self._lock:
            if self._loaded:
                return

            try:
                channels_csv = await self._fetch_if_stale(CHANNELS_URL, "channels.csv")
                logos_csv = await self._fetch_if_stale(LOGOS_URL, "logos.csv")
            except (httpx.HTTPError, OSError):
                # Network down — leave the index empty, caller will fall back
                return

            # channel_id → primary logo URL (prefer no-feed entry)
            primary_logo: dict[str, str] = {}
            for row in csv.DictReader(io.StringIO(logos_csv)):
                cid = row.get("channel", "")
                url = row.get("url", "").strip()
                feed = row.get("feed", "").strip()
                if not cid or not url:
                    continue
                if cid not in primary_logo or not feed:
                    primary_logo[cid] = url

            # channel_id → LogoEntry (with metadata), then index by name keys
            index: dict[str, LogoEntry] = {}
            for row in csv.DictReader(io.StringIO(channels_csv)):
                cid = row.get("id", "")
                if cid not in primary_logo:
                    continue

                english_name = row.get("name", "")
                alt_names = tuple(
                    a.strip() for a in (row.get("alt_names") or "").split(";") if a.strip()
                )
                entry = LogoEntry(
                    channel_id=cid,
                    country=row.get("country", ""),
                    url=primary_logo[cid],
                    english_name=english_name,
                    all_names=(english_name, *alt_names),
                )

                keys: list[str] = []
                name_key = normalize(row.get("name", ""))
                if is_specific_enough(name_key):
                    keys.append(name_key)
                for alt in (row.get("alt_names") or "").split(";"):
                    alt_key = normalize(alt)
                    if is_specific_enough(alt_key):
                        keys.append(alt_key)

                for key in keys:
                    existing = index.get(key)
                    # Prefer first occurrence; if a RU channel comes along, it wins
                    # over a foreign-country namesake.
                    if existing is None or existing.country != "RU" and entry.country == "RU":
                        index[key] = entry

            self._index = index
            self._loaded = True

    def lookup(self, channel_name: str) -> LogoEntry | None:
        """Return a LogoEntry for a channel name, or None if no match."""
        key = normalize(channel_name)
        if not is_specific_enough(key):
            return None
        return self._index.get(key)
