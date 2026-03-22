"""Config flow for My Rail Commute integration."""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    AuthenticationError,
    InvalidStationError,
    NationalRailAPI,
    NationalRailAPIError,
)
from .const import (
    CONF_ADD_RETURN_JOURNEY,
    CONF_COMMUTE_NAME,
    CONF_DEPARTED_TRAIN_GRACE_PERIOD,
    CONF_DESTINATION,
    CONF_DISRUPTION_MULTIPLE_COUNT,
    CONF_DISRUPTION_MULTIPLE_DELAY,
    CONF_DISRUPTION_SINGLE_DELAY,
    CONF_MAJOR_DELAY_THRESHOLD,
    CONF_MINOR_DELAY_THRESHOLD,
    CONF_NIGHT_UPDATES,
    CONF_NUM_SERVICES,
    CONF_ORIGIN,
    CONF_SEVERE_DELAY_THRESHOLD,
    CONF_TIME_WINDOW,
    DEFAULT_DEPARTED_TRAIN_GRACE_PERIOD,
    DEFAULT_MAJOR_DELAY_THRESHOLD,
    DEFAULT_MINOR_DELAY_THRESHOLD,
    DEFAULT_NAME,
    DEFAULT_NIGHT_UPDATES,
    DEFAULT_NUM_SERVICES,
    DEFAULT_SEVERE_DELAY_THRESHOLD,
    DEFAULT_TIME_WINDOW,
    DOMAIN,
    LOCATION_SEARCH_MAX_RADIUS_MILES,
    LOCATION_SEARCH_MIN_RADIUS_MILES,
    MAX_DELAY_THRESHOLD,
    MAX_GRACE_PERIOD,
    MAX_NUM_SERVICES,
    MAX_TIME_WINDOW,
    MIN_DELAY_THRESHOLD,
    MIN_GRACE_PERIOD,
    MIN_NUM_SERVICES,
    MIN_TIME_WINDOW,
)

