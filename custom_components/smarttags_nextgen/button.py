from homeassistant.components.button import ButtonEntity
from .entity import SmartTagEntity, async_setup_tag_entities


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the ring and refresh location buttons for every SmartTag."""
    async_setup_tag_entities(
        entry,
        async_add_entities,
        lambda coordinator, device_id: [
            SmartTagRingButton(coordinator, device_id),
            SmartTagRefreshLocationButton(coordinator, device_id),
        ],
    )


class SmartTagRingButton(SmartTagEntity, ButtonEntity):
    """Make a SmartTag ring."""

    _attr_translation_key = "ring"

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"smarttag_{device_id}_ring"

    async def async_press(self):
        await self.coordinator.async_start_operation(self.device_id, "RING", {"status": "start"})


class SmartTagRefreshLocationButton(SmartTagEntity, ButtonEntity):
    """Ask a SmartTag for its current location instead of waiting for the next report."""

    _attr_translation_key = "refresh_location"

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"smarttag_{device_id}_refresh_location"

    async def async_press(self):
        await self.coordinator.async_request_location(self.device_id)
