"""Daily digest generator — picks 10 highlights per theme from today's EPG.

Cached to disk by (theme, lang). The cache survives browser sessions,
server restarts, and day boundaries — items carry absolute ISO start/stop
so the frontend can show countdowns / "уже прошло" badges regardless of
how old the cache file is. The HTTP layer decides when to regenerate
(too few live items left, user asked for refresh, etc).
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
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
    poster_url: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "title": self.title,
            "start": self.start,
            "stop": self.stop,
            "blurb": self.blurb,
            "poster_keywords": self.poster_keywords,
            "poster_url": self.poster_url,
        }


@dataclass(frozen=True, slots=True)
class Digest:
    date: str  # ISO date (YYYY-MM-DD) — the day the digest was generated
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
            poster_url=str(i.get("poster_url", "")),
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
    """File-backed cache keyed by (theme, lang) — date-independent.

    One file per (theme, lang) means a digest generated yesterday is
    still served today as long as its items' ``stop`` times haven't all
    passed. The caller is responsible for deciding when the cache is
    too stale (generated_at age) or drained (not enough live items).

    Legacy ``digest-YYYY-MM-DD-*.json`` files from the previous cache
    layout are migrated on init: the most recent per (theme, lang) wins
    and is rewritten to the new path; older ones are deleted.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy()

    def _path(self, theme: Theme, lang: str) -> Path:
        return self._root / f"digest-{theme}-{lang}.json"

    def get(self, theme: Theme, lang: str) -> Digest | None:
        path = self._path(theme, lang)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return digest_from_dict(data)
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, digest: Digest) -> None:
        path = self._path(digest.theme, digest.lang)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(digest.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    def invalidate(self, theme: Theme | None = None, lang: str | None = None) -> int:
        """Delete cache entries. With no args — wipe all."""
        count = 0
        if theme is not None and lang is not None:
            p = self._path(theme, lang)
            if p.exists():
                try:
                    p.unlink()
                    count += 1
                except OSError:
                    pass
            return count
        for p in self._root.glob("digest-*.json"):
            try:
                p.unlink()
                count += 1
            except OSError:
                pass
        return count

    def _migrate_legacy(self) -> None:
        """One-shot sweep of ``digest-YYYY-MM-DD-theme-lang.json`` files.

        Pick the newest mtime per (theme, lang), rewrite it to the new
        path, delete the rest. Safe to call every init — if no legacy
        files exist it's a no-op.
        """
        legacy: dict[tuple[str, str], tuple[float, Path]] = {}
        for p in self._root.glob("digest-*.json"):
            # New format has exactly two '-' after "digest" in the stem:
            # "digest-<theme>-<lang>". Anything with a 10-char date block
            # after "digest-" is legacy.
            stem = p.stem  # "digest-YYYY-MM-DD-theme-lang" or "digest-theme-lang"
            parts = stem.split("-")
            # Legacy path: parts = ["digest", "YYYY", "MM", "DD", "theme", "lang"]
            if len(parts) < 6:
                continue
            try:
                # Treat parts[1:4] as a date — if any aren't numeric we skip.
                int(parts[1])
                int(parts[2])
                int(parts[3])
            except ValueError:
                continue
            theme = parts[4]
            lang = "-".join(parts[5:])
            mtime = p.stat().st_mtime
            key = (theme, lang)
            prev = legacy.get(key)
            if prev is None or mtime > prev[0]:
                # Delete the previously-held older file.
                if prev is not None:
                    with contextlib.suppress(OSError):
                        prev[1].unlink()
                legacy[key] = (mtime, p)
            else:
                with contextlib.suppress(OSError):
                    p.unlink()
        # Promote winners into the new (theme, lang) path.
        for (theme, lang), (_mtime, p) in legacy.items():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            new_path = self._root / f"digest-{theme}-{lang}.json"
            # Skip if new-format file already exists (a fresh write already
            # took precedence).
            if new_path.exists():
                with contextlib.suppress(OSError):
                    p.unlink()
                continue
            try:
                new_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                p.unlink()
            except OSError:
                pass


def live_items(digest: Digest, now: datetime | None = None) -> tuple[DigestEntry, ...]:
    """Items whose ``stop`` time is still in the future."""
    ts = (now or datetime.now(UTC)).timestamp()
    out: list[DigestEntry] = []
    for item in digest.items:
        try:
            stop_dt = datetime.fromisoformat(item.stop)
        except ValueError:
            continue
        if stop_dt.timestamp() > ts:
            out.append(item)
    return tuple(out)
