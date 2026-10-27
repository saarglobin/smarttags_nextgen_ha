from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the SmartTag device tracker platform for multiple tags."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known_device_ids = set()

    @callback
    def _async_add_new_tags():
        """Create entities for tags that are not tracked yet, including ones added to the account later."""
        new_device_ids = [device_id for device_id in coordinator.data if device_id not in known_device_ids]
        if not new_device_ids:
            return
        known_device_ids.update(new_device_ids)
        async_add_entities(SmartTagTracker(coordinator, device_id) for device_id in new_device_ids)

    _async_add_new_tags()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_tags))

class SmartTagTracker(CoordinatorEntity, TrackerEntity):
    """Representation of a specific Samsung SmartTag on the HA Map."""

    # The entity is the main feature of the tag's device, so it takes the device name
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator)
        self.device_id = device_id
        self._attr_unique_id = f"smarttag_{device_id}"
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

    @property
    def latitude(self):
        return self.tag_data.get("latitude")

    @property
    def longitude(self):
        return self.tag_data.get("longitude")

    @property
    def location_accuracy(self):
        return self.tag_data.get("gps_accuracy") or 0

    @property
    def source_type(self):
        return "gps"

    @property
    def battery_level(self):
        battery_map = {"HIGH": 100, "MEDIUM": 50, "LOW": 10}
        current_battery = self.tag_data.get("battery", "UNKNOWN")
        return battery_map.get(current_battery)

    @property
    def icon(self):
        return "mdi:tag-location"
        
    @property
    def extra_state_attributes(self):
        gps_date = self.tag_data.get("gps_date")
        return {
            "location_type": self.tag_data.get("location_type"),
            "last_seen": gps_date.isoformat() if gps_date else None,
        }