"""Tests for coordinator threshold validation."""
from __future__ import annotations

import logging
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.my_rail_commute.const import (
    CONF_DESTINATION,
    CONF_MAJOR_DELAY_THRESHOLD,
    CONF_MINOR_DELAY_THRESHOLD,
    CONF_NIGHT_UPDATES,
    CONF_NUM_SERVICES,
    CONF_ORIGIN,
    CONF_SEVERE_DELAY_THRESHOLD,
    CONF_TIME_WINDOW,
    DEFAULT_MAJOR_DELAY_THRESHOLD,
    DEFAULT_MINOR_DELAY_THRESHOLD,
    DEFAULT_SEVERE_DELAY_THRESHOLD,
)
from custom_components.my_rail_commute.coordinator import (
    NationalRailDataUpdateCoordinator,
)


def _make_config(severe=15, major=10, minor=3):
    """Build a config dict with the given thresholds."""
    return {
        CONF_API_KEY: "test_key",
        CONF_ORIGIN: "PAD",
        CONF_DESTINATION: "RDG",
        CONF_TIME_WINDOW: 60,
        CONF_NUM_SERVICES: 3,
        CONF_NIGHT_UPDATES: True,
        CONF_SEVERE_DELAY_THRESHOLD: severe,
        CONF_MAJOR_DELAY_THRESHOLD: major,
        CONF_MINOR_DELAY_THRESHOLD: minor,
    }


@pytest.mark.parametrize(
    ("severe", "major", "minor"),
    [
        (15, 10, 3),   # defaults
        (60, 30, 1),   # wide spread
        (5, 5, 5),     # all equal (valid: severe >= major >= minor >= 1)
        (1, 1, 1),     # minimum values
    ],
)
async def test_valid_thresholds_are_kept(
    hass: HomeAssistant,
    severe: int,
    major: int,
    minor: int,
) -> None:
    """Test that valid threshold hierarchies are preserved."""
    test_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=dt_util.UTC)
    with patch(
        "custom_components.my_rail_commute.coordinator.dt_util.now",
        return_value=test_time,
    ):
        api = AsyncMock()
        coordinator = NationalRailDataUpdateCoordinator(
            hass, api, _make_config(severe, major, minor)
        )

    assert coordinator.severe_delay_threshold == severe
    assert coordinator.major_delay_threshold == major
    assert coordinator.minor_delay_threshold == minor


@pytest.mark.parametrize(
    ("severe", "major", "minor"),
    [
        (5, 10, 3),    # severe < major
        (15, 3, 10),   # major < minor
        (15, 10, 0),   # minor below MIN_DELAY_THRESHOLD (1)
        (0, 0, 0),     # all below minimum
        (-1, -2, -3),  # negative values
        (10, 15, 3),   # severe < major (inverted top two)
        (3, 2, 5),     # minor > severe
    ],
)
async def test_invalid_thresholds_reset_to_defaults(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    severe: int,
    major: int,
    minor: int,
) -> None:
    """Test that invalid threshold hierarchies are reset to defaults."""
    test_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=dt_util.UTC)
    with patch(
        "custom_components.my_rail_commute.coordinator.dt_util.now",
        return_value=test_time,
    ):
        api = AsyncMock()
        with caplog.at_level(logging.WARNING):
            coordinator = NationalRailDataUpdateCoordinator(
                hass, api, _make_config(severe, major, minor)
            )

    assert coordinator.severe_delay_threshold == DEFAULT_SEVERE_DELAY_THRESHOLD
    assert coordinator.major_delay_threshold == DEFAULT_MAJOR_DELAY_THRESHOLD
    assert coordinator.minor_delay_threshold == DEFAULT_MINOR_DELAY_THRESHOLD
    assert "Invalid delay threshold hierarchy detected" in caplog.text
