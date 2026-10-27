import asyncio
from datetime import timedelta
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from .const import DOMAIN
from .coordinator import RING_ERROR, RING_IDLE, RING_NOT_RINGING, RING_PENDING, RING_RINGING, ring_state
from .entity import SmartTagEntity, async_setup_other_device_entities

# Devices answer a ring request after about 2 seconds; wait up to 10 seconds
ANSWER_POLL_INTERVAL = 1
ANSWER_POLLS = 10
# While a device rings, check every few seconds whether it stopped. Phones stop on their own
# after 60 seconds, earbuds after about 190 seconds.
RING_POLL_INTERVAL = timedelta(seconds=3)
RING_POLL_TIMEOUT = timedelta(seconds=200)

# Result codes of a failed ring, as the SmartThings Find website explains them
RING_ERRORS = {"1452": "ring_find_my_mobile_off", "507": "ring_on_call", "3009": "ring_earbuds_worn"}


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the ring switch for every device that isn't a tag."""
    async_setup_other_device_entities(
        entry, async_add_entities, lambda coordinator, device_id: [DeviceRingSwitch(coordinator, device_id)]
    )


class DeviceRingSwitch(SmartTagEntity, SwitchEntity):
    """Make a phone, tablet, watch or earbuds ring, showing whether it rings.

    Tags have a ring button instead, as they don't report whether they ring and Samsung
    can't stop a tag rung from the SmartThings Find website.
    """

    _attr_translation_key = "ring"

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"smarttag_{device_id}_ring"
        self._attr_is_on = False
        self._unsub_poll = None
        self._poll_until = None
        self._coordinator_result = None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._coordinator_result = self.tag_data.get("ring")
        self._apply(ring_state(self._coordinator_result))
        self.async_on_remove(self._stop_polling)

    @callback
    def _handle_coordinator_update(self):
        result = self.tag_data.get("ring")
        # Updates of other devices repeat a result that may be older than the switch's state. While
        # polling, the polled state is more recent anyway.
        if result != self._coordinator_result and self._unsub_poll is None:
            if self._apply(ring_state(result)) == RING_RINGING:
                # Rung from the SmartThings app or website
                self._start_polling()
        self._coordinator_result = result
        super()._handle_coordinator_update()

    def _apply(self, state):
        if state == RING_RINGING:
            self._attr_is_on = True
        elif state in (RING_IDLE, RING_ERROR):
            self._attr_is_on = False
        return state

    async def async_turn_on(self, **kwargs):
        state, result = await self._async_request("start")
        if state == RING_ERROR:
            self._attr_is_on = False
            self.async_write_ha_state()
            raise self._ring_error(result)
        # Also while the device hasn't answered yet
        self._attr_is_on = True
        self.async_write_ha_state()
        self._start_polling()

    async def async_turn_off(self, **kwargs):
        state, result = await self._async_request("stop")
        if state == RING_ERROR and str(result.get("oprnResultCode")) != RING_NOT_RINGING:
            raise self._ring_error(result)
        self._stop_polling()
        self._attr_is_on = False
        self.async_write_ha_state()

    def _ring_error(self, result):
        code = str(result.get("oprnResultCode"))
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=RING_ERRORS.get(code, "ring_failed"),
            translation_placeholders={"name": self.tag_data.get("name"), "code": code},
        )

    async def _async_request(self, status):
        """Start or stop ringing and wait for the device to answer; returns its state and result."""
        request_id = await self.coordinator.async_start_operation(self.device_id, "RING", {"status": status})
        if request_id is None:
            return None, None
        state, result = None, None
        for _ in range(ANSWER_POLLS):
            await asyncio.sleep(ANSWER_POLL_INTERVAL)
            result = await self.coordinator.async_get_ring_result(self.device_id, request_id)
            state = ring_state(result)
            if result is not None and state != RING_PENDING:
                break
        return state, result

    @callback
    def _start_polling(self):
        self._poll_until = dt_util.utcnow() + RING_POLL_TIMEOUT
        if self._unsub_poll is None:
            self._unsub_poll = async_track_time_interval(self.hass, self._async_poll, RING_POLL_INTERVAL)

    @callback
    def _stop_polling(self):
        if self._unsub_poll is not None:
            self._unsub_poll()
            self._unsub_poll = None

    async def _async_poll(self, _now):
        state = self._apply(ring_state(await self.coordinator.async_get_ring_result(self.device_id)))
        # An unknown state, e.g. after a failed request, keeps polling until the timeout
        if state in (RING_IDLE, RING_ERROR) or dt_util.utcnow() >= self._poll_until:
            self._stop_polling()
            # A device that never answered is assumed to have stopped by now. One still ringing
            # is polled again once the coordinator sees it ringing.
            if state != RING_RINGING:
                self._attr_is_on = False
        self.async_write_ha_state()
