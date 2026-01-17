"""Tests for the config flow."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries, data_entry_flow
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from custom_components.my_rail_commute.api import (
    AuthenticationError,
    InvalidStationError,
    NationalRailAPIError,
)
from custom_components.my_rail_commute.config_flow import (
    NationalRailCommuteConfigFlow,
    validate_api_key,
    validate_stations,
)
from custom_components.my_rail_commute.const import (
    CONF_COMMUTE_NAME,
    CONF_DESTINATION,
    CONF_NIGHT_UPDATES,
    CONF_NUM_SERVICES,
    CONF_ORIGIN,
    CONF_TIME_WINDOW,
    DOMAIN,
)


class TestValidateAPIKey:
    """Tests for API key validation function."""

    async def test_validate_api_key_success(self, hass: HomeAssistant):
        """Test successful API key validation."""
        with patch(
            "custom_components.my_rail_commute.config_flow.NationalRailAPI"
        ) as mock_api:
            mock_instance = mock_api.return_value
            mock_instance.validate_api_key = AsyncMock(return_value=True)

            result = await validate_api_key(hass, "test_api_key")

            assert result["title"] == "My Rail Commute"
            mock_instance.validate_api_key.assert_called_once()

    async def test_validate_api_key_failure(self, hass: HomeAssistant):
        """Test API key validation failure."""
        with patch(
            "custom_components.my_rail_commute.config_flow.NationalRailAPI"
        ) as mock_api:
            mock_instance = mock_api.return_value
            mock_instance.validate_api_key = AsyncMock(
                side_effect=AuthenticationError("Invalid API key")
            )

            with pytest.raises(AuthenticationError):
                await validate_api_key(hass, "invalid_key")


class TestValidateStations:
    """Tests for station validation function."""

    async def test_validate_stations_success(self, hass: HomeAssistant):
        """Test successful station validation."""
        with patch(
            "custom_components.my_rail_commute.config_flow.NationalRailAPI"
        ) as mock_api:
            mock_instance = mock_api.return_value
            mock_instance.validate_station = AsyncMock(
                side_effect=["London Paddington", "Reading"]
            )

            result = await validate_stations(hass, "test_key", "PAD", "RDG")

            assert result["origin_name"] == "London Paddington"
            assert result["destination_name"] == "Reading"
            assert mock_instance.validate_station.call_count == 2

    async def test_validate_stations_same_station(self, hass: HomeAssistant):
        """Test validation with same origin and destination."""
        with patch(
            "custom_components.my_rail_commute.config_flow.NationalRailAPI"
        ):
            with pytest.raises(ValueError, match="Origin and destination must be different"):
                await validate_stations(hass, "test_key", "PAD", "PAD")

    async def test_validate_stations_invalid_station(self, hass: HomeAssistant):
        """Test validation with invalid station."""
        with patch(
            "custom_components.my_rail_commute.config_flow.NationalRailAPI"
        ) as mock_api:
            mock_instance = mock_api.return_value
            mock_instance.validate_station = AsyncMock(
                side_effect=InvalidStationError("Invalid station")
            )

            with pytest.raises(InvalidStationError):
                await validate_stations(hass, "test_key", "XYZ", "RDG")


class TestConfigFlow:
    """Tests for the config flow."""

    async def test_form_user_step(self, hass: HomeAssistant):
        """Test the user step shows the form."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {}

    async def test_form_user_invalid_auth(self, hass: HomeAssistant):
        """Test invalid authentication in user step."""
        with patch(
            "custom_components.my_rail_commute.config_flow.validate_api_key",
            side_effect=AuthenticationError("Invalid API key"),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_API_KEY: "invalid_key"},
            )

            assert result["type"] == data_entry_flow.FlowResultType.FORM
            assert result["step_id"] == "user"
            assert result["errors"] == {"base": "invalid_auth"}

    async def test_form_user_cannot_connect(self, hass: HomeAssistant):
        """Test cannot connect error in user step."""
        with patch(
            "custom_components.my_rail_commute.config_flow.validate_api_key",
            side_effect=NationalRailAPIError("Cannot connect"),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_API_KEY: "test_key"},
            )

            assert result["type"] == data_entry_flow.FlowResultType.FORM
            assert result["step_id"] == "user"
            assert result["errors"] == {"base": "cannot_connect"}

    async def test_form_user_unknown_error(self, hass: HomeAssistant):
        """Test unknown error in user step."""
        with patch(
            "custom_components.my_rail_commute.config_flow.validate_api_key",
            side_effect=Exception("Unexpected error"),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_API_KEY: "test_key"},
            )

            assert result["type"] == data_entry_flow.FlowResultType.FORM
            assert result["step_id"] == "user"
            assert result["errors"] == {"base": "unknown"}

    async def test_form_user_success_proceeds_to_stations(self, hass: HomeAssistant):
        """Test successful API key validation proceeds to stations step."""
        with patch(
            "custom_components.my_rail_commute.config_flow.validate_api_key",
            return_value={"title": "My Rail Commute"},
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_API_KEY: "valid_key"},
            )

            assert result["type"] == data_entry_flow.FlowResultType.FORM
            assert result["step_id"] == "stations"

    async def test_form_user_reuses_existing_api_key(
        self, hass: HomeAssistant, mock_config_entry
    ):
        """Test that existing API key is reused for additional routes."""
        mock_config_entry.add_to_hass(hass)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "stations"

    async def test_form_stations_invalid_station(self, hass: HomeAssistant):
        """Test invalid station in stations step."""
        # Start flow
        with patch(
            "custom_components.my_rail_commute.config_flow.validate_api_key",
            return_value={"title": "My Rail Commute"},
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_API_KEY: "valid_key"},
            )

        # Submit invalid station
        with patch(
            "custom_components.my_rail_commute.config_flow.validate_stations",
            side_effect=InvalidStationError("Invalid station"),
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_ORIGIN: "XYZ", CONF_DESTINATION: "RDG"},
            )

            assert result["type"] == data_entry_flow.FlowResultType.FORM
            assert result["step_id"] == "stations"
            assert result["errors"] == {"base": "invalid_station"}

    async def test_form_stations_same_station(self, hass: HomeAssistant):
        """Test same origin and destination in stations step."""
        # Start flow
        with patch(
            "custom_components.my_rail_commute.config_flow.validate_api_key",
            return_value={"title": "My Rail Commute"},
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_API_KEY: "valid_key"},
            )

        # Submit same station
        with patch(
            "custom_components.my_rail_commute.config_flow.validate_stations",
            side_effect=ValueError("Origin and destination must be different"),
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_ORIGIN: "PAD", CONF_DESTINATION: "PAD"},
            )

            assert result["type"] == data_entry_flow.FlowResultType.FORM
            assert result["step_id"] == "stations"
            assert result["errors"] == {"base": "same_station"}

    async def test_form_stations_success_proceeds_to_settings(
        self, hass: HomeAssistant
    ):
        """Test successful station validation proceeds to settings step."""
        # Start flow
        with patch(
            "custom_components.my_rail_commute.config_flow.validate_api_key",
            return_value={"title": "My Rail Commute"},
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_API_KEY: "valid_key"},
            )

        # Submit valid stations
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

            assert result["type"] == data_entry_flow.FlowResultType.FORM
            assert result["step_id"] == "settings"

    async def test_complete_flow_creates_entry(self, hass: HomeAssistant):
        """Test complete flow creates config entry."""
        # Step 1: User (API key)
        with patch(
            "custom_components.my_rail_commute.config_flow.validate_api_key",
            return_value={"title": "My Rail Commute"},
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_API_KEY: "valid_key"},
            )

        # Step 2: Stations
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

        # Step 3: Settings
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_COMMUTE_NAME: "Morning Commute",
                CONF_TIME_WINDOW: 60,
                CONF_NUM_SERVICES: 3,
                CONF_NIGHT_UPDATES: True,
            },
        )

        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["title"] == "Morning Commute"
        assert result["data"][CONF_API_KEY] == "valid_key"
        assert result["data"][CONF_ORIGIN] == "PAD"
        assert result["data"][CONF_DESTINATION] == "RDG"
        assert result["data"][CONF_TIME_WINDOW] == 60
        assert result["data"][CONF_NUM_SERVICES] == 3
        assert result["data"][CONF_NIGHT_UPDATES] is True

    async def test_duplicate_route_aborts(self, hass: HomeAssistant, mock_config_entry):
        """Test that duplicate routes are detected and abort."""
        mock_config_entry.add_to_hass(hass)

        # Step 1: User (API key) - reuses existing
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        # Step 2: Stations - same as existing
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

        # Step 3: Settings - should abort due to duplicate unique_id
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_COMMUTE_NAME: "Test",
                CONF_TIME_WINDOW: 60,
                CONF_NUM_SERVICES: 3,
                CONF_NIGHT_UPDATES: False,
            },
        )

        assert result["type"] == data_entry_flow.FlowResultType.ABORT
        assert result["reason"] == "already_configured"


