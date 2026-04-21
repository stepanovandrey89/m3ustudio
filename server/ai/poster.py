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

TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w780"
TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/multi"
TMDB_IMAGES_URL = "https://api.themoviedb.org/3/{media_type}/{id}/images"
UNSPLASH_SEARCH_URL = "https://api.unsplash.com/search/photos"
# TheSportsDB — open sports database. Events, team crests, league
# logos. Free key "3" is explicitly provided for demo / small-site use
# in their API docs (https://www.thesportsdb.com/api.php). Swap in a
# registered key via env if we ever hit their rate limit.
SPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json"
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
        self._img_dir = cache_dir / "posters_img"
        self._img_dir.mkdir(parents=True, exist_ok=True)
        self._mem: dict[str, tuple[float, PosterHit | None]] = self._load()
        self._lock = asyncio.Lock()
        self._tmdb_key = os.environ.get("TMDB_API_KEY", "").strip()
        self._sportsdb_key = os.environ.get("SPORTSDB_API_KEY", "3").strip() or "3"
        self._unsplash_key = os.environ.get("UNSPLASH_ACCESS_KEY", "").strip()

    @property
    def root(self) -> Path:
        return self._cache_dir

    @property
    def img_dir(self) -> Path:
        return self._img_dir

    def local_path_for(self, remote_url: str) -> Path:
        """Return the deterministic local cache path for a remote image URL."""
        import hashlib
        from pathlib import PurePath

        digest = hashlib.sha1(remote_url.encode("utf-8")).hexdigest()
        ext = PurePath(remote_url.split("?", 1)[0]).suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            ext = ".jpg"
        return self._img_dir / f"{digest}{ext}"

    async def prefetch(self, remote_url: str, client: httpx.AsyncClient | None = None) -> bool:
        """Download a poster image to local cache if not already present."""
        path = self.local_path_for(remote_url)
        if path.exists() and path.stat().st_size > 0:
            return True
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=True,
                headers={"User-Agent": "m3u-studio/0.7 (poster-prefetch)"},
            )
        try:
            resp = await client.get(remote_url)
            resp.raise_for_status()
            path.write_bytes(resp.content)
            return True
        except (httpx.HTTPError, OSError):
            return False
        finally:
            if owns_client:
                await client.aclose()

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

    async def resolve(
        self,
        keywords: str,
        lang: str = "ru",
        *,
        allow_commons: bool = False,
    ) -> PosterHit | None:
        """Resolve a poster.

        ``allow_commons=True`` opens up Wikipedia Commons images — useful
        for sport (team crests, league logos) where Commons hosts the
        correct identity art. For film content keep it False; Commons
        there yields actor portraits that look wrong as film posters.
        """
        clean = keywords.strip()
        if not clean:
            return None
        key = self._key(clean, lang)
        if allow_commons:
            key = f"{key}::commons"
        cached = self._mem.get(key)
        if cached:
            ts, hit = cached
            ttl = CACHE_TTL_SECONDS if hit else NEGATIVE_TTL_SECONDS
            if time.time() - ts < ttl:
                return hit

        hit = await self._fetch(clean, lang, allow_commons=allow_commons)
        async with self._lock:
            self._mem[key] = (time.time(), hit)
            self._save()
        return hit

    async def resolve_tmdb_tv(self, keywords: str) -> PosterHit | None:
        """Sport fallback: search TMDB for a single team/league name and
        accept the first TV show or film result that has a poster.

        For queries like ``"FC Barcelona"``, ``"Real Madrid"`` or
        ``"Formula 1"`` TMDB indexes high-quality docuseries posters
        («Драйв выживания», «Вместе до конца», «Внутри ФК Барселоны»)
        that give a sport tile a respectable on-topic visual when no
        crest or match poster exists.

        Not useful for matchup queries like ``"Barcelona vs Espanyol"``
        — TMDB returns nothing. The caller is expected to split halves
        and feed each one separately.
        """
        clean = keywords.strip()
        if not clean or not self._tmdb_key:
            return None
        key = f"sport-tmdb::{self._key(clean, 'ru')}"
        cached = self._mem.get(key)
        if cached:
            ts, hit = cached
            ttl = CACHE_TTL_SECONDS if hit else NEGATIVE_TTL_SECONDS
            if time.time() - ts < ttl:
                return hit

        hit: PosterHit | None = None
        async with httpx.AsyncClient(
            timeout=4.0,
            follow_redirects=True,
            headers={"User-Agent": "m3u-studio/0.7 (sport-tmdb-tv)"},
        ) as client:
            try:
                resp = await client.get(
                    TMDB_SEARCH_URL,
                    params={
                        "api_key": self._tmdb_key,
                        "query": clean,
                        "language": "ru-RU",
                        "include_adult": "false",
                    },
                )
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
            except httpx.HTTPError:
                data = {}
            for cand in data.get("results") or []:
                media_type = cand.get("media_type") or ""
                if media_type not in ("tv", "movie"):
                    continue
                default_poster = cand.get("poster_path")
                if not default_poster:
                    continue
                tmdb_id = cand.get("id")
                chosen = None
                if tmdb_id:
                    chosen = await _tmdb_pick_localized_poster(
                        client, media_type, tmdb_id, "ru", self._tmdb_key
                    )
                final = chosen or default_poster
                if final:
                    hit = PosterHit(url=f"{TMDB_IMAGE_BASE}{final}", source="tmdb")
                    break
            if hit is not None:
                await self.prefetch(hit.url, client=client)

        async with self._lock:
            self._mem[key] = (time.time(), hit)
            self._save()
        return hit

    async def resolve_unsplash(self, keywords: str) -> PosterHit | None:
        """Free editorial-photo fallback for sport tiles.

        Unsplash indexes thousands of high-quality team / stadium /
        equipment photos. For a query like ``"Spartak Moscow"`` or
        ``"Formula 1 Ferrari"`` it returns real photos without the
        watermarks or licensing friction of paid stocks. Orientation is
        constrained to portrait/squarish so the frame matches the poster
        tile without awkward letterboxing.

        Requires ``UNSPLASH_ACCESS_KEY`` in the environment. Missing key
        → returns ``None`` so the caller falls through to the next
        fallback.
        """
        clean = keywords.strip()
        if not clean or not self._unsplash_key:
            return None
        key = f"sport-unsplash::{self._key(clean, 'ru')}"
        cached = self._mem.get(key)
        if cached:
            ts, hit = cached
            ttl = CACHE_TTL_SECONDS if hit else NEGATIVE_TTL_SECONDS
            if time.time() - ts < ttl:
                return hit

        hit: PosterHit | None = None
        async with httpx.AsyncClient(
            timeout=4.0,
            follow_redirects=True,
            headers={
                "User-Agent": "m3u-studio/0.7 (sport-unsplash)",
                "Authorization": f"Client-ID {self._unsplash_key}",
                "Accept-Version": "v1",
            },
        ) as client:
            # Portrait first to match the poster tile; if none available,
            # squarish is acceptable. Landscape we skip — it crops badly.
            for orientation in ("portrait", "squarish"):
                try:
                    resp = await client.get(
                        UNSPLASH_SEARCH_URL,
                        params={
                            "query": clean,
                            "per_page": "3",
                            "orientation": orientation,
                            "content_filter": "high",
                        },
                    )
                    resp.raise_for_status()
                    data: dict[str, Any] = resp.json()
                except httpx.HTTPError:
                    continue
                for result in data.get("results") or []:
                    urls = result.get("urls") or {}
                    url = urls.get("regular") or urls.get("small") or urls.get("full")
                    if url:
                        hit = PosterHit(url=str(url), source="unsplash")
                        break
                if hit:
                    break
            if hit is not None:
                await self.prefetch(hit.url, client=client)

        async with self._lock:
            self._mem[key] = (time.time(), hit)
            self._save()
        return hit

    async def resolve_sport(
        self, keywords: str, match_halves: list[str] | None = None
    ) -> PosterHit | None:
        """Sport-specific resolver. Uses TheSportsDB event/team endpoints
        which index real match posters and team crests, not TMDB films.

        ``match_halves`` is an optional pre-split list of opponents (e.g.
        ``["Bayern Munich", "Borussia Dortmund"]``) — if the event lookup
        misses we fall back to the first team's crest, and finally the
        second team's crest.
        """
        clean = keywords.strip()
        if not clean:
            return None
        # Cache key differs from film cache so sport-specific choices
        # (crest vs. film poster) don't collide.
        key = f"sport::{self._key(clean, 'ru')}"
        cached = self._mem.get(key)
        if cached:
            ts, hit = cached
            ttl = CACHE_TTL_SECONDS if hit else NEGATIVE_TTL_SECONDS
            if time.time() - ts < ttl:
                return hit

        async with httpx.AsyncClient(
            timeout=4.0,
            follow_redirects=True,
            headers={"User-Agent": "m3u-studio/0.7 (sport-lookup)"},
        ) as client:
            # Event search first — but TheSportsDB's ``strEvent`` is stored
            # as "Team A vs Team B", so we must synthesise that form even
            # when the caller's query has no explicit separator.
            event_queries = [clean]
            if match_halves and len(match_halves) >= 2:
                event_queries.insert(0, f"{match_halves[0]} vs {match_halves[1]}")
            hit: PosterHit | None = None
            for eq in event_queries:
                hit = await _sportsdb_event(client, eq, self._sportsdb_key)
                if hit:
                    break
            if hit is None and match_halves:
                for half in match_halves:
                    hit = await _sportsdb_team(client, half, self._sportsdb_key)
                    if hit:
                        break
            if hit is None:
                # Some broadcasts have no specific event or teams we can
                # find (F1 etap, NHL playoff game). Strip the per-event
                # suffix and look for the league/series logo instead so
                # "Формула 1. 1-й этап. Мельбурн" still surfaces the F1
                # series logo and "UFC Burns vs Malott" falls back to
                # UFC's crest.
                for q in _sportsdb_league_variants(clean):
                    hit = await _sportsdb_league(client, q, self._sportsdb_key)
                    if hit:
                        break
            if hit is None:
                # Last resort — full query as a team.
                hit = await _sportsdb_team(client, clean, self._sportsdb_key)
            if hit is not None:
                await self.prefetch(hit.url, client=client)

        async with self._lock:
            self._mem[key] = (time.time(), hit)
            self._save()
        return hit

    async def _fetch(
        self,
        keywords: str,
        lang: str,
        *,
        allow_commons: bool = False,
    ) -> PosterHit | None:
        async with httpx.AsyncClient(
            timeout=4.0,
            follow_redirects=True,
            headers={"User-Agent": "m3u-studio/0.7 (poster-lookup)"},
        ) as client:
            hit: PosterHit | None = None
            # TMDB first for every query — the /images endpoint now picks
            # the Russian-language poster variant when the film has one,
            # which gives us canonical theatrical artwork for both Russian
            # films and Hollywood dubs. Wikipedia is kept as a fallback
            # only; its fuzzy page-image heuristic routinely returns actor
            # portraits or unrelated paintings (e.g. "Матрица" → Muldashev,
            # "Интерны" → Ohlobystin) when the exact article title doesn't
            # resolve, which silently corrupts the digest.
            if self._tmdb_key:
                hit = await _tmdb_search(client, keywords, lang, self._tmdb_key)
            if hit is None:
                hit = await _wiki_lookup(client, keywords, lang, allow_commons=allow_commons)
            if hit is not None:
                # Pre-fetch the image so the frontend's first render hits a
                # file on disk instead of racing with the TMDB/Wiki round-trip.
                # Failure here is silent — the proxy endpoint will retry.
                await self.prefetch(hit.url, client=client)
            return hit


