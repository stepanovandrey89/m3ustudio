"""Thin Telegram Bot API wrapper focused on sending poster-cards with buttons.

Reads configuration from environment at call time so users can edit .env without
restarting the process. When the bot token or chat id is missing the client
silently no-ops — the rest of the app keeps working and the user just won't get
notifications.
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx


def _resolve_public_poster_url(raw: str | None) -> str | None:
    """Return a publicly fetchable absolute URL for Telegram's link
    preview, or ``None`` when no usable URL is available.

    Plans and digests store posters as ``/api/ai/poster-image?src=<url>``
    — relative paths into our proxy. Telegram rejects the whole
    ``sendMessage`` call with ``WEBPAGE_URL_INVALID`` when the
    ``link_preview_options.url`` isn't absolute. Besides, our proxy
    sits behind Cloudflare Access (Google-OAuth gate) so even an
    absolutised version isn't reachable by Telegram's bot. Passing
    the underlying ``src`` — the real TMDB / Wikipedia / DDG CDN URL
    — skips both issues.
    """
    if not raw:
        return None
    if raw.startswith(("http://", "https://")):
        return raw
    if raw.startswith("/api/ai/poster-image"):
        parsed = urlparse(raw)
        src = parse_qs(parsed.query).get("src") or []
        if src and src[0].startswith(("http://", "https://")):
            return src[0]
    return None


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    bot_token: str
    chat_id: str
    base_url: str

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    @classmethod
    def from_env(cls) -> TelegramConfig:
        return cls(
            bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
            chat_id=os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
            base_url=os.environ.get("PUBLIC_BASE_URL", "").rstrip("/"),
        )


def build_watch_url(channel_id: str, base_url: str) -> str | None:
    """Deep link back into the running server. Empty base_url disables the link."""
    if not base_url or not channel_id:
        return None
    return f"{base_url}/?watch={channel_id}"


class TelegramClient:
    """Sends rich poster notifications via sendPhoto/sendMessage."""

    def __init__(self, config: TelegramConfig | None = None) -> None:
        self._config = config or TelegramConfig.from_env()

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def _api(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self._config.bot_token}/{method}"

    async def send_card(
        self,
        *,
        caption_html: str,
        poster_url: str | None,
        watch_url: str | None,
        watch_label: str = "Смотреть",
    ) -> dict[str, Any]:
        """Send a compact card — poster becomes a small link-preview thumb.

        Telegram's ``sendPhoto`` renders the image at full width, which drowns
        the caption. Instead we use ``sendMessage`` with an invisible link to
        the poster at the start of the text and ask Telegram for a small
        preview via ``link_preview_options.prefer_small_media``. The result is
        a 60-80 px square thumbnail to the left of the text — classic Telegram
        rich-message style, compact and readable.
        """
        if not self.enabled:
            return {"ok": False, "error": "telegram disabled"}

        keyboard: dict[str, Any] | None = None
        if watch_url:
            keyboard = {"inline_keyboard": [[{"text": watch_label, "url": watch_url}]]}

        # Telegram needs a publicly-fetchable ABSOLUTE URL for the link
        # preview. Our plans store proxied "/api/ai/poster-image?src=..."
        # paths — reject-as-relative would bounce the whole message with
        # WEBPAGE_URL_INVALID. Extract the underlying src (TMDB / Wiki /
        # DDG CDN), which Telegram can fetch directly.
        poster_url = _resolve_public_poster_url(poster_url)

        if poster_url:
            # U+200B (ZWSP) hides the anchor visually but still triggers link preview.
            text = f'<a href="{poster_url}">\u200b</a>{caption_html}'
            payload: dict[str, Any] = {
                "chat_id": self._config.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "link_preview_options": {
                    "is_disabled": False,
                    "url": poster_url,
                    "prefer_small_media": True,
                    "show_above_text": False,
                },
            }
        else:
            payload = {
                "chat_id": self._config.chat_id,
                "text": caption_html,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            }
        if keyboard:
            payload["reply_markup"] = keyboard

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(self._api("sendMessage"), json=payload)
                return resp.json()
            except httpx.HTTPError as exc:
                return {"ok": False, "error": str(exc)}

    async def delete_message(self, message_id: int) -> dict[str, Any]:
        """Best-effort removal — bots can only delete messages they sent in
        the last 48 h. Older messages simply fail; we swallow that silently.
        """
        if not self.enabled or not message_id:
            return {"ok": False, "error": "telegram disabled"}
        async with httpx.AsyncClient(timeout=6.0) as client:
            try:
                resp = await client.post(
                    self._api("deleteMessage"),
                    json={
                        "chat_id": self._config.chat_id,
                        "message_id": message_id,
                    },
                )
                return resp.json()
            except httpx.HTTPError as exc:
                return {"ok": False, "error": str(exc)}


def escape(text: str) -> str:
    """HTML-escape a user-visible string for Telegram caption/parse_mode=HTML."""
    return html.escape(text or "", quote=False)
