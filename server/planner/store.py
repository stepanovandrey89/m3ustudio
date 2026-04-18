"""Durable JSON-backed store for scheduled "watch later" plans.

Each plan holds the programme metadata plus a per-stage notification flag so a
restart mid-day doesn't re-send "scheduled" alerts already delivered.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

PlanStatus = Literal["scheduled", "live_notified", "done", "cancelled", "missed"]
VALID_STATUSES: tuple[PlanStatus, ...] = (
    "scheduled",
    "live_notified",
    "done",
    "cancelled",
    "missed",
)


@dataclass(slots=True)
class Plan:
    id: str
    channel_id: str
    channel_name: str
    title: str
    start: str  # ISO-8601
    stop: str  # ISO-8601
    theme: str = "other"
    blurb: str = ""
    poster_url: str = ""
    status: PlanStatus = "scheduled"
    notified_created: bool = False
    notified_live: bool = False
    # Telegram message ids so we can delete the poster cards when the user
    # removes a plan from the dashboard.
    tg_created_msg_id: int | None = None
    tg_live_msg_id: int | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, object]:
        return dict(asdict(self))

    @property
    def start_dt(self) -> datetime | None:
        return _parse_iso(self.start)


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


class PlanStore:
    """Simple JSON file — a dedicated dir would be overkill for plan counts we expect."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._plans: dict[str, Plan] = self._load()

    def _load(self) -> dict[str, Plan]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        out: dict[str, Plan] = {}
        for entry in data.get("plans", []):
            try:
                plan = Plan(**entry)
                out[plan.id] = plan
            except TypeError:
                continue
        return out

    def _save(self) -> None:
        payload = {"plans": [p.to_dict() for p in self._plans.values()]}
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._path)

    # ----- CRUD ---------------------------------------------------------

    def list(self) -> list[Plan]:
        """Return plans sorted by start time ascending."""
        return sorted(
            self._plans.values(),
            key=lambda p: (p.start, p.created_at),
        )

    def get(self, plan_id: str) -> Plan | None:
        return self._plans.get(plan_id)

    def add(
        self,
        *,
        channel_id: str,
        channel_name: str,
        title: str,
        start: str,
        stop: str,
        theme: str = "other",
        blurb: str = "",
        poster_url: str = "",
    ) -> Plan:
        plan = Plan(
            id=uuid.uuid4().hex[:12],
            channel_id=channel_id,
            channel_name=channel_name,
            title=title,
            start=start,
            stop=stop,
            theme=theme,
            blurb=blurb,
            poster_url=poster_url,
        )
        self._plans[plan.id] = plan
        self._save()
        return plan

    def update(self, plan: Plan) -> None:
        self._plans[plan.id] = plan
        self._save()

    def mark_notified_created(self, plan_id: str, message_id: int | None = None) -> None:
        p = self._plans.get(plan_id)
        if p:
            self._plans[plan_id] = replace(p, notified_created=True, tg_created_msg_id=message_id)
            self._save()

    def mark_notified_live(self, plan_id: str, message_id: int | None = None) -> None:
        p = self._plans.get(plan_id)
        if p:
            # The live alert supersedes the original "Запланировано" card —
            # the scheduler deletes that message in the chat, so we also
            # clear the stored id to avoid redundant delete attempts on plan
            # removal.
            self._plans[plan_id] = replace(
                p,
                notified_live=True,
                status="live_notified",
                tg_live_msg_id=message_id,
                tg_created_msg_id=None,
            )
            self._save()

    def set_status(self, plan_id: str, status: PlanStatus) -> None:
        p = self._plans.get(plan_id)
        if p and status in VALID_STATUSES:
            self._plans[plan_id] = replace(p, status=status)
            self._save()

    def delete(self, plan_id: str) -> bool:
        if plan_id not in self._plans:
            return False
        del self._plans[plan_id]
        self._save()
        return True

    # ----- Scheduler queries --------------------------------------------

    def due_for_live_alert(self, now: datetime, lead_minutes: int = 1) -> list[Plan]:
        """Plans whose start is within ±lead_minutes and haven't been alerted yet."""
        out: list[Plan] = []
        for p in self._plans.values():
            if p.notified_live or p.status in ("cancelled", "done", "missed"):
                continue
            start = p.start_dt
            if start is None:
                continue
            delta = (start - now).total_seconds() / 60
            if -lead_minutes <= delta <= lead_minutes:
                out.append(p)
        return out

    def mark_missed_stale(self, now: datetime) -> int:
        """Flag plans whose stop is in the past and never got a live alert."""
        count = 0
        for p in list(self._plans.values()):
            if p.status not in ("scheduled",):
                continue
            stop = _parse_iso(p.stop)
            if stop and stop < now:
                self._plans[p.id] = replace(p, status="missed")
                count += 1
        if count:
            self._save()
        return count
