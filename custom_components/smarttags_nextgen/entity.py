from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

# Samsung only reports a coarse battery level for tags (FULL was seen from a SmartTag2)
BATTERY_LEVELS = {"FULL": 100, "HIGH": 100, "MEDIUM": 50, "LOW": 10, "VERY_LOW": 5}


def battery_percentage(value):
    """Battery level in percent from Samsung's level name, or a number as other devices report."""
    if value in BATTERY_LEVELS:
        return BATTERY_LEVELS[value]
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return None


def async_setup_tag_entities(entry, async_add_entities, entity_factory):
    """Add entities for every tag, including tags added to the account after setup."""
    coordinator = entry.runtime_data
    known_device_ids = set()

    @callback
    def _async_add_new_tags():
        """Create entities for tags that are not tracked yet."""
        new_device_ids = [device_id for device_id in coordinator.data if device_id not in known_device_ids]
        if not new_device_ids:
            return
        known_device_ids.update(new_device_ids)
        async_add_entities(
            entity for device_id in new_device_ids for entity in entity_factory(coordinator, device_id)
        )

    _async_add_new_tags()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_tags))


class SmartTagEntity(CoordinatorEntity):
    """Base class for entities belonging to one SmartTag."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator)
        self.device_id = device_id
        tag_data = coordinator.data.get(device_id, {})
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=tag_data.get("name", "SmartTag"),
            manufacturer="Samsung",
            model=tag_data.get("model"),
        )

    # Helper property to quickly grab this specific tag's data block
    @property
    def tag_data(self):
        return self.coordinator.data.get(self.device_id, {})

    @property
    def available(self):
        # A tag removed from the Samsung account is no longer part of the coordinator data
        return super().available and self.device_id in self.coordinator.data
