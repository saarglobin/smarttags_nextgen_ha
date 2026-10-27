from homeassistant.components.device_tracker import SourceType, TrackerEntity
from .entity import BATTERY_LEVELS, SmartTagEntity, async_setup_tag_entities

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the SmartTag device tracker platform for multiple tags."""
    async_setup_tag_entities(
        entry, async_add_entities, lambda coordinator, device_id: [SmartTagTracker(coordinator, device_id)]
    )

class SmartTagTracker(SmartTagEntity, TrackerEntity):
    """Representation of a specific Samsung SmartTag on the HA Map."""

    # The entity is the main feature of the tag's device, so it takes the device name
    _attr_name = None

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"smarttag_{device_id}"

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
        return SourceType.GPS

    @property
    def battery_level(self):
        return BATTERY_LEVELS.get(self.tag_data.get("battery"))

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
