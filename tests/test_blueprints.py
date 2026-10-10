"""Tests for the shipped Home Assistant automation blueprints."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
from homeassistant.components.blueprint import models
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import load_yaml_dict
import pytest
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

BLUEPRINT_DIR = (
    Path(__file__).parent.parent / "blueprints" / "automation" / "my_rail_commute"
)
BLUEPRINT_FILES = sorted(BLUEPRINT_DIR.glob("*.yaml"))
SOURCE_URL_PREFIX = (
    "https://github.com/adamf83/my-rail-commute/blob/main/"
    "blueprints/automation/my_rail_commute/"
)
LONDON = ZoneInfo("Europe/London")

# Replaces the default notification so tests can read what would be sent.
NOTIFY = [
    {
        "action": "test.notify",
        "data": {"title": "{{ title }}", "message": "{{ message }}"},
    }
]


def _load(path: Path) -> models.Blueprint:
    return models.Blueprint(
        load_yaml_dict(str(path)),
        expected_domain="automation",
        path=path.name,
        schema=AUTOMATION_BLUEPRINT_SCHEMA,
    )


@pytest.mark.parametrize("path", BLUEPRINT_FILES, ids=lambda p: p.stem)
def test_blueprint_is_valid(path: Path) -> None:
    """Each blueprint parses and follows the project conventions."""
    blueprint = _load(path)
    assert blueprint.name.startswith("My Rail Commute - ")
    assert blueprint.metadata["source_url"] == SOURCE_URL_PREFIX + path.name
    assert blueprint.metadata["author"] == "adamf83"
    # Every input is a real, selectable input with a name
    assert blueprint.inputs
    for key, value in blueprint.inputs.items():
        assert value is None or "name" in value, key
    # Entity pickers are limited to this integration's entities
    for key, value in blueprint.inputs.items():
        entity = (value or {}).get("selector", {}).get("entity")
        if entity and key not in ("person",):
            assert {"integration": "my_rail_commute"}.items() <= {
                k: v for f in entity["filter"] for k, v in f.items()
            }.items(), key
    # The default notify action must render with the documented variables
    assert "notify_action" in blueprint.inputs


def test_blueprint_set_is_complete() -> None:
    """Guard against a blueprint being added without being tested/documented."""
    assert {p.stem for p in BLUEPRINT_FILES} == {
        "commute_status_change",
        "pre_departure_reminder",
        "time_to_leave",
        "disruption_alert",
        "platform_change_alert",
        "connection_alert",
        "delay_repay_alert",
        "delay_repay_reminder",
    }


@pytest.fixture
async def notify_calls(hass: HomeAssistant):
    """Install the blueprints in the test config dir and capture notifications."""
    target = Path(hass.config.path("blueprints", "automation", "my_rail_commute"))
    target.mkdir(parents=True, exist_ok=True)
    for path in BLUEPRINT_FILES:
        shutil.copy(path, target / path.name)
    await hass.config.async_set_time_zone("Europe/London")
    return async_mock_service(hass, "test", "notify")


async def _setup(hass: HomeAssistant, name: str, inputs: dict) -> None:
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {
                    "path": f"my_rail_commute/{name}.yaml",
                    "input": {"notify_action": NOTIFY, **inputs},
                }
            }
        },
    )
    await hass.async_block_till_done()


# Monday 12 October 2026, 07:30 BST
MONDAY_0730 = datetime(2026, 10, 12, 7, 30, tzinfo=LONDON)


async def _tick(hass: HomeAssistant, freezer, when: datetime) -> None:
    freezer.move_to(when)
    async_fire_time_changed(hass, when)
    await hass.async_block_till_done()


async def test_status_change_alerts_and_recovery(hass, notify_calls) -> None:
    """Status worsening alerts; recovery only when asked."""
    sensor = "sensor.morning_status"
    hass.states.async_set(sensor, "Normal")
    await _setup(
        hass,
        "commute_status_change",
        {"status_sensor": sensor, "notify_recovery": True},
    )

    hass.states.async_set(
        sensor,
        "Major Delays",
        {"max_delay_minutes": 12, "major_delays_count": 1, "cancelled_count": 0},
    )
    await hass.async_block_till_done()
    assert len(notify_calls) == 1
    assert notify_calls[0].data["title"] == "Commute status: Major Delays"
    assert "Worst delay: 12 min" in notify_calls[0].data["message"]

    hass.states.async_set(sensor, "Normal")
    await hass.async_block_till_done()
    assert len(notify_calls) == 2
    assert notify_calls[1].data["title"] == "Commute back to normal"


async def test_status_change_ignores_unavailable_and_unselected(
    hass, notify_calls
) -> None:
    """Restarts (unavailable) and statuses the user did not pick don't alert."""
    sensor = "sensor.morning_status"
    hass.states.async_set(sensor, "unavailable")
    await _setup(
        hass,
        "commute_status_change",
        {"status_sensor": sensor, "alert_statuses": ["Critical"]},
    )
    hass.states.async_set(sensor, "Critical")  # from unavailable
    await hass.async_block_till_done()
    hass.states.async_set(sensor, "Minor Delays")  # not selected
    await hass.async_block_till_done()
    hass.states.async_set(sensor, "Normal")  # recovery is off
    await hass.async_block_till_done()
    assert notify_calls == []


