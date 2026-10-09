"""Tests for the Delay Repay tracker lifecycle."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from custom_components.my_rail_commute.delay_repay.models import (
    ClaimStatus,
    Confirmation,
)
from custom_components.my_rail_commute.delay_repay.schemes import build_scheme_set
from custom_components.my_rail_commute.delay_repay.tracker import DelayRepayTracker

from .delay_repay_helpers import (
    NOW,
    fake_storage,
    late_service,
    make_service,
    make_tracker,
    multi_leg,
    single_leg,
)

KEY = "2026-10-08|1|9494208WHYTELF"


async def test_late_train_creates_pending_record():
    with fake_storage():
        tracker, _ = await make_tracker()
        changed = await tracker.async_observe(single_leg([late_service()]), NOW)

        assert changed is True
        (record,) = tracker.records()
        assert record.key == KEY
        assert record.status is ClaimStatus.PENDING
        assert record.confirmation is Confirmation.ESTIMATED
        assert record.delay_minutes == 17
        assert record.tier == 15
        assert (record.origin, record.destination, record.leg) == ("WYT", "LBG", 1)
        assert record.operator == "Southern"
        assert record.delay_reason == "a speed restriction"
        # Live until scheduled arrival (22:53) plus the 10 minute grace period
        assert record.live_until == "2026-10-08T23:03:00+00:00"


async def test_delay_below_threshold_is_not_recorded():
    with fake_storage():
        tracker, _ = await make_tracker()
        # 22:53 -> 23:05 is 12 minutes, below the 15 minute threshold
        await tracker.async_observe(single_leg([late_service("23:05")]), NOW)
        assert tracker.records() == []


async def test_on_time_train_is_not_recorded():
    with fake_storage():
        tracker, _ = await make_tracker()
        await tracker.async_observe(single_leg([make_service()]), NOW)
        assert tracker.records() == []


async def test_cancelled_train_is_recorded_without_delay():
    with fake_storage():
        tracker, _ = await make_tracker()
        service = make_service(
            is_cancelled=True,
            status="cancelled",
            estimated_arrival="22:53",
            cancellation_reason="a fault with the signalling",
        )
        await tracker.async_observe(single_leg([service]), NOW)
        (record,) = tracker.records()
        assert record.is_cancelled is True
        assert record.delay_minutes is None
        assert record.tier is None
        assert record.delay_reason == "a fault with the signalling"


async def test_cancelled_train_without_arrival_times_is_recorded():
    with fake_storage():
        tracker, _ = await make_tracker()
        service = make_service(
            is_cancelled=True, scheduled_arrival=None, estimated_arrival=None
        )
        await tracker.async_observe(single_leg([service]), NOW)
        (record,) = tracker.records()
        assert record.is_cancelled is True
        # A cancellation is a fact on the board, not a forecast
        assert record.confirmation is Confirmation.CONFIRMED
        assert record.live_until == "2026-10-08T22:50:00+00:00"


async def test_pending_record_follows_the_forecast():
    with fake_storage():
        tracker, _ = await make_tracker()
        await tracker.async_observe(single_leg([late_service("23:10")]), NOW)
        later = NOW + timedelta(minutes=2)
        await tracker.async_observe(single_leg([late_service("23:25")]), later)

        (record,) = tracker.records()
        assert record.delay_minutes == 32
        assert record.tier == 30
        assert record.first_seen == NOW.isoformat()
        assert record.last_updated == later.isoformat()


async def test_pending_record_is_dropped_when_the_delay_recovers():
    with fake_storage():
        tracker, _ = await make_tracker()
        await tracker.async_observe(single_leg([late_service("23:10")]), NOW)
        assert len(tracker.records()) == 1
        await tracker.async_observe(
            single_leg([late_service("22:58")]), NOW + timedelta(minutes=2)
        )
        assert tracker.records() == []


async def test_unchanged_observation_does_not_write():
    with fake_storage() as backing:
        tracker, _ = await make_tracker()
        await tracker.async_observe(single_leg([late_service()]), NOW)
        saves = backing.save_count
        changed = await tracker.async_observe(
            single_leg([late_service()]), NOW + timedelta(minutes=2)
        )
        assert changed is False
        assert backing.save_count == saves


async def test_finished_journey_is_frozen_as_eligible():
    with fake_storage():
        tracker, _ = await make_tracker()
        await tracker.async_observe(single_leg([late_service()]), NOW)
        # Train has left the board; a later poll with no services freezes it
        await tracker.async_observe(single_leg([]), NOW + timedelta(minutes=34))
        (record,) = tracker.records()
        assert record.status is ClaimStatus.ELIGIBLE
        assert tracker.unclaimed() == [record]


async def test_frozen_record_ignores_later_observations():
    with fake_storage():
        tracker, _ = await make_tracker()
        await tracker.async_observe(single_leg([late_service("23:10")]), NOW)
        after = NOW + timedelta(minutes=40)
        await tracker.async_observe(single_leg([late_service("22:53")]), after)
        (record,) = tracker.records()
        assert record.status is ClaimStatus.ELIGIBLE
        assert record.delay_minutes == 17


@pytest.mark.parametrize("status", [ClaimStatus.CLAIMED, ClaimStatus.DISMISSED])
async def test_user_handled_records_are_not_reopened(status):
    with fake_storage():
        tracker, _ = await make_tracker()
        await tracker.async_observe(single_leg([late_service("23:10")]), NOW)
        await tracker.async_set_status(status, keys=[KEY], now=NOW)
        await tracker.async_observe(
            single_leg([late_service("23:25")]), NOW + timedelta(minutes=1)
        )
        (record,) = tracker.records()
        assert record.status is status
        assert record.delay_minutes == 17


async def test_eligible_record_expires_after_the_claim_window():
    with fake_storage():
        tracker, _ = await make_tracker(claim_window_days=28)
        await tracker.async_observe(single_leg([late_service()]), NOW)
        await tracker.async_maintain(NOW + timedelta(minutes=40))
        assert tracker.records()[0].status is ClaimStatus.ELIGIBLE

        await tracker.async_maintain(NOW + timedelta(days=29))
        assert tracker.records()[0].status is ClaimStatus.EXPIRED
        assert tracker.unclaimed() == []


async def test_pending_record_found_after_the_window_goes_straight_to_expired():
    with fake_storage():
        tracker, _ = await make_tracker(claim_window_days=7)
        await tracker.async_observe(single_leg([late_service()]), NOW)
        await tracker.async_maintain(NOW + timedelta(days=9))
        assert tracker.records()[0].status is ClaimStatus.EXPIRED


async def test_old_records_are_pruned():
    with fake_storage():
        tracker, store = await make_tracker(claim_window_days=28)
        await tracker.async_observe(single_leg([late_service()]), NOW)
        # window (28) + retention (28) days later
        await tracker.async_maintain(NOW + timedelta(days=57))
        assert len(store) == 0


async def test_multi_leg_records_each_leg_separately():
    with fake_storage():
        tracker, _ = await make_tracker()
        leg1 = late_service(service_id="LEG1ID")
        leg2 = late_service(
            service_id="LEG2ID",
            scheduled_departure="23:00",
            scheduled_arrival="23:30",
            estimated_arrival="23:50",
        )
        data = multi_leg([("WYT", "ECR", [leg1]), ("ECR", "VIC", [leg2])])
        await tracker.async_observe(data, NOW)

        records = {r.leg: r for r in tracker.records()}
        assert set(records) == {1, 2}
        assert (records[1].origin, records[1].destination) == ("WYT", "ECR")
        assert (records[2].origin, records[2].destination) == ("ECR", "VIC")
        assert records[2].delay_minutes == 20
        assert records[1].key != records[2].key


async def test_same_service_id_on_different_legs_is_distinct():
    with fake_storage():
        tracker, _ = await make_tracker()
        data = multi_leg(
            [
                ("WYT", "ECR", [late_service(service_id="SAME")]),
                ("ECR", "VIC", [late_service(service_id="SAME")]),
            ]
        )
        await tracker.async_observe(data, NOW)
        assert len(tracker.records()) == 2


async def test_no_destination_is_ignored():
    with fake_storage():
        tracker, _ = await make_tracker()
        await tracker.async_observe(single_leg([late_service()], destination=None), NOW)
        assert tracker.records() == []


async def test_missing_service_id_uses_origin_and_departure():
    with fake_storage():
        tracker, _ = await make_tracker()
        await tracker.async_observe(single_leg([late_service(service_id="")]), NOW)
        (record,) = tracker.records()
        assert record.key == "2026-10-08|1|WYT-22:40"


async def test_non_cancelled_service_without_arrival_times_is_skipped():
    with fake_storage():
        tracker, _ = await make_tracker()
        service = late_service(scheduled_arrival=None, estimated_arrival=None)
        await tracker.async_observe(single_leg([service]), NOW)
        assert tracker.records() == []


async def test_service_without_departure_time_is_skipped():
    with fake_storage():
        tracker, _ = await make_tracker()
        service = late_service(scheduled_departure="")
        await tracker.async_observe(single_leg([service]), NOW)
        assert tracker.records() == []


async def test_after_midnight_departure_is_dated_tomorrow():
    with fake_storage():
        tracker, _ = await make_tracker()
        now = NOW.replace(hour=23, minute=50)
        service = late_service(
            scheduled_departure="00:10",
            scheduled_arrival="00:25",
            estimated_arrival="00:45",
        )
        await tracker.async_observe(single_leg([service]), now)
        (record,) = tracker.records()
        assert record.date == "2026-10-09"
        assert record.delay_minutes == 20


async def test_operator_override_changes_the_threshold():
    with fake_storage():
        schemes = build_scheme_set("15,30", "Southern = 10,20 | https://example.com/c")
        tracker, _ = await make_tracker(schemes=schemes)
        # 12 minutes: below the default 15 but over Southern's 10
        data = single_leg(
            [
                late_service("23:05", service_id="SOUTH"),
                late_service("23:05", service_id="OTHER", operator="Thameslink"),
            ]
        )
        await tracker.async_observe(data, NOW)
        (record,) = tracker.records()
        assert record.service_id == "SOUTH"
        assert record.tier == 10
        assert tracker.claim_url(record) == "https://example.com/c"


async def test_state_survives_a_restart():
    with fake_storage():
        tracker, _ = await make_tracker()
        await tracker.async_observe(single_leg([late_service()]), NOW)
        restarted, _ = await make_tracker()
        assert [r.key for r in restarted.records()] == [KEY]


async def test_set_status_by_key_range_and_nothing():
    with fake_storage():
        tracker, _ = await make_tracker()
        await tracker.async_observe(
            single_leg([late_service(service_id="A"), late_service(service_id="B")]),
            NOW,
        )
        await tracker.async_maintain(NOW + timedelta(minutes=40))

        assert await tracker.async_set_status(ClaimStatus.CLAIMED) == []

        changed = await tracker.async_set_status(
            ClaimStatus.CLAIMED, keys=["2026-10-08|1|A", "missing"], now=NOW
        )
        assert changed == ["2026-10-08|1|A"]

        # Already claimed: not reported again
        assert (
            await tracker.async_set_status(ClaimStatus.CLAIMED, keys=["2026-10-08|1|A"])
            == []
        )

        changed = await tracker.async_set_status(
            ClaimStatus.DISMISSED,
            from_date=date(2026, 10, 8),
            to_date=date(2026, 10, 8),
        )
        assert sorted(changed) == ["2026-10-08|1|A", "2026-10-08|1|B"]
        assert {r.status for r in tracker.records()} == {ClaimStatus.DISMISSED}


async def test_set_status_date_range_bounds():
    with fake_storage():
        tracker, _ = await make_tracker()
        await tracker.async_observe(single_leg([late_service()]), NOW)
        assert (
            await tracker.async_set_status(
                ClaimStatus.CLAIMED, from_date=date(2026, 10, 9)
            )
            == []
        )
        assert (
            await tracker.async_set_status(
                ClaimStatus.CLAIMED, to_date=date(2026, 10, 7)
            )
            == []
        )
        assert await tracker.async_set_status(
            ClaimStatus.CLAIMED, to_date=date(2026, 10, 8)
        ) == [KEY]


async def test_set_status_rejects_non_user_statuses():
    with fake_storage():
        tracker, _ = await make_tracker()
        with pytest.raises(ValueError):
            await tracker.async_set_status(ClaimStatus.ELIGIBLE, keys=["x"])


async def test_queries():
    with fake_storage():
        tracker, _ = await make_tracker()
        await tracker.async_observe(single_leg([late_service()]), NOW)
        # A journey from an earlier day, already frozen
        earlier = NOW - timedelta(days=3)
        await tracker.async_observe(single_leg([late_service()]), earlier)
        await tracker.async_maintain(NOW)

        records = tracker.records()
        assert [r.date for r in records] == ["2026-10-08", "2026-10-05"]  # newest first
        assert [r.date for r in tracker.outstanding_today(NOW)] == ["2026-10-08"]
        assert tracker.claim_deadline(records[0]) == "2026-11-05"
        assert tracker.claim_url(records[0]) is None


async def test_tracker_constructor_defaults():
    with fake_storage():
        _, store = await make_tracker()
        tracker = DelayRepayTracker(store, build_scheme_set("15", ""))
        assert tracker.claim_window_days == 28
