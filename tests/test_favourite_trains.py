"""Tests for the favourite trains feature."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from homeassistant import data_entry_flow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest

from custom_components.my_rail_commute import async_cleanup_stale_entities
from custom_components.my_rail_commute.const import (
    CONF_FAVOURITE_TRAINS,
    CONF_MAJOR_DELAY_THRESHOLD,
    CONF_MINOR_DELAY_THRESHOLD,
    CONF_NIGHT_UPDATES,
    CONF_NUM_SERVICES,
    CONF_SEVERE_DELAY_THRESHOLD,
    CONF_TIME_WINDOW,
    DOMAIN,
    FAVOURITE_STATE_CANCELLED,
    FAVOURITE_STATE_DEPARTED,
    FAVOURITE_STATE_NOT_RUNNING,
    FAVOURITE_STATE_SCHEDULED,
    FAVOURITE_STATE_UPCOMING,
    MAX_FAVOURITE_TRAINS,
)
from custom_components.my_rail_commute.favourites import (
    FavouriteTrainsStore,
    build_favourite_states,
    favourite_slug,
    parse_favourite_trains,
)


def _service(std: str, delay: int = 0, cancelled: bool = False) -> dict:
    return {
        "scheduled_departure": std,
        "expected_departure": std,
        "platform": "3",
        "operator": "GWR",
        "service_id": f"svc{std}",
        "delay_minutes": delay,
        "is_cancelled": cancelled,
        "destination": "Reading",
    }


def _at(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=dt_util.DEFAULT_TIME_ZONE)


# ---- parsing -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, []),
        ("", []),
        ("08:15", ["08:15"]),
        ("17:42, 08:15", ["08:15", "17:42"]),
        ("8:15;08:15 09:00", ["08:15", "09:00"]),
        ("00:00,23:59", ["00:00", "23:59"]),
    ],
)
def test_parse_valid(text, expected) -> None:
    assert parse_favourite_trains(text) == expected


@pytest.mark.parametrize(
    "text", ["24:00", "08:60", "0815", "8", "abc", "08:15,nope", "08:5"]
)
def test_parse_invalid(text) -> None:
    with pytest.raises(ValueError):
        parse_favourite_trains(text)


def test_parse_rejects_too_many() -> None:
    text = ",".join(f"{h:02d}:00" for h in range(MAX_FAVOURITE_TRAINS + 1))
    with pytest.raises(ValueError):
        parse_favourite_trains(text)


def test_slug() -> None:
    assert favourite_slug("08:15") == "0815"


# ---- state logic ---------------------------------------------------------


def _state(favourite, services, store=None, now=8 * 60, window=60):
    out, _, _ = build_favourite_states([favourite], services, store, now, window)
    return out[0]


def test_upcoming_and_cancelled() -> None:
    assert _state("08:15", [_service("08:15", delay=4)])["state"] == (
        FAVOURITE_STATE_UPCOMING
    )
    assert _state("08:15", [_service("08:15", cancelled=True)])["state"] == (
        FAVOURITE_STATE_CANCELLED
    )


def test_scheduled_outside_window_and_not_running_inside() -> None:
    assert _state("10:00", [])["state"] == FAVOURITE_STATE_SCHEDULED
    assert _state("08:30", [])["state"] == FAVOURITE_STATE_NOT_RUNNING
    # Already past and never seen today
    assert _state("07:00", [])["state"] == FAVOURITE_STATE_NOT_RUNNING


def test_matches_only_on_scheduled_time() -> None:
    delayed = _service("08:15", delay=20)
    delayed["expected_departure"] = "08:35"
    assert _state("08:35", [delayed])["state"] == FAVOURITE_STATE_NOT_RUNNING
    assert _state("08:15", [delayed])["delay_minutes"] == 20


async def test_departed_keeps_last_delay_and_cancelled_stays_cancelled(
    hass: HomeAssistant, hass_storage
) -> None:
    store = FavouriteTrainsStore(hass, "e1")
    with patch.object(dt_util, "now", return_value=_at(14, 8, 10)):
        await store.async_load()
        fav, persist, seen = build_favourite_states(
            ["08:15", "08:20"],
            [_service("08:15", delay=6), _service("08:20", cancelled=True)],
            store,
            8 * 60 + 10,
            60,
        )
        await store.async_update(persist, seen)
        assert [f["state"] for f in fav] == ["upcoming", "cancelled"]

        # Both gone from the board once their time has passed
        fav, _, _ = build_favourite_states(
            ["08:15", "08:20"], [], store, 8 * 60 + 30, 60
        )
    assert fav[0]["state"] == FAVOURITE_STATE_DEPARTED
    assert fav[0]["delay_minutes"] == 6
    assert fav[1]["state"] == FAVOURITE_STATE_CANCELLED


async def test_departed_survives_restart_and_resets_next_day(
    hass: HomeAssistant, hass_storage
) -> None:
    with patch.object(dt_util, "now", return_value=_at(14, 8, 10)):
        store = FavouriteTrainsStore(hass, "e1")
        await store.async_load()
        _, persist, seen = build_favourite_states(
            ["08:15"], [_service("08:15", delay=2)], store, 8 * 60 + 10, 60
        )
        await store.async_update(persist, seen)

        restarted = FavouriteTrainsStore(hass, "e1")
        await restarted.async_load()
        assert restarted.get("08:15")["delay_minutes"] == 2

    with patch.object(dt_util, "now", return_value=_at(15, 8, 40)):
        next_day = FavouriteTrainsStore(hass, "e1")
        await next_day.async_load()
        assert next_day.get("08:15") is None
        fav, _, _ = build_favourite_states(["08:15"], [], next_day, 8 * 60 + 40, 60)
    assert fav[0]["state"] == FAVOURITE_STATE_NOT_RUNNING


# ---- integration ---------------------------------------------------------


def _board(services):
    return {
        "location_name": "London Paddington",
        "destination_name": "Reading",
        "services": services,
        "generated_at": "2026-10-14T08:00:00",
        "nrcc_messages": [],
    }


async def test_end_to_end_states_and_attribute(
    hass: HomeAssistant, mock_config_entry, mock_api_client, hass_storage
) -> None:
    """Favourite sensors and the summary attribute track a train through the day."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_FAVOURITE_TRAINS: "08:15, 09:30"}
    )
    mock_api_client.get_departure_board.return_value = _board(
        [_service("08:15", delay=3)]
    )

    with patch.object(dt_util, "now", return_value=_at(14, 8, 0)):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    # Over-fetches so a favourite is not cut off by num_services
    assert mock_api_client.get_departure_board.call_args.kwargs["num_rows"] >= 20

    ent_reg = er.async_get(hass)
    sensor_08 = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_favourite_0815"
    )
    sensor_0930 = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_favourite_0930"
    )
    assert hass.states.get(sensor_08).state == "upcoming"
    assert hass.states.get(sensor_08).attributes["delay_minutes"] == 3
    assert hass.states.get(sensor_0930).state == "scheduled"

    summary_id = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_summary"
    )
    listed = hass.states.get(summary_id).attributes["favourite_trains"]
    assert [(f["scheduled_departure"], f["state"]) for f in listed] == [
        ("08:15", "upcoming"),
        ("09:30", "scheduled"),
    ]

    # The train leaves: gone from the board, still shown as departed
    mock_api_client.get_departure_board.return_value = _board([])
    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]
    with patch.object(dt_util, "now", return_value=_at(14, 8, 30)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert hass.states.get(sensor_08).state == "departed"
    assert hass.states.get(sensor_08).attributes["delay_minutes"] == 3


async def test_no_favourites_leaves_everything_unchanged(
    hass: HomeAssistant, mock_config_entry, mock_api_client
) -> None:
    mock_config_entry.add_to_hass(hass)
    mock_api_client.get_departure_board.return_value = _board([_service("08:15")])
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_api_client.get_departure_board.call_args.kwargs["num_rows"] == 3
    ent_reg = er.async_get(hass)
    summary_id = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_summary"
    )
    assert "favourite_trains" not in hass.states.get(summary_id).attributes
    assert not [
        e
        for e in er.async_entries_for_config_entry(ent_reg, mock_config_entry.entry_id)
        if "_favourite_" in e.unique_id
    ]


