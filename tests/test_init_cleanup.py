"""Tests for stale train-entity cleanup, including leg-scoped entities."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.my_rail_commute import async_cleanup_stale_entities
from custom_components.my_rail_commute.const import CONF_NUM_SERVICES, DOMAIN


async def test_cleanup_removes_stale_single_leg_and_leg_scoped_train_entities(
    hass: HomeAssistant,
) -> None:
    """Reducing num_services removes excess train sensors for every leg."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_NUM_SERVICES: 3},
        unique_id="PAD_RDG_OXF",
    )
    entry.add_to_hass(hass)

    entity_reg = er.async_get(hass)
    kept_and_removed = {
        # Legacy single-leg shape
        f"{entry.entry_id}_train_1": True,
        f"{entry.entry_id}_train_3": True,
        f"{entry.entry_id}_train_5": False,
        # Leg-scoped shape
        f"{entry.entry_id}_leg1_train_1": True,
        f"{entry.entry_id}_leg1_train_3": True,
        f"{entry.entry_id}_leg1_train_5": False,
        f"{entry.entry_id}_leg2_train_2": True,
        f"{entry.entry_id}_leg2_train_4": False,
        # Non-train entities must never be touched
        f"{entry.entry_id}_summary": True,
        f"{entry.entry_id}_leg1_status": True,
    }

    entity_ids = {}
    for unique_id in kept_and_removed:
        entity = entity_reg.async_get_or_create(
            "sensor", DOMAIN, unique_id, config_entry=entry
        )
        entity_ids[unique_id] = entity.entity_id

    await async_cleanup_stale_entities(hass, entry)

    for unique_id, should_be_kept in kept_and_removed.items():
        still_exists = entity_reg.async_get(entity_ids[unique_id]) is not None
        assert still_exists == should_be_kept, (
            f"{unique_id}: expected kept={should_be_kept}"
        )