async def test_disruption_alert(hass, notify_calls) -> None:
    """Disruption alerts include the reasons; clearing is opt-in."""
    sensor = "binary_sensor.morning_has_disruption"
    hass.states.async_set(sensor, "off")
    await _setup(
        hass, "disruption_alert", {"disruption_sensor": sensor, "notify_clear": True}
    )
    hass.states.async_set(
        sensor,
        "on",
        {
            "current_status": "Critical",
            "cancelled_count": 1,
            "max_delay_minutes": 0,
            "disruption_reasons": ["Signalling problem", "Late running train"],
        },
    )
    await hass.async_block_till_done()
    assert notify_calls[0].data["title"] == "Commute disruption: Critical"
    assert "Signalling problem. Late running train." in notify_calls[0].data["message"]
    assert "1 cancelled" in notify_calls[0].data["message"]

    hass.states.async_set(sensor, "off")
    await hass.async_block_till_done()
    assert notify_calls[1].data["title"] == "Commute back to normal"


async def test_disruption_clear_off_by_default(hass, notify_calls) -> None:
    """No clear notification unless requested."""
    sensor = "binary_sensor.morning_has_disruption"
    hass.states.async_set(sensor, "on")
    await _setup(hass, "disruption_alert", {"disruption_sensor": sensor})
    hass.states.async_set(sensor, "off")
    await hass.async_block_till_done()
    assert notify_calls == []


async def test_active_days_respected(hass, freezer, notify_calls) -> None:
    """A notification outside the chosen days is suppressed."""
    freezer.move_to(datetime(2026, 10, 10, 9, 0, tzinfo=LONDON))  # Saturday
    sensor = "binary_sensor.morning_has_disruption"
    hass.states.async_set(sensor, "off")
    await _setup(
        hass,
        "disruption_alert",
        {"disruption_sensor": sensor, "active_days": ["mon", "tue"]},
    )
    hass.states.async_set(sensor, "on", {"current_status": "Critical"})
    await hass.async_block_till_done()
    assert notify_calls == []


async def test_platform_change_alert(hass, notify_calls) -> None:
    """A moved platform alerts; first announcement and unflagged moves do not."""
    sensor = "sensor.morning_next_train"
    hass.states.async_set(
        sensor,
        "On Time",
        {
            "departure_time": "08:15",
            "platform": "",
            "platform_changed": False,
            "previous_platform": None,
        },
    )
    await _setup(hass, "platform_change_alert", {"train_sensors": [sensor]})

    # Platform first announced: flagged by the integration, but not a "change"
    hass.states.async_set(
        sensor,
        "On Time",
        {
            "departure_time": "08:15",
            "platform": "3",
            "platform_changed": True,
            "previous_platform": "",
        },
    )
    await hass.async_block_till_done()
    assert notify_calls == []

    # Genuine change
    hass.states.async_set(
        sensor,
        "On Time",
        {
            "departure_time": "08:15",
            "platform": "5",
            "platform_changed": True,
            "previous_platform": "3",
        },
    )
    await hass.async_block_till_done()
    assert len(notify_calls) == 1
    assert "moved from platform 3 to platform 5" in notify_calls[0].data["message"]

    # Next train rolls in: platform differs but the integration didn't flag it
    hass.states.async_set(
        sensor,
        "On Time",
        {
            "departure_time": "08:30",
            "platform": "1",
            "platform_changed": False,
            "previous_platform": None,
        },
    )
    await hass.async_block_till_done()
    assert len(notify_calls) == 1


