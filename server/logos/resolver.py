"""Channel logo resolver.

Resolution pipeline (first hit wins, all others skipped):

1. On-disk cache (`logos_cache/<slug>.png`) — user can drop custom PNGs here.
2. vattik/picons-rtrs (GitHub raw) — the 20 Russian RTRS free-to-air channels
   matched by Cyrillic name; high quality, no rate limits.
3. tv-logo/tv-logos via cdn.jsdelivr.net, keyed on the English channel name
   and country from the iptv-org index. Covers ~86 Russian + thousands of
   international channels with high-quality artwork and no rate limits.
4. The raw URL from iptv-org/database (usually imgur) — last resort, subject
   to the host's rate limits.

All network I/O is best-effort. A failure at any step falls through to the
monogram chip in the frontend; the UI is never blocked on logo loads.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx

from server.logos.epg_icons import EpgIconIndex
from server.logos.iptv_db import IptvOrgIndex, LogoEntry


# Browser-like UA — many image hosts reject python-httpx default UA.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
_BROWSER_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
}

_TV_LOGOS_CDN = "https://cdn.jsdelivr.net/gh/tv-logo/tv-logos@main/countries"

# ── vattik/picons-rtrs — 20 Russian RTRS free-to-air channels ────────────────
# Files are named "NN КИРИЛЛИЦА.png" (number + uppercase Cyrillic).
# We match by lowercase channel name (+ stripped quality suffixes).
_RTRS_CDN = "https://raw.githubusercontent.com/vattik/picons-rtrs/master/220x100/"
_RTRS_MAP: dict[str, str] = {
    "первый канал":  "01 ПЕРВЫЙ КАНАЛ",
    "россия-1":      "02 РОССИЯ-1",
    "россия 1":      "02 РОССИЯ-1",
    "матч!":         "03 МАТЧ!",
    "матч тв":       "03 МАТЧ!",
    "нтв":           "04 НТВ",
    "пятый канал":   "05 ПЯТЫЙ КАНАЛ",
    "россия-к":      "06 РОССИЯ-К",
    "россия к":      "06 РОССИЯ-К",
    "культура":      "06 РОССИЯ-К",
    "россия-24":     "07 РОССИЯ-24",
    "россия 24":     "07 РОССИЯ-24",
    "карусель":      "08 КАРУСЕЛЬ",
    "отр":           "09 ОТР",
    "тв центр":      "10 ТВ Центр",
    "рен тв":        "11 РЕН ТВ",
    "рентв":         "11 РЕН ТВ",
    "спас":          "12 Спас",
    "стс":           "13 СТС",
    "домашний":      "14 Домашний",
    "тв3":           "15 ТВ3",
    "тв 3":          "15 ТВ3",
    "пятница":       "16 Пятница",
    "пятница!":      "16 Пятница",
    "звезда":        "17 Звезда",
    "мир":           "18 МИР",
    "тнт":           "19 ТНТ",
    "муз тв":        "20 МУЗ ТВ",
    "муз-тв":        "20 МУЗ ТВ",
}

_QUALITY_RE = re.compile(r"\s*(hd|fhd|uhd|4k|\+\d+)\s*$", re.IGNORECASE)


def _rtrs_candidate(name: str) -> Optional[str]:
    """Return a vattik/picons-rtrs URL if the channel is one of the 20 RTRS channels."""
    key = _QUALITY_RE.sub("", name.strip()).lower()
    filename = _RTRS_MAP.get(key)
    if filename is None:
        return None
    return _RTRS_CDN + quote(filename + ".png")

# Map iptv-org country code → tv-logos directory name.
# World directory is always a fallback.
_COUNTRY_DIRS: dict[str, str] = {
    "RU": "russia",
    "US": "united-states",
    "GB": "united-kingdom",
    "UK": "united-kingdom",
    "DE": "germany",
    "FR": "france",
    "IT": "italy",
    "ES": "spain",
    "PT": "portugal",
    "NL": "netherlands",
    "BE": "belgium",
    "PL": "poland",
    "CZ": "czech-republic",
    "SK": "slovakia",
    "UA": "ukraine",
    "BY": "belarus",
    "KZ": "kazakhstan",
}

# Suffix hint used in tv-logos filenames per directory.
_COUNTRY_SUFFIXES: dict[str, str] = {
    "russia": "ru",
    "united-states": "us",
    "united-kingdom": "uk",
    "germany": "de",
    "france": "fr",
    "italy": "it",
    "spain": "es",
    "portugal": "pt",
    "netherlands": "nl",
    "belgium": "be",
    "poland": "pl",
    "czech-republic": "cz",
    "slovakia": "sk",
    "ukraine": "ua",
    "belarus": "by",
    "kazakhstan": "kz",
}


def _ascii_slug(name: str) -> str:
    """Lowercase ASCII slug for tv-logos filenames.

    "Channel One" → "channel-one"
    "2x2" → "2x2"
    "Viju TV1000" → "viju-tv1000"
    Non-ASCII characters are dropped.
    """
    s = name.lower().strip()
    s = unicodedata.normalize("NFKD", s)
    # Drop combining marks and any non-ASCII bytes
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _fs_slug(name: str) -> str:
    """Filesystem-safe slug for local cache filenames. Keeps Cyrillic readable."""
    s = name.strip().lower()
    s = re.sub(r"[^\w\s-]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s.strip())
    return unicodedata.normalize("NFC", s) or "unknown"


def _tv_logos_candidates(entry: LogoEntry) -> list[str]:
    """Produce ordered candidate URLs against tv-logo/tv-logos CDN."""
    directory = _COUNTRY_DIRS.get(entry.country, "world")
    suffix = _COUNTRY_SUFFIXES.get(directory)

    slugs: list[str] = []
    for name in entry.all_names:
        slug = _ascii_slug(name)
        if slug and slug not in slugs:
            slugs.append(slug)

    candidates: list[str] = []
    for slug in slugs:
        if suffix:
            candidates.append(f"{_TV_LOGOS_CDN}/{directory}/{slug}-{suffix}.png")
        candidates.append(f"{_TV_LOGOS_CDN}/{directory}/{slug}.png")
    # World directory as a last-resort fallback
    if directory != "world":
        for slug in slugs:
            candidates.append(f"{_TV_LOGOS_CDN}/world/{slug}.png")
    return candidates


class LogoResolver:
    def __init__(
        self,
        cache_dir: Path,
        iptv_index: Optional[IptvOrgIndex] = None,
        epg_index: Optional[EpgIconIndex] = None,
        timeout: float = 8.0,
    ) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._timeout = timeout
        self._index = iptv_index
        self._epg = epg_index
        self._misses: set[str] = set()
        # Per-slug locks allow concurrent downloads for *different* channels
        # while still preventing duplicate concurrent fetches for the *same* channel.
        self._dict_lock = asyncio.Lock()  # guards _slug_locks
        self._slug_locks: dict[str, asyncio.Lock] = {}

    @property
    def index(self) -> Optional[IptvOrgIndex]:
        return self._index

    def cache_path(self, slug: str) -> Path:
        return self._cache_dir / f"{slug}.png"

    def cached_bytes(self, slug: str) -> Optional[bytes]:
        path = self.cache_path(slug)
        if path.exists() and path.stat().st_size > 0:
            try:
                return path.read_bytes()
            except OSError:
                return None
        return None

    def has_cached(self, name: str) -> bool:
        return self.cached_bytes(_fs_slug(name)) is not None

    async def resolve(self, name: str) -> Optional[bytes]:
        slug = _fs_slug(name)
        if not slug:
            return None

        cached = self.cached_bytes(slug)
        if cached is not None:
            return cached

        if slug in self._misses:
            return None

        # Get or create a per-slug lock (allows parallel downloads for different channels)
        async with self._dict_lock:
            if slug not in self._slug_locks:
                self._slug_locks[slug] = asyncio.Lock()
            slug_lock = self._slug_locks[slug]

        async with slug_lock:
            # Double-check after acquiring the slug lock
            cached = self.cached_bytes(slug)
            if cached is not None:
                return cached
            if slug in self._misses:
                return None

            candidates: list[str] = []

            # 1) RTRS: vattik/picons-rtrs for the 20 Russian free-to-air channels.
            #    Tried first because these are high-quality and always available.
            rtrs = _rtrs_candidate(name)
            if rtrs is not None:
                candidates.append(rtrs)

            # 2) tv-logos CDN, derived from iptv-org entry (English name + country).
            entry: Optional[LogoEntry] = None
            if self._index is not None:
                entry = self._index.lookup(name)
                if entry is not None:
                    candidates.extend(_tv_logos_candidates(entry))

            # 3) EPG icon index (epg.one/img/) — covers ~7000 channels including
            #    provider-specific Russian channels not in iptv-org.
            if self._epg is not None:
                epg_url = self._epg.lookup(name)
                if epg_url:
                    candidates.append(epg_url)

            # 4) Fallback: raw URL from iptv-org/database (often imgur / provider CDN)
            if entry is not None and entry.url:
                candidates.append(entry.url)

            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers=_BROWSER_HEADERS,
            ) as client:
                for url in candidates:
                    try:
                        resp = await client.get(url)
                    except httpx.HTTPError:
                        continue
                    if resp.status_code == 200 and resp.content:
                        self.cache_path(slug).write_bytes(resp.content)
                        return resp.content

            self._misses.add(slug)
            return None
