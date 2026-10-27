from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE
from .entity import SmartTagEntity, async_setup_tag_entities, battery_percentage


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the battery and last seen sensors for every SmartTag."""
    async_setup_tag_entities(
        entry,
        async_add_entities,
        lambda coordinator, device_id: [
            SmartTagBatterySensor(coordinator, device_id),
            SmartTagLastSeenSensor(coordinator, device_id),
        ],
    )


class SmartTagBatterySensor(SmartTagEntity, SensorEntity):
    """Battery level of a SmartTag."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"smarttag_{device_id}_battery"

    @property
    def native_value(self):
        return battery_percentage(self.tag_data.get("battery"))


class SmartTagLastSeenSensor(SmartTagEntity, SensorEntity):
    """Time the location of a SmartTag was last reported."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "last_seen"

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"smarttag_{device_id}_last_seen"

    @property
    def native_value(self):
        return self.tag_data.get("gps_date")
