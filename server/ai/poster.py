"""Poster resolver — TMDB (if key) → Wikipedia → None.

Given a handful of keywords (or a plain title) return a URL to a reasonable
hero image we can show on a digest card. Results are cached to disk so the
same title never re-queries a remote service.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/multi"
WIKI_SUMMARY_URL_TEMPLATE = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
WIKI_API_URL_TEMPLATE = "https://{lang}.wikipedia.org/w/api.php"
CACHE_TTL_SECONDS = 30 * 24 * 3600  # 30 days
# Negative cache: if lookup failed, only retry after 24h (not 30 days).
NEGATIVE_TTL_SECONDS = 24 * 3600


@dataclass(frozen=True, slots=True)
class PosterHit:
    url: str
    source: str  # tmdb | wikipedia | none

    def to_dict(self) -> dict[str, str]:
        return {"url": self.url, "source": self.source}


class PosterResolver:
    """Disk-cached poster resolver safe to share across coroutines."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._file = cache_dir / "posters.json"
        self._mem: dict[str, tuple[float, PosterHit | None]] = self._load()
        self._lock = asyncio.Lock()
        self._tmdb_key = os.environ.get("TMDB_API_KEY", "").strip()

    @property
    def root(self) -> Path:
        return self._cache_dir

    def _load(self) -> dict[str, tuple[float, PosterHit | None]]:
        if not self._file.exists():
            return {}
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        out: dict[str, tuple[float, PosterHit | None]] = {}
        for key, entry in data.items():
            ts = float(entry.get("ts", 0))
            hit = entry.get("hit")
            out[key] = (
                ts,
                PosterHit(url=hit["url"], source=hit["source"]) if hit else None,
            )
        return out

    def _save(self) -> None:
        payload = {
            key: {
                "ts": ts,
                "hit": hit.to_dict() if hit else None,
            }
            for key, (ts, hit) in self._mem.items()
        }
        with contextlib.suppress(OSError):
            self._file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    @staticmethod
    def _key(keywords: str, lang: str) -> str:
        return f"{lang}::{re.sub(r'\\s+', ' ', keywords.strip().lower())}"

    async def resolve(self, keywords: str, lang: str = "ru") -> PosterHit | None:
        clean = keywords.strip()
        if not clean:
            return None
        key = self._key(clean, lang)
        cached = self._mem.get(key)
        if cached:
            ts, hit = cached
            ttl = CACHE_TTL_SECONDS if hit else NEGATIVE_TTL_SECONDS
            if time.time() - ts < ttl:
                return hit

        hit = await self._fetch(clean, lang)
        async with self._lock:
            self._mem[key] = (time.time(), hit)
            self._save()
        return hit

    async def _fetch(self, keywords: str, lang: str) -> PosterHit | None:
        async with httpx.AsyncClient(
            timeout=6.0,
            follow_redirects=True,
            headers={"User-Agent": "m3u-studio/0.6 (poster-lookup)"},
        ) as client:
            if self._tmdb_key:
                tmdb = await _tmdb_search(client, keywords, lang, self._tmdb_key)
                if tmdb:
                    return tmdb
            wiki = await _wiki_lookup(client, keywords, lang)
            if wiki:
                return wiki
        return None


async def _tmdb_search(
    client: httpx.AsyncClient,
    keywords: str,
    lang: str,
    api_key: str,
) -> PosterHit | None:
    """Try the full query, then progressively simpler variants.

    TMDB's search treats every token as a filter — "Inception 2010 film"
    returns zero results because no title literally contains the word
    "film". Stripping the disambiguation tail recovers the match.
    """
    for query in _query_variants(keywords):
        params = {
            "api_key": api_key,
            "query": query,
            "language": "ru-RU" if lang == "ru" else "en-US",
            "include_adult": "false",
        }
        try:
            resp = await client.get(TMDB_SEARCH_URL, params=params)
            resp.raise_for_status()
        except httpx.HTTPError:
            continue
        data: dict[str, Any] = resp.json()
        for hit in data.get("results", []):
            poster = hit.get("poster_path")
            if poster:
                return PosterHit(url=f"{TMDB_IMAGE_BASE}{poster}", source="tmdb")
    return None


async def _wiki_lookup(
    client: httpx.AsyncClient,
    keywords: str,
    lang: str,
) -> PosterHit | None:
    """Resolve a Wikipedia image via three cascading strategies.

    1. REST summary with the exact title — fastest, great for unambiguous films.
    2. MediaWiki fuzzy search (handles mis-capitalization, inflected forms,
       and generic queries like "Roma vs Atalanta" by ranking pages by
       relevance and picking the top one that actually has an image).
    3. For "X vs Y" queries, fall back to searching just the first half so
       a match like "Roma vs Atalanta" can return the AS Roma crest when no
       match-specific article exists.
    """
    order = ["ru", "en"] if lang == "ru" else ["en", "ru"]
    stripped = keywords.strip()

    # 1. REST summary for each progressive variant. This is the ONLY path that
    # returns fair-use movie posters on en.wikipedia (hosted outside Commons,
    # which the pageimages API won't surface). So "Inception 2010 film" →
    # strip to "Inception" → REST returns the theatrical poster.
    for query in _query_variants(stripped):
        for wiki_lang in order:
            url = await _wiki_summary(client, query, wiki_lang)
            if url:
                return PosterHit(url=url, source="wikipedia")

    # 2. Direct page-image lookup for pages that don't surface via REST
    # (rare, but covers sports event pages whose lead image lives on Commons).
    for query in _query_variants(stripped):
        for wiki_lang in order:
            url = await _wiki_direct(client, query, wiki_lang)
            if url:
                return PosterHit(url=url, source="wikipedia")

    # 3. Fuzzy search — last resort when no exact page matches.
    for query in _query_variants(stripped):
        for wiki_lang in order:
            url = await _wiki_search(client, query, wiki_lang)
            if url:
                return PosterHit(url=url, source="wikipedia")

    # 4. "X vs Y" — try each half (useful for sports matchups without a
    # dedicated article; gives us a team crest or event logo).
    lower = stripped.lower()
    for splitter in (" vs ", " - ", " — "):
        if splitter in lower:
            halves = [h.strip() for h in stripped.split(splitter, 1) if h.strip()]
            for half in halves:
                for wiki_lang in order:
                    summary = await _wiki_summary(client, half, wiki_lang)
                    if summary:
                        return PosterHit(url=summary, source="wikipedia")
                for wiki_lang in order:
                    direct = await _wiki_direct(client, half, wiki_lang)
                    if direct:
                        return PosterHit(url=direct, source="wikipedia")
                for wiki_lang in order:
                    srch = await _wiki_search(client, half, wiki_lang)
                    if srch:
                        return PosterHit(url=srch, source="wikipedia")
            break

    return None