async def test_platform_change_first_announcement_opt_in(hass, notify_calls) -> None:
    """First announcements alert when the option is enabled."""
    sensor = "sensor.morning_next_train"
    hass.states.async_set(sensor, "On Time", {"platform": ""})
    await _setup(
        hass,
        "platform_change_alert",
        {"train_sensors": [sensor], "include_first_announcement": True},
    )
    hass.states.async_set(
        sensor,
        "On Time",
        {
            "departure_time": "08:15",
            "platform": "3",
            "platform_changed": True,
            "previous_platform": "",
        },
    )
    await hass.async_block_till_done()
    assert len(notify_calls) == 1
    assert "will now leave from platform 3" in notify_calls[0].data["message"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("Tight Connection", "is tight"),
        ("Delayed Connection", "is delayed"),
        ("Missed Connection", "not expected to make"),
    ],
)
async def test_connection_alert(hass, notify_calls, status, expected) -> None:
    """Each worsened connection status produces a tailored message."""
    sensor = "sensor.work_connection1_status"
    hass.states.async_set(sensor, "Connection OK")
    await _setup(hass, "connection_alert", {"connection_sensors": [sensor]})
    hass.states.async_set(
        sensor,
        status,
        {
            "station_name": "Reading",
            "connecting_summary": "Catching the 08:45 to Oxford (3m buffer)",
        },
    )
    await hass.async_block_till_done()
    assert len(notify_calls) == 1
    assert notify_calls[0].data["title"] == f"{status} at Reading"
    assert expected in notify_calls[0].data["message"]
    assert "Catching the 08:45 to Oxford" in notify_calls[0].data["message"]


async def test_connection_alert_ignores_connection_ok(hass, notify_calls) -> None:
    """Recovering to Connection OK doesn't alert."""
    sensor = "sensor.work_connection1_status"
    hass.states.async_set(sensor, "Missed Connection")
    await _setup(hass, "connection_alert", {"connection_sensors": [sensor]})
    hass.states.async_set(sensor, "Connection OK")
    await hass.async_block_till_done()
    assert notify_calls == []


async def test_delay_repay_alert(hass, notify_calls) -> None:
    """Eligibility alerts carry the delay, deadline and claim link."""
    sensor = "binary_sensor.morning_delay_repay_eligible"
    hass.states.async_set(sensor, "off")
    await _setup(hass, "delay_repay_alert", {"eligible_sensor": sensor})
    hass.states.async_set(
        sensor,
        "on",
        {
            "journeys_today": 1,
            "latest": {
                "scheduled_departure": "08:15",
                "delay_minutes": 32,
                "is_cancelled": False,
                "confirmation": "estimated",
                "claim_deadline": "2026-11-09",
                "claim_url": "https://example.com/claim",
            },
        },
    )
    await hass.async_block_till_done()
    message = notify_calls[0].data["message"]
    assert "08:15 train is 32 min late" in message
    assert "Estimated" in message
    assert "Claim by 2026-11-09" in message
    assert "https://example.com/claim" in message


async def test_delay_repay_alert_cancelled(hass, notify_calls) -> None:
    """A cancellation is described as such."""
    sensor = "binary_sensor.morning_delay_repay_eligible"
    hass.states.async_set(sensor, "off")
    await _setup(hass, "delay_repay_alert", {"eligible_sensor": sensor})
    hass.states.async_set(
        sensor,
        "on",
        {
            "journeys_today": 2,
            "latest": {
                "scheduled_departure": "17:40",
                "is_cancelled": True,
                "confirmation": "confirmed",
            },
        },
    )
    await hass.async_block_till_done()
    message = notify_calls[0].data["message"]
    assert "17:40 train was cancelled" in message
    assert "Estimated" not in message
    assert "2 journeys today" in message


