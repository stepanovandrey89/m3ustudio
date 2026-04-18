"""Build a compact EPG-based context for the AI assistant.

The digest / chat endpoints use this to give GPT a bounded snapshot of what is
airing across the user's *favorite* channels (= the Main list) for the next
day. We keep it short — channel name + upcoming programmes in a plain text
block — so that even a long favorites list stays under the token budget.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from server.epg import EpgGuide
from server.playlist import Channel


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
    max_per_channel: int = 12,
    only_upcoming: bool = False,
) -> list[ChannelSchedule]:
    """Build a schedule snapshot for every Main channel.

    ``only_upcoming=True`` drops anything whose start time is before "now" so
    the digest never recommends a programme that has already aired. For the
    live chat we still include the current show (past_hours > 0) so the user
    can ask "what's on right now".
    """
    past = timedelta(hours=max(0, past_hours))
    future = timedelta(hours=max(1, future_hours))
    schedules: list[ChannelSchedule] = []
    now = datetime.now(UTC)
    for ch in main_channels:
        entries = _collect(guide, ch, past, future, max_per_channel)
        if only_upcoming:
            # Strict upcoming window: programme must START at least 10 minutes
            # from now and no more than 12 hours out. Anything currently airing
            # is skipped — the user wants pure "what's next" recommendations.
            min_start = now + timedelta(minutes=10)
            max_start = now + timedelta(hours=12)
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


def schedule_to_text(schedules: list[ChannelSchedule], lang: str = "ru") -> str:
    """Flatten a schedule list into a compact prompt-friendly text block.

    The line format is structured so the model can copy ``start`` and ``stop``
    verbatim into its tool-call arguments. Keeping the full ISO-8601 string
    (with timezone offset) on every row is what lets the frontend countdown
    stay in sync — without it the model guesses a timezone and drifts by
    hours.
    """
    if not schedules:
        return "(EPG пуст — нет программ)" if lang == "ru" else "(EPG empty — no programmes)"

    lines: list[str] = []
    for sch in schedules:
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
