"""Home Assistant services for managing Delay Repay claims."""

from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from ..const import (
    DOMAIN,
    SERVICE_DISMISS_DELAY_REPAY,
    SERVICE_GET_DELAY_REPAY_CLAIMS,
    SERVICE_MARK_DELAY_REPAY_CLAIMED,
)
from .models import ClaimRecord, ClaimStatus
from .tracker import DelayRepayTracker

_SELECT_SCHEMA = {
    vol.Required("entry_id"): cv.string,
    vol.Optional("journeys"): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("from_date"): cv.date,
    vol.Optional("to_date"): cv.date,
}

_ALL_SERVICES = (
    SERVICE_MARK_DELAY_REPAY_CLAIMED,
    SERVICE_DISMISS_DELAY_REPAY,
    SERVICE_GET_DELAY_REPAY_CLAIMS,
)


def claim_to_dict(
    record: ClaimRecord, tracker: DelayRepayTracker, *, include_details: bool = False
) -> dict[str, Any]:
    """Return a record as a plain dict for attributes and service responses.

    The service snapshot is left out by default: with up to 30 claims in a
    sensor attribute it would push the state past the recorder's 16KB limit,
    so it is only returned by the get service.
    """
    data = record.to_dict()
    data["has_details"] = bool(record.details)
    if not include_details:
        data.pop("details", None)
    data["key"] = record.key
    data["claim_url"] = tracker.claim_url(record)
    data["claim_deadline"] = tracker.claim_deadline(record)
    return data


def _tracker_for(hass: HomeAssistant, entry_id: str) -> tuple[Any, DelayRepayTracker]:
    coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
    if coordinator is None:
        raise ServiceValidationError(f"No commute found with entry_id: {entry_id}")
    tracker = getattr(coordinator, "delay_repay", None)
    if tracker is None:
        raise ServiceValidationError(
            "Delay Repay tracking is not enabled for this commute"
        )
    return coordinator, tracker


def async_register_services(hass: HomeAssistant) -> None:
    """Register the Delay Repay services (once, domain-wide)."""

    async def _set_status(call: ServiceCall, status: ClaimStatus) -> None:
        coordinator, tracker = _tracker_for(hass, call.data["entry_id"])
        keys: list[str] | None = call.data.get("journeys")
        from_date: date | None = call.data.get("from_date")
        to_date: date | None = call.data.get("to_date")
        if not keys and from_date is None and to_date is None:
            raise ServiceValidationError(
                "Provide journeys, from_date or to_date to select what to change"
            )
        if from_date and to_date and from_date > to_date:
            raise ServiceValidationError("from_date must not be after to_date")
        await tracker.async_set_status(
            status, keys=keys, from_date=from_date, to_date=to_date
        )
        coordinator.async_update_listeners()

    async def _mark_claimed(call: ServiceCall) -> None:
        await _set_status(call, ClaimStatus.CLAIMED)

    async def _dismiss(call: ServiceCall) -> None:
        await _set_status(call, ClaimStatus.DISMISSED)

    async def _get_claims(call: ServiceCall) -> dict[str, Any]:
        _, tracker = _tracker_for(hass, call.data["entry_id"])
        keys = set(call.data.get("journeys") or [])
        if keys:
            # Asking for specific journeys: any status, so a card can show the
            # details of a live or handled one
            records = [r for r in tracker.records() if r.key in keys]
        else:
            statuses = (
                None if call.data["include_handled"] else (ClaimStatus.ELIGIBLE,)
            )
            records = tracker.records(statuses)
        return {
            "claims": [
                claim_to_dict(r, tracker, include_details=True) for r in records
            ]
        }

    if not hass.services.has_service(DOMAIN, SERVICE_MARK_DELAY_REPAY_CLAIMED):
        hass.services.async_register(
            DOMAIN,
            SERVICE_MARK_DELAY_REPAY_CLAIMED,
            _mark_claimed,
            schema=vol.Schema(_SELECT_SCHEMA),
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_DISMISS_DELAY_REPAY,
            _dismiss,
            schema=vol.Schema(_SELECT_SCHEMA),
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_DELAY_REPAY_CLAIMS,
            _get_claims,
            schema=vol.Schema(
                {
                    vol.Required("entry_id"): cv.string,
                    vol.Optional("include_handled", default=False): cv.boolean,
                    vol.Optional("journeys"): vol.All(cv.ensure_list, [cv.string]),
                }
            ),
            supports_response=SupportsResponse.ONLY,
        )


def async_remove_services(hass: HomeAssistant) -> None:
    """Remove the Delay Repay services."""
    for name in _ALL_SERVICES:
        hass.services.async_remove(DOMAIN, name)
