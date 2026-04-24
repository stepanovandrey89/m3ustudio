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
from typing import Literal
from zoneinfo import ZoneInfo

from server.epg import EpgGuide
from server.playlist import Channel

# Timezone used to interpret "evening/morning/night" user phrases. EPG
# data is Russian-sourced so Moscow is the meaningful local clock for
# every user we serve today.
_USER_LOCAL_TZ = ZoneInfo("Europe/Moscow")

TimeOfDay = Literal["morning", "afternoon", "evening", "night"]

# Local wall-clock hour ranges per phrase. Stop > 24 wraps past midnight
# into the next day (night window bleeds into early hours).
_TOD_RANGES: dict[TimeOfDay, tuple[int, int]] = {
    "morning": (6, 12),
    "afternoon": (12, 18),
    "evening": (18, 24),
    "night": (23, 30),  # 23:00-06:00 next day
}

# Phrase patterns mapped to a time-of-day label. Ordered longest-first so
# "поздним вечером" and "глубокой ночью" beat bare "вечером"/"ночью".
_TOD_PATTERNS: tuple[tuple[TimeOfDay, tuple[str, ...]], ...] = (
    (
        "evening",
        (
            "на вечер",
            "этим вечером",
            "сегодня вечером",
            "вечером",
            "вечер",
            "tonight",
            "this evening",
            "in the evening",
            "evening",
        ),
    ),
    (
        "morning",
        (
            "утром",
            "с утра",
            "на утро",
            "утро",
            "morning",
            "in the morning",
        ),
    ),
    (
        "afternoon",
        (
            "днём",
            "днем",
            "в обед",
            "после обеда",
            "afternoon",
            "in the afternoon",
        ),
    ),
    (
        "night",
        (
            "ночью",
            "в ночь",
            "поздно вечером",
            "поздним вечером",
            "ночь",
            "late night",
            "overnight",
            "tonight late",
        ),
    ),
)


def detect_time_of_day(text: str) -> TimeOfDay | None:
    """Return a coarse time-of-day label if the text contains a phrase.

    Matches are substring-based on the lowered text and stable under
    punctuation noise. Returns the first label whose phrases appear.
    """
    if not text:
        return None
    low = text.lower()
    for label, phrases in _TOD_PATTERNS:
        for phrase in phrases:
            if phrase in low:
                return label
    return None


def resolve_tod_window(label: TimeOfDay, now_utc: datetime) -> tuple[datetime, datetime]:
    """Translate ``label`` into a concrete ``(start, stop)`` time window.

    The window is anchored to the user's local timezone and points at the
    *next* occurrence of that time-of-day: if it's already 20:00 and the
    user says "вечер", we return (20:00 today, 24:00 today). If 05:00 and
    "вечер", we return (18:00 today, 24:00 today). The returned datetimes
    are aware (tz = Moscow) so downstream filtering compares directly
    against EPG programme timestamps.
    """
    start_h, stop_h = _TOD_RANGES[label]
    now_local = now_utc.astimezone(_USER_LOCAL_TZ)
    base = now_local.replace(minute=0, second=0, microsecond=0)
    window_start = base.replace(hour=start_h)
    window_stop = base.replace(hour=stop_h % 24)
    if stop_h >= 24:
        window_stop = window_stop + timedelta(days=1)
    if window_stop <= now_local:
        # Today's window has fully elapsed — point at tomorrow's.
        window_start = window_start + timedelta(days=1)
        window_stop = window_stop + timedelta(days=1)
    elif window_start < now_local:
        # We're already inside the window — start from "now" so the
        # caller doesn't include past-aired programmes.
        window_start = now_local
    return window_start, window_stop


# Day-of-week labels — Monday=0 to match `datetime.weekday()`.
# "weekend" stays special because it spans two days (Sat+Sun).
DayOfWeek = Literal[
    "mon", "tue", "wed", "thu", "fri", "sat", "sun", "weekend"
]

