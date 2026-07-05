"""Journey correlation and persistence for the Recent Train Times feature.

Pairs "departed origin" and "arrived destination" movement events for the
same train on the same day into a completed journey record, and persists a
rolling log of those records per config entry.
"""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    JOURNEY_CORRELATION_MAX_MINUTES,
    JOURNEY_PENDING_TIMEOUT_MINUTES,
    RECENT_JOURNEYS_MAX_STORED,
    RECENT_JOURNEYS_RETENTION_DAYS,
    RECENT_JOURNEYS_STORAGE_VERSION,
)
from .nrod_stomp import (
    EVENT_ARRIVAL,
    EVENT_DEPARTURE,
    EVENT_PASS,
    CancellationEvent,
    MovementEvent,
)

_LOGGER = logging.getLogger(__name__)


class RecentJourneysStore:
    """Persistent rolling log of recent completed/cancelled journeys for one route."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the store.

        Args:
            hass: Home Assistant instance
            entry_id: Config entry ID this log belongs to
        """
        self._store = Store(
            hass, RECENT_JOURNEYS_STORAGE_VERSION, f"{DOMAIN}_{entry_id}_journeys"
        )
        self._journeys: list[dict[str, Any]] = []

    async def async_load(self) -> None:
        """Load any persisted journeys from storage."""
        raw = await self._store.async_load()
        self._journeys = raw.get("journeys", []) if raw else []
        self._prune()
        _LOGGER.debug("Loaded %d recent journeys", len(self._journeys))

    async def async_add_journey(self, record: dict[str, Any]) -> None:
        """Append a completed or cancelled journey record and persist it."""
        self._journeys.append(record)
        self._journeys.sort(key=lambda j: j.get("recorded_at", ""))
        self._prune()
        await self._store.async_save({"journeys": self._journeys})
        _LOGGER.debug(
            "Recorded journey: train_id=%s cancelled=%s delay=%s",
            record.get("train_id"),
            record.get("is_cancelled"),
            record.get("delay_minutes"),
        )

    def get_recent_journeys(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent journeys, newest first."""
        return list(reversed(self._journeys[-limit:]))

    def get_last_journey(self) -> dict[str, Any] | None:
        """Return the most recently recorded journey, or None."""
        return self._journeys[-1] if self._journeys else None

    def count_for_date(self, service_date: str) -> int:
        """Return how many journeys were recorded for a given service date."""
        return sum(1 for j in self._journeys if j.get("service_date") == service_date)

    def _prune(self) -> None:
        """Remove entries beyond the retention window or storage cap."""
        cutoff = (
            dt_util.now().date() - timedelta(days=RECENT_JOURNEYS_RETENTION_DAYS)
        ).isoformat()
        before = len(self._journeys)
        self._journeys = [j for j in self._journeys if j.get("service_date", "") >= cutoff]
        if len(self._journeys) > RECENT_JOURNEYS_MAX_STORED:
            self._journeys = self._journeys[-RECENT_JOURNEYS_MAX_STORED:]
        if len(self._journeys) != before:
            _LOGGER.debug(
                "Pruned recent journeys log: %d -> %d entries", before, len(self._journeys)
            )


class JourneyCorrelationEngine:
    """Correlates origin/destination movement events into journey records."""

    def __init__(
        self,
        hass: HomeAssistant,
        origin_crs: str,
        destination_crs: str,
        origin_stanox: set[str],
        destination_stanox: set[str],
        journeys_store: RecentJourneysStore,
    ) -> None:
        """Initialize the engine for a single route.

        Args:
            hass: Home Assistant instance
            origin_crs: Origin station CRS code
            destination_crs: Destination station CRS code
            origin_stanox: STANOX codes that count as "departed the origin"
            destination_stanox: STANOX codes that count as "arrived at the destination"
            journeys_store: Store to persist completed journeys into
        """
        self._hass = hass
        self._origin_crs = origin_crs
        self._destination_crs = destination_crs
        self._origin_stanox = set(origin_stanox)
        self._destination_stanox = set(destination_stanox)
        self._journeys_store = journeys_store
        # Keyed by (train_id, service_date): headcodes are reused daily, so
        # service_date must be part of the key to avoid cross-day mismatches.
        self._pending: dict[tuple[str, str], dict[str, Any]] = {}

    @property
    def watched_stanox(self) -> set[str]:
        """Return every STANOX code this engine needs feed events for."""
        return self._origin_stanox | self._destination_stanox

    async def handle_event(self, event: MovementEvent | CancellationEvent) -> None:
        """Process a single feed event, dispatching by type."""
        self._expire_stale_pending()
        if isinstance(event, MovementEvent):
            await self._handle_movement(event)
        elif isinstance(event, CancellationEvent):
            await self._handle_cancellation(event)

    async def _handle_movement(self, event: MovementEvent) -> None:
        key = (event.train_id, event.service_date)

        if event.stanox in self._origin_stanox and event.event_type == EVENT_DEPARTURE:
            if key in self._pending:
                return  # duplicate/redelivered departure message, ignore
            self._pending[key] = {
                "train_id": event.train_id,
                "service_date": event.service_date,
                "toc": event.toc,
                "scheduled_departure": event.planned_time,
                "actual_departure": event.actual_time,
                "departure_platform": event.platform,
                "recorded_at_dt": dt_util.utcnow(),
                "arrival_seen": False,
                "completed": False,
            }
            return

        if event.stanox in self._destination_stanox and event.event_type in (
            EVENT_ARRIVAL,
            EVENT_PASS,
        ):
            await self._handle_arrival_candidate(key, event)

    async def _handle_arrival_candidate(
        self, key: tuple[str, str], event: MovementEvent
    ) -> None:
        pending = self._pending.get(key)
        if pending is None or pending["completed"]:
            return  # no matching departure on record, or already completed

        elapsed_minutes = (
            dt_util.utcnow() - pending["recorded_at_dt"]
        ).total_seconds() / 60
        if elapsed_minutes > JOURNEY_CORRELATION_MAX_MINUTES:
            _LOGGER.debug(
                "Discarding pending departure %s: exceeded max correlation window", key
            )
            del self._pending[key]
            return

        if event.event_type == EVENT_PASS and pending["arrival_seen"]:
            return  # a real ARRIVAL already completed this journey

        if event.event_type == EVENT_ARRIVAL:
            pending["arrival_seen"] = True

        pending["completed"] = True
        record = {
            "train_id": pending["train_id"],
            "service_date": pending["service_date"],
            "toc": pending["toc"],
            "origin_crs": self._origin_crs,
            "destination_crs": self._destination_crs,
            "scheduled_departure": pending["scheduled_departure"],
            "actual_departure": pending["actual_departure"],
            "departure_platform": pending["departure_platform"],
            "scheduled_arrival": event.planned_time,
            "actual_arrival": event.actual_time,
            "arrival_platform": event.platform,
            "delay_minutes": event.delay_minutes,
            "is_cancelled": False,
            "cancellation_reason": None,
            "cancelled_at": None,
            "recorded_at": dt_util.utcnow().isoformat(),
        }
        del self._pending[key]
        await self._journeys_store.async_add_journey(record)

    async def _handle_cancellation(self, event: CancellationEvent) -> None:
        key = (event.train_id, event.service_date)
        pending = self._pending.pop(key, None)

        # Only record a cancellation relevant to this route: either we already
        # saw the train depart the origin, or the cancellation is itself
        # reported at the origin station.
        if pending is None and event.stanox not in self._origin_stanox:
            return

        record = {
            "train_id": event.train_id,
            "service_date": event.service_date,
            "toc": pending.get("toc") if pending else None,
            "origin_crs": self._origin_crs,
            "destination_crs": self._destination_crs,
            "scheduled_departure": pending.get("scheduled_departure") if pending else None,
            "actual_departure": pending.get("actual_departure") if pending else None,
            "departure_platform": pending.get("departure_platform") if pending else None,
            "scheduled_arrival": None,
            "actual_arrival": None,
            "arrival_platform": None,
            "delay_minutes": None,
            "is_cancelled": True,
            "cancellation_reason": event.reason_code,
            "cancelled_at": "en_route" if pending else "origin",
            "recorded_at": dt_util.utcnow().isoformat(),
        }
        await self._journeys_store.async_add_journey(record)

    def _expire_stale_pending(self) -> None:
        """Discard unmatched departures that have been pending too long."""
        cutoff = dt_util.utcnow() - timedelta(minutes=JOURNEY_PENDING_TIMEOUT_MINUTES)
        stale_keys = [
            key for key, value in self._pending.items() if value["recorded_at_dt"] < cutoff
        ]
        for key in stale_keys:
            _LOGGER.debug("Discarding unmatched pending departure (timed out): %s", key)
            del self._pending[key]
