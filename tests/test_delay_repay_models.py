"""Tests for Delay Repay models and arrival-delay rules."""

from __future__ import annotations

import pytest

from custom_components.my_rail_commute.delay_repay.models import (
    ArrivalAssessment,
    arrival_delay_minutes,
    assess_arrival,
    is_on_time_text,
    parse_clock,
    signed_minutes_between,
    tier_for_delay,
)

DEFAULT_TIERS = (15, 30, 60, 120)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("00:00", 0),
        ("07:42", 7 * 60 + 42),
        ("23:59", 23 * 60 + 59),
        (" 08:05 ", 8 * 60 + 5),
        ("24:00", None),
        ("12:60", None),
        ("7:42", None),
        ("On time", None),
        ("Delayed", None),
        ("", None),
        (None, None),
        (742, None),
    ],
)
def test_parse_clock(value, expected):
    assert parse_clock(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("On time", True),
        ("on time", True),
        (" On Time ", True),
        ("22:50", False),
        ("Delayed", False),
        (None, False),
    ],
)
def test_is_on_time_text(value, expected):
    assert is_on_time_text(value) is expected


@pytest.mark.parametrize(
    ("scheduled", "actual", "expected"),
    [
        ("22:53", "23:05", 12),
        ("22:38", "22:50", 12),
        ("10:00", "10:00", 0),
        ("10:00", "09:58", -2),
        # Midnight wrap-around
        ("23:55", "00:03", 8),
        ("00:02", "23:58", -4),
        ("23:59", "00:00", 1),
        # Invalid input
        ("23:55", "On time", None),
        ("bad", "10:00", None),
    ],
)
def test_signed_minutes_between(scheduled, actual, expected):
    assert signed_minutes_between(scheduled, actual) == expected


@pytest.mark.parametrize(
    ("scheduled", "arrival", "expected"),
    [
        # Real responses: late actual, and "On time" markers
        ("22:53", "23:05", 12),
        ("22:57", "On time", 0),
        ("21:30", "On time", 0),
        # Early arrivals are clamped to zero
        ("10:00", "09:57", 0),
        # Midnight wrap-around
        ("23:55", "00:20", 25),
        # Indeterminate
        ("22:53", None, None),
        ("22:53", "", None),
        ("22:53", "Delayed", None),
        ("22:53", "Cancelled", None),
        (None, "23:05", None),
        # "On time" needs no valid schedule
        (None, "On time", 0),
    ],
)
def test_arrival_delay_minutes(scheduled, arrival, expected):
    assert arrival_delay_minutes(scheduled, arrival) == expected


@pytest.mark.parametrize(
    ("delay", "expected"),
    [
        (None, None),
        (0, None),
        (14, None),
        (15, 15),
        (29, 15),
        (30, 30),
        (59, 30),
        (60, 60),
        (119, 60),
        (120, 120),
        (500, 120),
    ],
)
def test_tier_for_delay_default_scheme(delay, expected):
    assert tier_for_delay(delay, DEFAULT_TIERS) == expected


def test_tier_for_delay_custom_and_unsorted_thresholds():
    assert tier_for_delay(35, [60, 30]) == 30
    assert tier_for_delay(29, [60, 30]) is None


def test_tier_for_delay_ignores_non_positive_thresholds():
    assert tier_for_delay(0, [0, -5, 15]) is None
    assert tier_for_delay(20, [0, -5, 15]) == 15


def test_tier_for_delay_no_thresholds():
    assert tier_for_delay(90, []) is None


def test_tier_for_delay_accepts_generators():
    assert tier_for_delay(31, (t for t in (15, 30))) == 30


def test_assess_arrival_late_below_threshold_is_not_claimable():
    # The 12-minute late example: under the usual 15-minute threshold
    result = assess_arrival(
        "22:53", "23:05", is_cancelled=False, thresholds=DEFAULT_TIERS
    )
    assert result == ArrivalAssessment(delay_minutes=12, tier=None, is_cancelled=False)
    assert result.claimable is False


def test_assess_arrival_meets_threshold():
    result = assess_arrival(
        "22:53", "23:10", is_cancelled=False, thresholds=DEFAULT_TIERS
    )
    assert result.delay_minutes == 17
    assert result.tier == 15
    assert result.claimable is True


def test_assess_arrival_lower_thresholds_make_12_minutes_claimable():
    result = assess_arrival("22:53", "23:05", is_cancelled=False, thresholds=(10, 20))
    assert result.tier == 10
    assert result.claimable is True


def test_assess_arrival_on_time_is_not_claimable():
    result = assess_arrival(
        "22:57", "On time", is_cancelled=False, thresholds=DEFAULT_TIERS
    )
    assert result.delay_minutes == 0
    assert result.claimable is False


def test_assess_arrival_unknown_arrival_is_not_claimable():
    result = assess_arrival("22:53", None, is_cancelled=False, thresholds=DEFAULT_TIERS)
    assert result.delay_minutes is None
    assert result.claimable is False


def test_assess_arrival_cancelled_is_claimable_without_delay_or_tier():
    result = assess_arrival(
        "22:53", "23:05", is_cancelled=True, thresholds=DEFAULT_TIERS
    )
    assert result.is_cancelled is True
    assert result.delay_minutes is None
    assert result.tier is None
    assert result.claimable is True


def test_assess_arrival_across_midnight():
    result = assess_arrival(
        "23:50", "00:25", is_cancelled=False, thresholds=DEFAULT_TIERS
    )
    assert result.delay_minutes == 35
    assert result.tier == 30