async def _tmdb_search(
    client: httpx.AsyncClient,
    keywords: str,
    lang: str,
    api_key: str,
) -> PosterHit | None:
    """Search TMDB, then pull the language-specific poster via /images.

    TMDB's default ``poster_path`` on a search result is whatever poster
    happens to be primary for the film's original release — usually the
    English theatrical one-sheet. For Russian films (and Hollywood titles
    we watch in Russian) the /images endpoint returns every poster variant
    including localized Russian theatrical plakats. We pick the best
    language match; if the images call fails we gracefully fall back to
    the default poster_path from search.

    The search still tries progressively simpler query variants ("Inception
    2010 film" → "Inception") because TMDB's search treats every token
    as a filter.
    """
    pref = "ru" if lang == "ru" else "en"

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
            media_type = hit.get("media_type") or "movie"
            if media_type not in ("movie", "tv"):
                continue
            tmdb_id = hit.get("id")
            default_poster = hit.get("poster_path")
            chosen = None
            if tmdb_id:
                chosen = await _tmdb_pick_localized_poster(
                    client, media_type, tmdb_id, pref, api_key
                )
            final = chosen or default_poster
            if final:
                return PosterHit(url=f"{TMDB_IMAGE_BASE}{final}", source="tmdb")
    return None


async def _tmdb_pick_localized_poster(
    client: httpx.AsyncClient,
    media_type: str,
    tmdb_id: int,
    pref: str,
    api_key: str,
) -> str | None:
    """Pull /images for a TMDB title and return the best poster path.

    Ranking (lower is better):
      0. poster whose ``iso_639_1`` matches ``pref`` (e.g. ``ru``)
      1. poster with no language tag (neutral artwork, often the best
         theatrical imagery TMDB has)
      2. English poster
      3. any other language
    Ties break by highest community vote_count, then vote_average.

    Returns the chosen poster_path or ``None`` if /images failed or the
    film has no poster variants at all.
    """
    url = TMDB_IMAGES_URL.format(media_type=media_type, id=tmdb_id)
    params = {
        "api_key": api_key,
        "include_image_language": f"{pref},null,en",
    }
    try:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    posters = resp.json().get("posters") or []
    if not posters:
        return None

    def rank(p: dict[str, Any]) -> tuple[int, float, float]:
        iso = p.get("iso_639_1") or ""
        if iso == pref:
            order = 0
        elif iso == "":
            order = 1
        elif iso == "en":
            order = 2
        else:
            order = 3
        return (order, -(p.get("vote_count") or 0), -(p.get("vote_average") or 0))

    posters.sort(key=rank)
    return posters[0].get("file_path")


