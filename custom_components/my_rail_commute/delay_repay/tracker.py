"""Tracks late and cancelled journeys and keeps the Delay Repay claim list.

Lifecycle of a record (forecast-based; confirmation from actual arrivals is
layered on later):

* A tracked train whose forecast arrival meets a scheme threshold (or which
  is cancelled) becomes a PENDING record. While live it is updated on every
  poll, and removed if the delay falls back below the threshold.
* Once its scheduled arrival plus a short grace period has passed it is frozen
  as ELIGIBLE using the last forecast.
* ELIGIBLE records not claimed within the claim window become EXPIRED.
* The user moves records to CLAIMED or DISMISSED through services.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_DELAY_REPAY_CLAIM_WINDOW_DAYS,
    CONF_DELAY_REPAY_OPERATORS,
    CONF_DELAY_REPAY_THRESHOLDS,
    DEFAULT_DELAY_REPAY_CLAIM_WINDOW_DAYS,
    DEFAULT_DELAY_REPAY_THRESHOLDS,
    DELAY_REPAY_LIVE_GRACE_MINUTES,
    DELAY_REPAY_RETENTION_EXTRA_DAYS,
)
from .models import (
    ClaimRecord,
    ClaimStatus,
    Confirmation,
    arrival_datetime,
    assess_arrival,
    claim_key,
    resolve_clock_datetime,
)
from .schemes import SchemeSet, build_scheme_set
from .store import DelayRepayStore

_LOGGER = logging.getLogger(__name__)

# Statuses that still count as "outstanding" for the user
_OUTSTANDING = (ClaimStatus.PENDING, ClaimStatus.ELIGIBLE)


class DelayRepayTracker:
    """Records claimable journeys from coordinator updates."""

    def __init__(
        self,
        store: DelayRepayStore,
        schemes: SchemeSet,
        claim_window_days: int = DEFAULT_DELAY_REPAY_CLAIM_WINDOW_DAYS,
    ) -> None:
        """Create a tracker over a store."""
        self._store = store
        self.schemes = schemes
        self.claim_window_days = claim_window_days

    # --- Observation -------------------------------------------------------

    async def async_observe(
        self, parsed_data: dict[str, Any], now: datetime | None = None
    ) -> bool:
        """Update records from one coordinator update. Returns True if changed."""
        now = now or dt_util.now()
        changed = self._maintain(now)
        for leg, origin, destination, service in _iter_services(parsed_data):
            changed |= self._observe_service(now, leg, origin, destination, service)
        saved = await self._store.async_save_if_dirty()
        return changed or saved

    def _observe_service(
        self,
        now: datetime,
        leg: int,
        origin: str,
        destination: str | None,
        service: dict[str, Any],
    ) -> bool:
        if not destination:
            return False  # "all departures" has no destination to arrive at
        scheduled_departure = service.get("scheduled_departure")
        departure = resolve_clock_datetime(scheduled_departure, now)
        if departure is None:
            return False

        is_cancelled = bool(service.get("is_cancelled", False))
        scheduled_arrival = service.get("scheduled_arrival")
        arrival_at = arrival_datetime(departure, scheduled_departure, scheduled_arrival)
        if arrival_at is None and not is_cancelled:
            return False  # cannot judge an arrival delay without arrival times

        operator = service.get("operator") or ""
        assessment = assess_arrival(
            scheduled_arrival,
            service.get("estimated_arrival"),
            is_cancelled=is_cancelled,
            thresholds=self.schemes.for_operator(operator).thresholds,
        )

        key = claim_key(
            departure.date().isoformat(),
            leg,
            service.get("service_id") or "",
            origin,
            scheduled_departure,
        )
        existing = self._store.get(key)
        if existing is not None and existing.status is not ClaimStatus.PENDING:
            return False  # frozen, or already handled by the user

        if not assessment.claimable:
            if existing is not None:
                self._store.remove(key)  # the delay recovered while live
                return True
            return False

        live_until = (arrival_at or departure) + timedelta(
            minutes=DELAY_REPAY_LIVE_GRACE_MINUTES
        )
        record = ClaimRecord(
            date=departure.date().isoformat(),
            leg=leg,
            service_id=service.get("service_id") or "",
            operator=operator,
            origin=origin,
            destination=destination,
            scheduled_departure=scheduled_departure,
            scheduled_arrival=scheduled_arrival,
            arrival=service.get("estimated_arrival"),
            delay_minutes=assessment.delay_minutes,
            tier=assessment.tier,
            is_cancelled=assessment.is_cancelled,
            # A cancellation is reported as fact, not forecast
            confirmation=(
                Confirmation.CONFIRMED if assessment.is_cancelled else Confirmation.ESTIMATED
            ),
            status=ClaimStatus.PENDING,
            live_until=live_until.isoformat(),
            first_seen=existing.first_seen if existing else now.isoformat(),
            last_updated=now.isoformat(),
            delay_reason=(
                service.get("cancellation_reason")
                if is_cancelled
                else service.get("delay_reason")
            ),
        )
        if existing is not None and _same_content(existing, record):
            return False
        self._store.set(record)
        return True

    # --- Maintenance -------------------------------------------------------

    def _maintain(self, now: datetime) -> bool:
        """Freeze finished journeys, expire old claims, prune ancient records."""
        today = now.date()
        expiry_cutoff = (today - timedelta(days=self.claim_window_days)).isoformat()
        prune_cutoff = (
            today
            - timedelta(days=self.claim_window_days + DELAY_REPAY_RETENTION_EXTRA_DAYS)
        ).isoformat()
        changed = False
        for record in self._store.values():
            if record.date < prune_cutoff:
                self._store.remove(record.key)
                changed = True
                continue
            status = record.status
            if status is ClaimStatus.PENDING and (
                datetime.fromisoformat(record.live_until) <= now
            ):
                status = ClaimStatus.ELIGIBLE
            if status is ClaimStatus.ELIGIBLE and record.date < expiry_cutoff:
                status = ClaimStatus.EXPIRED
            if status is not record.status:
                self._store.set(replace(record, status=status))
                changed = True
        return changed

    async def async_maintain(self, now: datetime | None = None) -> bool:
        """Run maintenance and persist. Returns True if anything changed."""
        changed = self._maintain(now or dt_util.now())
        await self._store.async_save_if_dirty()
        return changed

    # --- User actions ------------------------------------------------------

    async def async_set_status(
        self,
        status: ClaimStatus,
        *,
        keys: list[str] | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        now: datetime | None = None,
    ) -> list[str]:
        """Move matching journeys to CLAIMED or DISMISSED.

        Select by explicit ``keys`` and/or an inclusive date range (by
        departure date). With neither, nothing is changed. Records already at
        the target status are untouched. Returns the keys that changed.
        """
        if status not in (ClaimStatus.CLAIMED, ClaimStatus.DISMISSED):
            raise ValueError(f"cannot set status {status}")
        if not keys and from_date is None and to_date is None:
            return []
        wanted = set(keys or [])
        changed: list[str] = []
        stamp = (now or dt_util.now()).isoformat()
        for record in self._store.values():
            by_key = record.key in wanted
            by_range = (from_date is not None or to_date is not None) and (
                (from_date is None or record.date >= from_date.isoformat())
                and (to_date is None or record.date <= to_date.isoformat())
            )
            if not (by_key or by_range) or record.status is status:
                continue
            self._store.set(replace(record, status=status, last_updated=stamp))
            changed.append(record.key)
        await self._store.async_save_if_dirty()
        return changed

    # --- Queries -----------------------------------------------------------

    def records(
        self, statuses: tuple[ClaimStatus, ...] | None = None
    ) -> list[ClaimRecord]:
        """Return records (newest departure first), optionally by status."""
        selected = [
            r for r in self._store.values() if statuses is None or r.status in statuses
        ]
        return sorted(
            selected,
            key=lambda r: (r.date, r.scheduled_departure, r.leg),
            reverse=True,
        )

    def unclaimed(self) -> list[ClaimRecord]:
        """Return frozen, claimable journeys the user hasn't handled."""
        return self.records((ClaimStatus.ELIGIBLE,))

    def outstanding_today(self, now: datetime | None = None) -> list[ClaimRecord]:
        """Return live or frozen claimable journeys that departed today."""
        today = (now or dt_util.now()).date().isoformat()
        return [r for r in self.records(_OUTSTANDING) if r.date == today]

    def claim_deadline(self, record: ClaimRecord) -> str:
        """Return the last date a record can be claimed (ISO date)."""
        return (
            date.fromisoformat(record.date) + timedelta(days=self.claim_window_days)
        ).isoformat()

    def claim_url(self, record: ClaimRecord) -> str | None:
        """Return the configured claim link for the record's operator."""
        return self.schemes.for_operator(record.operator).claim_url


