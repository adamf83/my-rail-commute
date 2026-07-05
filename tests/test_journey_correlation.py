"""Tests for the journey correlation engine (the trickiest part of Recent Train Times)."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from homeassistant.util import dt as dt_util

from custom_components.my_rail_commute.const import (
    JOURNEY_CORRELATION_MAX_MINUTES,
    JOURNEY_PENDING_TIMEOUT_MINUTES,
)
from custom_components.my_rail_commute.journey_store import JourneyCorrelationEngine
from custom_components.my_rail_commute.nrod_stomp import (
    EVENT_ARRIVAL,
    EVENT_DEPARTURE,
    EVENT_PASS,
    CancellationEvent,
    MovementEvent,
)

ORIGIN_STANOX = "87701"
DESTINATION_STANOX = "88616"


def _departure(train_id="1A23", service_date="2026-07-05", delay=0, platform="9"):
    return MovementEvent(
        train_id=train_id,
        service_date=service_date,
        stanox=ORIGIN_STANOX,
        event_type=EVENT_DEPARTURE,
        planned_time=f"{service_date}T08:00:00+00:00",
        actual_time=f"{service_date}T08:0{delay}:00+00:00" if delay < 10 else None,
        platform=platform,
        toc="GW",
        variation_status="LATE" if delay else "ON TIME",
        delay_minutes=delay,
    )


def _arrival(
    train_id="1A23",
    service_date="2026-07-05",
    delay=0,
    event_type=EVENT_ARRIVAL,
    stanox=DESTINATION_STANOX,
):
    return MovementEvent(
        train_id=train_id,
        service_date=service_date,
        stanox=stanox,
        event_type=event_type,
        planned_time=f"{service_date}T08:30:00+00:00",
        actual_time=f"{service_date}T08:3{delay}:00+00:00" if delay < 10 else None,
        platform="4",
        toc="GW",
        variation_status="LATE" if delay else "ON TIME",
        delay_minutes=delay,
    )


def _cancellation(train_id="1A23", service_date="2026-07-05", stanox=ORIGIN_STANOX, cancelled_at_origin=True):
    return CancellationEvent(
        train_id=train_id,
        service_date=service_date,
        stanox=stanox,
        reason_code="YI",
        cancelled_at_origin=cancelled_at_origin,
    )


def _make_engine():
    journeys_store = MagicMock()
    journeys_store.async_add_journey = AsyncMock()
    engine = JourneyCorrelationEngine(
        MagicMock(),
        "PAD",
        "RDG",
        {ORIGIN_STANOX},
        {DESTINATION_STANOX},
        journeys_store,
    )
    return engine, journeys_store


class TestBasicCorrelation:
    async def test_departure_then_arrival_completes_journey(self):
        engine, journeys_store = _make_engine()

        await engine.handle_event(_departure(delay=0))
        await engine.handle_event(_arrival(delay=4))

        journeys_store.async_add_journey.assert_called_once()
        record = journeys_store.async_add_journey.call_args[0][0]
        assert record["train_id"] == "1A23"
        assert record["is_cancelled"] is False
        assert record["delay_minutes"] == 4
        assert record["origin_crs"] == "PAD"
        assert record["destination_crs"] == "RDG"
        assert ("1A23", "2026-07-05") not in engine._pending

    async def test_watched_stanox_union(self):
        engine, _ = _make_engine()
        assert engine.watched_stanox == {ORIGIN_STANOX, DESTINATION_STANOX}


class TestSameHeadcodeDifferentDays:
    async def test_same_train_id_different_days_not_cross_matched(self):
        """Headcodes are reused daily; a departure on day 1 must not match day 2's arrival."""
        engine, journeys_store = _make_engine()

        await engine.handle_event(_departure(service_date="2026-07-05"))
        # A different day's arrival for the same headcode must not complete anything
        await engine.handle_event(_arrival(service_date="2026-07-06"))

        journeys_store.async_add_journey.assert_not_called()
        # The original day's departure is still pending, waiting for its own arrival
        assert ("1A23", "2026-07-05") in engine._pending


