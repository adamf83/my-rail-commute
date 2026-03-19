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
    _haversine_miles,
    validate_api_key,
    validate_stations,
)
from custom_components.my_rail_commute.const import (
    CONF_ADD_RETURN_JOURNEY,
    CONF_COMMUTE_NAME,
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
                CONF_SEVERE_DELAY_THRESHOLD: DEFAULT_SEVERE_DELAY_THRESHOLD,
                CONF_MAJOR_DELAY_THRESHOLD: DEFAULT_MAJOR_DELAY_THRESHOLD,
                CONF_MINOR_DELAY_THRESHOLD: DEFAULT_MINOR_DELAY_THRESHOLD,
            },
        )

        # No existing reverse route, so the return_journey step is shown
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "return_journey"

        # Step 4: Return journey - decline
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_ADD_RETURN_JOURNEY: False},
        )

        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["title"] == "Morning Commute"
        assert result["data"][CONF_API_KEY] == "valid_key"
        assert result["data"][CONF_ORIGIN] == "PAD"
        assert result["data"][CONF_DESTINATION] == "RDG"
        assert result["data"][CONF_TIME_WINDOW] == 60
        assert result["data"][CONF_NUM_SERVICES] == 3
        assert result["data"][CONF_NIGHT_UPDATES] is True
        assert result["data"][CONF_SEVERE_DELAY_THRESHOLD] == DEFAULT_SEVERE_DELAY_THRESHOLD
        assert result["data"][CONF_MAJOR_DELAY_THRESHOLD] == DEFAULT_MAJOR_DELAY_THRESHOLD
        assert result["data"][CONF_MINOR_DELAY_THRESHOLD] == DEFAULT_MINOR_DELAY_THRESHOLD

    async def _complete_flow_to_settings(self, hass, flow_id):
        """Helper: run user + stations steps and return at the settings form."""
        with patch(
            "custom_components.my_rail_commute.config_flow.validate_api_key",
            return_value={"title": "My Rail Commute"},
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={"api_key": "valid_key"},
            )

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
        return result

    async def _submit_settings(self, hass, flow_id):
        """Helper: submit default settings and return the result."""
        return await hass.config_entries.flow.async_configure(
            flow_id,
            user_input={
                CONF_COMMUTE_NAME: "Morning Commute",
                CONF_TIME_WINDOW: 60,
                CONF_NUM_SERVICES: 3,
                CONF_NIGHT_UPDATES: False,
                CONF_SEVERE_DELAY_THRESHOLD: DEFAULT_SEVERE_DELAY_THRESHOLD,
                CONF_MAJOR_DELAY_THRESHOLD: DEFAULT_MAJOR_DELAY_THRESHOLD,
                CONF_MINOR_DELAY_THRESHOLD: DEFAULT_MINOR_DELAY_THRESHOLD,
            },
        )

    async def test_return_journey_step_offered_when_reverse_missing(
        self, hass: HomeAssistant
    ):
        """Return journey form is shown when the reverse route doesn't exist."""
        result = await self._complete_flow_to_settings(hass, None)
        result = await self._submit_settings(hass, result["flow_id"])

        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "return_journey"

    async def test_return_journey_step_skipped_when_reverse_exists(
        self, hass: HomeAssistant
    ):
        """Return journey form is skipped when the reverse route already exists."""
        from pytest_homeassistant_custom_component.common import MockConfigEntry

        reverse_entry = MockConfigEntry(
            domain=DOMAIN,
            data={},
            unique_id="RDG_PAD",
        )
        reverse_entry.add_to_hass(hass)

        result = await self._complete_flow_to_settings(hass, None)
        result = await self._submit_settings(hass, result["flow_id"])

        # Should skip return_journey and create entry directly
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY

    async def test_return_journey_accepted_schedules_reverse_flow(
        self, hass: HomeAssistant
    ):
        """Accepting the offer schedules creation of the reverse commute."""
        result = await self._complete_flow_to_settings(hass, None)
        result = await self._submit_settings(hass, result["flow_id"])
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
        call_args = mock_init.call_args
        assert call_args.args[0] == DOMAIN
        assert call_args.kwargs["context"]["source"] == config_entries.SOURCE_IMPORT
        reverse_data = call_args.kwargs["data"]
        assert reverse_data[CONF_ORIGIN] == "RDG"
        assert reverse_data[CONF_DESTINATION] == "PAD"
        assert reverse_data[CONF_COMMUTE_NAME] == "Reading to London Paddington"
        assert reverse_data[CONF_NIGHT_UPDATES] is False

    async def test_return_journey_declined_creates_only_primary(
        self, hass: HomeAssistant
    ):
        """Declining the offer creates only the primary entry."""
        result = await self._complete_flow_to_settings(hass, None)
        result = await self._submit_settings(hass, result["flow_id"])
        assert result["step_id"] == "return_journey"

        with patch.object(
            hass.config_entries.flow,
            "async_init",
            wraps=hass.config_entries.flow.async_init,
        ) as mock_init:
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_ADD_RETURN_JOURNEY: False},
            )

        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        mock_init.assert_not_called()

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
                CONF_SEVERE_DELAY_THRESHOLD: DEFAULT_SEVERE_DELAY_THRESHOLD,
                CONF_MAJOR_DELAY_THRESHOLD: DEFAULT_MAJOR_DELAY_THRESHOLD,
                CONF_MINOR_DELAY_THRESHOLD: DEFAULT_MINOR_DELAY_THRESHOLD,
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

        # Use Home Assistant's proper options flow initialization
        result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "init"

    async def test_options_flow_update(self, hass: HomeAssistant, mock_config_entry):
        """Test updating options."""
        # Add the config entry to hass
        mock_config_entry.add_to_hass(hass)

        # Initialize the options flow properly through Home Assistant
        result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

        # Configure the options
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_TIME_WINDOW: 90,
                CONF_NUM_SERVICES: 5,
                CONF_NIGHT_UPDATES: False,
                CONF_SEVERE_DELAY_THRESHOLD: 20,
                CONF_MAJOR_DELAY_THRESHOLD: 12,
                CONF_MINOR_DELAY_THRESHOLD: 5,
            },
        )

        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_TIME_WINDOW] == 90
        assert result["data"][CONF_NUM_SERVICES] == 5
        assert result["data"][CONF_NIGHT_UPDATES] is False
        assert result["data"][CONF_SEVERE_DELAY_THRESHOLD] == 20
        assert result["data"][CONF_MAJOR_DELAY_THRESHOLD] == 12
        assert result["data"][CONF_MINOR_DELAY_THRESHOLD] == 5