def _is_fair_use_poster(url: str | None) -> bool:
    """Whether a Wikipedia image URL looks like a fair-use film poster
    (hosted in a language-specific namespace, e.g. /wikipedia/ru/ or
    /wikipedia/en/), as opposed to a generic Commons photo.

    Commons-hosted images (/wikipedia/commons/) are usually free-license
    portraits of actors/authors/creators — Wikipedia's fuzzy search
    surfaces them instead of the film article when the EPG title mentions
    a recognisable name. Those false matches were the main source of
    "poster is Ivan Okhlobystin's face" cases, so the Wiki pipeline now
    only accepts images from fair-use language namespaces.
    """
    if not url:
        return False
    return "/wikipedia/ru/" in url or "/wikipedia/en/" in url


async def _wiki_lookup(
    client: httpx.AsyncClient,
    keywords: str,
    lang: str,
    *,
    allow_commons: bool = False,
) -> PosterHit | None:
    """Resolve a Wikipedia image via three cascading strategies.

    By default only fair-use namespace images are accepted
    (/wikipedia/ru/, /wikipedia/en/); Commons-hosted photos are rejected
    because Wikipedia's fuzzy page-image API returns actor portraits on
    ambiguous film queries. For sport ``allow_commons=True`` opens it
    up — team crests, league logos and event trophies live on Commons.
    """

    def acceptable(url: str | None) -> bool:
        if not url:
            return False
        return allow_commons or _is_fair_use_poster(url)

    order = ["ru", "en"] if lang == "ru" else ["en", "ru"]
    stripped = keywords.strip()

    # 1. REST summary for each progressive variant. This is the ONLY path that
    # returns fair-use movie posters on en.wikipedia (hosted outside Commons,
    # which the pageimages API won't surface). So "Inception 2010 film" →
    # strip to "Inception" → REST returns the theatrical poster.
    for query in _query_variants(stripped):
        for wiki_lang in order:
            url = await _wiki_summary(client, query, wiki_lang)
            if acceptable(url):
                return PosterHit(url=url, source="wikipedia")

    # 2. Direct page-image lookup for pages that don't surface via REST
    # (rare, but covers sports event pages whose lead image lives on Commons).
    for query in _query_variants(stripped):
        for wiki_lang in order:
            url = await _wiki_direct(client, query, wiki_lang)
            if acceptable(url):
                return PosterHit(url=url, source="wikipedia")

    # 3. Fuzzy search — last resort when no exact page matches.
    for query in _query_variants(stripped):
        for wiki_lang in order:
            url = await _wiki_search(client, query, wiki_lang)
            if acceptable(url):
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


