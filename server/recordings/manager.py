"""Recording manager — spin up ffmpeg subprocesses that write MKV files.

Design:
* Each recording is identified by a short slug (date+channel+hash).
* Metadata lives alongside the video as `<slug>.json` so listings survive
  restarts without a database.
* Status: `queued` · `running` · `paused` · `done` · `failed`.
* A recording can be split across multiple MKV segments (`parts`) when the
  user pauses+resumes or when a server restart lands inside the capture
  window. On normal completion the segments are concatenated into a single
  `<slug>.mkv` via ffmpeg's concat demuxer.
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

RecordingStatus = Literal["queued", "running", "paused", "done", "failed"]
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
    file: str = ""  # primary / finalised filename
    bytes: int = 0
    error: str = ""
    # Persisted so the recording can be resumed after a server restart.
    upstream_url: str = ""
    # Poster url (TMDB / Wikipedia) attached at schedule time for dashboard.
    poster_url: str = ""
    # Individual MKV segments written so far. Empty ⇒ legacy entry (single
    # file == `file`). Multi-entry lists are merged on `_finalize`.
    parts: list[str] = field(default_factory=list)
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
        # rec_ids the caller has asked to pause; _run checks this set before
        # flipping status on CancelledError so a pause doesn't overwrite with
        # "failed".
        self._pausing: set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def root(self) -> Path:
        return self._root

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _meta_path(self, rec_id: str) -> Path:
        return self._root / f"{rec_id}.json"

    def _file_path(self, filename: str) -> Path:
        return self._root / filename

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
        try:
            return RecordingEntry(**data)
        except TypeError:
            return None

    def _parts_of(self, entry: RecordingEntry) -> list[str]:
        """Return the ordered segment list, coercing legacy single-file entries."""
        if entry.parts:
            return list(entry.parts)
        return [entry.file] if entry.file else []

    def _total_size(self, entry: RecordingEntry) -> int:
        total = 0
        for p in self._parts_of(entry):
            fp = self._file_path(p)
            if fp.exists():
                total += fp.stat().st_size
        return total

    def list(self) -> list[RecordingEntry]:
        entries: list[RecordingEntry] = []
        for meta in self._root.glob("*.json"):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                entry = RecordingEntry(**data)
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            entry.bytes = self._total_size(entry)
            entries.append(entry)
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
        poster_url: str = "",
    ) -> RecordingEntry:
        """Queue a recording. Runs immediately if start is past / now."""
        start_dt = _parse_iso(start) or datetime.now(UTC)
        stop_dt = _parse_iso(stop) or start_dt
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
            upstream_url=upstream_url,
            poster_url=poster_url,
            parts=[file_name],
        )
        self._save(entry)

        async with self._lock:
            task = asyncio.create_task(
                self._run(rec_id, upstream_url, effective_start, duration, file_name)
            )
            self._tasks[rec_id] = task
        return entry

    # ------------------------------------------------------------------
    # Pause / Resume / Cancel
    # ------------------------------------------------------------------

    async def pause(self, rec_id: str) -> bool:
        """Gracefully stop the current ffmpeg segment; recording can resume later.

        Unlike cancel(), this preserves the recording so resume() can append a
        new segment for the remaining window.
        """
        entry = self._load(rec_id)
        if entry is None or entry.status not in ("queued", "running"):
            return False
        async with self._lock:
            task = self._tasks.pop(rec_id, None)
        self._pausing.add(rec_id)
        try:
            if task is not None:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
        finally:
            self._pausing.discard(rec_id)
        # Re-read to pick up any size updates, then flip to paused.
        entry = self._load(rec_id) or entry
        size = self._total_size(entry)
        self._save(replace(entry, status="paused", bytes=size, error=""))
        return True

    async def resume(self, rec_id: str) -> bool:
        """Start a new MKV segment for the remaining window of a paused recording."""
        entry = self._load(rec_id)
        if entry is None or entry.status != "paused":
            return False
        if not entry.upstream_url:
            return False
        now = datetime.now(UTC)
        stop_dt = _parse_iso(entry.stop)
        if stop_dt is None or stop_dt <= now:
            # Nothing left to record — finalise what we have.
            await self._finalize(rec_id)
            return False
        duration = max(30, int((stop_dt - now).total_seconds()))
        duration = min(duration, MAX_DURATION_SECONDS)
        parts = self._parts_of(entry)
        next_name = f"{rec_id}_p{len(parts) + 1}.mkv"
        new_parts = parts + [next_name]
        self._save(replace(entry, parts=new_parts, status="queued", error=""))
        async with self._lock:
            task = asyncio.create_task(
                self._run(rec_id, entry.upstream_url, now, duration, next_name)
            )
            self._tasks[rec_id] = task
        return True

    async def cancel(self, rec_id: str) -> bool:
        """Abort a recording permanently.

        Kills the ffmpeg task if alive and flips metadata to `failed` so the UI
        reflects the change immediately even when the asyncio task has already
        exited (e.g. after a server restart left a stale entry).
        """
        async with self._lock:
            task = self._tasks.pop(rec_id, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        entry = self._load(rec_id)
        if entry is None:
            return False
        if entry.status in ("queued", "running", "paused"):
            entry = replace(entry, status="failed", error="cancelled")
            self._save(entry)
        return True

    async def delete(self, rec_id: str) -> bool:
        entry = self._load(rec_id)
        if entry is None:
            return False
        await self.cancel(rec_id)
        meta = self._meta_path(rec_id)
        for p in self._parts_of(entry):
            with contextlib.suppress(OSError):
                self._file_path(p).unlink()
        with contextlib.suppress(OSError):
            meta.unlink()
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
        output_segment: str,
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
        self._save(replace(entry, status="running"))

        out_path = self._file_path(output_segment)
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
            # Wait briefly for MKV to finalise cleanly on SIGTERM.
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(process.wait(), timeout=5)
            raise

        entry = self._load(rec_id) or entry
        stop_dt = _parse_iso(entry.stop)
        now = datetime.now(UTC)
        # Anything within 5s of stop counts as "reached end"; ffmpeg's -t
        # doesn't hit the exact second.
        reached_end = stop_dt is not None and (stop_dt - now).total_seconds() <= 5

        if process.returncode == 0 and reached_end:
            await self._finalize(rec_id)
        elif process.returncode == 0:
            # Segment cap expired but window not over (e.g. 6h safety cap).
            # Treat as a pause-ish state so the user can resume.
            size = self._total_size(entry)
            self._save(replace(entry, status="paused", bytes=size, error=""))
        else:
            error_msg = err.decode("utf-8", "ignore").strip()[:500]
            size = self._total_size(entry)
            self._save(replace(entry, status="failed", bytes=size, error=error_msg))

        async with self._lock:
            self._tasks.pop(rec_id, None)

    async def _finalize(self, rec_id: str) -> None:
        """Merge segments into a single MKV and flip status to `done`.

        Single-segment recordings skip the merge. If concat fails, the entry
        is still marked `done` but keeps the raw parts so nothing is lost.
        """
        entry = self._load(rec_id)
        if entry is None:
            return
        parts = [p for p in self._parts_of(entry) if self._file_path(p).exists()]
        if not parts:
            self._save(replace(entry, status="failed", error="no segments produced"))
            return
        if len(parts) == 1:
            size = self._file_path(parts[0]).stat().st_size
            self._save(
                replace(entry, status="done", file=parts[0], parts=parts, bytes=size, error="")
            )
            return

        merged_name = f"{rec_id}.mkv"
        if merged_name in parts:
            # Would collide with an existing segment — use a distinct name.
            merged_name = f"{rec_id}_merged.mkv"
        merged_path = self._file_path(merged_name)
        list_path = self._root / f".{rec_id}.concat.txt"
        list_path.write_text(
            "\n".join(f"file '{self._file_path(p).as_posix()}'" for p in parts),
            encoding="utf-8",
        )
        command = [
            self._ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            "-y",
            str(merged_path),
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        _, err = await process.communicate()
        with contextlib.suppress(OSError):
            list_path.unlink()

        if process.returncode == 0 and merged_path.exists():
            for p in parts:
                if p != merged_name:
                    with contextlib.suppress(OSError):
                        self._file_path(p).unlink()
            size = merged_path.stat().st_size
            self._save(
                replace(
                    entry,
                    status="done",
                    file=merged_name,
                    parts=[merged_name],
                    bytes=size,
                    error="",
                )
            )
        else:
            err_msg = err.decode("utf-8", "ignore").strip()[:500]
            total = self._total_size(entry)
            self._save(
                replace(
                    entry,
                    status="done",
                    bytes=total,
                    error=f"merge failed: {err_msg}" if err_msg else "merge failed",
                )
            )

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    async def resume_pending(self) -> int:
        """Re-arm queued/running recordings after a server restart.

        Entries in `running` state get a fresh segment appended rather than
        overwriting the partial MKV left on disk. `paused` entries are left
        alone — the user explicitly stopped them.
        """
        now = datetime.now(UTC)
        resumed = 0
        for meta in self._root.glob("*.json"):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                entry = RecordingEntry(**data)
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            if entry.status not in ("queued", "running"):
                continue
            stop_dt = _parse_iso(entry.stop)
            start_dt = _parse_iso(entry.start)
            if stop_dt is None or start_dt is None or stop_dt <= now:
                # Window elapsed — salvage whatever was already captured.
                if self._parts_of(entry) and self._total_size(entry) > 0:
                    await self._finalize(entry.id)
                else:
                    self._save(replace(entry, status="failed", error="window elapsed"))
                continue
            if not entry.upstream_url:
                self._save(replace(entry, status="failed", error="upstream url not persisted"))
                continue

            effective_start = start_dt if start_dt > now else now
            duration = max(30, int((stop_dt - effective_start).total_seconds()))
            duration = min(duration, MAX_DURATION_SECONDS)

            if entry.status == "running":
                # ffmpeg was mid-stream when the process died. The existing MKV
                # has been finalised at SIGTERM time; start a new segment for
                # the remainder so nothing already captured is overwritten.
                parts = self._parts_of(entry)
                next_name = f"{entry.id}_p{len(parts) + 1}.mkv"
                new_parts = parts + [next_name]
                self._save(replace(entry, parts=new_parts, status="queued"))
                segment_name = next_name
            else:  # queued
                parts = self._parts_of(entry)
                segment_name = parts[-1] if parts else f"{entry.id}.mkv"
                if not parts:
                    self._save(replace(entry, parts=[segment_name]))

            async with self._lock:
                task = asyncio.create_task(
                    self._run(entry.id, entry.upstream_url, effective_start, duration, segment_name)
                )
                self._tasks[entry.id] = task
            resumed += 1
        return resumed

    async def stop_all(self) -> None:
        async with self._lock:
            tasks = list(self._tasks.values())
            self._tasks.clear()
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(Exception):
                await t