async def _wiki_summary(
    client: httpx.AsyncClient,
    title: str,
    wiki_lang: str,
) -> str | None:
    """Fetch the page summary image (this is the ONLY Wikipedia path that
    returns fair-use images like movie posters).
    """
    quoted = quote(title.strip().replace(" ", "_"), safe="")
    url = WIKI_SUMMARY_URL_TEMPLATE.format(lang=wiki_lang, title=quoted)
    try:
        resp = await client.get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    data: dict[str, Any] = resp.json()
    # A disambiguation page has no useful lead image — skip.
    if data.get("type") == "disambiguation":
        return None
    return _image_from_summary(data)


async def _wiki_direct(
    client: httpx.AsyncClient,
    title: str,
    wiki_lang: str,
) -> str | None:
    """Fetch the lead image of the exact page ``title`` (follows redirects)."""
    params = {
        "action": "query",
        "format": "json",
        "prop": "pageimages",
        "piprop": "thumbnail|original",
        "pithumbsize": "640",
        "redirects": "1",
        "titles": title,
    }
    try:
        resp = await client.get(
            WIKI_API_URL_TEMPLATE.format(lang=wiki_lang),
            params=params,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    data: dict[str, Any] = resp.json()
    pages = data.get("query", {}).get("pages", {})
    if not isinstance(pages, dict):
        return None
    for page in pages.values():
        if not isinstance(page, dict):
            continue
        # "missing" key is present when the page doesn't exist.
        if "missing" in page:
            continue
        for key in ("original", "thumbnail"):
            block = page.get(key)
            if isinstance(block, dict) and block.get("source"):
                return str(block["source"])
    return None


_STRIP_TAIL_RE = re.compile(
    r"\s+(?:"
    r"\(?\b(19|20)\d{2}\b\)?"  # 2010 | (2010)
    r"|film|movie|tv\s+series|tv\s+show|series"  # EN suffixes
    r"|фильм|сериал|телесериал|шоу|мультфильм"  # RU suffixes
    r")\s*$",
    re.IGNORECASE,
)


def _query_variants(query: str) -> list[str]:
    """Generate progressively simpler variants plus Wikipedia-style
    parenthetical disambiguations.

    Example: ``"Severance TV series"`` → ``["Severance TV series",
    "Severance (TV series)", "Severance"]``.
    """
    seen: set[str] = set()
    out: list[str] = []

    def push(v: str) -> None:
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)

    original = query.strip()
    push(original)

    # Wikipedia disambiguation style: "Title (film)", "Title (TV series)".
    lower = original.lower()
    base_stripped = _STRIP_TAIL_RE.sub("", original).strip()
    if base_stripped and base_stripped.lower() != lower:
        if "tv series" in lower or "tv show" in lower:
            push(f"{base_stripped} (TV series)")
        elif "film" in lower or "movie" in lower:
            push(f"{base_stripped} (film)")

    current = original
    for _ in range(3):
        new = _STRIP_TAIL_RE.sub("", current).strip()
        if new == current:
            break
        push(new)
        current = new
    return out


def _image_from_summary(data: dict[str, Any]) -> str | None:
    for key in ("originalimage", "thumbnail"):
        block = data.get(key)
        if isinstance(block, dict) and block.get("source"):
            return str(block["source"])
    return None


async def _wiki_search(
    client: httpx.AsyncClient,
    query: str,
    wiki_lang: str,
) -> str | None:
    """Ask MediaWiki for the top matching page that has a pageimage."""
    params = {
        "action": "query",
        "format": "json",
        "prop": "pageimages",
        "piprop": "thumbnail|original",
        "pithumbsize": "640",
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": "5",
        "gsrnamespace": "0",
    }
    try:
        resp = await client.get(
            WIKI_API_URL_TEMPLATE.format(lang=wiki_lang),
            params=params,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    data: dict[str, Any] = resp.json()
    pages = data.get("query", {}).get("pages", {})
    if not isinstance(pages, dict):
        return None
    # Order by search relevance (`index`), then prefer pages that actually
    # have an original image over thumbnail-only ones.
    ordered = sorted(
        pages.values(),
        key=lambda p: p.get("index", 99) if isinstance(p, dict) else 99,
    )
    for page in ordered:
        if not isinstance(page, dict):
            continue
        for key in ("original", "thumbnail"):
            block = page.get(key)
            if isinstance(block, dict) and block.get("source"):
                return str(block["source"])
    return None