async def test_delay_repay_reminder(hass, freezer, notify_calls) -> None:
    """The weekly reminder fires only when there are claims to make."""
    sensor = "sensor.morning_delay_repay_claims"
    sunday = datetime(2026, 10, 11, 17, 59, tzinfo=LONDON)
    freezer.move_to(sunday)
    hass.states.async_set(
        sensor,
        "0",
        {"oldest_unclaimed_date": None, "oldest_claim_deadline": None},
    )
    await _setup(hass, "delay_repay_reminder", {"claims_sensor": sensor})

    await _tick(hass, freezer, sunday.replace(hour=18, minute=0, second=1))
    assert notify_calls == []  # nothing to claim

    hass.states.async_set(
        sensor,
        "3",
        {"oldest_unclaimed_date": "2026-10-02", "oldest_claim_deadline": "2026-10-30"},
    )
    await _tick(
        hass, freezer, sunday.replace(hour=18, minute=0, second=1) + timedelta(days=7)
    )
    # Following Sunday at 18:00 is a Sunday, so it fires
    assert len(notify_calls) == 1
    assert notify_calls[0].data["title"] == "3 unclaimed Delay Repay journeys"
    assert "Claim it by 2026-10-30" in notify_calls[0].data["message"]


async def test_delay_repay_reminder_wrong_day(hass, freezer, notify_calls) -> None:
    """The reminder honours the chosen days."""
    sensor = "sensor.morning_delay_repay_claims"
    monday = datetime(2026, 10, 12, 17, 59, tzinfo=LONDON)
    freezer.move_to(monday)
    hass.states.async_set(sensor, "3", {})
    await _setup(hass, "delay_repay_reminder", {"claims_sensor": sensor})
    await _tick(hass, freezer, monday.replace(hour=18, minute=0, second=1))
    assert notify_calls == []


def _next_train(departure: str | None, **extra) -> dict:
    attrs = {
        "platform": "4",
        "delay_minutes": 0,
        "is_cancelled": False,
        "calling_points": ["Slough", "Reading"],
    }
    if departure:
        attrs["departure_time"] = departure
    return {**attrs, **extra}


async def test_pre_departure_reminder(hass, freezer, notify_calls) -> None:
    """Fires once when the train comes within the lead time."""
    sensor = "sensor.morning_next_train"
    freezer.move_to(MONDAY_0730)
    hass.states.async_set(sensor, "On Time", _next_train("08:00"))
    await _setup(
        hass,
        "pre_departure_reminder",
        {"next_train_sensor": sensor, "minutes_before": 10},
    )

    await _tick(hass, freezer, MONDAY_0730.replace(minute=45))
    assert notify_calls == []  # 15 minutes away

    await _tick(hass, freezer, MONDAY_0730.replace(minute=51))
    assert len(notify_calls) == 1
    message = notify_calls[0].data["message"]
    assert "08:00 train to Reading" in message
    assert "platform 4" in message

    await _tick(hass, freezer, MONDAY_0730.replace(minute=52))
    assert len(notify_calls) == 1  # no repeat while it stays true


async def test_pre_departure_reminder_follows_delay(
    hass, freezer, notify_calls
) -> None:
    """A delayed train (later departure_time) reminds later, mentioning the delay."""
    sensor = "sensor.morning_next_train"
    freezer.move_to(MONDAY_0730)
    hass.states.async_set(sensor, "Delayed", _next_train("08:20", delay_minutes=20))
    await _setup(
        hass,
        "pre_departure_reminder",
        {"next_train_sensor": sensor, "minutes_before": 10},
    )
    await _tick(hass, freezer, MONDAY_0730.replace(minute=51))
    assert notify_calls == []
    await _tick(hass, freezer, MONDAY_0730.replace(minute=11, hour=8))
    assert len(notify_calls) == 1
    assert "running 20 min late" in notify_calls[0].data["message"]


