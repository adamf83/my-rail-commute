"""Pure models and arrival-delay rules for Delay Repay claims."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta
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
    EXPIRED = "expired"  # eligible but the claim window has passed


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


def resolve_clock_datetime(clock: str | None, now: datetime) -> datetime | None:
    """Return the datetime for an ``HH:MM`` clock time nearest to ``now``.

    Board times carry no date, so pick whichever of yesterday, today or
    tomorrow lands closest to now. A 00:10 train seen at 23:55 is tomorrow's.
    """
    minutes = parse_clock(clock)
    if minutes is None:
        return None
    base = now.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)
    candidates = (base + timedelta(days=d) for d in (-1, 0, 1))
    return min(candidates, key=lambda c: abs(c - now))


def arrival_datetime(
    departure: datetime, scheduled_departure: str, scheduled_arrival: str
) -> datetime | None:
    """Return the scheduled arrival datetime for a journey departing at ``departure``.

    The arrival is the next occurrence of its clock time at or after the
    departure, so journeys that cross midnight roll onto the next day.
    """
    dep = parse_clock(scheduled_departure)
    arr = parse_clock(scheduled_arrival)
    if dep is None or arr is None:
        return None
    return departure + timedelta(minutes=(arr - dep) % _MINUTES_PER_DAY)


def claim_key(
    date: str, leg: int, service_id: str, origin: str, scheduled_departure: str
) -> str:
    """Return the stable key for a journey: date, leg and train.

    Falls back to origin and departure time when the board gives no service ID.
    """
    train = service_id or f"{origin}-{scheduled_departure}"
    return f"{date}|{leg}|{train}"


@dataclass(frozen=True, slots=True)
class ClaimRecord:
    """One tracked journey (a single leg of a single train on a single day)."""

    date: str  # departure date, ISO
    leg: int
    service_id: str
    operator: str
    origin: str
    destination: str
    scheduled_departure: str
    scheduled_arrival: str | None
    arrival: str | None  # estimated or actual, as reported: HH:MM or "On time"
    delay_minutes: int | None
    tier: int | None
    is_cancelled: bool
    confirmation: Confirmation
    status: ClaimStatus
    live_until: str  # ISO; the delay may change until then
    first_seen: str  # ISO
    last_updated: str  # ISO
    delay_reason: str | None = None

    @property
    def key(self) -> str:
        """Return this journey's unique key."""
        return claim_key(
            self.date,
            self.leg,
            self.service_id,
            self.origin,
            self.scheduled_departure,
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable dict."""
        data = asdict(self)
        data["confirmation"] = self.confirmation.value
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ClaimRecord:
        """Build a record from stored data, ignoring unknown keys.

        Raises:
            ValueError: if required fields are missing or enum values are invalid.
        """
        names = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in names}
        try:
            values["confirmation"] = Confirmation(values["confirmation"])
            values["status"] = ClaimStatus(values["status"])
            return cls(**values)  # type: ignore[arg-type]
        except (KeyError, TypeError) as err:
            raise ValueError(f"invalid claim record: {err}") from err
