"""Build a compact EPG-based context for the AI assistant.

The digest / chat endpoints use this to give GPT a bounded snapshot of what is
airing across the user's *favorite* channels (= the Main list) for the next
day. We keep it short — channel name + upcoming programmes in a plain text
block — so that even a long favorites list stays under the token budget.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from server.epg import EpgGuide
from server.playlist import Channel


def _normalize_for_match(text: str) -> str:
    """Lowercase + strip decorative bits we don't want to factor into matches.

    Keeps Cyrillic/Latin letters and digits, drops "HD/4K/UHD/+", punctuation,
    and collapses whitespace. Lets "Матч ТВ" match "Матч ТВ HD" and vice
    versa, without having the "HD" suffix eat the useful part of a query.
    """
    lowered = text.lower()
    lowered = re.sub(r"\b(hd|fhd|uhd|4k|hdr|sd)\b", " ", lowered)
    lowered = re.sub(r"[^\w\s+]", " ", lowered, flags=re.UNICODE)
    return re.sub(r"\s+", " ", lowered).strip()


def channels_mentioned(text: str, channels: list[Channel]) -> list[Channel]:
    """Return the subset of ``channels`` whose name appears in ``text``.

    Used to shrink the EPG context when the user's question explicitly names
    a channel ("last programme on Матч ТВ?") — no reason to send 149
    favourites worth of EPG if only one is relevant. Matches greedily on
    normalised names (HD-style suffixes stripped) and prefers longer names
    so "Матч! Футбол 1" wins over bare "Матч" when both fit.
    """
    if not text or not channels:
        return []
    haystack = f" {_normalize_for_match(text)} "
    hits: list[tuple[int, Channel]] = []
    for ch in channels:
        norm = _normalize_for_match(ch.name)
        if len(norm) < 3:
            continue
        if f" {norm} " in haystack:
            hits.append((len(norm), ch))
    if not hits:
        return []
    # Longest name first, dedupe by channel id preserving that order.
    hits.sort(key=lambda item: item[0], reverse=True)
    seen: set[str] = set()
    picked: list[Channel] = []
    for _, ch in hits:
        if ch.id in seen:
            continue
        seen.add(ch.id)
        picked.append(ch)
    return picked


@dataclass(frozen=True, slots=True)
class ProgrammeEntry:
    title: str
    description: str
    start: datetime
    stop: datetime

    def duration_minutes(self) -> int:
        return max(1, int((self.stop - self.start).total_seconds() // 60))

    def to_dict(self) -> dict[str, str | int]:
        return {
            "title": self.title,
            "description": self.description,
            "start": self.start.isoformat(),
            "stop": self.stop.isoformat(),
            "duration_min": self.duration_minutes(),
        }


@dataclass(frozen=True, slots=True)
class ChannelSchedule:
    channel_id: str
    channel_name: str
    group: str
    programmes: tuple[ProgrammeEntry, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "group": self.group,
            "programmes": [p.to_dict() for p in self.programmes],
        }


def _collect(
    guide: EpgGuide,
    channel: Channel,
    past: timedelta,
    future: timedelta,
    max_items: int,
) -> tuple[ProgrammeEntry, ...]:
    now = datetime.now(UTC)
    start_window = now - past
    end_window = now + future
    progs = guide.lookup(channel.name)
    out: list[ProgrammeEntry] = []
    for p in progs:
        if p.stop < start_window or p.start > end_window:
            continue
        out.append(
            ProgrammeEntry(
                title=p.title,
                description=p.description,
                start=p.start,
                stop=p.stop,
            )
        )
        if len(out) >= max_items:
            break
    return tuple(out)


def build_main_schedule(
    guide: EpgGuide,
    main_channels: list[Channel],
    past_hours: int = 2,
    future_hours: int = 22,
    max_per_channel: int | None = None,
    only_upcoming: bool = False,
) -> list[ChannelSchedule]:
    """Build a schedule snapshot for every Main channel.

    ``only_upcoming=True`` drops anything whose start time is before "now" so
    the digest never recommends a programme that has already aired. For the
    live chat we still include the current show (past_hours > 0) so the user
    can ask "what's on right now".

    ``max_per_channel`` defaults to a value proportional to ``future_hours``
    so that a wider window (e.g. 7-day deep-search) isn't silently clipped to
    the digest's 12-entry budget. Pass an explicit int to override.
    """
    past = timedelta(hours=max(0, past_hours))
    future = timedelta(hours=max(1, future_hours))
    # ~1 entry per hour of window, floor 12, so 12 h → 12, 168 h → 168.
    if max_per_channel is None:
        max_per_channel = max(12, future_hours)
    schedules: list[ChannelSchedule] = []
    now = datetime.now(UTC)
    for ch in main_channels:
        entries = _collect(guide, ch, past, future, max_per_channel)
        if only_upcoming:
            # Programme must START at least 10 minutes from now, and no more
            # than `future_hours` ahead. Anything currently airing is skipped
            # — the user wants pure "what's next" recommendations.
            min_start = now + timedelta(minutes=10)
            max_start = now + future
            entries = tuple(e for e in entries if min_start <= e.start <= max_start)
        if not entries:
            continue
        schedules.append(
            ChannelSchedule(
                channel_id=ch.id,
                channel_name=ch.name,
                group=ch.group,
                programmes=entries,
            )
        )
    return schedules


def schedule_to_text(
    schedules: list[ChannelSchedule],
    lang: str = "ru",
    compact: bool = False,
) -> str:
    """Flatten a schedule list into a prompt-friendly text block.

    The line format is structured so the model can copy ``start`` and ``stop``
    verbatim into its tool-call arguments. Keeping the full ISO-8601 string
    (with timezone offset) on every row is what lets the frontend countdown
    stay in sync — without it the model guesses a timezone and drifts by
    hours.

    ``compact=True`` drops decorative fields (human-readable weekday/time,
    duration, description) and keeps only the channel id, ISO start/stop, and
    title. Used by the 7-day deep-search context where 149 channels × ~300
    programmes per channel would otherwise blow past the OpenAI TPM limit.
    """
    if not schedules:
        return "(EPG пуст — нет программ)" if lang == "ru" else "(EPG empty — no programmes)"

    lines: list[str] = []
    for sch in schedules:
        if compact:
            # Short header: name + id only. Group label and brackets dropped.
            lines.append(f"== {sch.channel_name} (id={sch.channel_id})")
            for p in sch.programmes:
                # start/stop + title. No description, no when/dur decoration.
                lines.append(f"  start={p.start.isoformat()} stop={p.stop.isoformat()} · {p.title}")
            lines.append("")
            continue
        header = f"=== {sch.channel_name} [{sch.group}] (id={sch.channel_id})"
        lines.append(header)
        for p in sch.programmes:
            when = p.start.astimezone().strftime("%a %H:%M %z")
            start_iso = p.start.isoformat()
            stop_iso = p.stop.isoformat()
            dur = f"{p.duration_minutes()}м"
            desc = p.description.strip().replace("\n", " ")
            if len(desc) > 160:
                desc = desc[:157] + "…"
            lines.append(
                f"  {when} · {dur} · start={start_iso} stop={stop_iso} · {p.title}"
                + (f" — {desc}" if desc else "")
            )
        lines.append("")
    return "\n".join(lines)
