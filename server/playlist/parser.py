"""Streaming m3u/m3u8 parser producing an immutable Playlist.

The goal is byte-preservation: whatever tags (#EXTGRP, #EXTVLCOPT, ...) sit
between #EXTINF and the URL line are kept in `Channel.raw_lines` so we can
re-emit the playlist without losing fidelity.
"""

from __future__ import annotations

from pathlib import Path

from server.playlist.models import Channel, Playlist


def parse_playlist(path: str | Path) -> Playlist:
    raw = Path(path).read_text(encoding="utf-8", errors="ignore")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")

    header_lines: list[str] = []
    channels: list[Channel] = []

    i = 0
    n = len(lines)

    # Header: everything before the first #EXTINF
    while i < n and not lines[i].startswith("#EXTINF"):
        header_lines.append(lines[i])
        i += 1

    # Channels: each #EXTINF consumes until the next #EXTINF (or EOF)
    while i < n:
        if not lines[i].startswith("#EXTINF"):
            # Stray tag between channels — treat as channel continuation or skip
            i += 1
            continue

        item_lines = [lines[i]]
        i += 1
        while i < n and not lines[i].startswith("#EXTINF"):
            item_lines.append(lines[i])
            i += 1

        # Trim trailing blank lines from the item's slice
        while item_lines and item_lines[-1].strip() == "":
            item_lines.pop()

        channel = Channel.from_lines(item_lines)
        if channel is not None:
            channels.append(channel)

    return Playlist(header=tuple(header_lines), channels=tuple(channels))