# Phrases → day-of-week label. Ordered so longer/more-specific phrases win
# ("по субботам" before bare "суббота"). Kept inclusive for case/stemming:
# "в субботу", "субботний вечер", "на субботу" all land on Saturday.
_DOW_PATTERNS: tuple[tuple[DayOfWeek, tuple[str, ...]], ...] = (
    (
        "weekend",
        (
            "на выходных",
            "в выходные",
            "на выходные",
            "по выходным",
            "выходные",
            "выходных",
            "this weekend",
            "on the weekend",
            "weekends",
            "weekend",
        ),
    ),
    (
        "sat",
        (
            "в субботу",
            "на субботу",
            "по субботам",
            "субботу",
            "субботний",
            "субботним",
            "субботнего",
            "суббот",
            "saturday",
            "sat ",
        ),
    ),
    (
        "sun",
        (
            "в воскресенье",
            "на воскресенье",
            "по воскресеньям",
            "воскресенье",
            "воскресенья",
            "воскресный",
            "воскресным",
            "воскресенья",
            "sunday",
            "sun ",
        ),
    ),
    (
        "mon",
        (
            "в понедельник",
            "на понедельник",
            "понедельник",
            "понедельника",
            "monday",
            "mon ",
        ),
    ),
    (
        "tue",
        (
            "во вторник",
            "на вторник",
            "вторник",
            "вторника",
            "tuesday",
            "tue ",
        ),
    ),
    (
        "wed",
        (
            "в среду",
            "на среду",
            "среду",
            "в среду",
            "среда",
            "wednesday",
            "wed ",
        ),
    ),
    (
        "thu",
        (
            "в четверг",
            "на четверг",
            "четверг",
            "четверга",
            "thursday",
            "thu ",
        ),
    ),
    (
        "fri",
        (
            "в пятницу",
            "на пятницу",
            "по пятницам",
            "пятницу",
            "пятница",
            "пятницы",
            "пятничный",
            "friday",
            "fri ",
        ),
    ),
)

# Relative-date phrases → day offset from "today". Checked before day-of-week
# so "послезавтра" always wins over an accidental DOW collision.
_RELATIVE_DAY_PATTERNS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (2, ("послезавтра", "day after tomorrow")),
    (1, ("завтра", "на завтра", "tomorrow")),
    (0, ("сегодня", "today", "tonight")),
)


def detect_date_window(
    text: str, now_utc: datetime
) -> tuple[datetime, datetime] | None:
    """Return the date window implied by the user's message, or ``None``.

    Recognises "выходные/weekend", explicit weekdays ("в субботу",
    "saturday"), and relative phrases ("завтра", "послезавтра", "сегодня").
    The window is aligned to the user's local clock (Europe/Moscow) and
    expressed in the same tz as ``resolve_tod_window`` so the two can be
    combined.

    Precedence: weekend > explicit weekday > relative day. When the detected
    day is already in the past (e.g. user says "суббота" on Sunday evening)
    we target NEXT week's occurrence, not a historical one.
    """
    if not text:
        return None
    low = text.lower()
    now_local = now_utc.astimezone(_USER_LOCAL_TZ)
    today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    # 1. Day-of-week phrases (weekend takes precedence via ordering above).
    for label, phrases in _DOW_PATTERNS:
        if any(phrase in low for phrase in phrases):
            if label == "weekend":
                # Saturday 00:00 through Monday 00:00 (local).
                days_ahead = (5 - today_local.weekday()) % 7
                # If today IS Saturday or Sunday, start from today — don't
                # roll forward a whole week.
                if today_local.weekday() in (5, 6):
                    days_ahead = 0
                window_start = today_local + timedelta(days=days_ahead)
                window_stop = window_start + timedelta(
                    days=2 if today_local.weekday() != 6 else 1
                )
            else:
                target_dow = ("mon", "tue", "wed", "thu", "fri", "sat", "sun").index(
                    label
                )
                days_ahead = (target_dow - today_local.weekday()) % 7
                # days_ahead == 0 means the user said "в субботу" and it IS
                # Saturday — serve today's remaining slots rather than next
                # week.
                window_start = today_local + timedelta(days=days_ahead)
                window_stop = window_start + timedelta(days=1)
            # If the chosen day is today but the day already ended, bump
            # forward — happens if user types "сегодня" at 23:59.
            if window_stop <= now_local:
                window_start += timedelta(days=7)
                window_stop += timedelta(days=7)
            # Never include past-aired programmes on "today".
            if window_start < now_local:
                window_start = now_local
            return window_start, window_stop

    # 2. Relative day phrases.
    for offset, phrases in _RELATIVE_DAY_PATTERNS:
        if any(phrase in low for phrase in phrases):
            window_start = today_local + timedelta(days=offset)
            window_stop = window_start + timedelta(days=1)
            if window_start < now_local:
                window_start = now_local
            return window_start, window_stop

    return None


