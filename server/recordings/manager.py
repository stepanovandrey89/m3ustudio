"""Recording manager — spin up ffmpeg subprocesses that write MKV files.

Design:
* Each recording is identified by a short slug (date+channel+hash).
* Metadata lives alongside the video as `<slug>.json` so listings survive
  restarts without a database.
* Status: `queued` · `running` · `paused` · `done` · `failed`.
* A recording can be split across multiple MKV segments (`parts`) while
  it's still active (pause/resume or restart hops). On final completion
  `_mark_done` concatenates the segments into a single MKV via ffmpeg's
  concat demuxer (`-c copy`, no re-encode) and deletes the parts, so the
  browser player gets one continuous timeline. While a recording is
  `paused` the segments stay as separate files and the player plays them
  sequentially via `onEnded`.
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
    # file == `file`). The player streams them sequentially; we don't merge.
    parts: list[str] = field(default_factory=list)
    # Summed playable duration across all parts (seconds). 0 means "not yet
    # measured" (e.g. still recording); UI falls back to the <video> metadata.
    duration_seconds: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    # MP4-container remux of the finalised recording — mobile Safari and
    # Chrome can't reliably play MKV, so after _mark_done we produce an
    # MP4 sibling via `ffmpeg -c copy -movflags +faststart` (near-zero
    # cost, no re-encode). Empty string when the remux hasn't run yet
    # or failed; the /file endpoint falls back to `file` in that case.
    mp4_file: str = ""

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
        # Derive ffprobe alongside the configured ffmpeg binary so a custom
        # ffmpeg path (e.g. /opt/homebrew/bin/ffmpeg) still finds its sibling.
        ffmpeg_path = Path(ffmpeg_bin)
        self._ffprobe = (
            str(ffmpeg_path.with_name("ffprobe")) if ffmpeg_path.parent != Path("") else "ffprobe"
        )
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

    async def _probe_duration(self, path: Path) -> float:
        """Return MKV playable duration in seconds; 0.0 if unknown."""
        if not path.exists():
            return 0.0
        cmd = [
            self._ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            out, _ = await process.communicate()
        except (OSError, FileNotFoundError):
            return 0.0
        if process.returncode != 0:
            return 0.0
        try:
            return float(out.decode().strip())
        except ValueError:
            return 0.0

    async def _total_duration(self, entry: RecordingEntry) -> float:
        total = 0.0
        for p in self._parts_of(entry):
            total += await self._probe_duration(self._file_path(p))
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
        duration = await self._total_duration(entry)
        self._save(replace(entry, status="paused", bytes=size, duration_seconds=duration, error=""))
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
            # Nothing left to record — close out with whatever we have.
            await self._mark_done(rec_id)
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
        """Stop a recording.

        * `queued` / `running` with nothing on disk yet → flip to `failed`
          with error="cancelled".
        * `paused` (or `running` with real content captured) → treat as
          "finish early": run `_mark_done` so the captured parts merge into a
          single MKV and the recording lands in the archive as saved content.
          This matches user expectation — pausing then cancelling should not
          throw away data the user already chose to keep.
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
        if entry.status not in ("queued", "running", "paused"):
            return True
        # Refresh — pause/running may have written new bytes since we loaded.
        entry = self._load(rec_id) or entry
        has_content = self._parts_of(entry) and self._total_size(entry) > 0
        if has_content:
            await self._mark_done(rec_id)
        else:
            self._save(replace(entry, status="failed", error="cancelled"))
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

        # Wall-clock deadline: ffmpeg's ``-t`` is stream-time based, so a
        # stalled upstream can keep the subprocess alive past the scheduled
        # ``stop_dt``. Without this watchdog, the UI keeps pulsing "Идёт
        # запись…" for a programme that's already over. We give ffmpeg a
        # small grace window past stop_dt to finalise the MKV trailer
        # naturally, then SIGTERM it so the job closes promptly.
        WATCHDOG_GRACE_SECONDS = 10.0
        stop_dt_wall = _parse_iso(entry.stop)
        wall_deadline: float | None = None
        if stop_dt_wall is not None:
            wall_deadline = max(
                1.0,
                (stop_dt_wall - datetime.now(UTC)).total_seconds() + WATCHDOG_GRACE_SECONDS,
            )

        err: bytes = b""
        hit_wall_deadline = False
        try:
            if wall_deadline is not None:
                try:
                    _, err = await asyncio.wait_for(process.communicate(), timeout=wall_deadline)
                except TimeoutError:
                    # Programme window is over — terminate ffmpeg and drain
                    # its output so the MKV trailer is flushed.
                    hit_wall_deadline = True
                    with contextlib.suppress(ProcessLookupError):
                        process.terminate()
                    try:
                        _, err = await asyncio.wait_for(process.communicate(), timeout=5.0)
                    except TimeoutError:
                        with contextlib.suppress(ProcessLookupError):
                            process.kill()
                        with contextlib.suppress(Exception):
                            _, err = await process.communicate()
            else:
                _, err = await process.communicate()
        except asyncio.CancelledError:
            # User-initiated cancel/pause — different path from the
            # wall-deadline; bubble up so the caller's cleanup runs.
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(process.wait(), timeout=5)
            raise

        entry = self._load(rec_id) or entry
        stop_dt = _parse_iso(entry.stop)
        now = datetime.now(UTC)
        # Anything within 5s of stop counts as "reached end"; ffmpeg's -t
        # doesn't hit the exact second.
        reached_end = stop_dt is not None and (stop_dt - now).total_seconds() <= 5

        if hit_wall_deadline:
            # Watchdog fired: we're past stop_dt regardless of what ffmpeg
            # returned. Salvage whatever was captured and close the job so
            # the UI stops pulsing "recording in progress".
            if self._parts_of(entry) and self._total_size(entry) > 0:
                await self._mark_done(rec_id)
            else:
                self._save(
                    replace(
                        entry,
                        status="failed",
                        error="programme window elapsed before any content captured",
                    )
                )
        elif process.returncode == 0 and reached_end:
            await self._mark_done(rec_id)
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

    async def _remux_to_mp4(self, rec_id: str, mkv_name: str) -> str:
        """Produce an MP4 copy of the finalised MKV for mobile playback.

        iOS Safari / Chrome on Android can't decode the MKV container —
        the video element renders an empty progress bar because the
        stream never initialises. Remuxing to MP4 with
        ``-movflags +faststart`` moves the moov atom to the head of the
        file so progressive playback begins immediately. Streams are
        copied without re-encoding (close to disk-write speed).

        Tries ``-c copy`` first. If that fails — most commonly because
        the source carries AC-3 audio which MP4 doesn't permit — falls
        back to ``-c:v copy -c:a aac`` to transcode only the audio track.

        Returns the MP4 filename on success, empty string on any failure
        so the caller keeps the MKV as the only playable file.
        """
        mkv_path = self._file_path(mkv_name)
        if not mkv_path.exists():
            return ""
        mp4_name = f"{rec_id}.mp4"
        mp4_path = self._file_path(mp4_name)
        base_cmd = [
            self._ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-i",
            str(mkv_path),
            "-map",
            "0",
            "-movflags",
            "+faststart",
            "-y",
            str(mp4_path),
        ]
        # Pass 1 — full copy (works for H.264/AAC, most IPTV streams).
        for codec_args in (["-c", "copy"], ["-c:v", "copy", "-c:a", "aac", "-b:a", "160k"]):
            cmd = base_cmd[:-1] + codec_args + ["-y", str(mp4_path)]
            # base_cmd already had -y / str(mp4_path); rebuild with codec
            # args inserted before the output path.
            cmd = (
                base_cmd[:-2]  # drop -y + output
                + codec_args
                + ["-y", str(mp4_path)]
            )
            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                _, err = await process.communicate()
            except (OSError, FileNotFoundError):
                return ""
            if process.returncode == 0 and mp4_path.exists() and mp4_path.stat().st_size > 0:
                return mp4_name
            # Remove the broken partial from the previous attempt before
            # trying the fallback so a tiny file doesn't masquerade as OK.
            with contextlib.suppress(OSError):
                mp4_path.unlink()
        return ""

    async def _mark_done(self, rec_id: str) -> None:
        """Flip status to `done` and collapse segments into one MKV.

        Multi-segment recordings (pause/resume or restart hops) are concatenated
        via ffmpeg's concat demuxer so the browser player gets a single
        continuous timeline instead of jumping between files on `onEnded`.
        Concat runs with `-c copy` (no re-encode) so the cost is close to disk
        I/O. On failure the entry is still marked `done` with the original
        parts intact — the sequential player remains a working fallback.
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
            duration = await self._total_duration(replace(entry, parts=parts))
            mp4_name = await self._remux_to_mp4(rec_id, parts[0])
            self._save(
                replace(
                    entry,
                    status="done",
                    file=parts[0],
                    parts=parts,
                    bytes=size,
                    duration_seconds=duration,
                    mp4_file=mp4_name,
                    error="",
                )
            )
            return

        merged_name = f"{rec_id}.mkv"
        if merged_name in parts:
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
        err: bytes = b""
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            _, err = await process.communicate()
        finally:
            with contextlib.suppress(OSError):
                list_path.unlink()

        if process.returncode == 0 and merged_path.exists():
            for p in parts:
                if p != merged_name:
                    with contextlib.suppress(OSError):
                        self._file_path(p).unlink()
            size = merged_path.stat().st_size
            duration = await self._probe_duration(merged_path)
            mp4_name = await self._remux_to_mp4(rec_id, merged_name)
            self._save(
                replace(
                    entry,
                    status="done",
                    file=merged_name,
                    parts=[merged_name],
                    bytes=size,
                    duration_seconds=duration,
                    mp4_file=mp4_name,
                    error="",
                )
            )
        else:
            err_msg = err.decode("utf-8", "ignore").strip()[:500]
            total_size = sum(self._file_path(p).stat().st_size for p in parts)
            duration = await self._total_duration(replace(entry, parts=parts))
            self._save(
                replace(
                    entry,
                    status="done",
                    file=parts[0],
                    parts=parts,
                    bytes=total_size,
                    duration_seconds=duration,
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
                    await self._mark_done(entry.id)
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

    async def backfill_mp4(self) -> int:
        """Remux any finalised MKV that's missing an MP4 sibling.

        Recordings made before the MP4-output migration only have the
        MKV on disk — mobile browsers can't play those. Iterate all
        ``done`` entries, remux the ones missing ``mp4_file``, and
        update the sidecar JSON so the ``/file`` endpoint starts
        serving MP4. Safe to run every startup; entries already
        carrying a valid ``mp4_file`` are skipped. Returns the count
        of newly-remuxed files.

        Runs sequentially so we don't slam the server with parallel
        ffmpeg passes on startup. Each remux is near-zero cost
        (``-c copy``) so even a few dozen recordings finish in seconds.
        """
        remuxed = 0
        for entry in self.list():
            if entry.status != "done":
                continue
            if entry.mp4_file:
                mp4_path = self._file_path(entry.mp4_file)
                if mp4_path.exists() and mp4_path.stat().st_size > 0:
                    continue
            mkv_name = entry.file or (entry.parts[0] if entry.parts else "")
            if not mkv_name or not self._file_path(mkv_name).exists():
                continue
            produced = await self._remux_to_mp4(entry.id, mkv_name)
            if produced:
                self._save(replace(entry, mp4_file=produced))
                remuxed += 1
        return remuxed