class TestHaversineDistance:
    """Tests for the haversine distance calculation helper."""

    def test_zero_distance_same_point(self):
        """Distance between a point and itself is zero."""
        dist = _haversine_miles(51.5154, -0.17546, 51.5154, -0.17546)
        assert dist == pytest.approx(0.0, abs=0.001)

    def test_known_distance_paddington_to_reading(self):
        """London Paddington to Reading is roughly 36 miles."""
        # PAD: 51.5154, -0.17546 / RDG: 51.4585, -0.9723
        dist = _haversine_miles(51.5154, -0.17546, 51.4585, -0.9723)
        assert 34.0 < dist < 38.0

    def test_symmetry(self):
        """Distance A→B equals distance B→A."""
        d_ab = _haversine_miles(51.5154, -0.17546, 51.4585, -0.9723)
        d_ba = _haversine_miles(51.4585, -0.9723, 51.5154, -0.17546)
        assert d_ab == pytest.approx(d_ba, rel=1e-6)


class TestFindNearbyStations:
    """Tests for the _find_nearby_stations method."""

    # London Paddington: reference home location
    HOME_LAT = 51.5154
    HOME_LON = -0.17546

    # Station at exactly the home location (~0 miles)
    STATION_AT_HOME = {"crs": "PAD", "name": "London Paddington", "lat": 51.5154, "lon": -0.17546}
    # Station ~2 miles south (within 5 miles)
    # lat 51.5154 - 0.029° ≈ 2 miles south
    STATION_2_MILES = {"crs": "TS2", "name": "Test Station 2 Miles", "lat": 51.4864, "lon": -0.17546}
    # Station ~6 miles south (within 10 miles but outside 5 miles)
    # lat 51.5154 - 0.087° ≈ 6 miles south
    STATION_6_MILES = {"crs": "TST", "name": "Test Station", "lat": 51.4284, "lon": -0.17546}
    # Reading: ~35 miles away (well beyond 10 miles)
    STATION_FAR = {"crs": "RDG", "name": "Reading", "lat": 51.4585, "lon": -0.9723}

    async def test_returns_empty_when_location_is_zero(self, hass: HomeAssistant):
        """Returns empty list when HA home location is (0, 0) — i.e. not configured."""
        hass.config.latitude = 0.0
        hass.config.longitude = 0.0

        flow = NationalRailCommuteConfigFlow()
        flow.hass = hass
        result = await flow._find_nearby_stations()

        assert result == []

    async def test_returns_stations_within_5_miles(self, hass: HomeAssistant):
        """Only stations within the 5-mile minimum radius are returned."""
        hass.config.latitude = self.HOME_LAT
        hass.config.longitude = self.HOME_LON

        with patch(
            "custom_components.my_rail_commute.config_flow._load_station_data",
            return_value=[self.STATION_AT_HOME, self.STATION_6_MILES, self.STATION_FAR],
        ):
            flow = NationalRailCommuteConfigFlow()
            flow.hass = hass
            result = await flow._find_nearby_stations()

        assert len(result) == 1
        _, station = result[0]
        assert station["crs"] == "PAD"

    async def test_expands_to_10_miles_when_none_within_5(self, hass: HomeAssistant):
        """Expands to 10-mile max radius when no stations are within 5 miles."""
        hass.config.latitude = self.HOME_LAT
        hass.config.longitude = self.HOME_LON

        with patch(
            "custom_components.my_rail_commute.config_flow._load_station_data",
            return_value=[self.STATION_6_MILES, self.STATION_FAR],
        ):
            flow = NationalRailCommuteConfigFlow()
            flow.hass = hass
            result = await flow._find_nearby_stations()

        assert len(result) == 1
        _, station = result[0]
        assert station["crs"] == "TST"

    async def test_returns_empty_when_nothing_within_10_miles(self, hass: HomeAssistant):
        """Returns empty list when no stations are within the 10-mile maximum."""
        hass.config.latitude = self.HOME_LAT
        hass.config.longitude = self.HOME_LON

        with patch(
            "custom_components.my_rail_commute.config_flow._load_station_data",
            return_value=[self.STATION_FAR],
        ):
            flow = NationalRailCommuteConfigFlow()
            flow.hass = hass
            result = await flow._find_nearby_stations()

        assert result == []

    async def test_results_sorted_nearest_first(self, hass: HomeAssistant):
        """Returned stations are sorted by distance, nearest first."""
        hass.config.latitude = self.HOME_LAT
        hass.config.longitude = self.HOME_LON

        with patch(
            "custom_components.my_rail_commute.config_flow._load_station_data",
            # Provide farther station first to confirm sorting reorders them
            return_value=[self.STATION_2_MILES, self.STATION_AT_HOME],
        ):
            flow = NationalRailCommuteConfigFlow()
            flow.hass = hass
            result = await flow._find_nearby_stations()

        assert len(result) == 2
        assert result[0][1]["crs"] == "PAD"   # nearest (~0 miles) first
        assert result[1][1]["crs"] == "TS2"   # further (~2 miles) second

    async def test_handles_station_data_load_failure(self, hass: HomeAssistant):
        """Returns empty list gracefully if station data file cannot be read."""
        hass.config.latitude = self.HOME_LAT
        hass.config.longitude = self.HOME_LON

        with patch(
            "custom_components.my_rail_commute.config_flow._load_station_data",
            side_effect=OSError("File not found"),
        ):
            flow = NationalRailCommuteConfigFlow()
            flow.hass = hass
            result = await flow._find_nearby_stations()

        assert result == []


