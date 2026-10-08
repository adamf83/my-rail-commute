"""Tests for the Delay Repay services."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.my_rail_commute.const import (
    DOMAIN,
    SERVICE_DISMISS_DELAY_REPAY,
    SERVICE_GET_DELAY_REPAY_CLAIMS,
    SERVICE_MARK_DELAY_REPAY_CLAIMED,
)
from custom_components.my_rail_commute.delay_repay.models import ClaimStatus
from custom_components.my_rail_commute.delay_repay.schemes import build_scheme_set
from custom_components.my_rail_commute.delay_repay.services import (
    async_register_services,
    async_remove_services,
    claim_to_dict,
)

from .delay_repay_helpers import (
    NOW,
    fake_storage,
    late_service,
    make_tracker,
    single_leg,
)

KEY = "2026-10-08|1|9494208WHYTELF"


class _Call:
    def __init__(self, **data):
        self.data = data


def _register(hass):
    handlers = {}

    def capture(domain, name, handler, **kwargs):
        handlers[name] = handler
        handlers[f"{name}:kwargs"] = kwargs

    hass.services.has_service = MagicMock(return_value=False)
    hass.services.async_register = MagicMock(side_effect=capture)
    async_register_services(hass)
    return handlers


async def _setup(**tracker_kwargs):
    tracker, _ = await make_tracker(**tracker_kwargs)
    await tracker.async_observe(single_leg([late_service()]), NOW)
    await tracker.async_maintain(NOW + timedelta(minutes=40))
    coordinator = MagicMock()
    coordinator.delay_repay = tracker
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry1": coordinator}}
    return hass, coordinator, tracker, _register(hass)


async def test_registers_all_three_services_once():
    with fake_storage():
        hass, _, _, handlers = await _setup()
        for name in (
            SERVICE_MARK_DELAY_REPAY_CLAIMED,
            SERVICE_DISMISS_DELAY_REPAY,
            SERVICE_GET_DELAY_REPAY_CLAIMS,
        ):
            assert name in handlers
        assert hass.services.async_register.call_count == 3

        # Already registered (second entry enabling it): nothing more registered
        hass.services.async_register.reset_mock()
        hass.services.has_service = MagicMock(return_value=True)
        async_register_services(hass)
        hass.services.async_register.assert_not_called()


async def test_get_claims_returns_the_outstanding_list():
    with fake_storage():
        _, _, _, handlers = await _setup(
            schemes=build_scheme_set("15", "Southern = 15 | https://example.com/c")
        )
        result = await handlers[SERVICE_GET_DELAY_REPAY_CLAIMS](
            _Call(entry_id="entry1", include_handled=False)
        )
        (claim,) = result["claims"]
        assert claim["key"] == KEY
        assert claim["delay_minutes"] == 17
        assert claim["claim_url"] == "https://example.com/c"
        assert claim["claim_deadline"] == "2026-11-05"
        assert claim["status"] == "eligible"
        assert (
            handlers[f"{SERVICE_GET_DELAY_REPAY_CLAIMS}:kwargs"]["supports_response"]
            is not None
        )


async def test_get_claims_can_include_handled_journeys():
    with fake_storage():
        _, _, tracker, handlers = await _setup()
        await tracker.async_set_status(ClaimStatus.CLAIMED, keys=[KEY])
        call = _Call(entry_id="entry1", include_handled=False)
        assert (await handlers[SERVICE_GET_DELAY_REPAY_CLAIMS](call))["claims"] == []
        call = _Call(entry_id="entry1", include_handled=True)
        (claim,) = (await handlers[SERVICE_GET_DELAY_REPAY_CLAIMS](call))["claims"]
        assert claim["status"] == "claimed"


async def test_mark_claimed_by_key_updates_state_and_notifies():
    with fake_storage():
        _, coordinator, tracker, handlers = await _setup()
        await handlers[SERVICE_MARK_DELAY_REPAY_CLAIMED](
            _Call(entry_id="entry1", journeys=[KEY])
        )
        assert tracker.records()[0].status is ClaimStatus.CLAIMED
        coordinator.async_update_listeners.assert_called_once()


async def test_dismiss_by_date_range():
    with fake_storage():
        _, coordinator, tracker, handlers = await _setup()
        await handlers[SERVICE_DISMISS_DELAY_REPAY](
            _Call(
                entry_id="entry1",
                from_date=date(2026, 10, 1),
                to_date=date(2026, 10, 31),
            )
        )
        assert tracker.records()[0].status is ClaimStatus.DISMISSED
        coordinator.async_update_listeners.assert_called_once()


async def test_status_services_require_a_selector():
    with fake_storage():
        _, _, tracker, handlers = await _setup()
        with pytest.raises(ServiceValidationError, match="Provide journeys"):
            await handlers[SERVICE_MARK_DELAY_REPAY_CLAIMED](_Call(entry_id="entry1"))
        assert tracker.records()[0].status is ClaimStatus.ELIGIBLE


async def test_status_services_reject_inverted_date_range():
    with fake_storage():
        _, _, _, handlers = await _setup()
        with pytest.raises(ServiceValidationError, match="from_date"):
            await handlers[SERVICE_DISMISS_DELAY_REPAY](
                _Call(
                    entry_id="entry1",
                    from_date=date(2026, 10, 9),
                    to_date=date(2026, 10, 1),
                )
            )


async def test_unknown_entry_is_rejected():
    with fake_storage():
        _, _, _, handlers = await _setup()
        with pytest.raises(ServiceValidationError, match="No commute found"):
            await handlers[SERVICE_GET_DELAY_REPAY_CLAIMS](
                _Call(entry_id="nope", include_handled=False)
            )


async def test_entry_without_delay_repay_is_rejected():
    with fake_storage():
        hass, coordinator, _, handlers = await _setup()
        coordinator.delay_repay = None
        with pytest.raises(ServiceValidationError, match="not enabled"):
            await handlers[SERVICE_MARK_DELAY_REPAY_CLAIMED](
                _Call(entry_id="entry1", journeys=[KEY])
            )


async def test_remove_services():
    hass = MagicMock()
    async_remove_services(hass)
    removed = {c.args[1] for c in hass.services.async_remove.call_args_list}
    assert removed == {
        SERVICE_MARK_DELAY_REPAY_CLAIMED,
        SERVICE_DISMISS_DELAY_REPAY,
        SERVICE_GET_DELAY_REPAY_CLAIMS,
    }


async def test_claim_to_dict_adds_key_url_and_deadline():
    with fake_storage():
        _, _, tracker, _ = await _setup()
        data = claim_to_dict(tracker.records()[0], tracker)
        assert data["key"] == KEY
        assert data["claim_url"] is None
        assert data["claim_deadline"] == "2026-11-05"
        assert data["tier"] == 15
