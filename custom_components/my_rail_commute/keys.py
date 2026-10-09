"""Sharing of the optional per-product API keys between config entries."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN


def find_shared_key(
    hass: HomeAssistant, conf_key: str, exclude_entry_id: str | None = None
) -> str | None:
    """Return a key already entered on any other entry, if there is one.

    The arrival board and service details keys belong to the user's Rail Data
    subscriptions, not to a commute, so they are only asked for once.
    """
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == exclude_entry_id:
            continue
        value: Any = {**entry.data, **entry.options}.get(conf_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def resolve_key(
    hass: HomeAssistant,
    conf_key: str,
    config: dict[str, Any],
    entry_id: str | None = None,
) -> str | None:
    """Return the entry's own key for a product, else one shared by another."""
    own = config.get(conf_key)
    if isinstance(own, str) and own.strip():
        return own.strip()
    return find_shared_key(hass, conf_key, exclude_entry_id=entry_id)
