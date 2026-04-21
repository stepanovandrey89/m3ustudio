"""OpenAI token-usage tracker — per-day, per-model, per-operation.

Every place in the server that calls the OpenAI API records the
resulting ``response.usage`` (input / output / total tokens + call
count) here. The tracker accumulates in memory and persists to
``ai_cache/usage.json`` on every write so counts survive restarts.

Operations are free-form strings that describe the caller —
``digest``, ``chat``, ``cinema-enrich``, ``poster-backfill``, etc. —
so the audit endpoint can break costs down per feature.

Estimated cost is computed from a static price table. Prices drift
over time; the table at the bottom of this file is the single source
of truth the endpoint reports.
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

# Published OpenAI prices as of 2026-01 (USD per 1M tokens).
# Update the table when the pricing page changes. Unknown models
# fall through to ``_UNKNOWN_PRICE`` so we still report *something*.
_PRICES_PER_MILLION: dict[str, tuple[float, float]] = {
    # (input_usd_per_1m, output_usd_per_1m)
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
}
_UNKNOWN_PRICE: tuple[float, float] = (1.0, 4.0)


def _price_for(model: str) -> tuple[float, float]:
    """Return (input, output) USD per 1M tokens for the given model."""
    key = model.lower().strip()
    # Versioned model names like "gpt-5-mini-2025-04-18" — match prefix.
    for base, price in _PRICES_PER_MILLION.items():
        if key == base or key.startswith(base + "-"):
            return price
    return _UNKNOWN_PRICE


@dataclass(frozen=True)
class UsageRow:
    day: str  # YYYY-MM-DD (UTC)
    model: str
    operation: str
    calls: int
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def cost_usd(self) -> float:
        pin, pout = _price_for(self.model)
        return (self.prompt_tokens * pin + self.completion_tokens * pout) / 1_000_000

    def to_dict(self) -> dict[str, object]:
        return {
            "day": self.day,
            "model": self.model,
            "operation": self.operation,
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 4),
        }


class UsageTracker:
    """Accumulates OpenAI usage across the process, persists to disk.

    Thread + coroutine safe — the only shared mutable state is the
    in-memory counter dict, protected by a lock. ``record`` runs off
    the coroutine's hot path (synchronous file write is cheap — the
    JSON stays under a few KB even with a year of daily rows).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # Key: (day, model, operation) → [calls, prompt, completion]
        self._totals: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for row in raw.get("rows", []):
            key = (
                str(row.get("day", "")),
                str(row.get("model", "")),
                str(row.get("operation", "")),
            )
            if not all(key):
                continue
            self._totals[key] = [
                int(row.get("calls", 0)),
                int(row.get("prompt_tokens", 0)),
                int(row.get("completion_tokens", 0)),
            ]

    def _save(self) -> None:
        rows = [
            UsageRow(
                day=day,
                model=model,
                operation=op,
                calls=vals[0],
                prompt_tokens=vals[1],
                completion_tokens=vals[2],
            ).to_dict()
            for (day, model, op), vals in sorted(self._totals.items())
        ]
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with contextlib.suppress(OSError):
            tmp.write_text(
                json.dumps({"rows": rows}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._path)

    def record(
        self,
        *,
        model: str,
        operation: str,
        prompt_tokens: int,
        completion_tokens: int,
        when: datetime | None = None,
    ) -> None:
        """Add one call's usage to today's running total."""
        day = (when or datetime.now(UTC)).strftime("%Y-%m-%d")
        with self._lock:
            key = (day, model.strip(), operation.strip() or "unknown")
            vals = self._totals[key]
            vals[0] += 1
            vals[1] += max(0, int(prompt_tokens))
            vals[2] += max(0, int(completion_tokens))
            self._save()
        # Useful for journalctl forensics.
        print(
            f"[ai-usage] op={operation} model={model} "
            f"prompt={prompt_tokens} completion={completion_tokens}",
            flush=True,
        )

    def record_from_response(
        self, response: Any, *, operation: str, model: str | None = None
    ) -> None:  # noqa: ANN401 — OpenAI response type
        """Convenience wrapper — pull ``usage`` off an OpenAI
        ``ChatCompletion`` and call ``record``. Swallows any
        attribute errors so a broken response can't crash the caller.
        """
        try:
            usage = getattr(response, "usage", None)
            if usage is None:
                return
            prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion = int(getattr(usage, "completion_tokens", 0) or 0)
            resp_model = model or getattr(response, "model", None) or "unknown"
            self.record(
                model=resp_model,
                operation=operation,
                prompt_tokens=prompt,
                completion_tokens=completion,
            )
        except Exception as exc:  # noqa: BLE001 — usage is diagnostic only
            print(f"[ai-usage] record-fail op={operation}: {exc}", flush=True)

    def summary(self, *, since: date | None = None, until: date | None = None) -> dict[str, object]:
        """Aggregate totals across all rows in ``[since, until]`` inclusive."""
        with self._lock:
            rows = [
                UsageRow(
                    day=day,
                    model=model,
                    operation=op,
                    calls=vals[0],
                    prompt_tokens=vals[1],
                    completion_tokens=vals[2],
                )
                for (day, model, op), vals in self._totals.items()
            ]
        if since is not None:
            rows = [r for r in rows if r.day >= since.isoformat()]
        if until is not None:
            rows = [r for r in rows if r.day <= until.isoformat()]
        # Also produce per-day totals and grand total.
        by_day: dict[str, dict[str, float]] = defaultdict(
            lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
        )
        by_model: dict[str, dict[str, float]] = defaultdict(
            lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
        )
        by_operation: dict[str, dict[str, float]] = defaultdict(
            lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
        )
        grand = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
        for r in rows:
            for bucket, key in (
                (by_day, r.day),
                (by_model, r.model),
                (by_operation, r.operation),
            ):
                bucket[key]["calls"] += r.calls
                bucket[key]["prompt_tokens"] += r.prompt_tokens
                bucket[key]["completion_tokens"] += r.completion_tokens
                bucket[key]["cost_usd"] += r.cost_usd
            grand["calls"] += r.calls
            grand["prompt_tokens"] += r.prompt_tokens
            grand["completion_tokens"] += r.completion_tokens
            grand["cost_usd"] += r.cost_usd
        for bucket in (by_day, by_model, by_operation):
            for v in bucket.values():
                v["cost_usd"] = round(float(v["cost_usd"]), 4)
        grand["cost_usd"] = round(float(grand["cost_usd"]), 4)
        return {
            "rows": sorted([r.to_dict() for r in rows], key=lambda r: (r["day"], r["model"])),
            "by_day": dict(sorted(by_day.items(), reverse=True)),
            "by_model": dict(by_model),
            "by_operation": dict(by_operation),
            "grand": grand,
            "prices_per_million_usd": _PRICES_PER_MILLION,
        }


# Global singleton bound at app startup.
_singleton: UsageTracker | None = None


def bind(path: Path) -> UsageTracker:
    """Install the process-wide tracker. Called once from ``server.main``."""
    global _singleton
    _singleton = UsageTracker(path)
    return _singleton


def tracker() -> UsageTracker | None:
    """Return the global tracker. ``None`` when ``bind`` hasn't run
    — call sites should tolerate that (tracker is diagnostic).
    """
    return _singleton