_LOGGER = logging.getLogger(__name__)


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance in miles using the haversine formula."""
    R = 3958.8  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def _load_station_data() -> list[dict]:
    """Load UK station data from bundled JSON file (blocking I/O)."""
    data_path = Path(__file__).parent / "station_data.json"
    with open(data_path) as f:
        return json.load(f)


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): str,
    }
)


async def validate_api_key(hass: HomeAssistant, api_key: str) -> dict[str, Any]:
    """Validate the API key.

    Args:
        hass: Home Assistant instance
        api_key: API key to validate

    Returns:
        Dict with validation result

    Raises:
        AuthenticationError: If authentication fails
    """
    session = async_get_clientsession(hass)
    api = NationalRailAPI(api_key, session)

    await api.validate_api_key()

    return {"title": DEFAULT_NAME}


async def validate_stations(
    hass: HomeAssistant,
    api_key: str,
    origin: str,
    destination: str,
) -> dict[str, str]:
    """Validate station codes.

    Args:
        hass: Home Assistant instance
        api_key: API key
        origin: Origin station CRS code
        destination: Destination station CRS code

    Returns:
        Dict with origin_name and destination_name

    Raises:
        InvalidStationError: If station codes are invalid
        ValueError: If stations are the same
    """
    session = async_get_clientsession(hass)
    api = NationalRailAPI(api_key, session)

    # Validate stations are different
    if origin.upper() == destination.upper():
        raise ValueError("Origin and destination must be different")

    # Validate both stations
    origin_name = await api.validate_station(origin)
    destination_name = await api.validate_station(destination)

    if not origin_name or not destination_name:
        raise InvalidStationError("Could not validate station codes")

    return {
        "origin_name": origin_name,
        "destination_name": destination_name,
    }


def validate_delay_thresholds(
    severe: int,
    major: int,
    minor: int,
) -> None:
    """Validate delay threshold values maintain proper hierarchy.

    Args:
        severe: Severe disruption threshold (minutes)
        major: Major delays threshold (minutes)
        minor: Minor delays threshold (minutes)

    Raises:
        ValueError: If thresholds don't maintain hierarchy (severe >= major >= minor >= 1)
    """
    if not (severe >= major >= minor >= MIN_DELAY_THRESHOLD):
        raise ValueError(
            f"Delay thresholds must maintain hierarchy: "
            f"severe ({severe}) >= major ({major}) >= minor ({minor}) >= {MIN_DELAY_THRESHOLD}"
        )


class NationalRailCommuteConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for My Rail Commute."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._api_key: str | None = None
        self._origin: str | None = None
        self._destination: str | None = None
        self._origin_name: str | None = None
        self._destination_name: str | None = None
        self._commute_name: str | None = None
        self._time_window: int | None = None
        self._num_services: int | None = None
        self._night_updates: bool | None = None
        self._severe_delay_threshold: int | None = None
        self._major_delay_threshold: int | None = None
        self._minor_delay_threshold: int | None = None
        self._departed_train_grace_period: int | None = None
        self._nearby_stations: list[tuple[float, dict]] | None = None
        self._all_stations: list[dict] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - API key input.

        Args:
            user_input: User input data

        Returns:
            FlowResult for next step or errors
        """
        # Check if we already have an existing config entry with an API key
        existing_entries = self._async_current_entries()
        if existing_entries:
            # Reuse API key from existing entry
            existing_api_key = existing_entries[0].data.get(CONF_API_KEY)
            if existing_api_key:
                _LOGGER.debug("Reusing API key from existing integration")
                self._api_key = existing_api_key
                return await self.async_step_stations()

        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await validate_api_key(self.hass, user_input[CONF_API_KEY])

                self._api_key = user_input[CONF_API_KEY]

                return await self.async_step_stations()

            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except NationalRailAPIError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "signup_url": "https://raildata.org.uk/",
            },
        )

    async def _find_nearby_stations(self) -> list[tuple[float, dict]]:
        """Find UK rail stations near the HA home location.

        Searches within LOCATION_SEARCH_MIN_RADIUS_MILES first. If no stations
        are found, expands the search up to LOCATION_SEARCH_MAX_RADIUS_MILES.

        Returns:
            List of (distance_miles, station_dict) tuples sorted by distance,
            or an empty list if HA location is not set or no stations are found.
        """
        home_lat = self.hass.config.latitude
        home_lon = self.hass.config.longitude

        if not home_lat and not home_lon:
            return []

        try:
            stations = await self.hass.async_add_executor_job(_load_station_data)
        except (OSError, ValueError):
            _LOGGER.warning("Could not load station data for location-based lookup")
            return []

        with_distances = [
            (_haversine_miles(home_lat, home_lon, s["lat"], s["lon"]), s)
            for s in stations
        ]

        nearby_min = sorted(
            [(d, s) for d, s in with_distances if d <= LOCATION_SEARCH_MIN_RADIUS_MILES],
            key=lambda x: x[0],
        )
        if nearby_min:
            return nearby_min

        return sorted(
            [(d, s) for d, s in with_distances if d <= LOCATION_SEARCH_MAX_RADIUS_MILES],
            key=lambda x: x[0],
        )

    async def async_step_stations(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle station configuration step.

        For origin, nearby stations (within 5–10 miles of the HA home location)
        are presented as a dropdown. The user may also type a CRS code directly.
        Destination is always a free-text CRS code entry.

        Args:
            user_input: User input data

        Returns:
            FlowResult for next step or errors
        """
        errors: dict[str, str] = {}

        # Discover nearby stations once on first visit to this step
        if self._nearby_stations is None:
            self._nearby_stations = await self._find_nearby_stations()

        # Load all stations (sorted by CRS) for the destination dropdown
        if self._all_stations is None:
            try:
                raw = await self.hass.async_add_executor_job(_load_station_data)
                self._all_stations = sorted(raw, key=lambda s: s["crs"])
            except (OSError, ValueError):
                _LOGGER.warning("Could not load station data for destination lookup")
                self._all_stations = []

        if user_input is not None:
            try:
                origin = user_input[CONF_ORIGIN].strip().upper()
                destination = user_input[CONF_DESTINATION]

                # Validate stations
                station_info = await validate_stations(
                    self.hass, self._api_key, origin, destination
                )

                self._origin = origin
                self._destination = destination.strip().upper()
                self._origin_name = station_info["origin_name"]
                self._destination_name = station_info["destination_name"]

                return await self.async_step_settings()

            except ValueError:
                errors["base"] = "same_station"
            except InvalidStationError:
                errors["base"] = "invalid_station"
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except NationalRailAPIError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        # Build origin field: dropdown of nearby stations (with manual entry
        # fallback via custom_value=True), or plain text if none found
        if self._nearby_stations:
            origin_field = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value=s["crs"],
                            label=f"{i + 1}. {s['name']} ({dist:.1f} mi)",
                        )
                        for i, (dist, s) in enumerate(self._nearby_stations)
                    ],
                    custom_value=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        else:
            origin_field = str

        if self._all_stations:
            destination_field = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value=s["crs"],
                            label=f"{s['crs']} – {s['name']}",
                        )
                        for s in self._all_stations
                    ],
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        else:
            destination_field = str

        data_schema = vol.Schema(
            {
                vol.Required(CONF_ORIGIN): origin_field,
                vol.Required(CONF_DESTINATION): destination_field,
            }
        )

        return self.async_show_form(
            step_id="stations",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle settings configuration step.

        Args:
            user_input: User input data

        Returns:
            FlowResult for creating entry
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate delay thresholds
            try:
                validate_delay_thresholds(
                    user_input[CONF_SEVERE_DELAY_THRESHOLD],
                    user_input[CONF_MAJOR_DELAY_THRESHOLD],
                    user_input[CONF_MINOR_DELAY_THRESHOLD],
                )
            except ValueError as err:
                errors["base"] = "invalid_thresholds"
                _LOGGER.error("Invalid delay thresholds: %s", err)

            if not errors:
                # Store settings in instance variables
                self._commute_name = user_input.get(
                    CONF_COMMUTE_NAME,
                    f"{self._origin_name} to {self._destination_name}",
                )
                self._time_window = user_input[CONF_TIME_WINDOW]
                self._num_services = user_input[CONF_NUM_SERVICES]
                self._night_updates = user_input[CONF_NIGHT_UPDATES]
                self._severe_delay_threshold = user_input[CONF_SEVERE_DELAY_THRESHOLD]
                self._major_delay_threshold = user_input[CONF_MAJOR_DELAY_THRESHOLD]
                self._minor_delay_threshold = user_input[CONF_MINOR_DELAY_THRESHOLD]
                self._departed_train_grace_period = user_input.get(
                    CONF_DEPARTED_TRAIN_GRACE_PERIOD, DEFAULT_DEPARTED_TRAIN_GRACE_PERIOD
                )

                # Set unique ID based on route
                await self.async_set_unique_id(
                    f"{self._origin}_{self._destination}"
                )
                self._abort_if_unique_id_configured()

                return await self.async_step_return_journey()

        # Default commute name
        default_name = f"{self._origin_name} to {self._destination_name}"

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_COMMUTE_NAME,
                    default=default_name,
                ): str,
                vol.Required(
                    CONF_TIME_WINDOW,
                    default=DEFAULT_TIME_WINDOW,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_TIME_WINDOW,
                        max=MAX_TIME_WINDOW,
                        step=5,
                        unit_of_measurement="minutes",
                        mode=selector.NumberSelectorMode.SLIDER,
                    ),
                ),
                vol.Required(
                    CONF_NUM_SERVICES,
                    default=DEFAULT_NUM_SERVICES,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_NUM_SERVICES,
                        max=MAX_NUM_SERVICES,
                        step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                    ),
                ),
                vol.Required(
                    CONF_SEVERE_DELAY_THRESHOLD,
                    default=DEFAULT_SEVERE_DELAY_THRESHOLD,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_DELAY_THRESHOLD,
                        max=MAX_DELAY_THRESHOLD,
                        step=1,
                        unit_of_measurement="minutes",
                        mode=selector.NumberSelectorMode.SLIDER,
                    ),
                ),
                vol.Required(
                    CONF_MAJOR_DELAY_THRESHOLD,
                    default=DEFAULT_MAJOR_DELAY_THRESHOLD,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_DELAY_THRESHOLD,
                        max=MAX_DELAY_THRESHOLD,
                        step=1,
                        unit_of_measurement="minutes",
                        mode=selector.NumberSelectorMode.SLIDER,
                    ),
                ),
                vol.Required(
                    CONF_MINOR_DELAY_THRESHOLD,
                    default=DEFAULT_MINOR_DELAY_THRESHOLD,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_DELAY_THRESHOLD,
                        max=MAX_DELAY_THRESHOLD,
                        step=1,
                        unit_of_measurement="minutes",
                        mode=selector.NumberSelectorMode.SLIDER,
                    ),
                ),
                vol.Required(
                    CONF_NIGHT_UPDATES,
                    default=DEFAULT_NIGHT_UPDATES,
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_DEPARTED_TRAIN_GRACE_PERIOD,
                    default=DEFAULT_DEPARTED_TRAIN_GRACE_PERIOD,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_GRACE_PERIOD,
                        max=MAX_GRACE_PERIOD,
                        step=1,
                        unit_of_measurement="minutes",
                        mode=selector.NumberSelectorMode.SLIDER,
                    ),
                ),
            }
        )

        return self.async_show_form(
            step_id="settings",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "origin": self._origin_name,
                "destination": self._destination_name,
            },
        )

    async def async_step_return_journey(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Offer to set up the reverse commute if it doesn't already exist.

        Args:
            user_input: User input data

        Returns:
            FlowResult for creating entry
        """
        reverse_unique_id = f"{self._destination}_{self._origin}"
        reverse_exists = any(
            entry.unique_id == reverse_unique_id
            for entry in self._async_current_entries()
        )

        if reverse_exists:
            return self._create_entry()

        if user_input is not None:
            if user_input.get(CONF_ADD_RETURN_JOURNEY, False):
                self.hass.async_create_task(
                    self.hass.config_entries.flow.async_init(
                        DOMAIN,
                        context={"source": config_entries.SOURCE_IMPORT},
                        data={
                            CONF_API_KEY: self._api_key,
                            CONF_ORIGIN: self._destination,
                            CONF_DESTINATION: self._origin,
                            CONF_COMMUTE_NAME: f"{self._destination_name} to {self._origin_name}",
                            CONF_TIME_WINDOW: self._time_window,
                            CONF_NUM_SERVICES: self._num_services,
                            CONF_NIGHT_UPDATES: self._night_updates,
                            CONF_SEVERE_DELAY_THRESHOLD: self._severe_delay_threshold,
                            CONF_MAJOR_DELAY_THRESHOLD: self._major_delay_threshold,
                            CONF_MINOR_DELAY_THRESHOLD: self._minor_delay_threshold,
                            CONF_DEPARTED_TRAIN_GRACE_PERIOD: self._departed_train_grace_period,
                        },
                    )
                )
            return self._create_entry()

        return self.async_show_form(
            step_id="return_journey",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADD_RETURN_JOURNEY, default=True): selector.BooleanSelector(),
                }
            ),
            description_placeholders={
                "origin": self._origin_name,
                "destination": self._destination_name,
            },
        )

    async def async_step_import(
        self, user_input: dict[str, Any]
    ) -> FlowResult:
        """Handle automatic creation of a return journey config entry.

        Called programmatically (no UI) when the user accepts the return
        journey offer. Creates the reverse route using the same settings.

        Args:
            user_input: Pre-populated entry data for the reverse route

        Returns:
            FlowResult creating the entry, or aborting if already configured
        """
        await self.async_set_unique_id(
            f"{user_input[CONF_ORIGIN]}_{user_input[CONF_DESTINATION]}"
        )
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=user_input[CONF_COMMUTE_NAME],
            data=user_input,
        )

    def _create_entry(self) -> FlowResult:
        """Create the config entry using stored instance variables."""
        return self.async_create_entry(
            title=self._commute_name,
            data={
                CONF_API_KEY: self._api_key,
                CONF_ORIGIN: self._origin,
                CONF_DESTINATION: self._destination,
                CONF_COMMUTE_NAME: self._commute_name,
                CONF_TIME_WINDOW: self._time_window,
                CONF_NUM_SERVICES: self._num_services,
                CONF_NIGHT_UPDATES: self._night_updates,
                CONF_SEVERE_DELAY_THRESHOLD: self._severe_delay_threshold,
                CONF_MAJOR_DELAY_THRESHOLD: self._major_delay_threshold,
                CONF_MINOR_DELAY_THRESHOLD: self._minor_delay_threshold,
                CONF_DEPARTED_TRAIN_GRACE_PERIOD: self._departed_train_grace_period,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler.

        Args:
            config_entry: Config entry instance

        Returns:
            Options flow handler
        """
        return NationalRailCommuteOptionsFlow()


class NationalRailCommuteOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for My Rail Commute."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options.

        Args:
            user_input: User input data

        Returns:
            FlowResult for options
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate delay thresholds
            try:
                validate_delay_thresholds(
                    user_input[CONF_SEVERE_DELAY_THRESHOLD],
                    user_input[CONF_MAJOR_DELAY_THRESHOLD],
                    user_input[CONF_MINOR_DELAY_THRESHOLD],
                )
            except ValueError as err:
                errors["base"] = "invalid_thresholds"
                _LOGGER.error("Invalid delay thresholds: %s", err)

            if not errors:
                # Update the config entry
                return self.async_create_entry(title="", data=user_input)

        # Get current values
        current_data = self.config_entry.data
        options = self.config_entry.options

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_TIME_WINDOW,
                    default=options.get(
                        CONF_TIME_WINDOW,
                        current_data.get(CONF_TIME_WINDOW, DEFAULT_TIME_WINDOW),
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_TIME_WINDOW,
                        max=MAX_TIME_WINDOW,
                        step=5,
                        unit_of_measurement="minutes",
                        mode=selector.NumberSelectorMode.SLIDER,
                    ),
                ),
                vol.Required(
                    CONF_NUM_SERVICES,
                    default=options.get(
                        CONF_NUM_SERVICES,
                        current_data.get(CONF_NUM_SERVICES, DEFAULT_NUM_SERVICES),
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_NUM_SERVICES,
                        max=MAX_NUM_SERVICES,
                        step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                    ),
                ),
                vol.Required(
                    CONF_SEVERE_DELAY_THRESHOLD,
                    default=options.get(
                        CONF_SEVERE_DELAY_THRESHOLD,
                        current_data.get(
                            CONF_SEVERE_DELAY_THRESHOLD,
                            # Migration from old config
                            current_data.get(CONF_DISRUPTION_SINGLE_DELAY, DEFAULT_SEVERE_DELAY_THRESHOLD),
                        ),
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_DELAY_THRESHOLD,
                        max=MAX_DELAY_THRESHOLD,
                        step=1,
                        unit_of_measurement="minutes",
                        mode=selector.NumberSelectorMode.SLIDER,
                    ),
                ),
                vol.Required(
                    CONF_MAJOR_DELAY_THRESHOLD,
                    default=options.get(
                        CONF_MAJOR_DELAY_THRESHOLD,
                        current_data.get(
                            CONF_MAJOR_DELAY_THRESHOLD,
                            # Migration from old config
                            current_data.get(CONF_DISRUPTION_MULTIPLE_DELAY, DEFAULT_MAJOR_DELAY_THRESHOLD),
                        ),
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_DELAY_THRESHOLD,
                        max=MAX_DELAY_THRESHOLD,
                        step=1,
                        unit_of_measurement="minutes",
                        mode=selector.NumberSelectorMode.SLIDER,
                    ),
                ),
                vol.Required(
                    CONF_MINOR_DELAY_THRESHOLD,
                    default=options.get(
                        CONF_MINOR_DELAY_THRESHOLD,
                        current_data.get(CONF_MINOR_DELAY_THRESHOLD, DEFAULT_MINOR_DELAY_THRESHOLD),
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_DELAY_THRESHOLD,
                        max=MAX_DELAY_THRESHOLD,
                        step=1,
                        unit_of_measurement="minutes",
                        mode=selector.NumberSelectorMode.SLIDER,
                    ),
                ),
                vol.Required(
                    CONF_NIGHT_UPDATES,
                    default=options.get(
                        CONF_NIGHT_UPDATES,
                        current_data.get(CONF_NIGHT_UPDATES, DEFAULT_NIGHT_UPDATES),
                    ),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_DEPARTED_TRAIN_GRACE_PERIOD,
                    default=options.get(
                        CONF_DEPARTED_TRAIN_GRACE_PERIOD,
                        current_data.get(CONF_DEPARTED_TRAIN_GRACE_PERIOD, DEFAULT_DEPARTED_TRAIN_GRACE_PERIOD),
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_GRACE_PERIOD,
                        max=MAX_GRACE_PERIOD,
                        step=1,
                        unit_of_measurement="minutes",
                        mode=selector.NumberSelectorMode.SLIDER,
                    ),
                ),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=data_schema,
            errors=errors,
        )
