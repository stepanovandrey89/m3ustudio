"""Serialize a Playlist back into m3u8 text.

Emits the original header (ensuring #EXTM3U is present), followed by each
channel's raw_lines in order. This round-trips the input byte-for-byte for
channels we haven't touched and preserves any tags we don't parse.
"""

from __future__ import annotations

from collections.abc import Iterable

from server.playlist.models import Channel


def build_playlist(header: Iterable[str], channels: Iterable[Channel]) -> str:
    out: list[str] = []
    header_list = list(header)

    if not header_list:
        out.append("#EXTM3U")
    else:
        first = header_list[0].lstrip("\ufeff")
        if not first.startswith("#EXTM3U"):
            out.append("#EXTM3U")
        out.extend(header_list)

    for ch in channels:
        out.extend(ch.raw_lines)

    text = "\n".join(line for line in out if line is not None)
    if not text.endswith("\n"):
        text += "\n"
    return text


def build_with_main_group(
    header: Iterable[str],
    all_channels: Iterable[Channel],
    main_ids: Iterable[str],
    group_name: str = "основное",
    original_groups: dict[str, str] | None = None,
) -> str:
    """Produce the final playlist: main_ids (in order, rewritten to group_name),
    followed by the remaining channels in their original order.

    Missing ids are silently skipped.

    `original_groups` is a snapshot of each channel's first-seen group-title
    (id → group). When provided, channels that are currently tagged as
    `group_name` in memory but are no longer in `main_ids` are restored to
    their original group — this prevents removed-from-Main channels from
    leaking as ghosts in the output's `group_name` section.
    """
    all_list = list(all_channels)
    by_id = {ch.id: ch for ch in all_list}
    wanted_order: list[str] = list(main_ids)

    main_seen: set[str] = set()
    main_channels: list[Channel] = []
    for cid in wanted_order:
        if cid in main_seen or cid not in by_id:
            continue
        main_seen.add(cid)
        main_channels.append(by_id[cid].with_group(group_name))

    group_name_lc = group_name.lower()
    rest_channels: list[Channel] = []
    for ch in all_list:
        if ch.id in main_seen:
            continue
        # Ghost fix: channel was previously in Main (tagged group_name in
        # memory) but is no longer → restore to its original group.
        if (
            original_groups
            and ch.group.lower() == group_name_lc
            and ch.id in original_groups
            and original_groups[ch.id].lower() != group_name_lc
        ):
            rest_channels.append(ch.with_group(original_groups[ch.id]))
        else:
            rest_channels.append(ch)

    return build_playlist(header, [*main_channels, *rest_channels])