def _has_cyrillic(text: str) -> bool:
    """True when ``text`` contains any Cyrillic codepoint — our signal to
    prefer Wikipedia over TMDB for Russian films.
    """
    return any("\u0400" <= c <= "\u04ff" for c in text)


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

    For Cyrillic titles the helper also appends Russian
    disambiguations — "Панчер" → ["Панчер", "Панчер (фильм)",
    "Панчер (сериал)"]. Bare Cyrillic names on ru.wiki frequently
    route to disambiguation pages (a dictionary entry, a town, a
    band) and those have no pageimage; the explicit "(фильм)"
    variant lands on the film article with its fair-use poster.
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
        if "tv series" in lower or "tv show" in lower or "сериал" in lower:
            push(f"{base_stripped} (TV series)")
            push(f"{base_stripped} (сериал)")
            push(f"{base_stripped} (телесериал)")
        elif "film" in lower or "movie" in lower or "фильм" in lower:
            push(f"{base_stripped} (film)")
            push(f"{base_stripped} (фильм)")

    current = original
    for _ in range(3):
        new = _STRIP_TAIL_RE.sub("", current).strip()
        if new == current:
            break
        push(new)
        current = new

    # Cyrillic titles without an explicit film/series marker still need
    # the Russian disambiguation — we don't know which kind, so push
    # both and let Wikipedia's 404 tell us which one is right.
    if _has_cyrillic(original):
        base_for_disambig = base_stripped or original
        if "(фильм)" not in base_for_disambig.lower():
            push(f"{base_for_disambig} (фильм)")
        if "(сериал)" not in base_for_disambig.lower():
            push(f"{base_for_disambig} (сериал)")
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