async def test_pre_departure_reminder_skips_cancelled_and_no_service(
    hass, freezer, notify_calls
) -> None:
    """Cancelled trains and 'No service' (no departure_time) never remind."""
    sensor = "sensor.morning_next_train"
    freezer.move_to(MONDAY_0730)
    hass.states.async_set(sensor, "Cancelled", _next_train("08:00", is_cancelled=True))
    await _setup(
        hass,
        "pre_departure_reminder",
        {"next_train_sensor": sensor, "minutes_before": 10},
    )
    await _tick(hass, freezer, MONDAY_0730.replace(minute=51))
    assert notify_calls == []

    hass.states.async_set(sensor, "No service", {"status": "no_service"})
    await _tick(hass, freezer, MONDAY_0730.replace(minute=52))
    assert notify_calls == []


async def test_pre_departure_reminder_across_midnight(
    hass, freezer, notify_calls
) -> None:
    """A 00:10 departure is recognised the previous evening (no midnight bug)."""
    sensor = "sensor.late_next_train"
    late = datetime(2026, 10, 12, 23, 30, tzinfo=LONDON)  # Monday
    freezer.move_to(late)
    hass.states.async_set(sensor, "On Time", _next_train("00:10"))
    await _setup(
        hass,
        "pre_departure_reminder",
        {"next_train_sensor": sensor, "minutes_before": 30},
    )
    await _tick(hass, freezer, late.replace(minute=35))
    assert notify_calls == []  # 35 minutes away
    await _tick(hass, freezer, late.replace(minute=45))
    assert len(notify_calls) == 1  # 25 minutes away, across midnight


async def test_pre_departure_reminder_weekend_excluded(
    hass, freezer, notify_calls
) -> None:
    """Default weekday filter suppresses weekend reminders."""
    sensor = "sensor.morning_next_train"
    saturday = datetime(2026, 10, 10, 7, 30, tzinfo=LONDON)
    freezer.move_to(saturday)
    hass.states.async_set(sensor, "On Time", _next_train("08:00"))
    await _setup(
        hass,
        "pre_departure_reminder",
        {"next_train_sensor": sensor, "minutes_before": 10},
    )
    await _tick(hass, freezer, saturday.replace(minute=51))
    assert notify_calls == []


async def test_time_to_leave(hass, freezer, notify_calls) -> None:
    """Alert comes at departure minus travel time minus buffer."""
    sensor = "sensor.morning_next_train"
    freezer.move_to(MONDAY_0730)
    hass.states.async_set(sensor, "On Time", _next_train("08:30"))
    await _setup(
        hass,
        "time_to_leave",
        {"next_train_sensor": sensor, "travel_minutes": 20, "buffer_minutes": 5},
    )
    await _tick(hass, freezer, MONDAY_0730.replace(hour=7, minute=58))
    assert notify_calls == []  # leave-by is 08:05, 32 minutes before... not yet
    await _tick(hass, freezer, MONDAY_0730.replace(hour=8, minute=6))
    assert len(notify_calls) == 1
    assert notify_calls[0].data["message"] == (
        "Leave now to catch the 08:30 train (platform 4)."
    )


async def test_time_to_leave_only_when_home(hass, freezer, notify_calls) -> None:
    """The optional person condition suppresses the alert when away."""
    sensor = "sensor.morning_next_train"
    freezer.move_to(MONDAY_0730)
    hass.states.async_set("person.adam", "not_home")
    hass.states.async_set(sensor, "On Time", _next_train("08:30"))
    await _setup(
        hass,
        "time_to_leave",
        {
            "next_train_sensor": sensor,
            "travel_minutes": 20,
            "buffer_minutes": 5,
            "person": "person.adam",
        },
    )
    await _tick(hass, freezer, MONDAY_0730.replace(hour=8, minute=6))
    assert notify_calls == []


async def test_time_to_leave_mentions_delay(hass, freezer, notify_calls) -> None:
    """Delayed trains explain the new departure time."""
    sensor = "sensor.morning_next_train"
    freezer.move_to(MONDAY_0730)
    hass.states.async_set(sensor, "Delayed", _next_train("08:40", delay_minutes=10))
    await _setup(
        hass,
        "time_to_leave",
        {"next_train_sensor": sensor, "travel_minutes": 20, "buffer_minutes": 5},
    )
    await _tick(hass, freezer, MONDAY_0730.replace(hour=8, minute=16))
    assert len(notify_calls) == 1
    assert "10 min late and now leaves at 08:40" in notify_calls[0].data["message"]
