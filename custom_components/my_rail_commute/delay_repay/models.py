"""Pure models and arrival-delay rules for Delay Repay claims."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Final

_CLOCK_RE: Final = re.compile(r"^(\d{2}):(\d{2})$")
_MINUTES_PER_DAY: Final = 24 * 60
_HALF_DAY: Final = _MINUTES_PER_DAY // 2

# The feed reports "On time" instead of a clock time when a time equals the
# schedule (seen for ``et``, ``ata`` and ``at``).
ON_TIME_TEXT: Final = "on time"


class Confirmation(StrEnum):
    """How reliable a record's arrival time is."""

    ESTIMATED = "estimated"  # forecast from the departure board
    CONFIRMED = "confirmed"  # actual arrival reported by the API


class ClaimStatus(StrEnum):
    """Lifecycle of a claimable journey."""

    PENDING = "pending"  # still live; the delay may change
    ELIGIBLE = "eligible"  # frozen and claimable
    CLAIMED = "claimed"
    DISMISSED = "dismissed"


def parse_clock(value: str | None) -> int | None:
    """Return minutes since midnight for an ``HH:MM`` string, else None."""
    if not isinstance(value, str):
        return None
    match = _CLOCK_RE.match(value.strip())
    if match is None:
        return None
    hours, minutes = int(match[1]), int(match[2])
    if hours > 23 or minutes > 59:
        return None
    return hours * 60 + minutes


def is_on_time_text(value: str | None) -> bool:
    """Return True if the value is the feed's "On time" marker."""
    return isinstance(value, str) and value.strip().lower() == ON_TIME_TEXT


def signed_minutes_between(scheduled: str, actual: str) -> int | None:
    """Return actual minus scheduled in minutes, handling midnight wrap-around.

    Positive means later than scheduled. A raw difference beyond twelve hours
    is read as the other side of midnight (23:55 -> 00:03 is +8, not -1432).
    Returns None if either value is not a valid ``HH:MM`` time.
    """
    sched = parse_clock(scheduled)
    act = parse_clock(actual)
    if sched is None or act is None:
        return None
    diff = act - sched
    if diff > _HALF_DAY:
        diff -= _MINUTES_PER_DAY
    elif diff < -_HALF_DAY:
        diff += _MINUTES_PER_DAY
    return diff


def arrival_delay_minutes(scheduled: str | None, arrival: str | None) -> int | None:
    """Return the arrival delay in whole minutes, never negative.

    ``arrival`` is an estimated or actual value as reported by the API: an
    ``HH:MM`` time or "On time". Returns None when the delay cannot be
    determined (missing or non-time values such as "Delayed").
    """
    if is_on_time_text(arrival):
        return 0
    if scheduled is None or arrival is None:
        return None
    diff = signed_minutes_between(scheduled, arrival)
    if diff is None:
        return None
    return max(diff, 0)


def tier_for_delay(delay_minutes: int | None, thresholds: Iterable[int]) -> int | None:
    """Return the highest threshold the delay meets or exceeds, else None.

    Thresholds are minutes, e.g. (15, 30, 60, 120). Non-positive values are
    ignored and order does not matter.
    """
    if delay_minutes is None:
        return None
    met = [t for t in thresholds if t > 0 and delay_minutes >= t]
    return max(met) if met else None


@dataclass(frozen=True, slots=True)
class ArrivalAssessment:
    """Outcome of evaluating one arrival against a scheme's thresholds."""

    delay_minutes: int | None
    tier: int | None
    is_cancelled: bool

    @property
    def claimable(self) -> bool:
        """A cancellation is claimable; otherwise a threshold must be met."""
        return self.is_cancelled or self.tier is not None


def assess_arrival(
    scheduled_arrival: str | None,
    arrival: str | None,
    *,
    is_cancelled: bool,
    thresholds: Iterable[int],
) -> ArrivalAssessment:
    """Evaluate an arrival against Delay Repay thresholds.

    A cancelled service has no meaningful delay, so delay and tier are None
    and the record is claimable regardless of thresholds.
    """
    if is_cancelled:
        return ArrivalAssessment(delay_minutes=None, tier=None, is_cancelled=True)
    delay = arrival_delay_minutes(scheduled_arrival, arrival)
    return ArrivalAssessment(
        delay_minutes=delay,
        tier=tier_for_delay(delay, thresholds),
        is_cancelled=False,
    )
