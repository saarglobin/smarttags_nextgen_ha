"""Actions to ask tags for their location and to refresh all devices, e.g. from automations."""
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids
from .const import DOMAIN

SERVICE_LOCATE = "locate"
SERVICE_REFRESH = "refresh"


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions, once for all config entries."""

    async def async_locate(call: ServiceCall) -> None:
        """Ask the targeted tags for their current location, like their refresh location button."""
        tags = _targeted_tags(hass, call)
        if not tags:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_tags_targeted")
        for coordinator, device_id in tags:
            await coordinator.async_request_location(device_id)

    async def async_refresh(call: ServiceCall) -> None:
        """Update all devices now instead of waiting for the next scheduled update."""
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            coordinator = entry.runtime_data
            await coordinator.async_refresh()
            if not coordinator.last_update_success:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="refresh_failed",
                    translation_placeholders={"error": str(coordinator.last_exception)},
                )

    hass.services.async_register(DOMAIN, SERVICE_LOCATE, async_locate)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH, async_refresh)


def _targeted_tags(hass, call):
    """The coordinator and Samsung device id of every tag the call targets.

    Devices, areas and labels are resolved to their entities first, so any target
    that includes one of a tag's entities selects the tag.
    """
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    coordinators = {entry.entry_id: entry.runtime_data for entry in hass.config_entries.async_loaded_entries(DOMAIN)}
    selected = async_extract_referenced_entity_ids(hass, TargetSelection(call.data))
    tags = {}
    for entity_id in selected.referenced | selected.indirectly_referenced:
        entity = entity_registry.async_get(entity_id)
        if entity is None or entity.platform != DOMAIN or entity.device_id is None:
            continue
        coordinator = coordinators.get(entity.config_entry_id)
        device = device_registry.async_get(entity.device_id)
        if coordinator is None or device is None:
            continue
        for domain, device_id in device.identifiers:
            if domain == DOMAIN and (coordinator.data or {}).get(device_id, {}).get("is_tag"):
                tags[(entity.config_entry_id, device_id)] = (coordinator, device_id)
    return list(tags.values())