class TestOptionsFlow:
    """Tests for the options flow."""

    async def test_options_flow_init(self, hass: HomeAssistant, mock_config_entry):
        """Test options flow initialization."""
        # Add the config entry to hass
        mock_config_entry.add_to_hass(hass)

        # Create the options flow using the proper Home Assistant method
        # This avoids the deprecated direct config_entry setting
        from custom_components.my_rail_commute.config_flow import (
            NationalRailCommuteOptionsFlow,
        )

        # Initialize via the handler manager which properly sets config_entry
        options_flow = NationalRailCommuteOptionsFlow()

        # Use the handler attribute which is the proper way in HA 2024+
        with patch.object(options_flow, 'config_entry', mock_config_entry):
            options_flow.hass = hass

            # Call the init step directly
            result = await options_flow.async_step_init()

            assert result["type"] == data_entry_flow.FlowResultType.FORM
            assert result["step_id"] == "init"

    async def test_options_flow_update(self, hass: HomeAssistant, mock_config_entry):
        """Test updating options."""
        # Add the config entry to hass
        mock_config_entry.add_to_hass(hass)

        from custom_components.my_rail_commute.config_flow import (
            NationalRailCommuteOptionsFlow,
        )

        options_flow = NationalRailCommuteOptionsFlow()

        # Use patch to mock config_entry instead of setting directly
        with patch.object(options_flow, 'config_entry', mock_config_entry):
            options_flow.hass = hass

            # Initialize the form
            result = await options_flow.async_step_init()

            # Configure the options
            result = await options_flow.async_step_init(
                user_input={
                    CONF_TIME_WINDOW: 90,
                    CONF_NUM_SERVICES: 5,
                    CONF_NIGHT_UPDATES: False,
                }
            )

            assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
            assert result["data"][CONF_TIME_WINDOW] == 90
            assert result["data"][CONF_NUM_SERVICES] == 5
            assert result["data"][CONF_NIGHT_UPDATES] is False
