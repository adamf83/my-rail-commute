"""Tests for multi-leg journey support in the config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries, data_entry_flow
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from custom_components.my_rail_commute.const import (
    CONF_ADD_LEG,
    CONF_ADD_RETURN_JOURNEY,
    CONF_COMMUTE_NAME,
    CONF_DESTINATION,
    CONF_LEG_DESTINATION,
    CONF_LEGS,
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
    DOMAIN,
)


async def _start_flow(hass: HomeAssistant):
    """Start the flow and submit a valid API key."""
    with patch(
        "custom_components.my_rail_commute.config_flow.validate_api_key",
        return_value={"title": "My Rail Commute"},
    ):
        return await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_API_KEY: "valid_key"},
        )


async def _submit_settings(hass: HomeAssistant, flow_id: str):
    """Submit the settings step with default values."""
    return await hass.config_entries.flow.async_configure(
        flow_id,
        user_input={
            CONF_COMMUTE_NAME: "Test Journey",
            CONF_TIME_WINDOW: 60,
            CONF_NUM_SERVICES: 3,
            CONF_NIGHT_UPDATES: False,
            CONF_SEVERE_DELAY_THRESHOLD: DEFAULT_SEVERE_DELAY_THRESHOLD,
            CONF_MAJOR_DELAY_THRESHOLD: DEFAULT_MAJOR_DELAY_THRESHOLD,
            CONF_MINOR_DELAY_THRESHOLD: DEFAULT_MINOR_DELAY_THRESHOLD,
        },
    )


class TestAddLegStep:
    """Tests for the add_leg looping step."""

    async def test_declining_first_leg_creates_single_leg_entry(
        self, hass: HomeAssistant
    ):
        """Declining to add a leg produces a plain single-leg entry (no CONF_LEGS)."""
        result = await _start_flow(hass)

        with patch(
            "custom_components.my_rail_commute.config_flow.validate_stations",
            return_value={
                "origin_name": "London Paddington",
                "destination_name": "Reading",
            },
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_ORIGIN: "PAD", CONF_DESTINATION: "RDG"},
            )

        assert result["step_id"] == "add_leg"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_ADD_LEG: False}
        )
        assert result["step_id"] == "settings"

        result = await _submit_settings(hass, result["flow_id"])
        assert result["step_id"] == "return_journey"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_ADD_RETURN_JOURNEY: False}
        )

        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert CONF_LEGS not in result["data"]
        assert result["data"][CONF_ORIGIN] == "PAD"
        assert result["data"][CONF_DESTINATION] == "RDG"

    async def test_adding_two_legs_inserts_interchanges_before_destination(
        self, hass: HomeAssistant
    ):
        """Accepting add_leg twice then declining inserts interchanges before
        the fixed destination chosen in the stations step, rather than
        appending stations past it."""
        result = await _start_flow(hass)

        with patch(
            "custom_components.my_rail_commute.config_flow.validate_stations",
            return_value={
                "origin_name": "London Paddington",
                "destination_name": "Birmingham New Street",
            },
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_ORIGIN: "PAD", CONF_DESTINATION: "BHM"},
            )
        assert result["step_id"] == "add_leg"

        with patch(
            "custom_components.my_rail_commute.config_flow.validate_stations",
            return_value={"origin_name": "London Paddington", "destination_name": "Reading"},
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_ADD_LEG: True, CONF_LEG_DESTINATION: "RDG"},
            )
        assert result["step_id"] == "add_leg"

        with patch(
            "custom_components.my_rail_commute.config_flow.validate_stations",
            return_value={
                "origin_name": "Reading",
                "destination_name": "Oxford",
            },
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_ADD_LEG: True, CONF_LEG_DESTINATION: "OXF"},
            )
        assert result["step_id"] == "add_leg"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_ADD_LEG: False}
        )
        assert result["step_id"] == "settings"

        result = await _submit_settings(hass, result["flow_id"])
        assert result["step_id"] == "return_journey"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_ADD_RETURN_JOURNEY: False}
        )

        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_LEGS] == [
            {"origin": "PAD", "destination": "RDG"},
            {"origin": "RDG", "destination": "OXF"},
            {"origin": "OXF", "destination": "BHM"},
        ]
        # The originally configured origin/destination stay fixed as the
        # journey's overall endpoints, regardless of how many interchanges
        # were inserted between them.
        assert result["data"][CONF_ORIGIN] == "PAD"
        assert result["data"][CONF_DESTINATION] == "BHM"

    async def test_add_leg_rejects_final_destination_as_interchange(
        self, hass: HomeAssistant
    ):
        """Entering the fixed final destination as a connecting leg errors."""
        result = await _start_flow(hass)

        with patch(
            "custom_components.my_rail_commute.config_flow.validate_stations",
            return_value={
                "origin_name": "London Paddington",
                "destination_name": "Reading",
            },
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_ORIGIN: "PAD", CONF_DESTINATION: "RDG"},
            )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_ADD_LEG: True, CONF_LEG_DESTINATION: "RDG"},
        )

        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "add_leg"
        assert result["errors"] == {CONF_LEG_DESTINATION: "same_as_destination"}

    async def test_unique_id_is_chain_based_for_multi_leg(self, hass: HomeAssistant):
        """The config entry's unique_id embeds every station in the chain,
        with the inserted interchange ordered before the fixed destination."""
        result = await _start_flow(hass)

        with patch(
            "custom_components.my_rail_commute.config_flow.validate_stations",
            return_value={
                "origin_name": "London Paddington",
                "destination_name": "Reading",
            },
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_ORIGIN: "PAD", CONF_DESTINATION: "RDG"},
            )

        with patch(
            "custom_components.my_rail_commute.config_flow.validate_stations",
            return_value={"origin_name": "London Paddington", "destination_name": "Oxford"},
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_ADD_LEG: True, CONF_LEG_DESTINATION: "OXF"},
            )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_ADD_LEG: False}
        )
        result = await _submit_settings(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_ADD_RETURN_JOURNEY: False}
        )

        entries = hass.config_entries.async_entries(DOMAIN)
        assert len(entries) == 1
        assert entries[0].unique_id == "PAD_OXF_RDG"

    async def test_add_leg_same_station_shows_error(self, hass: HomeAssistant):
        """Requesting a leg destination identical to the current station errors."""
        result = await _start_flow(hass)

        with patch(
            "custom_components.my_rail_commute.config_flow.validate_stations",
            return_value={
                "origin_name": "London Paddington",
                "destination_name": "Reading",
            },
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_ORIGIN: "PAD", CONF_DESTINATION: "RDG"},
            )

        with patch(
            "custom_components.my_rail_commute.config_flow.validate_stations",
            side_effect=ValueError("Origin and destination must be different"),
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_ADD_LEG: True, CONF_LEG_DESTINATION: "PAD"},
            )

        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "add_leg"
        assert result["errors"] == {"base": "same_station"}

    async def test_add_leg_missing_destination_shows_error(self, hass: HomeAssistant):
        """Accepting add_leg without a destination shows a required-field error."""
        result = await _start_flow(hass)

        with patch(
            "custom_components.my_rail_commute.config_flow.validate_stations",
            return_value={
                "origin_name": "London Paddington",
                "destination_name": "Reading",
            },
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_ORIGIN: "PAD", CONF_DESTINATION: "RDG"},
            )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_ADD_LEG: True, CONF_LEG_DESTINATION: ""},
        )

        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "add_leg"
        assert result["errors"] == {CONF_LEG_DESTINATION: "required"}


class TestMultiLegReturnJourney:
    """Tests for the return-journey toggle reversing the full leg chain."""

    async def test_return_journey_reverses_full_leg_sequence(self, hass: HomeAssistant):
        """Accepting the return-journey offer reverses both leg order and direction."""
        result = await _start_flow(hass)

        with patch(
            "custom_components.my_rail_commute.config_flow.validate_stations",
            return_value={
                "origin_name": "London Paddington",
                "destination_name": "Reading",
            },
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_ORIGIN: "PAD", CONF_DESTINATION: "RDG"},
            )

        with patch(
            "custom_components.my_rail_commute.config_flow.validate_stations",
            return_value={"origin_name": "London Paddington", "destination_name": "Oxford"},
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_ADD_LEG: True, CONF_LEG_DESTINATION: "OXF"},
            )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_ADD_LEG: False}
        )
        result = await _submit_settings(hass, result["flow_id"])
        assert result["step_id"] == "return_journey"

        with patch.object(
            hass.config_entries.flow,
            "async_init",
            wraps=hass.config_entries.flow.async_init,
        ) as mock_init:
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_ADD_RETURN_JOURNEY: True},
            )

        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert mock_init.call_count == 1
        reverse_data = mock_init.call_args.kwargs["data"]

        # Original chain is PAD -> OXF -> RDG (OXF inserted before the fixed
        # destination); the reverse route flips both order and direction.
        assert reverse_data[CONF_LEGS] == [
            {"origin": "RDG", "destination": "OXF"},
            {"origin": "OXF", "destination": "PAD"},
        ]
        assert reverse_data[CONF_ORIGIN] == "RDG"
        assert reverse_data[CONF_DESTINATION] == "PAD"

        # The reverse import flow itself must persist CONF_LEGS and use a
        # chain-based unique_id, not just the overall origin/destination
        entries = hass.config_entries.async_entries(DOMAIN)
        reverse_entry = next(e for e in entries if e.unique_id == "RDG_OXF_PAD")
        assert reverse_entry.data[CONF_LEGS] == reverse_data[CONF_LEGS]
