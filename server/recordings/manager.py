"""Recording manager — spin up ffmpeg subprocesses that write MKV files.

Design:
* Each recording is identified by a short slug (date+channel+hash).
* Metadata lives alongside the video as `<slug>.json` so listings survive
  restarts without a database.
* Status: `queued` (waiting for start) · `running` · `done` · `failed`.
* A single asyncio task per recording handles scheduling + ffmpeg launch,
  updates the sidecar file as things progress.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

RecordingStatus = Literal["queued", "running", "done", "failed"]
VALID_THEMES: tuple[str, ...] = ("sport", "cinema", "assistant")
MAX_DURATION_SECONDS = 6 * 3600  # safety cap: never record more than 6h


def _slug(value: str, limit: int = 40) -> str:
    s = re.sub(r"[^\w\-]+", "_", value, flags=re.UNICODE).strip("_")
    return s[:limit] or "rec"


@dataclass(slots=True)
class RecordingEntry:
    id: str
    channel_id: str
    channel_name: str
    title: str
    theme: str
    start: str  # ISO UTC
    stop: str  # ISO UTC
    status: RecordingStatus
    file: str = ""  # relative filename
    bytes: int = 0
    error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, object]:
        return dict(asdict(self))


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class RecordingManager:
    def __init__(self, root: Path, ffmpeg_bin: str = "ffmpeg") -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._ffmpeg = ffmpeg_bin
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    @property
    def root(self) -> Path:
        return self._root

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _meta_path(self, rec_id: str) -> Path:
        return self._root / f"{rec_id}.json"

    def _file_path(self, rec_id: str, filename: str) -> Path:
        return self._root / filename if filename else self._root / f"{rec_id}.mkv"

    def _save(self, entry: RecordingEntry) -> None:
        path = self._meta_path(entry.id)
        path.write_text(
            json.dumps(entry.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load(self, rec_id: str) -> RecordingEntry | None:
        path = self._meta_path(rec_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return RecordingEntry(**data)

    def list(self) -> list[RecordingEntry]:
        entries: list[RecordingEntry] = []
        for meta in self._root.glob("*.json"):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                entry = RecordingEntry(**data)
                # Refresh size from disk for entries that finished.
                file_path = self._file_path(entry.id, entry.file)
                if file_path.exists():
                    entry.bytes = file_path.stat().st_size
                entries.append(entry)
            except (OSError, json.JSONDecodeError, TypeError):
                continue
        entries.sort(key=lambda e: e.created_at, reverse=True)
        return entries

    def get(self, rec_id: str) -> RecordingEntry | None:
        return self._load(rec_id)

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    async def schedule(
        self,
        *,
        channel_id: str,
        channel_name: str,
        upstream_url: str,
        title: str,
        start: str,
        stop: str,
        theme: str = "other",
    ) -> RecordingEntry:
        """Queue a recording. Runs immediately if start is past / now."""
        start_dt = _parse_iso(start) or datetime.now(UTC)
        stop_dt = _parse_iso(stop) or start_dt
        # If start is in the past but stop is in the future, clip start=now.
        now = datetime.now(UTC)
        effective_start = start_dt if start_dt > now else now
        duration = max(30, int((stop_dt - effective_start).total_seconds()))
        duration = min(duration, MAX_DURATION_SECONDS)
        if stop_dt <= now:
            raise ValueError("Recording stop time is in the past")

        rec_id = f"{int(time.time())}-{_slug(title, 24)}-{uuid.uuid4().hex[:6]}"
        file_name = f"{rec_id}.mkv"
        theme_value = theme if theme in VALID_THEMES else "other"

        entry = RecordingEntry(
            id=rec_id,
            channel_id=channel_id,
            channel_name=channel_name,
            title=title,
            theme=theme_value,
            start=start_dt.isoformat(),
            stop=stop_dt.isoformat(),
            status="queued",
            file=file_name,
        )
        self._save(entry)

        async with self._lock:
            task = asyncio.create_task(self._run(entry.id, upstream_url, effective_start, duration))
            self._tasks[entry.id] = task
        return entry

    async def cancel(self, rec_id: str) -> bool:
        """Best-effort cancel: kill the asyncio task if it's alive AND flip
        the metadata status so the UI reflects the change immediately.

        Previously this returned False when the task was already gone (e.g.
        after a server restart left a stale "queued"/"running" entry in
        state.json). That made the UI's Cancel button a silent no-op.
        """
        async with self._lock:
            task = self._tasks.pop(rec_id, None)
        if task is not None:
            task.cancel()
        entry = self._load(rec_id)
        if entry is None:
            return False
        if entry.status in ("queued", "running"):
            entry = replace(entry, status="failed", error="cancelled")
            self._save(entry)
        return True

    async def delete(self, rec_id: str) -> bool:
        entry = self._load(rec_id)
        if entry is None:
            return False
        await self.cancel(rec_id)
        meta = self._meta_path(rec_id)
        file_path = self._file_path(rec_id, entry.file)
        with contextlib.suppress(OSError):
            meta.unlink()
        with contextlib.suppress(OSError):
            file_path.unlink()
        return True

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def _run(
        self,
        rec_id: str,
        upstream_url: str,
        effective_start: datetime,
        duration: int,
    ) -> None:
        # Wait until start moment.
        delta = (effective_start - datetime.now(UTC)).total_seconds()
        if delta > 0:
            try:
                await asyncio.sleep(delta)
            except asyncio.CancelledError:
                return

        entry = self._load(rec_id)
        if entry is None:
            return
        entry = replace(entry, status="running")
        self._save(entry)

        out_path = self._file_path(rec_id, entry.file)
        command = [
            self._ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-user_agent",
            "VLC/3.0.20 LibVLC/3.0.20",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
            "-i",
            upstream_url,
            "-t",
            str(duration),
            "-c",
            "copy",
            "-map",
            "0",
            "-f",
            "matroska",
            str(out_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        try:
            _, err = await process.communicate()
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            raise

        final_status: RecordingStatus = "done" if process.returncode == 0 else "failed"
        error_msg = ""
        if process.returncode != 0:
            error_msg = err.decode("utf-8", "ignore").strip()[:500]

        size = out_path.stat().st_size if out_path.exists() else 0
        entry = replace(entry, status=final_status, bytes=size, error=error_msg)
        self._save(entry)

        async with self._lock:
            self._tasks.pop(rec_id, None)

    async def stop_all(self) -> None:
        async with self._lock:
            tasks = list(self._tasks.values())
            self._tasks.clear()
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(Exception):
                await t
