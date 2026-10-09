"""Tests for confirming tracked journeys against actual arrivals."""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.my_rail_commute.api import NationalRailAPIError
from custom_components.my_rail_commute.const import (
    DELAY_REPAY_CONFIRM_MAX_ATTEMPTS,
    DELAY_REPAY_CONFIRM_MAX_PER_UPDATE,
)
from custom_components.my_rail_commute.delay_repay.models import (
    ClaimStatus,
    Confirmation,
)
from custom_components.my_rail_commute.delay_repay.parsing import ArrivalObservation

from .delay_repay_helpers import (
    NOW,
    fake_storage,
    late_service,
    make_tracker,
    single_leg,
)

KEY = "2026-10-08|1|9494208WHYTELF"
# The late service is forecast to arrive at 23:10
EXPECTED = NOW + timedelta(minutes=40)


class FakeSource:
    """Returns queued observations (or raises queued exceptions)."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    async def async_fetch(self, record):
        self.calls.append(record.key)
        result = self.results.pop(0) if self.results else None
        if isinstance(result, Exception):
            raise result
        return result


async def _tracker_with(source, *, services=None, **kwargs):
    tracker, store = await make_tracker(**kwargs)
    tracker.confirmation_source = source
    await tracker.async_observe(single_leg(services or [late_service()]), NOW)
    return tracker, store


async def test_record_stores_the_expected_arrival():
    with fake_storage():
        tracker, _ = await _tracker_with(None)
        (record,) = tracker.records()
        assert record.expected_arrival_at == "2026-10-08T23:10:00+00:00"
        assert record.confirmation is Confirmation.ESTIMATED
        assert record.confirm_attempts == 0


async def test_late_arrival_is_confirmed_with_the_actual_delay():
    with fake_storage():
        source = FakeSource(ArrivalObservation(actual="23:25"))
        tracker, _ = await _tracker_with(source)
        changed = await tracker.async_observe(single_leg([]), EXPECTED)

        assert changed is True
        (record,) = tracker.records()
        assert record.confirmation is Confirmation.CONFIRMED
        assert record.status is ClaimStatus.ELIGIBLE
        assert record.arrival == "23:25"
        assert record.delay_minutes == 32  # 22:53 -> 23:25
        assert record.tier == 30  # moved up from the forecast's 15
        assert source.calls == [KEY]


async def test_on_time_arrival_drops_the_record():
    with fake_storage():
        source = FakeSource(ArrivalObservation(actual="On time"))
        tracker, _ = await _tracker_with(source)
        await tracker.async_observe(single_leg([]), EXPECTED)
        assert tracker.records() == []


async def test_arrival_below_threshold_drops_the_record():
    with fake_storage():
        # Forecast was 17 minutes late; it actually arrived 12 minutes late
        source = FakeSource(ArrivalObservation(actual="23:05"))
        tracker, _ = await _tracker_with(source)
        await tracker.async_observe(single_leg([]), EXPECTED)
        assert tracker.records() == []


async def test_cancelled_en_route_becomes_a_confirmed_cancellation():
    with fake_storage():
        source = FakeSource(ArrivalObservation(is_cancelled=True))
        tracker, _ = await _tracker_with(source)
        await tracker.async_observe(single_leg([]), EXPECTED)
        (record,) = tracker.records()
        assert record.is_cancelled is True
        assert record.delay_minutes is None
        assert record.confirmation is Confirmation.CONFIRMED
        assert record.status is ClaimStatus.ELIGIBLE


async def test_not_arrived_yet_counts_an_attempt_and_stays_an_estimate():
    with fake_storage():
        source = FakeSource(ArrivalObservation(), None)
        tracker, _ = await _tracker_with(source)
        await tracker.async_observe(single_leg([]), EXPECTED)
        await tracker.async_observe(single_leg([]), EXPECTED + timedelta(minutes=2))

        (record,) = tracker.records()
        assert record.confirmation is Confirmation.ESTIMATED
        assert record.confirm_attempts == 2
        assert record.delay_minutes == 17


async def test_api_errors_are_counted_and_do_not_propagate():
    with fake_storage():
        source = FakeSource(NationalRailAPIError("down"))
        tracker, _ = await _tracker_with(source)
        await tracker.async_observe(single_leg([]), EXPECTED)
        (record,) = tracker.records()
        assert record.confirm_attempts == 1
        assert record.confirmation is Confirmation.ESTIMATED


async def test_nothing_is_checked_before_the_expected_arrival():
    with fake_storage():
        source = FakeSource(ArrivalObservation(actual="23:25"))
        tracker, _ = await _tracker_with(source)
        await tracker.async_observe(single_leg([]), EXPECTED - timedelta(minutes=1))
        assert source.calls == []


async def test_nothing_is_checked_after_the_window_closes():
    with fake_storage():
        source = FakeSource(ArrivalObservation(actual="23:25"))
        tracker, _ = await _tracker_with(source)
        await tracker.async_observe(single_leg([]), EXPECTED + timedelta(minutes=31))
        assert source.calls == []
        assert tracker.records()[0].confirmation is Confirmation.ESTIMATED


async def test_attempts_are_capped():
    with fake_storage():
        source = FakeSource()
        tracker, _ = await _tracker_with(source)
        for i in range(DELAY_REPAY_CONFIRM_MAX_ATTEMPTS + 3):
            await tracker.async_observe(
                single_leg([]), EXPECTED + timedelta(seconds=10 * i)
            )
        assert len(source.calls) == DELAY_REPAY_CONFIRM_MAX_ATTEMPTS


async def test_confirmed_records_are_not_checked_again():
    with fake_storage():
        source = FakeSource(ArrivalObservation(actual="23:25"))
        tracker, _ = await _tracker_with(source)
        await tracker.async_observe(single_leg([]), EXPECTED)
        await tracker.async_observe(single_leg([]), EXPECTED + timedelta(minutes=2))
        assert len(source.calls) == 1


@pytest.mark.parametrize("status", [ClaimStatus.CLAIMED, ClaimStatus.DISMISSED])
async def test_handled_records_are_left_alone(status):
    with fake_storage():
        source = FakeSource(ArrivalObservation(actual="On time"))
        tracker, _ = await _tracker_with(source)
        await tracker.async_set_status(status, keys=[KEY], now=NOW)
        await tracker.async_observe(single_leg([]), EXPECTED)
        assert source.calls == []
        assert tracker.records()[0].status is status


async def test_cancelled_at_origin_is_not_checked():
    with fake_storage():
        source = FakeSource(ArrivalObservation(actual="23:25"))
        cancelled = late_service(is_cancelled=True, status="cancelled")
        tracker, _ = await _tracker_with(source, services=[cancelled])
        await tracker.async_observe(single_leg([]), EXPECTED)
        assert source.calls == []


async def test_a_frozen_record_can_still_be_confirmed():
    with fake_storage():
        source = FakeSource(ArrivalObservation(actual="23:25"))
        tracker, _ = await _tracker_with(source)
        # 23:12: past the live window (23:03), so already frozen as eligible
        await tracker.async_observe(single_leg([]), EXPECTED + timedelta(minutes=2))
        (record,) = tracker.records()
        assert record.confirmation is Confirmation.CONFIRMED


async def test_checks_per_update_are_limited_and_oldest_first():
    with fake_storage():
        source = FakeSource()
        tracker, _ = await make_tracker()
        tracker.confirmation_source = source
        services = [
            late_service(service_id=f"{i}0000000X", scheduled_departure="22:40")
            for i in range(DELAY_REPAY_CONFIRM_MAX_PER_UPDATE + 2)
        ]
        await tracker.async_observe(single_leg(services), NOW)
        await tracker.async_observe(single_leg([]), EXPECTED)
        assert len(source.calls) == DELAY_REPAY_CONFIRM_MAX_PER_UPDATE


async def test_without_a_source_records_stay_estimates():
    with fake_storage():
        tracker, _ = await _tracker_with(None)
        await tracker.async_observe(single_leg([]), EXPECTED)
        (record,) = tracker.records()
        assert record.confirmation is Confirmation.ESTIMATED
        assert record.confirm_attempts == 0


async def test_operator_thresholds_apply_to_the_actual_delay():
    from custom_components.my_rail_commute.delay_repay.schemes import build_scheme_set

    with fake_storage():
        schemes = build_scheme_set("15", "Southern = 30,60")
        source = FakeSource(ArrivalObservation(actual="23:20"))
        tracker, _ = await _tracker_with(
            source, schemes=schemes, services=[late_service("23:25")]
        )
        # Forecast 32 minutes (tier 30); actual 27 minutes is under Southern's 30
        await tracker.async_observe(single_leg([]), NOW + timedelta(minutes=55))
        assert tracker.records() == []
