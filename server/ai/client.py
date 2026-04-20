"""OpenAI async client singleton.

Reads OPENAI_API_KEY and OPENAI_MODEL from .env / environment. The client is
created lazily so the server can boot even when the key is missing — callers
must handle `None` and surface a friendly error to the UI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()


@dataclass(frozen=True, slots=True)
class AIConfig:
    api_key: str
    model: str
    digest_model: str
    enabled: bool

    @classmethod
    def from_env(cls) -> AIConfig:
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        model = os.environ.get("OPENAI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"
        # Digest runs non-interactively and just picks 10 items from a
        # pre-filtered list — reasoning models are overkill and 40-60s is
        # longer than Cloudflare's 100s edge timeout can tolerate on Free.
        # Falls back to gpt-4o-mini, overridable via env.
        digest_model = (
            os.environ.get("OPENAI_DIGEST_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
        )
        return cls(api_key=key, model=model, digest_model=digest_model, enabled=bool(key))


@lru_cache(maxsize=1)
def get_client() -> AsyncOpenAI | None:
    """Return a cached async OpenAI client, or None when no API key is set."""
    cfg = AIConfig.from_env()
    if not cfg.enabled:
        return None
    return AsyncOpenAI(api_key=cfg.api_key)
