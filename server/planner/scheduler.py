"""Background scheduler — polls plans once a minute and fires Telegram alerts.

Runs as an asyncio task launched at app startup and cancelled at shutdown.
Failures are swallowed per-iteration so one bad network call can't kill the
loop.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

from server.notify.telegram import TelegramClient, TelegramConfig, build_watch_url, escape
from server.planner.store import Plan, PlanStore

TICK_SECONDS = 60.0


def _fmt_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return dt.astimezone().strftime("%d.%m %H:%M")


def _caption_created(plan: Plan) -> str:
    lines = [
        "📌 <b>Запланировано</b>",
        f"<b>{escape(plan.title)}</b>",
        f"🎬 {escape(plan.channel_name)}  ·  🕐 {_fmt_time(plan.start)}",
    ]
    if plan.blurb:
        lines.append("")
        lines.append(escape(plan.blurb))
    return "\n".join(lines)


def _caption_live(plan: Plan) -> str:
    lines = [
        "🔴 <b>Начинается сейчас</b>",
        f"<b>{escape(plan.title)}</b>",
        f"🎬 {escape(plan.channel_name)}  ·  🕐 {_fmt_time(plan.start)}",
    ]
    if plan.blurb:
        lines.append("")
        lines.append(escape(plan.blurb))
    return "\n".join(lines)


async def run_scheduler_loop(store: PlanStore) -> None:
    """Forever loop that fires alerts as plans age into their windows."""
    while True:
        try:
            await asyncio.sleep(TICK_SECONDS)
            await _tick(store)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — keep the loop alive
            pass


async def _tick(store: PlanStore) -> None:
    cfg = TelegramConfig.from_env()
    if not cfg.enabled:
        # Still sweep "missed" so the UI is accurate.
        store.mark_missed_stale(datetime.now(UTC))
        return
    client = TelegramClient(cfg)
    now = datetime.now(UTC)

    for plan in store.due_for_live_alert(now):
        msg_id = await _notify_live(plan, cfg, client)
        # The old "📌 Запланировано" card is now redundant — the live alert
        # carries the same info plus the "🔴 Смотреть сейчас" CTA. Drop it
        # so the chat shows only one active card per plan. Failures are
        # swallowed (Telegram won't delete messages older than 48 h).
        if plan.tg_created_msg_id:
            with contextlib.suppress(Exception):
                await client.delete_message(plan.tg_created_msg_id)
        store.mark_notified_live(plan.id, message_id=msg_id)

    store.mark_missed_stale(now)

    # After the sweep, any plan newly flipped to `done` still has its
    # "🔴 Начинается сейчас" card hanging in the chat — clear it so the
    # Telegram group only keeps active / upcoming entries, not stale ones.
    for plan in list(store.list()):
        if plan.status != "done":
            continue
        if not plan.tg_live_msg_id:
            continue
        with contextlib.suppress(Exception):
            await client.delete_message(plan.tg_live_msg_id)
        store.clear_tg_messages(plan.id)


async def notify_plan_created(
    plan: Plan,
    store: PlanStore,
    client: TelegramClient | None = None,
) -> dict:
    """Fire the first alert the moment a plan is saved."""
    cfg = TelegramConfig.from_env()
    if client is None:
        client = TelegramClient(cfg)
    if not client.enabled:
        return {"ok": False, "error": "telegram disabled"}
    watch_url = build_watch_url(plan.channel_id, cfg.base_url)
    result = await client.send_card(
        caption_html=_caption_created(plan),
        poster_url=plan.poster_url or None,
        watch_url=watch_url,
        watch_label="Открыть в плеере",
    )
    if result.get("ok"):
        message_id = _extract_message_id(result)
        store.mark_notified_created(plan.id, message_id=message_id)
    return result


async def _notify_live(
    plan: Plan,
    cfg: TelegramConfig,
    client: TelegramClient,
) -> int | None:
    watch_url = build_watch_url(plan.channel_id, cfg.base_url)
    try:
        result = await client.send_card(
            caption_html=_caption_live(plan),
            poster_url=plan.poster_url or None,
            watch_url=watch_url,
            watch_label="🔴 Смотреть сейчас",
        )
    except Exception:  # noqa: BLE001
        return None
    return _extract_message_id(result)


def _extract_message_id(result: dict) -> int | None:
    payload = result.get("result") if isinstance(result, dict) else None
    if isinstance(payload, dict):
        mid = payload.get("message_id")
        if isinstance(mid, int):
            return mid
    return None


async def delete_plan_messages(plan: Plan, client: TelegramClient | None = None) -> None:
    """Remove any Telegram cards we sent for this plan. Best-effort."""
    if client is None:
        client = TelegramClient()
    if not client.enabled:
        return
    for mid in (plan.tg_created_msg_id, plan.tg_live_msg_id):
        if mid:
            with contextlib.suppress(Exception):
                await client.delete_message(mid)
