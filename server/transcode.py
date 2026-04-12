"""On-demand ffmpeg HLS transcode sessions.

Used to fix channels whose audio is encoded in AC-3 (Dolby Digital) or
similar formats that browsers can't decode natively. Video is copied
without re-encoding (cheap) and the audio track is transmuxed to AAC
stereo (mid-cost — AC-3 decode + AAC encode).

Lifecycle:
    start(channel_id, upstream_url)  → spawn ffmpeg, wait for manifest
    touch(channel_id)                → called on every GET to keep alive
    stop(channel_id)                 → terminate process, clean temp dir
    cleanup_idle()                   → background task kills stale sessions

Each session writes HLS files into `session_dir/<channel_id>/index.m3u8`
which is served via `/api/transcode/{id}/{filename}` from main.py.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


FFMPEG_READY_TIMEOUT_SECONDS = 12.0
IDLE_TIMEOUT_SECONDS = 120.0
CLEANUP_INTERVAL_SECONDS = 30.0


@dataclass
class TranscodeSession:
    channel_id: str
    upstream_url: str
    session_dir: Path
    process: subprocess.Popen[bytes]
    started_at: float = field(default_factory=time.time)
    last_access: float = field(default_factory=time.time)

    @property
    def manifest_path(self) -> Path:
        return self.session_dir / "index.m3u8"

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def is_running(self) -> bool:
        return self.process.poll() is None

    def touch(self) -> None:
        self.last_access = time.time()

    def idle_for(self) -> float:
        return time.time() - self.last_access


class TranscodeManager:
    """Thread/async-safe registry of per-channel transcode sessions."""

    def __init__(self, root_dir: Path, ffmpeg_bin: str = "ffmpeg") -> None:
        self._root = root_dir
        self._root.mkdir(parents=True, exist_ok=True)
        self._ffmpeg = ffmpeg_bin
        self._sessions: dict[str, TranscodeSession] = {}
        self._lock = asyncio.Lock()

    @property
    def active_ids(self) -> list[str]:
        return list(self._sessions.keys())

    def _session_dir(self, channel_id: str) -> Path:
        safe = "".join(c if c.isalnum() else "_" for c in channel_id)[:32]
        return self._root / safe

    def get(self, channel_id: str) -> Optional[TranscodeSession]:
        return self._sessions.get(channel_id)

    async def ensure_started(self, channel_id: str, upstream_url: str) -> TranscodeSession:
        """Start a session if one doesn't exist; return the current session."""
        async with self._lock:
            existing = self._sessions.get(channel_id)
            if existing is not None and existing.is_running:
                existing.touch()
                return existing

            # If an existing session is dead (crashed, etc.) clean it up first
            if existing is not None:
                self._cleanup_session(existing)
                self._sessions.pop(channel_id, None)

            session_dir = self._session_dir(channel_id)
            # Wipe leftover files from a previous run so ffmpeg starts fresh
            if session_dir.exists():
                shutil.rmtree(session_dir, ignore_errors=True)
            session_dir.mkdir(parents=True, exist_ok=True)

            command = [
                self._ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel", "warning",
                "-user_agent", "VLC/3.0.20 LibVLC/3.0.20",
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
                "-i", upstream_url,
                "-map", "0:v:0?",
                "-map", "0:a:0?",
                "-c:v", "copy",
                "-c:a", "aac",
                "-ac", "2",
                "-b:a", "192k",
                "-f", "hls",
                "-hls_time", "4",
                "-hls_list_size", "6",
                "-hls_flags", "delete_segments+independent_segments+omit_endlist",
                "-hls_segment_filename", str(session_dir / "seg%03d.ts"),
                str(session_dir / "index.m3u8"),
            ]

            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                close_fds=True,
            )

            session = TranscodeSession(
                channel_id=channel_id,
                upstream_url=upstream_url,
                session_dir=session_dir,
                process=process,
            )
            self._sessions[channel_id] = session

        # Wait (outside the lock) for the manifest file to appear. Don't block
        # other session starts while we're polling the filesystem.
        manifest = session.manifest_path
        deadline = time.monotonic() + FFMPEG_READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if not session.is_running:
                # Process died — read whatever it said to stderr for diagnostics.
                try:
                    _, err = session.process.communicate(timeout=0.1)
                except subprocess.TimeoutExpired:
                    err = b""
                async with self._lock:
                    self._cleanup_session(session)
                    self._sessions.pop(channel_id, None)
                raise RuntimeError(
                    f"ffmpeg exited early: {err.decode('utf-8', 'ignore').strip()[:500]}"
                )
            if manifest.exists() and manifest.stat().st_size > 0:
                return session
            await asyncio.sleep(0.15)

        # Timed out — kill and fail.
        async with self._lock:
            self._cleanup_session(session)
            self._sessions.pop(channel_id, None)
        raise TimeoutError(
            f"ffmpeg did not produce a manifest within {FFMPEG_READY_TIMEOUT_SECONDS:.0f}s"
        )

    async def stop(self, channel_id: str) -> bool:
        async with self._lock:
            session = self._sessions.pop(channel_id, None)
        if session is None:
            return False
        self._cleanup_session(session)
        return True

    async def stop_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            self._cleanup_session(s)

    def touch(self, channel_id: str) -> None:
        session = self._sessions.get(channel_id)
        if session is not None:
            session.touch()

    async def cleanup_idle(self) -> int:
        """Kill sessions that haven't been accessed recently."""
        victims: list[TranscodeSession] = []
        async with self._lock:
            for channel_id, session in list(self._sessions.items()):
                if not session.is_running or session.idle_for() > IDLE_TIMEOUT_SECONDS:
                    victims.append(session)
                    self._sessions.pop(channel_id, None)
        for s in victims:
            self._cleanup_session(s)
        return len(victims)

    # ------------------------------------------------------------------

    def _cleanup_session(self, session: TranscodeSession) -> None:
        # Terminate ffmpeg gracefully, then force-kill if stubborn
        if session.process.poll() is None:
            try:
                session.process.terminate()
                try:
                    session.process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    session.process.kill()
                    session.process.wait(timeout=1.0)
            except OSError:
                pass

        # Remove session dir
        try:
            shutil.rmtree(session.session_dir, ignore_errors=True)
        except OSError:
            pass


async def run_cleanup_loop(manager: TranscodeManager) -> None:
    """Periodic background task: call manager.cleanup_idle() forever."""
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            await manager.cleanup_idle()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — cleanup must not crash the server
            pass
