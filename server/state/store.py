"""Application state: the ordered list of channel NAMES that form the main group.

Persisting names (not ids) is the key design decision — it lets the user
swap out `playlist.m3u8` for a different provider while keeping the same
curated ordering of "основное". Channel ids are derived from stream URLs
which change across providers; names are stable.

Storage: JSON file, atomic write on every mutation.

File format:
    {"version": 2, "main_names": ["Первый канал HD", ...]}

Legacy v1 state ({"version": 1, "main_ids": [...]}) is migrated to v2 by
looking up each id in the currently-parsed playlist and writing back the
matching names.

First-run bootstrap: if the state file is missing entirely, the initial
main list is seeded from DEFAULT_ORDERED_NAMES (the same list that drives
sort_playlist_osnovnoe.py).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

from server.playlist.models import Playlist
from server.state.defaults import DEFAULT_ORDERED_NAMES


@dataclass(frozen=True, slots=True)
class MainState:
    """Immutable snapshot of the curated main-group ordering."""

    main_names: tuple[str, ...]

    def to_json(self) -> dict:
        return {"version": 2, "main_names": list(self.main_names)}


def _dedupe(names: list[str]) -> list[str]:
    """Preserve order, drop repeats."""
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


class StateStore:
    """Thread-safe owner of the curated main-group state.

    Public API is id-facing for the frontend's benefit, but internally
    everything is persisted and compared by channel name. The store keeps a
    reference to the current Playlist so it can translate id ↔ name at the
    boundary.
    """

    def __init__(self, state_path: Path, default_names: tuple[str, ...] | None = None) -> None:
        self._path = state_path
        self._lock = threading.RLock()
        self._state: MainState = MainState(main_names=())
        self._playlist: Playlist = Playlist(header=(), channels=())
        self._default_names: tuple[str, ...] = (
            default_names if default_names is not None else DEFAULT_ORDERED_NAMES
        )

    def set_default_names(self, names: tuple[str, ...]) -> None:
        """Update the bootstrap defaults used when state.json is absent."""
        with self._lock:
            self._default_names = names

    def bind_playlist(self, playlist: Playlist) -> None:
        """Update the playlist reference without touching persisted state.

        Used when the in-memory playlist has been rebuilt (e.g. after a
        group-title rewrite) but Main state is already authoritative.
        """
        with self._lock:
            self._playlist = playlist

    @property
    def state(self) -> MainState:
        with self._lock:
            return self._state

    # ---- Playlist binding ------------------------------------------------

    def _name_for_id(self, channel_id: str) -> str | None:
        for ch in self._playlist.channels:
            if ch.id == channel_id:
                return ch.name
        return None

    def _id_to_name_map(self) -> dict[str, str]:
        return {ch.id: ch.name for ch in self._playlist.channels}

    def _name_to_id_map(self) -> dict[str, str]:
        # First occurrence wins so we deterministically pick one channel
        # when a single name appears multiple times in the source.
        out: dict[str, str] = {}
        for ch in self._playlist.channels:
            out.setdefault(ch.name, ch.id)
        return out

    def current_ids(self) -> list[str]:
        """Return stored names translated into current playlist ids.

        Names that no longer exist in the playlist are silently skipped
        from this view but kept in the stored state, so re-loading a
        playlist that contains them again will light them back up.
        """
        with self._lock:
            name_to_id = self._name_to_id_map()
            return [name_to_id[n] for n in self._state.main_names if n in name_to_id]

    # ---- Load / bootstrap / migrate --------------------------------------

    def load_or_bootstrap(self, playlist: Playlist) -> MainState:
        with self._lock:
            self._playlist = playlist

            if self._path.exists():
                try:
                    data = json.loads(self._path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    data = None

                if isinstance(data, dict):
                    version = data.get("version")

                    if version == 2 and isinstance(data.get("main_names"), list):
                        self._state = MainState(
                            main_names=tuple(str(n) for n in data["main_names"] if n)
                        )
                        return self._state

                    if version == 1 and isinstance(data.get("main_ids"), list):
                        # v1 → v2 migration: look up each id's current name.
                        id_to_name = self._id_to_name_map()
                        names = [id_to_name[i] for i in data["main_ids"] if i in id_to_name]
                        self._state = MainState(main_names=tuple(_dedupe(names)))
                        self._persist_unlocked()
                        return self._state

            # Bootstrap from the configured default names — only keep names that
            # actually exist in the current playlist, preserving the
            # canonical order.
            present = {ch.name for ch in playlist.channels}
            seeded = [name for name in self._default_names if name in present]
            self._state = MainState(main_names=tuple(seeded))
            self._persist_unlocked()
            return self._state

    # ---- Id-facing mutations ---------------------------------------------

    def replace_ids(self, ids: list[str]) -> MainState:
        """Replace the full main list from an ordered list of channel ids."""
        with self._lock:
            id_to_name = self._id_to_name_map()
            names = [id_to_name[cid] for cid in ids if cid in id_to_name]
            self._replace_names(names)
            return self._state

    def add_id(self, channel_id: str, position: int | None = None) -> MainState:
        with self._lock:
            name = self._name_for_id(channel_id)
            if name is None:
                return self._state
            current = list(self._state.main_names)
            if name in current:
                return self._state
            if position is None or position >= len(current):
                current.append(name)
            else:
                current.insert(max(0, position), name)
            self._replace_names(current)
            return self._state

    def remove_id(self, channel_id: str) -> MainState:
        with self._lock:
            name = self._name_for_id(channel_id)
            if name is None:
                return self._state
            self._replace_names([n for n in self._state.main_names if n != name])
            return self._state

    def move_id(self, channel_id: str, to_index: int) -> MainState:
        with self._lock:
            name = self._name_for_id(channel_id)
            if name is None:
                return self._state
            without = [n for n in self._state.main_names if n != name]
            to_index = max(0, min(to_index, len(without)))
            without.insert(to_index, name)
            self._replace_names(without)
            return self._state

    # ---- Internals -------------------------------------------------------

    def _replace_names(self, names: list[str]) -> None:
        self._state = MainState(main_names=tuple(_dedupe(names)))
        self._persist_unlocked()

    def _persist_unlocked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self._state.to_json(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._path)
