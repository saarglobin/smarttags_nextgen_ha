from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import callback
from .entity import SmartTagEntity, async_setup_tag_entities

# The tag data shown by the tracker's state
LOCATION_KEYS = ("latitude", "longitude", "gps_accuracy", "gps_date", "location_type")

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
        self._written_location = None

    def _location(self):
        """What the state shows: availability and the tag's location."""
        return (self.available, *(self.tag_data.get(key) for key in LOCATION_KEYS))

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._written_location = self._location()

    @callback
    def _handle_coordinator_update(self):
        # A tracker writes its state on every update, even an unchanged one, which renews
        # last_updated. The person integration follows the GPS tracker updated last, so a tag
        # that hasn't moved would win over a phone whenever any tag's data changed.
        location = self._location()
        if location == self._written_location:
            return
        self._written_location = location
        self.async_write_ha_state()

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
    def icon(self):
        return "mdi:tag-location"
        
    @property
    def extra_state_attributes(self):
        gps_date = self.tag_data.get("gps_date")
        return {
            "location_type": self.tag_data.get("location_type"),
            "last_seen": gps_date.isoformat() if gps_date else None,
        }
