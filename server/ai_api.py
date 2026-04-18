"""HTTP surface for AI assistant + daily digest + recordings.

Wired into the main app via `include_router(ai_api.build_router(state))`.
Keeps the AI/recording surface out of main.py so it stays approachable.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from server.ai.client import AIConfig, get_client
from server.ai.context import build_main_schedule, channels_mentioned
from server.ai.digest import ALL_THEMES, DigestCache, Theme
from server.ai.generate import ToolExecutor, generate_digest, stream_chat
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
        schedules = build_main_schedule(
            state.epg,
            main_channels,
            past_hours=0,
            future_hours=12,
            only_upcoming=True,
        )
        result = await generate_digest(client, cfg, schedules, theme_typed, lang)
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
    ) -> JSONResponse:
        hit = await posters.resolve(keywords, lang)
        if hit is None:
            return JSONResponse({"url": None, "source": "none"})
        return JSONResponse(hit.to_dict())

    # ------------- Chat (SSE) --------------------------------------------

    @router.post("/ai/chat")
    async def chat(body: ChatBody, request: Request) -> StreamingResponse:
        client = get_client()
        cfg = AIConfig.from_env()
        if client is None:
            raise HTTPException(503, "OPENAI_API_KEY is not configured")

        main_channels = _main_channels(state)
        # Default chat scope: next 12 h, strictly upcoming. `deep_search`
        # widens to 7 days for queries like "what's on Champions League next
        # Tuesday" where the everyday window can't reach.
        future_hours = 168 if body.deep_search else 12
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
            only_upcoming=True,
        )
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
        poster_url = ""
        keywords = (body.poster_keywords or body.title).strip()
        if keywords:
            try:
                hit = await posters.resolve(keywords, body.lang)
                poster_url = hit.url if hit else ""
            except Exception:  # noqa: BLE001
                poster_url = ""

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
    channel = state.playlist.by_id(channel_id)
    if channel is None:
        return {"ok": False, "error": f"unknown channel_id: {channel_id}"}
    # Poster lookup is best-effort — a miss just leaves the card without art.
    poster_url = ""
    keywords = (poster_keywords or title).strip()
    if keywords:
        try:
            hit = await state.posters.resolve(keywords, lang)
            poster_url = hit.url if hit else ""
        except Exception:  # noqa: BLE001
            poster_url = ""
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
    keywords = (poster_keywords or title).strip()
    poster_url: str | None = None
    if keywords:
        try:
            hit = await state.posters.resolve(keywords, lang)
            poster_url = hit.url if hit else None
        except Exception:  # noqa: BLE001 — UI degrades gracefully
            poster_url = None
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


def _main_channels(state: Any) -> list:  # noqa: ANN401
    ids = state.store.current_ids()
    by_id = {ch.id: ch for ch in state.playlist.channels}
    return [by_id[cid] for cid in ids if cid in by_id]


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Not JSON serialisable: {type(obj)!r}")