def combine_windows(
    date_window: tuple[datetime, datetime] | None,
    tod_window: tuple[datetime, datetime] | None,
) -> tuple[datetime, datetime] | None:
    """Intersect a date and time-of-day window into one range.

    "вечер в субботу" asks for Saturday 18:00–24:00. The date window is
    Sat 00:00–Sun 00:00; the time-of-day window (computed against today)
    is 18:00–24:00 on whatever day it resolves to. This projects the
    time-of-day's start/stop hours onto the date window's day.
    """
    if date_window is None:
        return tod_window
    if tod_window is None:
        return date_window
    date_start, date_stop = date_window
    tod_start, tod_stop = tod_window
    duration = tod_stop - tod_start
    # Project the time-of-day onto the first day of the date window.
    projected_start = date_start.replace(
        hour=tod_start.hour, minute=tod_start.minute, second=0, microsecond=0
    )
    projected_stop = projected_start + duration
    # Clip to the date window.
    projected_start = max(projected_start, date_start)
    projected_stop = min(projected_stop, date_stop)
    if projected_stop <= projected_start:
        # Incompatible combination — fall back to the wider date window.
        return date_window
    return projected_start, projected_stop


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


# Stop words we want to skip when extracting programme-search terms from the
# user's free-form question. These are so common that requiring a title
# contain them would either match everything or filter out all the useful
# programmes. Kept short; we're not chasing a full-blown NLP stop-list.
_SEARCH_STOPWORDS: frozenset[str] = frozenset(
    {
        # RU
        "когда",
        "будет",
        "ближайший",
        "ближайшая",
        "следующий",
        "следующая",
        "показывают",
        "показ",
        "сеанс",
        "фильм",
        "фильма",
        "фильмы",
        "кино",
        "сериал",
        "матч",
        "трансляция",
        "передача",
        "передачи",
        "что",
        "где",
        "какой",
        "какая",
        "сегодня",
        "завтра",
        "вчера",
        "вечером",
        "утром",
        "днём",
        "ночью",
        "час",
        "часа",
        "часов",
        "мин",
        "минут",
        "канал",
        "канале",
        "каналу",
        "каналы",
        "каналов",
        "каналах",
        "телеканал",
        "телеканала",
        "есть",
        "ли",
        "да",
        "нет",
        "мне",
        "тебе",
        "про",
        "для",
        "эти",
        "это",
        "того",
        "этого",
        "как",
        "или",
        "но",
        "нужно",
        "хочу",
        "покажи",
        "найди",
        "пожалуйста",
        # Imperative verbs + quantity / option chatter that leak into
        # chat-style queries: "подбери 3-5 вариантов разных каналов …".
        # Without these the keyword filter latches onto "подбери" or
        # "разных" and strips the actual answer set.
        "подбери",
        "подбор",
        "подборка",
        "предложи",
        "предложение",
        "дай",
        "дайте",
        "вариант",
        "варианта",
        "вариантов",
        "варианты",
        "разный",
        "разная",
        "разное",
        "разные",
        "разных",
        "разным",
        "один",
        "одна",
        "одно",
        "одни",
        "штук",
        "штуки",
        "штука",
        "пара",
        "пары",
        "несколько",
        "чтото",
        "что-то",
        "интересное",
        "интересного",
        "посоветуй",
        "совет",
        "совета",
        # Time-of-day / calendar words are already handled by the
        # dedicated time-of-day detector — keeping them out of the
        # keyword set prevents a second round of over-filtering.
        "вечер",
        "вечера",
        "вечеру",
        "утро",
        "утра",
        "ночь",
        "ночи",
        "день",
        "дня",
        # EN
        "when",
        "will",
        "next",
        "nearest",
        "show",
        "movie",
        "film",
        "series",
        "match",
        "broadcast",
        "programme",
        "program",
        "what",
        "where",
        "today",
        "tomorrow",
        "yesterday",
        "evening",
        "morning",
        "afternoon",
        "night",
        "channel",
        "there",
        "any",
        "please",
        "find",
        "me",
        "the",
        "a",
        "an",
        "is",
        "are",
        "be",
        "on",
        "at",
        "in",
        "for",
        "of",
        "to",
        "and",
        "or",
        "but",
        "with",
    }
)


