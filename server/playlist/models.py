"""Immutable domain models for m3u playlist items.

A Channel is a single #EXTINF entry plus its URL and any inter-line metadata
(#EXTGRP, #EXTVLCOPT, etc). Channels are identified by a stable id derived
from their URL so that reorder operations survive playlist reloads.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace

_EXTINF_RE = re.compile(r"^#EXTINF:(?P<dur>[^,]*),(?P<name>.*)$")
_GROUP_ATTR_RE = re.compile(r'group-title="(?P<group>[^"]*)"')
_LOGO_ATTR_RE = re.compile(r'tvg-logo="(?P<logo>[^"]*)"')
_TVGID_ATTR_RE = re.compile(r'tvg-id="(?P<id>[^"]*)"')
_TVGREC_ATTR_RE = re.compile(r'tvg-rec="(?P<rec>\d+)"')


def _stable_id(url: str) -> str:
    """Short, deterministic id derived from the stream URL."""
    digest = hashlib.sha1(url.encode("utf-8"), usedforsecurity=False).hexdigest()
    return digest[:12]


@dataclass(frozen=True, slots=True)
class Channel:
    """A single playlist entry. All fields are immutable.

    `raw_lines` preserves the full original byte representation (EXTINF + any
    extra tags + URL line) so we can round-trip the file without losing tags
    we don't yet understand.

    `catchup_days` comes from the non-standard but widely adopted
    `tvg-rec="N"` attribute, indicating how many days of archive/timeshift
    the provider supports on this channel. 0 means "no archive".
    """

    id: str
    name: str
    url: str
    group: str
    duration: str = "0"
    tvg_id: str = ""
    logo_url: str = ""
    catchup_days: int = 0
    raw_lines: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_lines(cls, lines: list[str]) -> Channel | None:
        """Build a Channel from a slice of m3u lines beginning with #EXTINF.

        Returns None if the slice doesn't contain a valid channel.
        """
        if not lines:
            return None

        extinf = lines[0].strip()
        match = _EXTINF_RE.match(extinf)
        if not match:
            return None

        duration = match.group("dur").split()[0] if match.group("dur") else "0"
        name = match.group("name").strip()

        group_m = _GROUP_ATTR_RE.search(extinf)
        group = group_m.group("group") if group_m else ""

        logo_m = _LOGO_ATTR_RE.search(extinf)
        logo = logo_m.group("logo") if logo_m else ""

        tvgid_m = _TVGID_ATTR_RE.search(extinf)
        tvg_id = tvgid_m.group("id") if tvgid_m else ""

        rec_m = _TVGREC_ATTR_RE.search(extinf)
        catchup_days = int(rec_m.group("rec")) if rec_m else 0

        url = ""
        for line in lines[1:]:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                # Fallback group discovery: #EXTGRP:<name>
                if not group and stripped.upper().startswith("#EXTGRP:"):
                    group = stripped.split(":", 1)[1].strip()
                continue
            url = stripped
            break

        if not url:
            return None

        return cls(
            id=_stable_id(url),
            name=name,
            url=url,
            group=group,
            duration=duration,
            tvg_id=tvg_id,
            logo_url=logo,
            catchup_days=catchup_days,
            raw_lines=tuple(lines),
        )

    def with_group(self, group: str) -> Channel:
        """Return a new Channel with group-title rewritten in both fields and raw_lines."""
        new_lines: list[str] = []
        for line in self.raw_lines:
            if line.lstrip().startswith("#EXTINF"):
                new_lines.append(_rewrite_group_title(line, group))
            else:
                new_lines.append(line)
        return replace(self, group=group, raw_lines=tuple(new_lines))


def _rewrite_group_title(extinf_line: str, group: str) -> str:
    """Replace or insert group-title="..." in a single #EXTINF line."""
    match = re.match(r"^(#EXTINF:)([^,]*),(.*)$", extinf_line.rstrip("\n"))
    if not match:
        return extinf_line

    prefix, attrs, name_part = match.groups()
    attrs = attrs.strip()

    if 'group-title="' in attrs:
        attrs_new = re.sub(r'group-title="[^"]*"', f'group-title="{group}"', attrs)
    elif attrs:
        attrs_new = f'{attrs} group-title="{group}"'
    else:
        attrs_new = f'group-title="{group}"'

    suffix = "\n" if extinf_line.endswith("\n") else ""
    return f"{prefix}{attrs_new},{name_part}{suffix}"


@dataclass(frozen=True, slots=True)
class Playlist:
    """A parsed playlist: header + ordered channel list."""

    header: tuple[str, ...]
    channels: tuple[Channel, ...]

    def by_id(self, channel_id: str) -> Channel | None:
        for ch in self.channels:
            if ch.id == channel_id:
                return ch
        return None

    def groups(self) -> dict[str, list[Channel]]:
        """Channels grouped by their group-title, preserving insertion order."""
        out: dict[str, list[Channel]] = {}
        for ch in self.channels:
            key = ch.group or "без группы"
            out.setdefault(key, []).append(ch)
        return out