class TestUnmatchedDeparture:
    async def test_unmatched_departure_times_out_and_is_discarded(self):
        engine, journeys_store = _make_engine()

        await engine.handle_event(_departure())
        key = ("1A23", "2026-07-05")
        assert key in engine._pending

        # Simulate enough time passing for the pending-timeout sweep to fire
        engine._pending[key]["recorded_at_dt"] = dt_util.utcnow() - timedelta(
            minutes=JOURNEY_PENDING_TIMEOUT_MINUTES + 1
        )

        # Any subsequent event triggers the expiry sweep
        await engine.handle_event(_departure(train_id="1B99"))

        assert key not in engine._pending
        journeys_store.async_add_journey.assert_not_called()

    async def test_arrival_beyond_correlation_window_is_not_matched(self):
        """An arrival for a departure recorded too long ago is not treated as a match."""
        engine, journeys_store = _make_engine()

        await engine.handle_event(_departure())
        key = ("1A23", "2026-07-05")
        engine._pending[key]["recorded_at_dt"] = dt_util.utcnow() - timedelta(
            minutes=JOURNEY_CORRELATION_MAX_MINUTES + 1
        )

        await engine.handle_event(_arrival())

        journeys_store.async_add_journey.assert_not_called()
        assert key not in engine._pending


class TestCancellations:
    async def test_cancellation_before_departure_is_at_origin(self):
        engine, journeys_store = _make_engine()

        await engine.handle_event(_cancellation(cancelled_at_origin=True))

        journeys_store.async_add_journey.assert_called_once()
        record = journeys_store.async_add_journey.call_args[0][0]
        assert record["is_cancelled"] is True
        assert record["cancelled_at"] == "origin"
        assert record["actual_departure"] is None

    async def test_cancellation_en_route_after_departure(self):
        engine, journeys_store = _make_engine()

        await engine.handle_event(_departure(delay=0))
        await engine.handle_event(_cancellation(stanox=DESTINATION_STANOX, cancelled_at_origin=False))

        journeys_store.async_add_journey.assert_called_once()
        record = journeys_store.async_add_journey.call_args[0][0]
        assert record["is_cancelled"] is True
        assert record["cancelled_at"] == "en_route"
        assert record["actual_departure"] is not None
        assert ("1A23", "2026-07-05") not in engine._pending

    async def test_cancellation_unrelated_to_route_is_ignored(self):
        """A cancellation reported at neither origin nor a tracked pending journey is ignored."""
        engine, journeys_store = _make_engine()

        await engine.handle_event(_cancellation(stanox="99999", cancelled_at_origin=False))

        journeys_store.async_add_journey.assert_not_called()


class TestDuplicateAndOutOfOrder:
    async def test_duplicate_departure_message_is_idempotent(self):
        engine, journeys_store = _make_engine()

        await engine.handle_event(_departure())
        await engine.handle_event(_departure())  # redelivered
        await engine.handle_event(_arrival())

        journeys_store.async_add_journey.assert_called_once()

    async def test_duplicate_arrival_message_is_idempotent(self):
        engine, journeys_store = _make_engine()

        await engine.handle_event(_departure())
        await engine.handle_event(_arrival())
        await engine.handle_event(_arrival())  # redelivered

        journeys_store.async_add_journey.assert_called_once()

    async def test_arrival_before_departure_is_dropped_without_crash(self):
        """Out-of-order delivery (arrival before its departure) is dropped, not an error."""
        engine, journeys_store = _make_engine()

        await engine.handle_event(_arrival())
        journeys_store.async_add_journey.assert_not_called()

        # The subsequent (late) departure should still work normally on its own
        await engine.handle_event(_departure())
        assert ("1A23", "2026-07-05") in engine._pending


class TestThroughStationPassFallback:
    async def test_pass_completes_journey_when_no_arrival_seen(self):
        engine, journeys_store = _make_engine()

        await engine.handle_event(_departure())
        await engine.handle_event(_arrival(event_type=EVENT_PASS, delay=2))

        journeys_store.async_add_journey.assert_called_once()
        record = journeys_store.async_add_journey.call_args[0][0]
        assert record["delay_minutes"] == 2

    async def test_real_arrival_after_pass_is_ignored(self):
        """Once a PASS has completed the journey, a later ARRIVAL must not re-trigger it."""
        engine, journeys_store = _make_engine()

        await engine.handle_event(_departure())
        await engine.handle_event(_arrival(event_type=EVENT_PASS))
        await engine.handle_event(_arrival(event_type=EVENT_ARRIVAL))

        journeys_store.async_add_journey.assert_called_once()

    async def test_pass_ignored_once_real_arrival_already_seen(self):
        """A duplicate PASS after a real ARRIVAL must not produce a second record."""
        engine, journeys_store = _make_engine()

        await engine.handle_event(_departure())
        await engine.handle_event(_arrival(event_type=EVENT_ARRIVAL))
        await engine.handle_event(_arrival(event_type=EVENT_PASS))

        journeys_store.async_add_journey.assert_called_once()