def _same_content(a: ClaimRecord, b: ClaimRecord) -> bool:
    """Compare records ignoring the update timestamp."""
    return replace(a, last_updated=b.last_updated) == b


def _iter_services(parsed_data: dict[str, Any]):
    """Yield (leg number, origin, destination, service) from coordinator data."""
    if parsed_data.get("is_multi_leg"):
        for number, leg in enumerate(parsed_data.get("legs", []), start=1):
            for service in leg.get("services", []):
                yield number, leg.get("origin"), leg.get("destination"), service
        return
    for service in parsed_data.get("services", []):
        yield 1, parsed_data.get("origin"), parsed_data.get("destination"), service


async def async_create_tracker(
    hass: HomeAssistant, entry_id: str, config: dict[str, Any]
) -> DelayRepayTracker:
    """Build, load and return a tracker for a config entry.

    Invalid scheme text (for example from a hand-edited entry) falls back to
    the default scheme rather than failing setup.
    """
    store = DelayRepayStore(hass, entry_id)
    await store.async_load()
    try:
        schemes = build_scheme_set(
            config.get(CONF_DELAY_REPAY_THRESHOLDS) or DEFAULT_DELAY_REPAY_THRESHOLDS,
            config.get(CONF_DELAY_REPAY_OPERATORS) or "",
        )
    except ValueError as err:
        _LOGGER.warning("Invalid Delay Repay scheme options, using defaults: %s", err)
        schemes = build_scheme_set(DEFAULT_DELAY_REPAY_THRESHOLDS, "")
    tracker = DelayRepayTracker(
        store,
        schemes,
        int(
            config.get(
                CONF_DELAY_REPAY_CLAIM_WINDOW_DAYS,
                DEFAULT_DELAY_REPAY_CLAIM_WINDOW_DAYS,
            )
        ),
    )
    await tracker.async_maintain()
    return tracker