def _search_keywords(text: str) -> list[str]:
    """Return lowercase content words from ``text`` useful for EPG filtering.

    Tokens are ≥ 4 chars to skip prepositions/particles the stop-list might
    miss, and stop-listed forms are dropped. Preserves the original order
    so multi-word titles can still be matched as phrases later if needed.
    """
    tokens = re.findall(r"[\wёЁ]+", text.lower(), flags=re.UNICODE)
    out: list[str] = []
    for tok in tokens:
        if len(tok) < 4:
            continue
        if tok in _SEARCH_STOPWORDS:
            continue
        if tok.isdigit():
            continue
        out.append(tok)
    return out


# Broadcast-slot placeholders — 2-4 hour EPG blocks where the channel
# hasn't published the concrete titles of the films / shows airing
# inside ("Кино non-stop", "Хиты кино", "Сериалы подряд"). For the AI
# assistant these are useless: the user asked for concrete recs, and
# the slot carries no title to recommend. Drop them before the model
# sees the schedule so it has to reach for channels with real titles.
_EPG_PLACEHOLDER_TITLE_RE = re.compile(
    r"^\s*(?:"
    # "Кино" family — bare category, "non-stop", "подряд", "дня/вечера/ночи/недели"
    r"кино(?:\s*non[-\s]*stop|\s+подряд|\s+дня|\s+вечера|\s+ночи|\s+недели)?"
    r"|кино\s+24|кинозал|кинопоказ(?:\s+\w+)?|киномарафон"
    # Quality-flavoured cinema blocks
    r"|хиты\s+кино|кинохиты|лучшее\s+кино|новинки\s+кино|премьеры\s+кино"
    # Generic slot labels
    r"|художественн(?:ые|ый)\s+фильм(?:ы)?"
    r"|фильм(?:\s+(?:вечера|дня|недели|месяца|сезона|года))?"
    r"|премьер(?:а|ы)?(?:\s+(?:недели|месяца|сезона))?"
    r"|сериал(?:ы)?(?:\s+подряд|\s+дня|\s+вечера|\s+ночи)?"
    r"|мультфильм(?:ы)?\s+подряд|мультсериал(?:ы)?\s+подряд|мульт(?:ы)?\s+подряд"
    r"|ночной\s+(?:киносеанс|сеанс|эфир|кинозал)"
    r"|вечерн(?:ий|ее)\s+(?:киносеанс|кино|шоу)"
    r"|дневн(?:ой|ое)\s+(?:киносеанс|кино)"
    # English placeholders on international channels
    r"|movies?\s+(?:non[-\s]*stop|marathon|block|hour|night)"
    r"|films?\s+(?:non[-\s]*stop|marathon|block)"
    r"|cinema\s+(?:non[-\s]*stop|block|hour)"
    r"|prime\s+time\s+(?:movies?|cinema)"
    r"|back[-\s]to[-\s]back\s+(?:movies?|films?|shows?)"
    # Sport / generic category blocks (RU + EN)
    r"|спорт\s+(?:non[-\s]*stop|подряд|дня|вечера)"
    r"|футбол\s+(?:non[-\s]*stop|подряд|дня)"
    r"|хоккей\s+(?:non[-\s]*stop|подряд)"
    r"|(?:football|hockey|basketball|tennis|soccer|sports?)"
    r"\s+(?:non[-\s]*stop|marathon|block|hour)"
    r")\s*[.!]*\s*$",
    re.IGNORECASE,
)


