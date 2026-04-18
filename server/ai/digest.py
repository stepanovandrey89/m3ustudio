"""Daily digest generator — picks 10 highlights per theme from today's EPG.

Cached to disk so a re-open in the same day doesn't re-hit the API.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

Theme = Literal["sport", "cinema", "assistant"]
ALL_THEMES: tuple[Theme, ...] = ("sport", "cinema", "assistant")


@dataclass(frozen=True, slots=True)
class DigestEntry:
    channel_id: str
    channel_name: str
    title: str
    start: str
    stop: str
    blurb: str
    poster_keywords: str

    def to_dict(self) -> dict[str, str]:
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "title": self.title,
            "start": self.start,
            "stop": self.stop,
            "blurb": self.blurb,
            "poster_keywords": self.poster_keywords,
        }


@dataclass(frozen=True, slots=True)
class Digest:
    date: str  # ISO date (YYYY-MM-DD)
    theme: Theme
    lang: str
    items: tuple[DigestEntry, ...]
    generated_at: str = ""  # ISO-8601 timestamp of generation (UTC)

    def to_dict(self) -> dict[str, object]:
        return {
            "date": self.date,
            "theme": self.theme,
            "lang": self.lang,
            "generated_at": self.generated_at,
            "items": [i.to_dict() for i in self.items],
        }


def digest_from_dict(data: dict) -> Digest:
    items = tuple(
        DigestEntry(
            channel_id=str(i.get("channel_id", "")),
            channel_name=str(i.get("channel_name", "")),
            title=str(i.get("title", "")),
            start=str(i.get("start", "")),
            stop=str(i.get("stop", "")),
            blurb=str(i.get("blurb", "")),
            poster_keywords=str(i.get("poster_keywords", "")),
        )
        for i in data.get("items", [])
    )
    return Digest(
        date=str(data.get("date", "")),
        theme=data.get("theme", "other"),  # type: ignore[arg-type]
        lang=str(data.get("lang", "ru")),
        generated_at=str(data.get("generated_at", "")),
        items=items,
    )


class DigestCache:
    """File-backed cache keyed by (date, theme, lang)."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, day: date, theme: Theme, lang: str) -> Path:
        return self._root / f"digest-{day.isoformat()}-{theme}-{lang}.json"

    def get(self, day: date, theme: Theme, lang: str) -> Digest | None:
        path = self._path(day, theme, lang)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return digest_from_dict(data)
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, digest: Digest) -> None:
        day = date.fromisoformat(digest.date)
        path = self._path(day, digest.theme, digest.lang)
        path.write_text(
            json.dumps(digest.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def invalidate(self, day: date | None = None) -> int:
        """Delete all cache entries for a given date (all if None). Returns count."""
        count = 0
        for p in self._root.glob("digest-*.json"):
            if day is not None and not p.name.startswith(f"digest-{day.isoformat()}-"):
                continue
            try:
                p.unlink()
                count += 1
            except OSError:
                pass
        return count