# ---------------------------------------------------------------------------
# TheSportsDB helpers
# ---------------------------------------------------------------------------


async def _sportsdb_event(client: httpx.AsyncClient, query: str, api_key: str) -> PosterHit | None:
    """Look up a specific match / fight / race on TheSportsDB."""
    url = f"{SPORTSDB_BASE}/{api_key}/searchevents.php"
    try:
        r = await client.get(url, params={"e": query})
        r.raise_for_status()
    except httpx.HTTPError:
        return None
    events = r.json().get("event") or []
    if not events:
        return None
    e = events[0]
    for field in ("strPoster", "strThumb", "strBanner", "strSquare"):
        v = e.get(field)
        if v:
            return PosterHit(url=str(v), source="sportsdb")
    return None


async def _sportsdb_team(client: httpx.AsyncClient, name: str, api_key: str) -> PosterHit | None:
    """Look up a team / club / fighter by name; return their crest."""
    url = f"{SPORTSDB_BASE}/{api_key}/searchteams.php"
    try:
        r = await client.get(url, params={"t": name})
        r.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"[sportsdb-team] http-fail {name!r}: {exc}", flush=True)
        return None
    try:
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        print(f"[sportsdb-team] json-fail {name!r}: {exc}", flush=True)
        return None
    teams = data.get("teams") or []
    if not teams:
        print(f"[sportsdb-team] no-teams {name!r} resp={len(r.content)}b", flush=True)
        return None
    t = teams[0]
    badge = t.get("strBadge") or t.get("strLogo")
    print(
        f"[sportsdb-team] {t.get('strTeam')!r} for {name!r} badge={'Y' if badge else 'N'}",
        flush=True,
    )
    if badge:
        return PosterHit(url=str(badge), source="sportsdb")
    return None


