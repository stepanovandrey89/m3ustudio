"""Application state: the ordered list of channel NAMES that form the main group.

Persisting names (not ids) is the key design decision — it lets the user
swap out `playlist.m3u8` for a different provider while keeping the same
curated ordering of "основное". Channel ids are derived from stream URLs
which change across providers; names are stable.

Storage: JSON file, atomic write on every mutation.

File format (v3):
    {
        "version": 3,
        "main_names": ["Первый канал HD", ...],
        "original_groups": {"<channel_id>": "<group name>", ...}
    }

`original_groups` is a snapshot of each channel's original group-title —
the one it had when first imported, BEFORE we rewrote it to "основное" as
part of Main/Source mirroring. When a channel is later removed from Main,
the rewriter uses this snapshot to restore it to its real group instead
of leaving it stuck in "основное" as a ghost entry.

Legacy v1 ({"version": 1, "main_ids": [...]}) and v2 ({"version": 2,
"main_names": [...]}) state files are migrated on load. v1 looks up each
id in the currently-parsed playlist and writes back the matching names.
v2 just adds an empty `original_groups` which is then populated from a
fresh snapshot of the current playlist.

First-run bootstrap: if the state file is missing entirely, the initial
main list is seeded from the configured default channel names list.
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
        # Snapshot of each channel's original group-title, keyed by channel id.
        # Populated on import / bootstrap, and extended (never overwritten)
        # when new channels appear. Used by build_with_main_group to restore
        # removed-from-Main channels to their real groups.
        self._original_groups: dict[str, str] = {}

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

    # ---- Original-group snapshot ----------------------------------------

    def original_groups_map(self) -> dict[str, str]:
        """Return a copy of the id → original group snapshot."""
        with self._lock:
            return dict(self._original_groups)

    def capture_original_groups(self, playlist: Playlist) -> None:
        """Extend the snapshot with any channels that aren't tracked yet.

        Non-destructive: never overwrites existing entries, so group-title
        rewrites from `_sync_main_to_source` don't clobber historical data.
        """
        with self._lock:
            changed = False
            for ch in playlist.channels:
                if ch.id not in self._original_groups:
                    self._original_groups[ch.id] = ch.group
                    changed = True
            if changed:
                self._persist_unlocked()

    def reset_original_groups(self, playlist: Playlist) -> None:
        """Discard the existing snapshot and take a fresh one.

        Called when a brand-new playlist is imported — the old provider's
        ids / groups no longer apply and must be replaced wholesale.
        """
        with self._lock:
            self._original_groups = {ch.id: ch.group for ch in playlist.channels}
            self._persist_unlocked()

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

                    if version == 3 and isinstance(data.get("main_names"), list):
                        self._state = MainState(
                            main_names=tuple(str(n) for n in data["main_names"] if n)
                        )
                        raw_groups = data.get("original_groups") or {}
                        if isinstance(raw_groups, dict):
                            self._original_groups = {
                                str(k): str(v) for k, v in raw_groups.items() if k and v
                            }
                        return self._state

                    if version == 2 and isinstance(data.get("main_names"), list):
                        # v2 → v3 migration: preserve main_names verbatim and take
                        # a snapshot of the current playlist as the original-group
                        # source of truth. Caveat: any channels that are already
                        # tagged "основное" in the source file will keep that as
                        # their "original" group — re-importing the playlist will
                        # give a fully accurate snapshot.
                        self._state = MainState(
                            main_names=tuple(str(n) for n in data["main_names"] if n)
                        )
                        self._original_groups = {ch.id: ch.group for ch in playlist.channels}
                        self._persist_unlocked()
                        return self._state

                    if version == 1 and isinstance(data.get("main_ids"), list):
                        # v1 → v3 migration: look up each id's current name and
                        # take a fresh original-groups snapshot.
                        id_to_name = self._id_to_name_map()
                        names = [id_to_name[i] for i in data["main_ids"] if i in id_to_name]
                        self._state = MainState(main_names=tuple(_dedupe(names)))
                        self._original_groups = {ch.id: ch.group for ch in playlist.channels}
                        self._persist_unlocked()
                        return self._state

            # Bootstrap from the configured default names — only keep names that
            # actually exist in the current playlist, preserving the canonical
            # order. Also take an initial original-groups snapshot.
            present = {ch.name for ch in playlist.channels}
            seeded = [name for name in self._default_names if name in present]
            self._state = MainState(main_names=tuple(seeded))
            self._original_groups = {ch.id: ch.group for ch in playlist.channels}
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
        payload = {
            "version": 3,
            "main_names": list(self._state.main_names),
            "original_groups": dict(self._original_groups),
        }
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._path)
