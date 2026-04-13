"""Persistent logo registry — tracks resolution status per channel.

Stored as ``logos_cache/_registry.json``. Each entry records:
- channel name, EPG icon URL (if any), resolution source, attempt count,
  and whether the logo is cached on disk.

The warming loop updates this registry on every attempt. The frontend
reads it through ``/api/logos/registry`` for the Logo Management UI.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class LogoEntry:
    name: str
    epg_url: str = ""
    source: str = ""  # 'rtrs' | 'tv-logos' | 'epg' | 'iptv-org' | 'manual' | ''
    status: str = "pending"  # 'found' | 'missing' | 'pending' | 'skipped'
    attempts: int = 0
    cached: bool = False


MAX_ATTEMPTS = 5


class LogoRegistry:
    """Thread-safe persistent registry of logo resolution state."""

    def __init__(self, cache_dir: Path) -> None:
        self._path = cache_dir / "_registry.json"
        self._lock = threading.RLock()
        self._entries: dict[str, LogoEntry] = {}
        self._load()

    # ---- Persistence -------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for cid, data in raw.items():
                self._entries[cid] = LogoEntry(
                    **{k: v for k, v in data.items() if k in LogoEntry.__dataclass_fields__}
                )
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {cid: asdict(e) for cid, e in self._entries.items()}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)

    # ---- Public API --------------------------------------------------------

    def get(self, channel_id: str) -> LogoEntry | None:
        with self._lock:
            return self._entries.get(channel_id)

    def all_entries(self) -> dict[str, LogoEntry]:
        with self._lock:
            return dict(self._entries)

    def ensure_channel(self, channel_id: str, name: str, epg_url: str = "") -> LogoEntry:
        """Create entry if missing; never overwrite existing."""
        with self._lock:
            if channel_id not in self._entries:
                self._entries[channel_id] = LogoEntry(name=name, epg_url=epg_url)
            elif epg_url and not self._entries[channel_id].epg_url:
                self._entries[channel_id].epg_url = epg_url
            return self._entries[channel_id]

    def mark_found(self, channel_id: str, source: str) -> None:
        with self._lock:
            e = self._entries.get(channel_id)
            if e:
                e.status = "found"
                e.source = source
                e.cached = True
                e.attempts += 1
                self._persist()

    def mark_miss(self, channel_id: str) -> None:
        with self._lock:
            e = self._entries.get(channel_id)
            if e:
                e.attempts += 1
                if e.attempts >= MAX_ATTEMPTS:
                    e.status = "missing"
                self._persist()

    def mark_manual(self, channel_id: str, name: str) -> None:
        with self._lock:
            e = self._entries.setdefault(channel_id, LogoEntry(name=name))
            e.status = "found"
            e.source = "manual"
            e.cached = True
            self._persist()

    def reset_for_retry(self, channel_id: str) -> None:
        """Reset a single channel so warming picks it up again."""
        with self._lock:
            e = self._entries.get(channel_id)
            if e:
                e.status = "pending"
                e.attempts = 0
                self._persist()

    def reset_all_failed(self) -> int:
        """Reset all 'missing' entries for retry. Returns count."""
        with self._lock:
            count = 0
            for e in self._entries.values():
                if e.status == "missing":
                    e.status = "pending"
                    e.attempts = 0
                    count += 1
            if count:
                self._persist()
            return count

    def mark_skipped(self, channel_id: str) -> None:
        """Skip a channel — no more retries on future startups."""
        with self._lock:
            e = self._entries.get(channel_id)
            if e:
                e.status = "skipped"
                self._persist()

    def should_retry(self, channel_id: str) -> bool:
        with self._lock:
            e = self._entries.get(channel_id)
            if not e:
                return True
            return e.status == "pending" and e.attempts < MAX_ATTEMPTS

    def update_cached_flags(self, has_cached_fn: ...) -> None:
        """Sync cached flag with what's actually on disk."""
        with self._lock:
            changed = False
            for _cid, e in self._entries.items():
                on_disk = has_cached_fn(e.name)
                if e.cached != on_disk:
                    e.cached = on_disk
                    if on_disk and e.status != "found":
                        e.status = "found"
                        e.source = e.source or "manual"
                    changed = True
            if changed:
                self._persist()

    def stats(self) -> dict[str, int]:
        with self._lock:
            total = len(self._entries)
            found = sum(1 for e in self._entries.values() if e.status == "found")
            missing = sum(1 for e in self._entries.values() if e.status == "missing")
            pending = sum(1 for e in self._entries.values() if e.status == "pending")
            skipped = sum(1 for e in self._entries.values() if e.status == "skipped")
            return {
                "total": total,
                "found": found,
                "missing": missing,
                "pending": pending,
                "skipped": skipped,
            }

    def save(self) -> None:
        with self._lock:
            self._persist()