async def _sportsdb_league(client: httpx.AsyncClient, name: str, api_key: str) -> PosterHit | None:
    """Look up a league / series by name; return its badge or logo."""
    url = f"{SPORTSDB_BASE}/{api_key}/search_all_leagues.php"
    try:
        r = await client.get(url, params={"l": name})
        r.raise_for_status()
    except httpx.HTTPError:
        return None
    data = r.json()
    # API is inconsistent — sometimes {"countries": [..]}, sometimes [..]
    leagues: list[dict[str, Any]] = []
    if isinstance(data, dict):
        leagues = data.get("countries") or data.get("leagues") or []
    if not leagues:
        # Fallback: all_leagues.php then filter
        try:
            r2 = await client.get(f"{SPORTSDB_BASE}/{api_key}/all_leagues.php")
            r2.raise_for_status()
            all_leagues = r2.json().get("leagues") or []
        except httpx.HTTPError:
            return None
        name_lower = name.lower()
        leagues = [
            lg
            for lg in all_leagues
            if name_lower in (lg.get("strLeague") or "").lower()
            or name_lower in (lg.get("strLeagueAlternate") or "").lower()
        ]
    if not leagues:
        return None
    lg = leagues[0]
    for field in ("strPoster", "strBadge", "strLogo", "strTrophy"):
        v = lg.get(field)
        if v:
            return PosterHit(url=str(v), source="sportsdb")
    return None


# Well-known league/series keyword → TheSportsDB search name. Covers the
# common shows we see in the EPG; misses fall through to generic variants.
_SPORTSDB_LEAGUE_MAP: dict[str, str] = {
    "формула 1": "Formula 1",
    "formula 1": "Formula 1",
    "formula1": "Formula 1",
    "f1": "Formula 1",
    "формула 2": "Formula 2",
    "formula 2": "Formula 2",
    "формула 3": "Formula 2",
    "formula 3": "Formula 2",
    "нхл": "NHL",
    "nhl": "NHL",
    "нба": "NBA",
    "nba": "NBA",
    "кхл": "KHL",
    "khl": "KHL",
    "мхл": "MHL",
    "mhl": "MHL",
    "рпл": "Russian Premier League",
    "rpl": "Russian Premier League",
    "российская премьер-лига": "Russian Premier League",
    "russian premier league": "Russian Premier League",
    "премьер-лига": "English Premier League",
    "premier league": "English Premier League",
    "ла лига": "Spanish La Liga",
    "la liga": "Spanish La Liga",
    "чемпионат испании": "Spanish La Liga",
    "кубок испании": "Spanish La Liga",
    "copa del rey": "Spanish La Liga",
    "ligue 1": "French Ligue 1",
    "чемпионат франции": "French Ligue 1",
    "серия а": "Italian Serie A",
    "serie a": "Italian Serie A",
    "чемпионат италии": "Italian Serie A",
    "бундеслига": "German Bundesliga",
    "bundesliga": "German Bundesliga",
    "чемпионат германии": "German Bundesliga",
    "лига чемпионов": "UEFA Champions League",
    "champions league": "UEFA Champions League",
    "лига европы": "UEFA Europa League",
    "europa league": "UEFA Europa League",
    "ufc": "UFC",
    "mma": "UFC",
    "смешанные единоборства": "UFC",
    "nascar": "NASCAR Cup Series",
    "наскар": "NASCAR Cup Series",
    "moto gp": "MotoGP",
    "motogp": "MotoGP",
    "дартс": "Professional Darts",
    "darts": "Professional Darts",
    "теннис": "ATP Tour",
    "tennis": "ATP Tour",
    "atp": "ATP Tour",
    "wta": "WTA Tour",
    "волейбол": "Russian Volleyball Super League",
    "volleyball": "Russian Volleyball Super League",
    "гандбол": "Handball-Bundesliga",
    "handball": "Handball-Bundesliga",
    "snl": "Swiss NL A",
    "swiss national league": "Swiss NL A",
    "чемпионат турции": "Turkish Super Lig",
    "super lig": "Turkish Super Lig",
}


def _sportsdb_league_variants(query: str) -> list[str]:
    """Produce league/series search candidates from a messy EPG title.

    Scans for known keywords (NHL, UFC, Формула 1, RPL, Champions League)
    and yields the canonical TheSportsDB search name for each match.
    Falls back to the first 2-3 tokens of the query so unknown leagues
    get some chance.
    """
    lower = query.lower()
    out: list[str] = []
    seen: set[str] = set()
    for needle, canonical in _SPORTSDB_LEAGUE_MAP.items():
        if needle in lower and canonical not in seen:
            seen.add(canonical)
            out.append(canonical)
    if not out:
        # Generic fallback: first meaningful word(s)
        tokens = [t for t in re.split(r"[^\wёЁ]+", query) if t]
        if tokens:
            out.append(" ".join(tokens[:2]))
    return out