async def test_cleanup_removes_dropped_favourites(
    hass: HomeAssistant, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_FAVOURITE_TRAINS: "08:15"}
    )
    ent_reg = er.async_get(hass)
    ids = {}
    for slug in ("0815", "0930"):
        ids[slug] = ent_reg.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{mock_config_entry.entry_id}_favourite_{slug}",
            config_entry=mock_config_entry,
        ).entity_id
    summary = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{mock_config_entry.entry_id}_summary",
        config_entry=mock_config_entry,
    ).entity_id

    await async_cleanup_stale_entities(hass, mock_config_entry)

    assert ent_reg.async_get(ids["0815"]) is not None
    assert ent_reg.async_get(ids["0930"]) is None
    assert ent_reg.async_get(summary) is not None


# ---- options flow --------------------------------------------------------


def _options_input(**extra):
    return {
        CONF_TIME_WINDOW: 60,
        CONF_NUM_SERVICES: 3,
        CONF_NIGHT_UPDATES: True,
        CONF_SEVERE_DELAY_THRESHOLD: 15,
        CONF_MAJOR_DELAY_THRESHOLD: 10,
        CONF_MINOR_DELAY_THRESHOLD: 3,
        **extra,
    }


async def test_options_flow_saves_favourites(
    hass: HomeAssistant, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input=_options_input(**{CONF_FAVOURITE_TRAINS: "08:15, 17:42"}),
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_FAVOURITE_TRAINS] == "08:15, 17:42"


async def test_options_flow_rejects_bad_favourites(
    hass: HomeAssistant, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input=_options_input(**{CONF_FAVOURITE_TRAINS: "8am"}),
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_favourite_trains"}
