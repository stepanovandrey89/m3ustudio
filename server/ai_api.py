"""HTTP surface for AI assistant + daily digest + recordings.

Wired into the main app via `include_router(ai_api.build_router(state))`.
Keeps the AI/recording surface out of main.py so it stays approachable.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
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
    detect_time_of_day,
    narrow_by_programme_content,
    narrow_by_time_window,
    resolve_tod_window,
)
from server.ai.digest import ALL_THEMES, DigestCache, Theme, live_items
from server.ai.generate import ToolExecutor, _clean_channel_id, generate_digest, stream_chat
from server.ai.poster import _SPORTSDB_LEAGUE_MAP, PosterResolver
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

        # Persistent cache: date-independent key (theme, lang). A digest
        # generated yesterday is reused today as long as enough items
        # haven't aired yet (≥ 3 future-starting tiles). The user sees
        # their saved digest instantly on every new login / device — no
        # daily regeneration churn, no "empty on fresh browser" gap.
        if not refresh:
            cached = digest_cache.get(theme_typed, lang)
            if cached is not None:
                remaining = live_items(cached)
                if len(remaining) >= 3:
                    # If the cached digest still has pending posters
                    # (server restart dropped the task, or a previous
                    # backfill didn't fully converge), re-kick the
                    # worker. The scheduler is a no-op when a worker
                    # is already running for this (theme, lang).
                    if any(not i.poster_url for i in remaining):
                        _schedule_backfill(
                            digest_cache, posters, theme_typed, lang, restart=False
                        )
                    # Response only includes items that already have a
                    # resolved poster — matches the "no empty cards"
                    # requirement. Pending ones stay in the disk JSON
                    # so the worker can keep chipping away at them.
                    filled = tuple(i for i in remaining if i.poster_url)
                    payload = cached.to_dict()
                    payload["items"] = [i.to_dict() for i in filled]
                    return JSONResponse({"cached": True, **payload})

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
        if theme_typed == "cinema":
            # Belt-and-braces: strip series episodes and sport broadcasts
            # that the positive keyword filter let through. Combined with
            # the poster-mandatory step in _hydrate_digest_posters this
            # produces feature-films-only output even when gpt-4o-mini
            # misbehaves.
            cinema_clean = _exclude_non_cinema(schedules)
            if cinema_clean:
                schedules = cinema_clean
        print(
            f"[digest-debug] theme={theme_typed} schedules={len(schedules)} "
            f"channels={len(main_channels)}",
            flush=True,
        )
        result = await generate_digest(client, cfg, schedules, theme_typed, lang)
        print(
            f"[digest-debug] theme={theme_typed} model_returned={len(result.items)} items",
            flush=True,
        )
        # Resolve every poster in parallel BEFORE responding / caching so
        # the frontend never renders a "blank card → flash of content"
        # when the browser plays catch-up on /api/ai/poster requests.
        result = await _hydrate_digest_posters(result, state.posters, lang)
        print(
            f"[digest-debug] theme={theme_typed} after_hydrate={len(result.items)} items",
            flush=True,
        )
        # Don't persist empty digests — a transient model glitch would otherwise
        # freeze an "empty" result on disk for the rest of the day, and the
        # frontend would keep serving it until the user hits refresh or the
        # date rolls over. Letting the next request regenerate is cheap.
        if result.items:
            digest_cache.put(result)
            # Any items left without a poster on the first pass? Kick off
            # the background backfill worker so a subsequent page load
            # sees a complete digest. The worker writes hits back into
            # the JSON and, after max rounds, prunes truly unresolvable
            # items so the visible digest has "no empty cards".
            if any(not i.poster_url for i in result.items):
                _schedule_backfill(
                    digest_cache, posters, theme_typed, lang, restart=True
                )
        # Response only surfaces cards that already have a poster — the
        # JSON on disk still carries the missing ones for the backfill
        # worker to work on. A page refresh once the worker has made
        # progress will surface the newly-filled cards automatically.
        payload = result.to_dict()
        payload["items"] = [i.to_dict() for i in result.items if i.poster_url]
        return JSONResponse({"cached": False, **payload})

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
            "r2.thesportsdb.com",
            "www.thesportsdb.com",
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
        last_user_msg = next(
            (m.content for m in reversed(body.messages) if m.role == "user"),
            "",
        )
        # Detect a time-of-day cue in the user's question ("на вечер",
        # "утром", "ночью"). When present we widen the EPG window so we
        # don't truncate the asked slice of the day — e.g. a query at
        # 10:00 about "вечером" needs EPG through 24:00, well past the
        # default 8h horizon — and later narrow programmes to exactly
        # that window so the model can only recommend from it.
        tod_label = (
            None if body.deep_search else detect_time_of_day(last_user_msg)
        )
        tod_window: tuple[datetime, datetime] | None = None
        if tod_label is not None:
            window_start, window_stop = resolve_tod_window(
                tod_label, datetime.now(UTC)
            )
            hours_needed = (window_stop - datetime.now(UTC)).total_seconds() / 3600
            future_hours = max(8, min(30, int(hours_needed) + 2))
            tod_window = (window_start, window_stop)
        elif body.deep_search:
            future_hours = 168
        else:
            future_hours = 8
        # Cap entries per channel to keep the prompt bounded. A normal-mode
        # "what's on tonight" never needs 12 programmes from a single channel.
        max_per_channel = None if body.deep_search else 6
        # If the user named a channel in their latest message, restrict the
        # EPG context to just those channels — no reason to send 149
        # favourites of programme data when the question is about one.
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
        # Time-of-day narrow: strip programmes outside the asked window
        # so the model can't reach for an earlier/later slot.
        if tod_window is not None:
            narrowed_tod = narrow_by_time_window(schedules, *tod_window)
            if narrowed_tod:
                schedules = narrowed_tod
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


_SPORT_LEAGUE_WIKI_MAP: dict[str, str] = {
    # Well-known competition/league keywords → Russian Wikipedia article
    # name whose lead image is the league logo.
    "рпл": "Российская Премьер-Лига",
    "российская премьер-лига": "Российская Премьер-Лига",
    "russian premier league": "Российская Премьер-Лига",
    "премьер-лига": "Премьер-лига Англии",
    "premier league": "Премьер-лига Англии",
    "ла лига": "Ла Лига",
    "la liga": "Ла Лига",
    "чемпионат испании": "Ла Лига",
    "segunda": "Сегунда",
    "сегунда": "Сегунда",
    "кубок испании": "Кубок Испании по футболу",
    "copa del rey": "Кубок Испании по футболу",
    "ligue 1": "Лига 1",
    "чемпионат франции": "Лига 1",
    "bundesliga": "Бундеслига",
    "бундеслига": "Бундеслига",
    "чемпионат германии": "Бундеслига",
    "serie a": "Серия A (Италия)",
    "серия а": "Серия A (Италия)",
    "чемпионат италии": "Серия A (Италия)",
    "чемпионат турции": "Суперлига Турции",
    "super lig": "Суперлига Турции",
    "лига чемпионов": "Лига чемпионов УЕФА",
    "champions league": "Лига чемпионов УЕФА",
    "лига европы": "Лига Европы УЕФА",
    "europa league": "Лига Европы УЕФА",
    "nhl": "Национальная хоккейная лига",
    "нхл": "Национальная хоккейная лига",
    "nba": "Национальная баскетбольная ассоциация",
    "нба": "Национальная баскетбольная ассоциация",
    "кхл": "Континентальная хоккейная лига",
    "khl": "Континентальная хоккейная лига",
    "formula 1": "Формула-1",
    "формула 1": "Формула-1",
    "formula 2": "Формула-2",
    "формула 2": "Формула-2",
    "formula 3": "Формула-3",
    "формула 3": "Формула-3",
    "ufc": "Ultimate Fighting Championship",
    "motogp": "MotoGP",
    "nascar": "NASCAR",
    "наскар": "NASCAR",
    "волейбол чемпионат россии": "Суперлига России по волейболу среди мужчин",
    "российская волейбольная суперлига": "Суперлига России по волейболу среди мужчин",
    "гандбол кубок россии": "Чемпионат России по гандболу среди мужчин",
    "snl": "Национальная лига (Швейцария)",
    "кубок россии": "Кубок России по футболу",
}


# Hockey-specific canonicals for ambiguous team names that exist as
# BOTH football and hockey clubs (Спартак, ЦСКА, Динамо, Локомотив —
# same names, different sports). Consulted when the entry's context
# is hockey; otherwise we fall back to the football map below.
_RU_HOCKEY_CLUB_CANONICAL: dict[str, tuple[str, ...]] = {
    "spartak": ("HC Spartak Moscow", "ХК Спартак Москва"),
    "спартак": ("ХК Спартак Москва", "HC Spartak Moscow"),
    "cska": ("HC CSKA Moscow", "ХК ЦСКА Москва"),
    "цска": ("ХК ЦСКА Москва", "HC CSKA Moscow"),
    "dynamo": ("HC Dynamo Moscow", "ХК Динамо Москва"),
    "dinamo": ("HC Dynamo Moscow", "ХК Динамо Москва"),
    "динамо": ("ХК Динамо Москва", "HC Dynamo Moscow"),
    "lokomotiv": ("Lokomotiv Yaroslavl", "ХК Локомотив Ярославль"),
    "локомотив": ("ХК Локомотив Ярославль", "Lokomotiv Yaroslavl"),
    "torpedo": ("Torpedo Nizhny Novgorod", "ХК Торпедо Нижний Новгород"),
    "торпедо": ("ХК Торпедо Нижний Новгород", "Torpedo Nizhny Novgorod"),
    # Hockey-only clubs (kept here for consistency; also in the shared map below)
    "ска": ("ХК СКА", "SKA Saint Petersburg"),
    "ska": ("SKA Saint Petersburg", "ХК СКА"),
    "ak bars": ("Ak Bars Kazan", "ХК Ак Барс"),
    "ак барс": ("ХК Ак Барс", "Ak Bars Kazan"),
    "avangard": ("Avangard Omsk", "ХК Авангард"),
    "авангард": ("ХК Авангард", "Avangard Omsk"),
    "metallurg": ("Metallurg Magnitogorsk", "ХК Металлург (Магнитогорск)"),
    "металлург": ("ХК Металлург (Магнитогорск)", "Metallurg Magnitogorsk"),
    "salavat": ("Salavat Yulaev Ufa", "ХК Салават Юлаев"),
    "салават юлаев": ("ХК Салават Юлаев", "Salavat Yulaev Ufa"),
    "sibir": ("HC Sibir Novosibirsk", "ХК Сибирь"),
    "сибирь": ("ХК Сибирь", "HC Sibir Novosibirsk"),
    "traktor": ("Traktor Chelyabinsk", "ХК Трактор"),
    "трактор": ("ХК Трактор", "Traktor Chelyabinsk"),
}

# Russian / ex-Soviet football + hockey club canonicals. Each key maps
# to the explicit Wikipedia article title(s) that reliably return the
# club's crest — bare Latin names like "Spartak" otherwise drift to
# films or player portraits. For ambiguous names shared across sports
# (Спартак and Динамо are both football AND hockey) we include both
# ФК- and ХК- variants; the caller's sport-aware query order picks the
# right one via the ``halves`` path.
_RU_CLUB_CANONICAL: dict[str, tuple[str, ...]] = {
    # Moscow-default football clubs
    "spartak": ("FC Spartak Moscow", "ФК Спартак Москва"),
    "спартак": ("ФК Спартак Москва", "FC Spartak Moscow"),
    "lokomotiv": ("FC Lokomotiv Moscow", "ФК Локомотив Москва"),
    "локомотив": ("ФК Локомотив Москва", "FC Lokomotiv Moscow"),
    "cska": ("PFC CSKA Moscow", "FC CSKA Moscow", "ПФК ЦСКА"),
    "цска": ("ПФК ЦСКА", "PFC CSKA Moscow"),
    "dynamo": ("FC Dynamo Moscow", "ФК Динамо Москва"),
    "dinamo": ("FC Dynamo Moscow", "ФК Динамо Москва"),
    "динамо": ("ФК Динамо Москва", "FC Dynamo Moscow"),
    "torpedo": ("FC Torpedo Moscow", "ФК Торпедо Москва"),
    "торпедо": ("ФК Торпедо Москва", "FC Torpedo Moscow"),
    # Saint Petersburg
    "zenit": ("FC Zenit Saint Petersburg", "ФК Зенит"),
    "зенит": ("ФК Зенит", "FC Zenit Saint Petersburg"),
    # Single-city clubs
    "krasnodar": ("FC Krasnodar", "ФК Краснодар"),
    "краснодар": ("ФК Краснодар", "FC Krasnodar"),
    "rostov": ("FC Rostov", "ФК Ростов"),
    "ростов": ("ФК Ростов", "FC Rostov"),
    "rubin": ("FC Rubin Kazan", "ФК Рубин"),
    "рубин": ("ФК Рубин", "FC Rubin Kazan"),
    "akhmat": ("FC Akhmat Grozny", "ФК Ахмат"),
    "ахмат": ("ФК Ахмат", "FC Akhmat Grozny"),
    "sochi": ("PFC Sochi", "ФК Сочи"),
    "сочи": ("ФК Сочи", "PFC Sochi"),
    "ural": ("FC Ural Yekaterinburg", "ФК Урал"),
    "урал": ("ФК Урал", "FC Ural Yekaterinburg"),
    "krylia sovetov": ("FC Krylia Sovetov Samara", "ФК Крылья Советов"),
    "крылья советов": ("ФК Крылья Советов", "FC Krylia Sovetov Samara"),
    "orenburg": ("FC Orenburg", "ФК Оренбург"),
    "оренбург": ("ФК Оренбург", "FC Orenburg"),
    "fakel": ("FC Fakel Voronezh", "ФК Факел"),
    "факел": ("ФК Факел", "FC Fakel Voronezh"),
    "baltika": ("FC Baltika Kaliningrad", "ФК Балтика"),
    "балтика": ("ФК Балтика", "FC Baltika Kaliningrad"),
    "pari nn": ("FC Nizhny Novgorod", "ФК Нижний Новгород"),
    "нижний новгород": ("ФК Нижний Новгород", "FC Nizhny Novgorod"),
    # Foreign football — most hit en.wiki via Cyrillic redirect; add
    # canonical ru.wiki titles so searches for the Latin half land
    # directly on the club article.
    "barcelona": ("FC Barcelona", "ФК Барселона"),
    "барселона": ("ФК Барселона", "FC Barcelona"),
    "real madrid": ("Real Madrid CF", "Реал Мадрид"),
    "реал мадрид": ("Реал Мадрид", "Real Madrid CF"),
    "real": ("Real Madrid CF",),
    "реал": ("Реал Мадрид", "Real Madrid CF"),
    "atletico": ("Atlético Madrid", "Атлетико Мадрид"),
    "атлетико": ("Атлетико Мадрид", "Atlético Madrid"),
    "sevilla": ("Sevilla FC", "Севилья"),
    "севилья": ("Севилья", "Sevilla FC"),
    "valencia": ("Valencia CF", "Валенсия"),
    "валенсия": ("Валенсия (футбольный клуб)", "Valencia CF"),
    "bilbao": ("Athletic Bilbao", "Атлетик Бильбао"),
    "atletic bilbao": ("Athletic Bilbao", "Атлетик Бильбао"),
    "girona": ("Girona FC", "Жирона (футбольный клуб)"),
    "жирона": ("Жирона (футбольный клуб)", "Girona FC"),
    "betis": ("Real Betis", "Реал Бетис"),
    "бетис": ("Реал Бетис", "Real Betis"),
    "espanyol": ("RCD Espanyol", "Эспаньол"),
    "эспаньол": ("Эспаньол", "RCD Espanyol"),
    "villarreal": ("Villarreal CF", "Вильярреал"),
    "вильярреал": ("Вильярреал", "Villarreal CF"),
    "getafe": ("Getafe CF", "Хетафе"),
    "хетафе": ("Хетафе", "Getafe CF"),
    "rayo": ("Rayo Vallecano", "Райо Вальекано"),
    "райо": ("Райо Вальекано", "Rayo Vallecano"),
    "osasuna": ("CA Osasuna", "Осасуна"),
    "осасуна": ("Осасуна", "CA Osasuna"),
    "alaves": ("Deportivo Alavés", "Алавес"),
    "алавес": ("Алавес", "Deportivo Alavés"),
    "mallorca": ("RCD Mallorca", "Мальорка (футбольный клуб)"),
    "мальорка": ("Мальорка (футбольный клуб)", "RCD Mallorca"),
    "celta": ("RC Celta de Vigo", "Сельта"),
    "сельта": ("Сельта", "RC Celta de Vigo"),
    "las palmas": ("UD Las Palmas", "Лас-Пальмас (футбольный клуб)"),
    "лас-пальмас": ("Лас-Пальмас (футбольный клуб)", "UD Las Palmas"),
    "manchester united": ("Manchester United F.C.", "Манчестер Юнайтед"),
    "manchester city": ("Manchester City F.C.", "Манчестер Сити"),
    "liverpool": ("Liverpool F.C.", "Ливерпуль"),
    "chelsea": ("Chelsea F.C.", "Челси"),
    "arsenal": ("Arsenal F.C.", "Арсенал Лондон"),
    "tottenham": ("Tottenham Hotspur F.C.", "Тоттенхэм Хотспур"),
    "bayern munich": ("FC Bayern Munich", "Бавария"),
    "bayern": ("FC Bayern Munich", "Бавария"),
    "бавария": ("Бавария", "FC Bayern Munich"),
    "dortmund": ("Borussia Dortmund", "Боруссия Дортмунд"),
    "boruss": ("Borussia Dortmund", "Боруссия Дортмунд"),
    "leverkusen": ("Bayer 04 Leverkusen", "Байер 04"),
    "juventus": ("Juventus F.C.", "Ювентус"),
    "милан": ("Милан (футбольный клуб)", "A.C. Milan"),
    "milan": ("A.C. Milan", "Милан (футбольный клуб)"),
    "inter": ("Inter Milan", "Интернационале"),
    "интер": ("Интернационале", "Inter Milan"),
    "napoli": ("S.S.C. Napoli", "Наполи"),
    "roma": ("A.S. Roma", "Рома (футбольный клуб)"),
    "lazio": ("S.S. Lazio", "Лацио"),
    "fiorentina": ("ACF Fiorentina", "Фиорентина"),
    "lecce": ("U.S. Lecce", "Лечче"),
    "psg": ("Paris Saint-Germain F.C.", "Пари Сен-Жермен"),
    "paris": ("Paris Saint-Germain F.C.",),
    "marseille": ("Olympique de Marseille", "Олимпик Марсель"),
    "lyon": ("Olympique Lyonnais", "Олимпик Лион"),
    "benfica": ("S.L. Benfica", "Бенфика"),
    "porto": ("FC Porto", "Порту"),
    "ajax": ("AFC Ajax", "Аякс"),
    "psv": ("PSV Eindhoven", "ПСВ"),
    "galatasaray": ("Galatasaray S.K.", "Галатасарай"),
    "fenerbahce": ("Fenerbahçe S.K.", "Фенербахче"),
    # Russian hockey (KHL) — ambiguous Спартак/Динамо/ЦСКА get the
    # ФК form above; hockey-context queries add the ХК prefix via
    # _club_variants. Named-only clubs here.
    "ска": ("ХК СКА", "SKA Saint Petersburg"),
    "ska": ("SKA Saint Petersburg", "ХК СКА"),
    "авангард": ("ХК Авангард", "Avangard Omsk"),
    "avangard": ("Avangard Omsk", "ХК Авангард"),
    "ак барс": ("ХК Ак Барс", "Ak Bars Kazan"),
    "ak bars": ("Ak Bars Kazan", "ХК Ак Барс"),
    "металлург": ("ХК Металлург (Магнитогорск)", "Metallurg Magnitogorsk"),
    "metallurg": ("Metallurg Magnitogorsk", "ХК Металлург (Магнитогорск)"),
    "салават": ("ХК Салават Юлаев", "Salavat Yulaev Ufa"),
    "salavat": ("Salavat Yulaev Ufa", "ХК Салават Юлаев"),
    "сибирь": ("ХК Сибирь", "HC Sibir Novosibirsk"),
    "sibir": ("HC Sibir Novosibirsk", "ХК Сибирь"),
    "трактор": ("ХК Трактор", "Traktor Chelyabinsk"),
    "traktor": ("Traktor Chelyabinsk", "ХК Трактор"),
}


# EPG broadcast / commentary trailers that the per-sport splitter
# should drop from the team halves. Without this, lookups for names
# that sit next to the " — " separator ("Жирона — Трансляция",
# "Реал Мадрид — Прямой эфир") fall to fuzzy Wikipedia search and
# return the wrong article ("Трансляция" is a page of its own).
_EPG_TAIL_RE = re.compile(
    r"\s*[—–-]\s*(?:трансляц\S*|прямой эфир|прямая трансляция|в записи|повтор\S*|repeat|live)"
    r"\s*$",
    re.IGNORECASE,
)
_EPG_PARENS_RE = re.compile(r"\s*\([^)]{1,60}\)\s*$")


def _strip_epg_tail(s: str) -> str:
    """Remove trailing broadcast markers + commentator-name parentheses."""
    s = s.strip()
    prev = ""
    # Loop until stable — some titles stack "(…) — Трансляция" in either
    # order so a single pass isn't enough.
    while s and s != prev:
        prev = s
        s = _EPG_TAIL_RE.sub("", s).strip()
        s = _EPG_PARENS_RE.sub("", s).strip()
    # Also strip outer straight + fancy quotes that the EPG source
    # wraps around team names.
    return s.strip('"«»\'').strip()


async def _resolve_sport_art(posters: PosterResolver, entry: Any) -> str:  # noqa: ANN401
    """Pick an image for a sport event card via Wikipedia.

    Strategy (progressive fallback, stop on first hit):
      1. Explicit league articles from a curated map (РПЛ, NHL, La Liga,
         Champions League, Формула-1, UFC, etc.) — these reliably have
         logos on Russian Wikipedia.
      2. Each "X vs Y" half as a football club ("ФК ЦСКА" finds the
         club article rather than the city). Also "ХК Динамо" for
         hockey queries.
      3. Raw halves and the full hint / title.
    """
    hint = (entry.poster_keywords or "").strip()
    title = (entry.title or "").strip()
    combined = f"{title} {hint}".lower()

    def _halves(text: str) -> list[str]:
        low = text.lower()
        for splitter in (" vs ", " - ", " — ", " – ", " v "):
            if splitter in low:
                idx = low.find(splitter)
                left = text[:idx].strip()
                right = text[idx + len(splitter) :].strip()
                # Strip EPG preamble like "Футбол. РПЛ. Спартак" →
                # "Спартак". Preamble always ends in a period and is
                # followed by the actual team name. Works for both
                # Russian ("Хоккей. КХЛ. Ак Барс") and Latin hints.
                if "." in left:
                    left = left.rsplit(".", 1)[-1].strip()
                # Strip broadcast/commentary trailers that stick to the
                # right half: "… — Трансляция", "Прямой эфир", "(Куинтон
                # Гриценко)", "(комментирует …)". Without this, canonical
                # lookup for "Жирона — Трансляция" falls straight to
                # fuzzy Wikipedia search and lands on random pages.
                right = _strip_epg_tail(right)
                left = _strip_epg_tail(left)
                return [left, right]
        return []

    def _club_variants(name: str) -> list[str]:
        """Produce football/hockey club search variants.

        Russian clubs have canonical Wikipedia article titles with the
        full "FC <Name> <City>" form on en.wiki and "ФК <Name> <City>"
        on ru.wiki. Both resolve to the same crest. Bare names like
        "Spartak" return random disambiguation hits (a film, an actor,
        a player portrait); the explicit "FC Spartak Moscow" /
        "ФК Спартак Москва" form returns the club crest reliably.

        We look up a curated map first, then fall back to generic
        prefixed variants.
        """
        n = name.strip().strip('"«»').strip()
        if not n:
            return []
        out: list[str] = []
        is_football = any(
            kw in combined
            for kw in ("футбол", "football", "soccer", "premier", "liga", "serie", "bundesliga")
        )
        is_hockey = any(kw in combined for kw in ("хоккей", "hockey", "nhl", "кхл", "snl"))
        # Sport-aware canonical lookup: hockey context picks the ХК/HC
        # article for ambiguous names like "Спартак" (which has both an
        # FC and an HC club), football context picks ФК/FC.
        lkey = n.lower()
        if is_hockey:
            hockey_canon = _RU_HOCKEY_CLUB_CANONICAL.get(lkey)
            if hockey_canon:
                out.extend(hockey_canon)
        football_canon = _RU_CLUB_CANONICAL.get(lkey)
        if football_canon and not is_hockey:
            out.extend(football_canon)
        elif football_canon and is_hockey:
            # Keep football canonical as late fallback — better than the
            # bare name producing a random film/portrait.
            out.extend(v for v in football_canon if v not in out)
        out.append(n)
        is_volley = any(kw in combined for kw in ("волейбол", "volleyball"))
        is_basket = any(kw in combined for kw in ("баскетбол", "basketball", "nba"))
        # For names we don't have in the curated map, still try Russian
        # abbreviation prefixes (ФК/ХК/ВК/БК) — works for foreign clubs
        # with ru.wiki articles like "ФК Барселона", "ФК Реал Мадрид".
        if is_football:
            out.append(f"ФК {n}")
        if is_hockey:
            out.append(f"ХК {n}")
        if is_volley:
            out.append(f"ВК {n}")
        if is_basket:
            out.append(f"БК {n}")
        return out

    queries: list[str] = []

    # 1. Club crests FIRST — for a real matchup ("Спартак — Локомотив")
    # we want the home team's crest, not the league logo. The canonical
    # map gives full-article titles that reliably return the crest image
    # via Wikipedia's summary endpoint.
    halves = _halves(hint) or _halves(title)
    for half in halves:
        for variant in _club_variants(half):
            if variant and variant not in queries:
                queries.append(variant)

    # 2. League logo — fallback when there are no halves (F1 race, UFC
    # numbered card, tournament standings show) or when no half resolved
    # to a club article. Longest key first so "russian premier league"
    # beats the bare "premier league".
    for key in sorted(_SPORT_LEAGUE_WIKI_MAP, key=len, reverse=True):
        if key in combined:
            article = _SPORT_LEAGUE_WIKI_MAP[key]
            if article not in queries:
                queries.append(article)
            break

    # 3. Raw fallbacks — original hint, original title.
    if hint and hint not in queries:
        queries.append(hint)
    if title and title not in queries:
        queries.append(title)

    for q in queries:
        if not q:
            continue
        try:
            hit = await posters.resolve(q, "ru", allow_commons=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[sport-art] error q={q!r}: {exc}", flush=True)
            continue
        if hit:
            print(f"[sport-art] OK q={q!r} via {hit.source}", flush=True)
            return f"/api/ai/poster-image?src={quote(hit.url, safe='')}"

    # TheSportsDB fallback — official team crests + league badges. Kicks in
    # when Wikipedia returns nothing OR the top-hit was a bad fuzzy match.
    # ``resolve_sport`` internally tries event → team → league in that
    # order, using the halves we already extracted so matchup pages hit
    # the right home/away crest instead of the league icon.
    sportsdb_queries: list[str] = []
    if halves:
        sportsdb_queries.append(" vs ".join(halves))
    for half in halves:
        if half and half not in sportsdb_queries:
            sportsdb_queries.append(half)
    for key in sorted(_SPORTSDB_LEAGUE_MAP, key=len, reverse=True):
        if key in combined:
            canonical = _SPORTSDB_LEAGUE_MAP[key]
            if canonical not in sportsdb_queries:
                sportsdb_queries.append(canonical)
            break
    if hint and hint not in sportsdb_queries:
        sportsdb_queries.append(hint)
    if title and title not in sportsdb_queries:
        sportsdb_queries.append(title)

    for q in sportsdb_queries:
        if not q:
            continue
        try:
            hit = await posters.resolve_sport(q, match_halves=halves or None)
        except Exception as exc:  # noqa: BLE001
            print(f"[sport-sportsdb] error q={q!r}: {exc}", flush=True)
            continue
        if hit:
            print(f"[sport-sportsdb] OK q={q!r}", flush=True)
            return f"/api/ai/poster-image?src={quote(hit.url, safe='')}"

    # TMDB TV fallback — docuseries about a team/league give reasonable
    # on-topic art when Wikipedia has nothing («Драйв выживания» for F1,
    # «Вместе до конца» for Real Madrid). Only single-entity queries work
    # — TMDB returns nothing for "X vs Y" matchup strings.
    tmdb_queries: list[str] = []

    def _push_tmdb(q: str) -> None:
        q = q.strip().strip('"«»').strip()
        if q and q not in tmdb_queries:
            tmdb_queries.append(q)

    _football_kw = ("футбол", "football", "soccer", "premier", "liga", "serie", "bundesliga")
    _is_football = any(kw in combined for kw in _football_kw)
    for half in halves:
        _push_tmdb(half)
        # Latin hints often come as team names the model wrote (e.g.
        # "FC Barcelona"). Club prefixes raise TMDB recall too.
        if _is_football and half and any(c.isascii() and c.isalpha() for c in half):
            _push_tmdb(f"FC {half}")
    for key in sorted(_SPORTSDB_LEAGUE_MAP, key=len, reverse=True):
        if key in combined:
            _push_tmdb(_SPORTSDB_LEAGUE_MAP[key])
            break
    # If the Latin hint has no splitter, try it whole — covers things like
    # "UFC 300", "Formula 1 Monaco", "Wimbledon".
    if hint and not halves and any(c.isascii() and c.isalpha() for c in hint):
        _push_tmdb(hint)

    for q in tmdb_queries:
        try:
            hit = await posters.resolve_tmdb_tv(q)
        except Exception as exc:  # noqa: BLE001
            print(f"[sport-tmdb-tv] error q={q!r}: {exc}", flush=True)
            continue
        if hit:
            print(f"[sport-tmdb-tv] OK q={q!r}", flush=True)
            return f"/api/ai/poster-image?src={quote(hit.url, safe='')}"

    # No Unsplash fallback for sport — generic stadium/arena photos
    # were off-theme and indistinct ("хуета полная" per user). When
    # Wikipedia crest + TMDB docuseries both miss, we return empty and
    # let the frontend fall back to the blurred channel logo.
    print(
        f"[sport-art] MISS title={title[:40]!r} wiki={len(queries)} tmdb={len(tmdb_queries)}",
        flush=True,
    )
    return ""


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


DIGEST_TARGET_ITEMS = 9

# Background backfill: fill missing ``poster_url`` values in the cached
# digest iteratively until "no empty cards" remain, then stop.
#
# Running cost is bounded per (theme, lang): at most BACKFILL_MAX_ROUNDS
# rounds, each with one gpt-4.1 call + one resolver call per still-
# missing item. The per-key task dict prevents duplicate concurrent
# workers — a fresh /api/ai/digest call can request a restart (new
# digest contents) or a no-op (cached read while a worker is already
# running).
BACKFILL_MAX_ROUNDS = 3
BACKFILL_ROUND_DELAY_SECONDS = 20.0
_backfill_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}


def _schedule_backfill(
    digest_cache: Any,  # noqa: ANN401 — DigestCache (avoid import cycle)
    posters: PosterResolver,
    theme: str,
    lang: str,
    *,
    restart: bool,
) -> None:
    """Start the poster backfill worker for ``(theme, lang)``.

    ``restart=True`` cancels any in-flight worker and launches a fresh
    one — used after a brand-new digest is generated. ``restart=False``
    is a no-op when a worker is already running — used on cached reads
    to re-kick the worker after a server restart without stomping on
    a live one.
    """
    import server.ai.client as _ai_client

    cli = _ai_client.get_client()
    if cli is None:
        return
    key = (theme, lang)
    prev = _backfill_tasks.get(key)
    if prev is not None and not prev.done():
        if not restart:
            return
        prev.cancel()
    task = asyncio.create_task(
        _backfill_digest_posters(digest_cache, posters, cli, theme, lang)
    )
    _backfill_tasks[key] = task


async def _backfill_digest_posters(
    digest_cache: Any,  # noqa: ANN401 — DigestCache
    posters: PosterResolver,
    client: Any,  # noqa: ANN401 — AsyncOpenAI
    theme: str,
    lang: str,
) -> None:
    """Iteratively fill ``poster_url`` for digest items still missing one.

    After each successful resolve we patch the item in place on disk so
    a browser refresh surfaces it immediately. On the final round we
    prune any items still without a poster so the visible digest has
    "no empty cards" — the user's explicit requirement.
    """
    from server.ai.digest import Digest, DigestEntry  # local import

    try:
        for round_idx in range(BACKFILL_MAX_ROUNDS):
            current = digest_cache.get(theme, lang)
            if current is None:
                return
            missing = [i for i in current.items if not i.poster_url]
            if not missing:
                print(
                    f"[backfill] {theme}/{lang} complete after round {round_idx}",
                    flush=True,
                )
                return
            print(
                f"[backfill] {theme}/{lang} round={round_idx + 1}/"
                f"{BACKFILL_MAX_ROUNDS} missing={len(missing)}",
                flush=True,
            )
            for entry in missing:
                new_url = await _retry_poster_for_entry(
                    entry, theme, posters, client, lang
                )
                if not new_url:
                    continue
                # Read the latest on-disk snapshot before patching — a
                # fresh generation may have replaced the digest while we
                # were waiting on the LLM. Match by (channel_id, start)
                # so we only update items that still exist.
                snapshot = digest_cache.get(theme, lang)
                if snapshot is None:
                    return
                patched_items: list[DigestEntry] = []
                did_patch = False
                for e in snapshot.items:
                    if (
                        not e.poster_url
                        and e.channel_id == entry.channel_id
                        and e.start == entry.start
                    ):
                        patched_items.append(
                            DigestEntry(
                                channel_id=e.channel_id,
                                channel_name=e.channel_name,
                                title=e.title,
                                start=e.start,
                                stop=e.stop,
                                blurb=e.blurb,
                                poster_keywords=e.poster_keywords,
                                poster_url=new_url,
                            )
                        )
                        did_patch = True
                    else:
                        patched_items.append(e)
                if did_patch:
                    digest_cache.put(
                        Digest(
                            date=snapshot.date,
                            theme=snapshot.theme,
                            lang=snapshot.lang,
                            generated_at=snapshot.generated_at,
                            items=tuple(patched_items),
                        )
                    )
                    print(
                        f"[backfill] HIT {entry.title[:40]!r}",
                        flush=True,
                    )
            if round_idx < BACKFILL_MAX_ROUNDS - 1:
                await asyncio.sleep(BACKFILL_ROUND_DELAY_SECONDS)
        # Final sweep: prune items still without a poster so the visible
        # digest has no empty cards. Keep at least one item around even
        # if the sweep would empty the digest — better a single card
        # than "пусто" until the next generation.
        final = digest_cache.get(theme, lang)
        if final is None:
            return
        keep = tuple(i for i in final.items if i.poster_url)
        dropped = len(final.items) - len(keep)
        if dropped and keep:
            print(
                f"[backfill] {theme}/{lang} pruned {dropped} unresolvable "
                f"item(s) after {BACKFILL_MAX_ROUNDS} rounds",
                flush=True,
            )
            digest_cache.put(
                Digest(
                    date=final.date,
                    theme=final.theme,
                    lang=final.lang,
                    generated_at=final.generated_at,
                    items=keep,
                )
            )
        elif dropped and not keep:
            print(
                f"[backfill] {theme}/{lang} all {dropped} items still "
                f"unresolved — leaving them for the next refresh",
                flush=True,
            )
    except asyncio.CancelledError:
        print(f"[backfill] {theme}/{lang} cancelled", flush=True)
        raise


async def _retry_poster_for_entry(
    entry: Any,  # noqa: ANN401 — DigestEntry
    theme: str,
    posters: PosterResolver,
    client: Any,  # noqa: ANN401 — AsyncOpenAI
    lang: str,
) -> str:
    """Ask gpt-4.1 for cleaner keywords then hit the poster resolver.

    Returns a proxied URL on hit, empty string on miss or error. The
    backfill loop uses the return value to decide whether to patch the
    entry on disk.
    """
    prompt = (
        f"Suggest 2-4 concise English search keywords to find a poster "
        f"or logo image for this TV item on TMDB or Wikipedia. "
        f'Theme: {theme}. Title: "{entry.title[:120]}". '
        f'Description: "{(entry.blurb or "")[:200]}". '
        f"For a sport event reply with the league name (e.g. 'NHL', "
        f"'Russian Premier League') or the first team name with the "
        f"sport suffix ('FC Krasnodar', 'Spartak Moscow football'). "
        f"For a film reply with the canonical title, year, and the word "
        f"'film' (e.g. 'Inception 2010 film'). "
        f"ONLY the keywords, no prose, no quotes."
    )
    try:
        response = await client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=40,
        )
    except Exception as exc:  # noqa: BLE001 — loop must never crash
        print(f"[backfill] llm-fail {entry.title[:40]!r}: {exc}", flush=True)
        return ""
    text = (response.choices[0].message.content or "").strip()
    if not text or len(text) > 120:
        return ""
    try:
        hit = await posters.resolve(text, lang, allow_commons=(theme == "sport"))
    except Exception as exc:  # noqa: BLE001
        print(f"[backfill] resolve-fail {text!r}: {exc}", flush=True)
        return ""
    if not hit:
        return ""
    return f"/api/ai/poster-image?src={quote(hit.url, safe='')}"


async def _hydrate_digest_posters(
    digest: Any,  # noqa: ANN401
    posters: PosterResolver,
    lang: str,
) -> Any:  # noqa: ANN401
    """Resolve poster URLs, deduplicate, slice to 9, return a Digest.

    Pipeline (runs AFTER the model picks items, BEFORE we cache / send):
      1. Parallel poster resolve via TMDB → Wiki (see ``_resolve_poster_for_title``).
      2. ALL items are kept — including ones whose first-pass poster
         lookup missed. The HTTP layer filters those out of the response,
         but they stay in the on-disk JSON so the background backfill
         worker can retry with gpt-4.1-rewritten keywords and eventually
         produce a full "no empty cards" state.
      3. Dedupe by lowercased title so the same film recommended from two
         channels never shows twice.
      4. Sort by start time — nearest first — and slice to 9.
    """
    from server.ai.digest import Digest, DigestEntry  # local import

    if not digest.items:
        return digest

    theme = digest.theme

    async def _resolve(entry: DigestEntry) -> DigestEntry:
        # Sport: specific matches are not in TMDB, but Wikipedia has team
        # crests and league logos on Commons (Barça, Bayern, RPL, UEFA,
        # etc). Allow Commons through for sport so we can at least get
        # club/event identity art. Cinema keeps the strict fair-use
        # filter — Commons photos of actors make bad film posters.
        if theme == "sport":
            url = await _resolve_sport_art(posters, entry)
        else:
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

    hydrated = list(await asyncio.gather(*(_resolve(i) for i in digest.items)))

    # Dedupe by normalized title. For sport the model often returns the
    # same match with different prefixes ("Футбол. Чемпионат Италии.
    # Лечче - Фиорентина" vs "Чемпионат Италии. Лечче - Фиорентина"),
    # so we strip common wrapper words, quotes, and punctuation, then
    # also match "A contains B" / "B contains A" so the shorter variant
    # dedupes against the longer.
    def _norm_dedupe_key(title: str) -> str:
        t = title.lower()
        # drop obvious wrapper/genre prefixes
        for prefix in (
            "футбол.",
            "хоккей.",
            "баскетбол.",
            "волейбол.",
            "теннис.",
            "автоспорт.",
            "мотоспорт.",
            "трансляция",
        ):
            t = t.replace(prefix, " ")
        # collapse punctuation + whitespace to single spaces
        t = re.sub(r"[«»\"'`().\-–—,:;!?]", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    seen: list[str] = []
    unique: list[DigestEntry] = []
    for entry in hydrated:
        key = _norm_dedupe_key(entry.title)
        if not key:
            continue
        # Drop if we already saw this key exactly OR if it's a substring
        # of an earlier (longer) key, or vice versa — same match, one
        # variant is a prefix/superset of the other.
        dupe = any(key == s or key in s or s in key for s in seen)
        if dupe:
            continue
        seen.append(key)
        unique.append(entry)

    # Sort by start time — nearest first.
    def _start_key(entry: DigestEntry) -> str:
        return entry.start or "9999"

    sorted_items = sorted(unique, key=_start_key)
    return Digest(
        date=digest.date,
        theme=digest.theme,
        lang=digest.lang,
        generated_at=digest.generated_at,
        items=tuple(sorted_items[:DIGEST_TARGET_ITEMS]),
    )


# Episode / series markers — matches "Интерны. 57 с.", "Клон (с.184)",
# "Папины дочки, 8 серия", "2 сезон 3 эп.", etc. Catches both "N с."
# and "с.N" orders plus keyword variants.
_SERIES_MARKER_RE = re.compile(
    r"("
    r"\b\d+\s*(?:с\.|серия|серии|эп\.|эпизод)"
    r"|\bс\.\s*\d+"
    r"|\bсезон\b"
    r"|\bсерия\s*\d+"
    r"|\(\s*\d+\s*сер(?:ия|ий)?"
    r")",
    re.IGNORECASE,
)
# Sport / broadcast markers that occasionally slip into cinema slots on
# generalist channels ("5-й этап", "Гран-при", "трансляция"). Combined
# with the channel-name filter this catches e.g. НАСКАР on ТНТ.
_SPORT_BROADCAST_RE = re.compile(
    r"\b("
    r"гонка|этап|матч|чемпионат|кубок\b|трансляц|гран-при|ufc|дерби|"
    r"премьер-лига|лига чемпион|лига европ|кхл|рпл|нба|nba|nhl|"
    r"football|soccer|basketball|hockey|tennis|formula|nascar|grand prix"
    r")\b",
    re.IGNORECASE,
)


def _exclude_non_cinema(schedules: list) -> list:
    """Drop series episodes and sports broadcasts from cinema schedules.

    The keyword-inclusion filter alone can't catch an episode of «Интерны»
    whose description mentions «комедия» — it matches the cinema theme by
    word but is still a series. A negative regex applied AFTER the
    positive filter makes the cinema set cleanly feature-film.
    """
    from server.ai.context import ChannelSchedule  # local import, tight loop

    clean: list[ChannelSchedule] = []
    for sch in schedules:
        matching = tuple(
            p
            for p in sch.programmes
            if not _SERIES_MARKER_RE.search(p.title) and not _SPORT_BROADCAST_RE.search(p.title)
        )
        if matching:
            clean.append(
                ChannelSchedule(
                    channel_id=sch.channel_id,
                    channel_name=sch.channel_name,
                    group=sch.group,
                    programmes=matching,
                )
            )
    return clean


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