def is_placeholder_title(title: str) -> bool:
    """True when a title is a generic broadcast-slot container with no
    concrete programme name (e.g. "Кино non-stop", "Сериалы подряд").
    Callers filter these out of schedules passed to the LLM so the
    assistant can't recommend them as if they were specific shows.
    """
    if not title:
        return True
    stripped = title.strip()
    if len(stripped) < 2:
        return True
    return bool(_EPG_PLACEHOLDER_TITLE_RE.match(stripped))


def drop_placeholder_slots(
    schedules: list[ChannelSchedule],
) -> list[ChannelSchedule]:
    """Filter every schedule to keep only programmes with a concrete
    title. Channels that become empty after filtering drop out.
    """
    clean: list[ChannelSchedule] = []
    for sch in schedules:
        matching = tuple(p for p in sch.programmes if not is_placeholder_title(p.title))
        if matching:
            clean.append(
                ChannelSchedule(
                    channel_id=sch.channel_id,
                    channel_name=sch.channel_name,
                    group=sch.group,
                    programmes=matching,
                )
            )
    return clean


def narrow_by_time_window(
    schedules: list[ChannelSchedule],
    window_start: datetime,
    window_stop: datetime,
) -> list[ChannelSchedule]:
    """Keep only programmes starting within ``[window_start, window_stop)``.

    Used by the chat endpoint when the user asks "на вечер" / "утром" so
    the model sees only picks from the right slice of the day. Channels
    with zero matches are dropped entirely so the prompt stays tight.
    Programmes' start timestamps must be aware — EPG parsing normalises
    them already, so this comparison is safe.
    """
    narrowed: list[ChannelSchedule] = []
    for sch in schedules:
        matching = tuple(p for p in sch.programmes if window_start <= p.start < window_stop)
        if matching:
            narrowed.append(
                ChannelSchedule(
                    channel_id=sch.channel_id,
                    channel_name=sch.channel_name,
                    group=sch.group,
                    programmes=matching,
                )
            )
    return narrowed


def narrow_by_programme_content(
    text: str,
    schedules: list[ChannelSchedule],
    min_hits_to_apply: int = 1,
) -> list[ChannelSchedule]:
    """Shrink a schedule list to only programmes whose title/description match
    keywords in the user's latest message.

    Used in deep-search mode when the question targets a specific programme
    ("когда будет фильм Американский ниндзя?") — no reason to ship 149
    channels × 7 days of EPG when a handful of title matches answer the
    question. Returns the input unchanged if fewer than ``min_hits_to_apply``
    matches are found so open-ended queries don't get clipped to empty.
    """
    keywords = _search_keywords(text)
    if not keywords:
        return schedules
    narrowed: list[ChannelSchedule] = []
    hits_total = 0
    for sch in schedules:
        matching = tuple(
            p
            for p in sch.programmes
            if any(k in p.title.lower() or k in p.description.lower() for k in keywords)
        )
        if matching:
            narrowed.append(
                ChannelSchedule(
                    channel_id=sch.channel_id,
                    channel_name=sch.channel_name,
                    group=sch.group,
                    programmes=matching,
                )
            )
            hits_total += len(matching)
    if hits_total < min_hits_to_apply:
        return schedules
    return narrowed


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
