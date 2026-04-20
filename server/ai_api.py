"""HTTP surface for AI assistant + daily digest + recordings.

Wired into the main app via `include_router(ai_api.build_router(state))`.
Keeps the AI/recording surface out of main.py so it stays approachable.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from server.ai.client import AIConfig, get_client
from server.ai.context import (
    build_main_schedule,
    channels_mentioned,
    narrow_by_programme_content,
)
from server.ai.digest import ALL_THEMES, DigestCache, Theme
from server.ai.generate import ToolExecutor, _clean_channel_id, generate_digest, stream_chat
from server.ai.poster import PosterResolver
from server.notify.telegram import TelegramClient, TelegramConfig
from server.planner import PlanStore
from server.planner.scheduler import delete_plan_messages, notify_plan_created
from server.recordings import RecordingManager

# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatBody(BaseModel):
    messages: list[ChatMessage]
    lang: str = Field(default="ru")
    # When true, the chat request is allowed to pull 7 days of EPG instead of
    # 12 h. Triggered by the "Хочу больше" chip in the UI so everyday chats
    # don't pay the token cost of a week-wide schedule.
    deep_search: bool = Field(default=False)


class RecordBody(BaseModel):
    channel_id: str
    title: str
    start: str  # ISO
    stop: str
    theme: str = "other"
    poster_keywords: str = ""
    lang: str = "ru"


class PlanBody(BaseModel):
    channel_id: str
    title: str
    start: str
    stop: str
    theme: str = "other"
    blurb: str = ""
    poster_keywords: str = ""
    lang: str = "ru"


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def build_router(state: Any) -> APIRouter:  # noqa: ANN401 — state is the main AppState
    router = APIRouter(prefix="/api")

    digest_cache: DigestCache = state.digest_cache
    recordings: RecordingManager = state.recordings
    posters: PosterResolver = state.posters
    plans: PlanStore = state.plans

    # ------------- Status -------------------------------------------------

    @router.get("/ai/status")
    def ai_status() -> JSONResponse:
        cfg = AIConfig.from_env()
        return JSONResponse({"enabled": cfg.enabled, "model": cfg.model})

    # ------------- Daily Digest ------------------------------------------

    @router.get("/ai/digest")
    async def digest(
        theme: str = Query(default="sport"),
        lang: str = Query(default="ru"),
        refresh: bool = Query(default=False),
    ) -> JSONResponse:
        if theme not in ALL_THEMES:
            raise HTTPException(400, f"Unknown theme: {theme}")
        theme_typed: Theme = theme  # type: ignore[assignment]

        client = get_client()
        cfg = AIConfig.from_env()
        if client is None:
            raise HTTPException(503, "OPENAI_API_KEY is not configured")

        today = date.today()
        if not refresh:
            cached = digest_cache.get(today, theme_typed, lang)
            if cached is not None:
                return JSONResponse({"cached": True, **cached.to_dict()})

        main_channels = _main_channels(state)
        # Cinema: feature films only. All Main channels sit in the
        # "Основное" group in the source playlist so we can't filter by
        # channel-group here — use name patterns instead. Drops sport/
        # news/kids/music channels plus generalist news-heavy channels
        # (ТВЦ / НТВ) that otherwise leak talk-shows into "cinema".
        if theme_typed == "cinema":
            excluded_name_patterns = (
                "матч",
                "match",
                "setanta",
                "eurosport",
                "sport",
                "нтв",
                "твц",
                "россия 24",
                "мир 24",
                "рбк",
                "euronews",
                "дождь",
                "карусель",
                "мульт",
                "nick",
                "disney",
                "дисней",
                "детск",
                "kids",
                "муз тв",
                "mtv",
                "music",
                "музыка",
            )
            main_channels = [
                ch
                for ch in main_channels
                if not any(p in ch.name.lower() for p in excluded_name_patterns)
            ]
        schedules = build_main_schedule(
            state.epg,
            main_channels,
            past_hours=0,
            future_hours=12,
            only_upcoming=True,
        )
        # Pre-narrow EPG to programmes that match the theme via title/desc
        # keywords. Cuts prompt size 5-10x and keeps the OpenAI response
        # under Cloudflare's 100s edge cap. Empty result falls back to the
        # full slate so the model can still look at "assistant picks" for
        # anything.
        theme_keywords = _THEME_KEYWORDS.get(theme_typed, [])
        if theme_keywords:
            narrowed = _narrow_by_keywords(schedules, theme_keywords)
            if narrowed:
                schedules = narrowed
        result = await generate_digest(client, cfg, schedules, theme_typed, lang)
        # Resolve every poster in parallel BEFORE responding / caching so
        # the frontend never renders a "blank card → flash of content"
        # when the browser plays catch-up on /api/ai/poster requests.
        result = await _hydrate_digest_posters(result, state.posters, lang)
        # Don't persist empty digests — a transient model glitch would otherwise
        # freeze an "empty" result on disk for the rest of the day, and the
        # frontend would keep serving it until the user hits refresh or the
        # date rolls over. Letting the next request regenerate is cheap.
        if result.items:
            digest_cache.put(result)
        return JSONResponse({"cached": False, **result.to_dict()})

    @router.delete("/ai/digest")
    def invalidate_digest() -> JSONResponse:
        count = digest_cache.invalidate()
        return JSONResponse({"ok": True, "deleted": count})

    @router.get("/ai/poster")
    async def get_poster(
        keywords: str = Query(..., min_length=1),
        lang: str = Query(default="ru"),
        fallback: str = Query(default=""),
    ) -> JSONResponse:
        """Resolve a poster for keywords; if that fails, try the optional
        fallback. Returned URL points at our local image proxy so the browser
        never talks to TMDB/Wikipedia directly — avoids the half-dozen CORS /
        CSP / referrer issues that caused some cards to silently skip the
        image.
        """
        hit = await posters.resolve(keywords, lang)
        if hit is None and fallback and fallback.strip() != keywords.strip():
            hit = await posters.resolve(fallback, lang)
        if hit is None:
            return JSONResponse({"url": None, "source": "none"})
        proxied = f"/api/ai/poster-image?src={quote(hit.url, safe='')}"
        return JSONResponse({"url": proxied, "source": hit.source})

    @router.get("/ai/poster-image")
    async def poster_image(src: str = Query(..., min_length=8)) -> FileResponse:
        """Download-and-cache TMDB / Wikipedia images locally.

        Only the two trusted CDNs are allowed — we won't become an open proxy
        for arbitrary URLs. Cached files are keyed by sha1 of the full source
        URL; extension is preserved where possible so browsers send the right
        Accept.
        """
        allowed_hosts = {
            "image.tmdb.org",
            "upload.wikimedia.org",
            "commons.wikimedia.org",
        }
        parsed = urlparse(src)
        if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
            raise HTTPException(400, "image source not allowed")

        img_dir: Path = state.posters.root / "posters_img"
        img_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(src.encode("utf-8")).hexdigest()
        ext = Path(parsed.path).suffix.lower() or ".jpg"
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            ext = ".jpg"
        cache_path = img_dir / f"{digest}{ext}"

        if not cache_path.exists():
            try:
                async with httpx.AsyncClient(
                    timeout=10.0,
                    follow_redirects=True,
                    headers={"User-Agent": "m3u-studio/0.7 (poster-proxy)"},
                ) as client:
                    resp = await client.get(src)
                    resp.raise_for_status()
                    cache_path.write_bytes(resp.content)
            except httpx.HTTPError as exc:
                raise HTTPException(502, f"upstream fetch failed: {exc}") from exc

        media_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }
        return FileResponse(
            cache_path,
            media_type=media_map.get(ext, "image/jpeg"),
            headers={"Cache-Control": "public, max-age=604800"},
        )

    # ------------- Chat (SSE) --------------------------------------------

    @router.post("/ai/chat")
    async def chat(body: ChatBody, request: Request) -> StreamingResponse:
        client = get_client()
        cfg = AIConfig.from_env()
        if client is None:
            raise HTTPException(503, "OPENAI_API_KEY is not configured")

        main_channels = _main_channels(state)
        # Default chat scope: next 8 h, strictly upcoming. `deep_search`
        # widens to 7 days for queries like "what's on Champions League next
        # Tuesday" where the everyday window can't reach.
        future_hours = 168 if body.deep_search else 8
        # Cap entries per channel to keep the prompt bounded. A normal-mode
        # "what's on tonight" never needs 12 programmes from a single channel.
        max_per_channel = None if body.deep_search else 6
        # If the user named a channel in their latest message, restrict the
        # EPG context to just those channels — no reason to send 149
        # favourites of programme data when the question is about one.
        last_user_msg = next(
            (m.content for m in reversed(body.messages) if m.role == "user"),
            "",
        )
        scoped_channels = channels_mentioned(last_user_msg, main_channels) or main_channels
        schedules = build_main_schedule(
            state.epg,
            scoped_channels,
            past_hours=0,
            future_hours=future_hours,
            max_per_channel=max_per_channel,
            only_upcoming=True,
        )
        # Keyword-narrow whenever the user's message has concrete search
        # terms — "футбол сегодня", "фильм про космос", "когда Спартак?".
        # If there are no matches we keep the full slate so open-ended
        # "что посмотреть?" queries still have context. Previously this
        # only ran in deep-search mode; applying it in normal mode too
        # routinely cuts the EPG block 5-10x.
        if len(scoped_channels) == len(main_channels):
            schedules = narrow_by_programme_content(last_user_msg, schedules)
        history = [m.model_dump() for m in body.messages]

        # Tool executor bound to this request's channel map.
        tools = ToolExecutor(
            on_record=lambda **kw: _tool_record(state, **kw),
            on_list_recordings=lambda: _tool_list(recordings),
            on_recommend=lambda **kw: _tool_recommend(state, body.lang, **kw),
        )

        async def event_stream():
            async for event in stream_chat(
                client, cfg, history, schedules, body.lang, tools, deep=body.deep_search
            ):
                if await request.is_disconnected():
                    break
                payload = json.dumps(event, ensure_ascii=False, default=_json_safe)
                yield f"data: {payload}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------- Recordings --------------------------------------------

    @router.get("/recordings")
    def list_recordings() -> JSONResponse:
        entries = [e.to_dict() for e in recordings.list()]
        return JSONResponse({"items": entries})

    @router.post("/recordings")
    async def start_recording(body: RecordBody) -> JSONResponse:
        result = await _tool_record(state, **body.model_dump())
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "recording failed"))
        return JSONResponse(result)

    @router.delete("/recordings/{rec_id}")
    async def delete_recording(rec_id: str) -> JSONResponse:
        ok = await recordings.delete(rec_id)
        if not ok:
            raise HTTPException(404, "Recording not found")
        return JSONResponse({"ok": True})

    @router.post("/recordings/{rec_id}/cancel")
    async def cancel_recording(rec_id: str) -> JSONResponse:
        ok = await recordings.cancel(rec_id)
        return JSONResponse({"ok": ok})

    @router.post("/recordings/{rec_id}/pause")
    async def pause_recording(rec_id: str) -> JSONResponse:
        ok = await recordings.pause(rec_id)
        if not ok:
            raise HTTPException(400, "cannot pause")
        return JSONResponse({"ok": True})

    @router.post("/recordings/{rec_id}/resume")
    async def resume_recording(rec_id: str) -> JSONResponse:
        ok = await recordings.resume(rec_id)
        if not ok:
            raise HTTPException(400, "cannot resume")
        return JSONResponse({"ok": True})

    @router.get("/recordings/{rec_id}/file")
    def recording_file(rec_id: str) -> FileResponse:
        entry = recordings.get(rec_id)
        if entry is None:
            raise HTTPException(404, "Recording not found")
        path = recordings.root / entry.file
        if not path.exists():
            raise HTTPException(404, "File not on disk")
        return FileResponse(
            path,
            media_type="video/x-matroska",
            filename=f"{entry.title or entry.id}.mkv",
        )

    @router.get("/recordings/{rec_id}/part/{index}")
    def recording_part(rec_id: str, index: int) -> FileResponse:
        """Serve an individual recording segment for sequential playback."""
        entry = recordings.get(rec_id)
        if entry is None:
            raise HTTPException(404, "Recording not found")
        parts = entry.parts or ([entry.file] if entry.file else [])
        if index < 0 or index >= len(parts):
            raise HTTPException(404, "Part index out of range")
        path = recordings.root / parts[index]
        if not path.exists():
            raise HTTPException(404, "File not on disk")
        return FileResponse(path, media_type="video/x-matroska")

    # ------------- Plans (watch-later) -----------------------------------

    @router.get("/plans")
    def list_plans() -> JSONResponse:
        return JSONResponse({"items": [p.to_dict() for p in plans.list()]})

    @router.post("/plans")
    async def create_plan(body: PlanBody) -> JSONResponse:
        channel = state.playlist.by_id(body.channel_id)
        if channel is None:
            raise HTTPException(404, f"unknown channel_id: {body.channel_id}")

        # Resolve poster synchronously so the Telegram card has a hero image.
        poster_url = await _resolve_poster_for_title(
            posters,
            body.title,
            body.poster_keywords,
            body.lang,
        )

        plan = plans.add(
            channel_id=body.channel_id,
            channel_name=channel.name,
            title=body.title,
            start=body.start,
            stop=body.stop,
            theme=body.theme,
            blurb=body.blurb,
            poster_url=poster_url,
        )

        # Fire-and-forget Telegram notification — failure must not block UI.
        notify_result = await notify_plan_created(plan, plans)

        return JSONResponse({"ok": True, "plan": plan.to_dict(), "telegram": notify_result})

    @router.delete("/plans/{plan_id}")
    async def delete_plan(plan_id: str) -> JSONResponse:
        plan = plans.get(plan_id)
        if plan is None:
            raise HTTPException(404, "plan not found")
        # Wipe the Telegram cards first so the chat stays in sync with the UI.
        # Failures are swallowed inside delete_plan_messages — we still remove
        # the local record even if Telegram refuses (e.g. 48 h retention cap).
        await delete_plan_messages(plan)
        plans.delete(plan_id)
        return JSONResponse({"ok": True})

    @router.post("/plans/{plan_id}/cancel")
    def cancel_plan(plan_id: str) -> JSONResponse:
        plan = plans.get(plan_id)
        if plan is None:
            raise HTTPException(404, "plan not found")
        plans.set_status(plan_id, "cancelled")
        return JSONResponse({"ok": True})

    @router.get("/plans/status")
    def plans_status() -> JSONResponse:
        cfg = TelegramConfig.from_env()
        return JSONResponse(
            {
                "telegram_enabled": cfg.enabled,
                "base_url": cfg.base_url,
                "count": len(plans.list()),
            }
        )

    @router.post("/plans/test")
    async def plans_test_notify() -> JSONResponse:
        """Ping Telegram with a dummy card so the user can verify setup."""
        cfg = TelegramConfig.from_env()
        client = TelegramClient(cfg)
        if not client.enabled:
            raise HTTPException(503, "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
        result = await client.send_card(
            caption_html=(
                "✅ <b>m3u Studio — тест</b>\nЕсли ты это видишь, бот настроен правильно."
            ),
            poster_url=None,
            watch_url=None,
        )
        return JSONResponse(result)

    return router


# ---------------------------------------------------------------------------
# Tool handlers (bound inside router factory via closures)
# ---------------------------------------------------------------------------


async def _resolve_poster_for_title(
    posters: PosterResolver,
    title: str,
    poster_keywords: str,
    lang: str,
) -> str:
    """Resolve a poster URL for a programme.

    TMDB and Wikipedia canonical titles are almost always Latin-script, while
    EPG feed titles are Russian pirate/dub translations that rarely match
    exactly ("Остин Пауэрс: Похитители времени" vs TMDB's "Austin Powers: The
    Spy Who Shagged Me"). We therefore try the model's Latin
    ``poster_keywords`` hint FIRST when it is present and distinct from the
    title, and only fall back to the raw Cyrillic title if the Latin query
    turned up nothing. For English-language UI the order is identical.
    Returns an empty string on any failure so callers can just plug it in.
    """
    title_clean = (title or "").strip()
    latin_hint = (poster_keywords or "").strip()
    # Signal routing: the model supplies Latin ``poster_keywords`` only for
    # FOREIGN films (Hollywood titles, sports events). Russian films usually
    # arrive with no Latin hint.
    #   * useful Latin hint → foreign film → TMDB via Latin query
    #   * no Latin hint      → probably Russian → Wiki via Cyrillic title
    # ``_fetch`` internally orders providers (TMDB-first for Latin queries,
    # Wiki-first for Cyrillic), so both paths land on the right source.
    has_useful_latin = (
        bool(latin_hint)
        and latin_hint.lower() != title_clean.lower()
        and any(c.isascii() and c.isalpha() for c in latin_hint)
    )
    if has_useful_latin:
        primary = latin_hint
        fallback = title_clean
    else:
        primary = title_clean
        fallback = latin_hint if latin_hint and latin_hint != title_clean else ""
    try:
        hit = None
        if primary:
            hit = await posters.resolve(primary, lang)
        if hit is None and fallback:
            hit = await posters.resolve(fallback, lang)
        # Always return a proxied URL so the browser never talks directly to
        # image.tmdb.org / upload.wikimedia.org (avoids CORS, TSPU/ISP
        # blocks, and hides the CDN hostname from the client network).
        proxied = f"/api/ai/poster-image?src={quote(hit.url, safe='')}" if hit else ""
        print(
            f"[poster] title='{title_clean[:60]}' hint='{latin_hint[:60]}' "
            f"-> {'OK' if proxied else 'MISS'}",
            flush=True,
        )
        return proxied
    except Exception as exc:  # noqa: BLE001 — poster is cosmetic
        print(f"[poster] error for '{title_clean[:60]}': {exc}", flush=True)
        return ""


async def _tool_record(
    state: Any,  # noqa: ANN401
    *,
    channel_id: str,
    title: str,
    start: str,
    stop: str,
    theme: str = "other",
    poster_keywords: str = "",
    lang: str = "ru",
) -> dict[str, Any]:
    channel_id = _clean_channel_id(channel_id)
    channel = state.playlist.by_id(channel_id)
    if channel is None:
        return {"ok": False, "error": f"unknown channel_id: {channel_id}"}
    poster_url = await _resolve_poster_for_title(
        state.posters,
        title,
        poster_keywords,
        lang,
    )
    try:
        entry = await state.recordings.schedule(
            channel_id=channel_id,
            channel_name=channel.name,
            upstream_url=channel.url,
            title=title,
            start=start,
            stop=stop,
            theme=theme,
            poster_url=poster_url,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "recording": entry.to_dict()}


async def _tool_list(recordings: RecordingManager) -> dict[str, Any]:
    entries = recordings.list()
    return {
        "ok": True,
        "count": len(entries),
        "items": [e.to_dict() for e in entries[:20]],
    }


async def _tool_recommend(
    state: Any,  # noqa: ANN401
    lang: str,
    *,
    channel_id: str,
    title: str,
    start: str,
    stop: str,
    poster_keywords: str = "",
    blurb: str = "",
) -> dict[str, Any]:
    """Resolve a poster for a programme the assistant is recommending.

    Rejects the call if ``channel_id`` isn't in the playlist — the model
    occasionally hallucinates an id (wrong hex, or invents one wholesale)
    and a tolerant fallback was rendering broken cards with the raw id
    instead of a channel name. Returning ``ok:false`` surfaces the mistake
    so the next tool-calling round can self-correct or skip.
    """
    # Sanitise '(id=X)' / 'id=X' wrappers the model occasionally keeps.
    channel_id = _clean_channel_id(channel_id)
    channel = state.playlist.by_id(channel_id)
    if channel is None:
        return {
            "ok": False,
            "error": (
                f"unknown channel_id: {channel_id}. Use only ids from the EPG context marked id=…"
            ),
            "channel_id": channel_id,
            "title": title,
        }
    resolved = await _resolve_poster_for_title(
        state.posters,
        title,
        poster_keywords,
        lang,
    )
    poster_url: str | None = resolved or None
    return {
        "ok": True,
        "channel_id": channel_id,
        "channel_name": channel.name,
        "title": title,
        "start": start,
        "stop": stop,
        "blurb": blurb,
        "poster_url": poster_url,
    }


_THEME_KEYWORDS: dict[str, list[str]] = {
    "sport": [
        "футбол",
        "хоккей",
        "баскетбол",
        "теннис",
        "волейбол",
        "бокс",
        "единоборств",
        "регби",
        "лыжн",
        "биатлон",
        "гольф",
        "велоспорт",
        "формула",
        "гонк",
        "мото",
        "наскар",
        "ралли",
        "снукер",
        "дартс",
        "матч",
        "чемпионат",
        "кубок",
        "евролиг",
        "еврокубок",
        "премьер-лига",
        "лига чемпион",
        "лига европ",
        "нба",
        "nba",
        "nhl",
        "кхл",
        "рпл",
        "ufc",
        "mma",
        "wta",
        "atp",
        "бой ",
        "football",
        "hockey",
        "basketball",
        "tennis",
        "soccer",
        "boxing",
        "formula 1",
        "formula 2",
        "motogp",
        "premier league",
        "champions league",
        "euroleague",
        "nascar",
        "grand prix",
        "world cup",
        "derby",
        "снооkер",
        "трансляц",
    ],
    "cinema": [
        # Feature films only — series keywords intentionally excluded so
        # multi-episode shows don't drown out real cinema picks.
        "фильм",
        "кино",
        "художествен",
        "драма",
        "комедия",
        "боевик",
        "триллер",
        "детектив",
        "мелодрам",
        "фантастика",
        "приключени",
        "вестерн",
        "ужасы",
        "хоррор",
        "нуар",
        "анимаци",
        "мультфильм",
        "премьера",
        "film",
        "movie",
        "drama",
        "comedy",
        "thriller",
        "action",
        "horror",
        "sci-fi",
        "animated",
        "premiere",
    ],
}


async def _hydrate_digest_posters(
    digest: Any,  # noqa: ANN401
    posters: PosterResolver,
    lang: str,
) -> Any:  # noqa: ANN401
    """Resolve poster URLs for every digest entry in parallel.

    Runs AFTER the model picks items but BEFORE we return the JSON so the
    client receives fully-formed cards with ``poster_url`` populated. Hits
    the TMDB / Wikipedia cache so repeated requests for the same title
    cost nothing. Failures silently fall through to ``""`` — a blank
    poster is better than a failed card.
    """
    from server.ai.digest import Digest, DigestEntry  # local import

    if not digest.items:
        return digest

    async def _resolve(entry: DigestEntry) -> DigestEntry:
        url = await _resolve_poster_for_title(posters, entry.title, entry.poster_keywords, lang)
        return DigestEntry(
            channel_id=entry.channel_id,
            channel_name=entry.channel_name,
            title=entry.title,
            start=entry.start,
            stop=entry.stop,
            blurb=entry.blurb,
            poster_keywords=entry.poster_keywords,
            poster_url=url,
        )

    hydrated = await asyncio.gather(*(_resolve(i) for i in digest.items))

    # Sort by start time — nearest first. The prompt asks for this order
    # but gpt-4o-mini occasionally returns items in arrival/channel order,
    # so enforce it server-side. Falls back gracefully for items missing
    # a valid ISO timestamp.
    def _start_key(entry: DigestEntry) -> str:
        return entry.start or "9999"

    hydrated_sorted = sorted(hydrated, key=_start_key)
    return Digest(
        date=digest.date,
        theme=digest.theme,
        lang=digest.lang,
        generated_at=digest.generated_at,
        items=tuple(hydrated_sorted),
    )


def _narrow_by_keywords(schedules: list, keywords: list[str]) -> list:
    """Return a new schedule list keeping only programmes whose title or
    description mentions one of ``keywords`` (case-insensitive).

    Channels with zero matches are dropped entirely so the digest prompt
    only carries on-theme content.
    """
    from server.ai.context import ChannelSchedule  # local import, tight loop

    lowered = [k.lower() for k in keywords]
    narrowed: list[ChannelSchedule] = []
    for sch in schedules:
        matching = tuple(
            p
            for p in sch.programmes
            if any(k in p.title.lower() or k in p.description.lower() for k in lowered)
        )
        if matching:
            narrowed.append(
                ChannelSchedule(
                    channel_id=sch.channel_id,
                    channel_name=sch.channel_name,
                    group=sch.group,
                    programmes=matching,
                )
            )
    return narrowed


def _main_channels(state: Any) -> list:  # noqa: ANN401
    ids = state.store.current_ids()
    by_id = {ch.id: ch for ch in state.playlist.channels}
    return [by_id[cid] for cid in ids if cid in by_id]


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Not JSON serialisable: {type(obj)!r}")
