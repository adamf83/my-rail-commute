"""Config flow for National Rail Commute integration."""
from __future__ import annotations

import logging
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
    CONF_COMMUTE_NAME,
    CONF_DESTINATION,
    CONF_NIGHT_UPDATES,
    CONF_NUM_SERVICES,
    CONF_ORIGIN,
    CONF_TIME_WINDOW,
    DEFAULT_NAME,
    DEFAULT_NIGHT_UPDATES,
    DEFAULT_NUM_SERVICES,
    DEFAULT_TIME_WINDOW,
    DOMAIN,
    MAX_NUM_SERVICES,
    MAX_TIME_WINDOW,
    MIN_NUM_SERVICES,
    MIN_TIME_WINDOW,
)

_LOGGER = logging.getLogger(__name__)

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


class NationalRailCommuteConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for National Rail Commute."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._api_key: str | None = None
        self._origin: str | None = None
        self._destination: str | None = None
        self._origin_name: str | None = None
        self._destination_name: str | None = None

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

    async def async_step_stations(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle station configuration step.

        Args:
            user_input: User input data

        Returns:
            FlowResult for next step or errors
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                origin = user_input[CONF_ORIGIN]
                destination = user_input[CONF_DESTINATION]

                # Validate stations
                station_info = await validate_stations(
                    self.hass, self._api_key, origin, destination
                )

                self._origin = origin.upper()
                self._destination = destination.upper()
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

        data_schema = vol.Schema(
            {
                vol.Required(CONF_ORIGIN): str,
                vol.Required(CONF_DESTINATION): str,
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
        if user_input is not None:
            # Create the config entry
            commute_name = user_input.get(
                CONF_COMMUTE_NAME,
                f"{self._origin_name} to {self._destination_name}",
            )

            # Set unique ID based on route
            await self.async_set_unique_id(
                f"{self._origin}_{self._destination}"
            )
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=commute_name,
                data={
                    CONF_API_KEY: self._api_key,
                    CONF_ORIGIN: self._origin,
                    CONF_DESTINATION: self._destination,
                    CONF_COMMUTE_NAME: commute_name,
                    CONF_TIME_WINDOW: user_input[CONF_TIME_WINDOW],
                    CONF_NUM_SERVICES: user_input[CONF_NUM_SERVICES],
                    CONF_NIGHT_UPDATES: user_input[CONF_NIGHT_UPDATES],
                },
            )

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
                    CONF_NIGHT_UPDATES,
                    default=DEFAULT_NIGHT_UPDATES,
                ): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="settings",
            data_schema=data_schema,
            description_placeholders={
                "origin": self._origin_name,
                "destination": self._destination_name,
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
    """Handle options flow for National Rail Commute."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options.

        Args:
            user_input: User input data

        Returns:
            FlowResult for options
        """
        if user_input is not None:
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
                    CONF_NIGHT_UPDATES,
                    default=options.get(
                        CONF_NIGHT_UPDATES,
                        current_data.get(CONF_NIGHT_UPDATES, DEFAULT_NIGHT_UPDATES),
                    ),
                ): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=data_schema,
        )