class TestStationsStepLocationBased:
    """Integration tests for the stations step with location-based lookup."""

    # Station ~6 miles from Paddington (between 5 and 10 miles)
    NEARBY_STATION = {"crs": "TST", "name": "Test Station", "lat": 51.4284, "lon": -0.17546}
    # Station at home location
    STATION_AT_HOME = {"crs": "PAD", "name": "London Paddington", "lat": 51.5154, "lon": -0.17546}

    async def _init_flow_to_stations(self, hass: HomeAssistant):
        """Helper: start flow and advance past the API key step."""
        with patch(
            "custom_components.my_rail_commute.config_flow.validate_api_key",
            return_value={"title": "My Rail Commute"},
        ):
            return await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_API_KEY: "valid_key"},
            )

    async def test_nearby_stations_used_as_origin_dropdown(self, hass: HomeAssistant):
        """SelectSelector is used for origin when nearby stations are found."""
        from homeassistant.helpers.selector import SelectSelector

        hass.config.latitude = 51.5154
        hass.config.longitude = -0.17546

        with patch(
            "custom_components.my_rail_commute.config_flow._load_station_data",
            return_value=[self.STATION_AT_HOME],
        ):
            result = await self._init_flow_to_stations(hass)

        assert result["step_id"] == "stations"
        # Inspect the schema: origin field should be a SelectSelector
        schema_mapping = result["data_schema"].schema
        origin_validator = next(
            v for k, v in schema_mapping.items() if str(k) == CONF_ORIGIN
        )
        assert isinstance(origin_validator, SelectSelector)

    async def test_manual_text_input_when_no_location_set(self, hass: HomeAssistant):
        """Plain text str is used for origin when HA home location is (0, 0)."""
        hass.config.latitude = 0.0
        hass.config.longitude = 0.0

        result = await self._init_flow_to_stations(hass)

        assert result["step_id"] == "stations"
        schema_mapping = result["data_schema"].schema
        origin_validator = next(
            v for k, v in schema_mapping.items() if str(k) == CONF_ORIGIN
        )
        assert origin_validator is str

    async def test_manual_text_input_when_no_nearby_stations(self, hass: HomeAssistant):
        """Plain text str is used for origin when no stations are within 10 miles."""
        hass.config.latitude = 51.5154
        hass.config.longitude = -0.17546

        far_stations = [{"crs": "MAN", "name": "Manchester Piccadilly", "lat": 53.4777, "lon": -2.2309}]

        with patch(
            "custom_components.my_rail_commute.config_flow._load_station_data",
            return_value=far_stations,
        ):
            result = await self._init_flow_to_stations(hass)

        assert result["step_id"] == "stations"
        schema_mapping = result["data_schema"].schema
        origin_validator = next(
            v for k, v in schema_mapping.items() if str(k) == CONF_ORIGIN
        )
        assert origin_validator is str

    async def test_station_selection_from_dropdown_proceeds(self, hass: HomeAssistant):
        """Selecting a CRS from the dropdown validates and proceeds to settings."""
        hass.config.latitude = 51.5154
        hass.config.longitude = -0.17546

        with patch(
            "custom_components.my_rail_commute.config_flow._load_station_data",
            return_value=[self.STATION_AT_HOME],
        ):
            result = await self._init_flow_to_stations(hass)

        with patch(
            "custom_components.my_rail_commute.config_flow.validate_stations",
            return_value={"origin_name": "London Paddington", "destination_name": "Reading"},
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_ORIGIN: "PAD", CONF_DESTINATION: "RDG"},
            )

        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "settings"

    async def test_custom_crs_entry_in_dropdown_proceeds(self, hass: HomeAssistant):
        """Typing a custom CRS code in the SelectSelector (custom_value=True) works."""
        hass.config.latitude = 51.5154
        hass.config.longitude = -0.17546

        with patch(
            "custom_components.my_rail_commute.config_flow._load_station_data",
            return_value=[self.STATION_AT_HOME],
        ):
            result = await self._init_flow_to_stations(hass)

        with patch(
            "custom_components.my_rail_commute.config_flow.validate_stations",
            return_value={"origin_name": "Manchester Piccadilly", "destination_name": "Leeds"},
        ):
            # User types a custom CRS not in the nearby list
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_ORIGIN: "MAN", CONF_DESTINATION: "LDS"},
            )

        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "settings"

    async def test_origin_crs_normalised_to_uppercase(self, hass: HomeAssistant):
        """Origin CRS code entered in lowercase is normalised to uppercase."""
        hass.config.latitude = 0.0
        hass.config.longitude = 0.0

        result = await self._init_flow_to_stations(hass)

        with patch(
            "custom_components.my_rail_commute.config_flow.validate_stations",
            return_value={"origin_name": "London Paddington", "destination_name": "Reading"},
        ) as mock_validate:
            await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_ORIGIN: "pad", CONF_DESTINATION: "rdg"},
            )

        call_args = mock_validate.call_args
        assert call_args.args[2] == "PAD"   # origin normalised
        assert call_args.args[3] == "rdg"   # destination passed as-is to validate_stations
