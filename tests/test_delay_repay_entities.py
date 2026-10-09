"""Tests for the Delay Repay entities."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from unittest.mock import MagicMock

from custom_components.my_rail_commute.binary_sensor import DelayRepayEligibleSensor
from custom_components.my_rail_commute.const import (
    DELAY_REPAY_MAX_ATTRIBUTE_CLAIMS,
)
from custom_components.my_rail_commute.coordinator import (
    NationalRailDataUpdateCoordinator,
)
from custom_components.my_rail_commute.delay_repay.models import ClaimStatus
from custom_components.my_rail_commute.sensor import DelayRepayClaimsSensor

from .delay_repay_helpers import (
    NOW,
    fake_storage,
    late_service,
    make_tracker,
    single_leg,
)


def _coordinator(tracker):
    coordinator = MagicMock(spec=NationalRailDataUpdateCoordinator)
    coordinator.origin = "WYT"
    coordinator.destination = "LBG"
    coordinator.legs = [{"origin": "WYT", "destination": "LBG"}]
    coordinator.delay_repay = tracker
    coordinator.data = {}
    return coordinator


def _entry():
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.data = {"commute_name": "Test"}
    return entry


async def test_claims_sensor_identity():
    with fake_storage():
        tracker, _ = await make_tracker()
        sensor = DelayRepayClaimsSensor(_coordinator(tracker), _entry())
        assert sensor.unique_id == "entry1_delay_repay_claims"
        assert sensor.name == "Delay Repay Claims"


async def test_claims_sensor_counts_only_frozen_unclaimed_journeys():
    with fake_storage():
        tracker, _ = await make_tracker()
        sensor = DelayRepayClaimsSensor(_coordinator(tracker), _entry())
        assert sensor.native_value == 0

        # Live journey: pending, not yet counted
        await tracker.async_observe(single_leg([late_service()]), NOW)
        assert sensor.native_value == 0
        assert sensor.extra_state_attributes["pending_count"] == 1
        (pending,) = sensor.extra_state_attributes["pending_claims"]
        assert pending["key"] == "2026-10-08|1|9494208WHYTELF"
        assert pending["status"] == "pending"

        await tracker.async_maintain(NOW + timedelta(minutes=40))
        assert sensor.native_value == 1
        attrs = sensor.extra_state_attributes
        assert attrs["entry_id"] == "entry1"
        assert attrs["pending_count"] == 0
        assert attrs["pending_claims"] == []
        assert attrs["claims_truncated"] is False
        assert attrs["claim_window_days"] == 28
        assert attrs["oldest_unclaimed_date"] == "2026-10-08"
        assert attrs["oldest_claim_deadline"] == "2026-11-05"
        (claim,) = attrs["claims"]
        assert claim["key"] == "2026-10-08|1|9494208WHYTELF"
        assert claim["delay_minutes"] == 17


async def test_claims_sensor_drops_claimed_journeys():
    with fake_storage():
        tracker, _ = await make_tracker()
        await tracker.async_observe(single_leg([late_service()]), NOW)
        await tracker.async_maintain(NOW + timedelta(minutes=40))
        sensor = DelayRepayClaimsSensor(_coordinator(tracker), _entry())
        await tracker.async_set_status(
            ClaimStatus.CLAIMED, keys=["2026-10-08|1|9494208WHYTELF"]
        )
        assert sensor.native_value == 0
        attrs = sensor.extra_state_attributes
        assert attrs["claims"] == []
        assert attrs["oldest_unclaimed_date"] is None
        assert attrs["oldest_claim_deadline"] is None


async def test_claims_sensor_caps_the_attribute_list():
    with fake_storage():
        tracker, store = await make_tracker()
        await tracker.async_observe(single_leg([late_service()]), NOW)
        await tracker.async_maintain(NOW + timedelta(minutes=40))
        template = tracker.records()[0]
        for i in range(DELAY_REPAY_MAX_ATTRIBUTE_CLAIMS + 5):
            store.set(replace(template, service_id=f"S{i}", leg=i + 2))

        sensor = DelayRepayClaimsSensor(_coordinator(tracker), _entry())
        assert sensor.native_value == DELAY_REPAY_MAX_ATTRIBUTE_CLAIMS + 6
        attrs = sensor.extra_state_attributes
        assert len(attrs["claims"]) == DELAY_REPAY_MAX_ATTRIBUTE_CLAIMS
        assert attrs["claims_truncated"] is True


async def test_claims_sensor_without_tracker():
    sensor = DelayRepayClaimsSensor(_coordinator(None), _entry())
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


async def test_eligible_binary_sensor_follows_todays_journeys(freezer):
    freezer.move_to(NOW)
    with fake_storage():
        tracker, _ = await make_tracker()
        sensor = DelayRepayEligibleSensor(_coordinator(tracker), _entry())
        assert sensor.unique_id == "entry1_delay_repay_eligible"
        assert sensor.is_on is False
        assert sensor.extra_state_attributes == {"journeys_today": 0, "latest": None}

        await tracker.async_observe(single_leg([late_service()]), NOW)
        assert sensor.is_on is True
        attrs = sensor.extra_state_attributes
        assert attrs["journeys_today"] == 1
        assert attrs["latest"]["delay_minutes"] == 17

        # Claiming it turns the sensor off
        await tracker.async_set_status(
            ClaimStatus.CLAIMED, keys=["2026-10-08|1|9494208WHYTELF"]
        )
        assert sensor.is_on is False


async def test_eligible_binary_sensor_ignores_earlier_days(freezer):
    freezer.move_to(NOW + timedelta(days=1))
    with fake_storage():
        tracker, _ = await make_tracker()
        await tracker.async_observe(single_leg([late_service()]), NOW)
        await tracker.async_maintain(NOW + timedelta(minutes=40))
        sensor = DelayRepayEligibleSensor(_coordinator(tracker), _entry())
        assert sensor.is_on is False


async def test_eligible_binary_sensor_without_tracker():
    sensor = DelayRepayEligibleSensor(_coordinator(None), _entry())
    assert sensor.is_on is False
    assert sensor.extra_state_attributes == {}


async def test_claims_sensor_exposes_entry_id_for_cards():
    """Cards need the entry ID to call the mark-claimed and dismiss services."""
    with fake_storage():
        tracker, _ = await make_tracker()
        sensor = DelayRepayClaimsSensor(_coordinator(tracker), _entry())
        assert sensor.extra_state_attributes["entry_id"] == "entry1"
