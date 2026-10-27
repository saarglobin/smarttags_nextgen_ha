"""Tests for the ring switch of phones, tablets, watches and earbuds."""
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.smarttags_nextgen.coordinator import ring_state

from .common import PHONE, TAG_A, create_entry

ADD = "https://smartthingsfind.samsung.com/dm/addOperation.do"
RESULTS = "custom_components.smarttags_nextgen.api.SmartTagsAPI.get_operation_results"
SWITCH = "switch.galaxy_s_ring"


def result(status, request_id="r1", **extra):
    return {"oprnType": "RING", "reqId": request_id, "oprnStsCd": status, **extra}


RINGING = result("2800", extra={"status": "4"})
IDLE = result("2800", extra={"status": "0"})


@pytest.fixture(autouse=True)
def no_answer_wait():
    with patch("custom_components.smarttags_nextgen.switch.ANSWER_POLL_INTERVAL", 0):
        yield


async def _setup(hass, mock_devices, results=None):
    mock_devices.extend([TAG_A, PHONE])
    entry = create_entry(hass, unique_id="12345")
    with patch(RESULTS, AsyncMock(return_value=results or [])):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    entry.runtime_data.api.csrf_token = "token"
    return entry


async def _switch(hass, service):
    await hass.services.async_call("switch", service, {"entity_id": SWITCH}, blocking=True)


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (None, None),
        (result("1000"), "pending"),
        (result("2100"), "pending"),
        (result("2900", oprnResultCode="1452"), "error"),
        (result("1900"), "error"),
        (RINGING, "ringing"),
        (result("2800", extra={"status": "5"}), "ringing"),
        (IDLE, "idle"),
        # earbuds report each side; one ringing side is enough
        (result("2800", extra={"left": {"status": "2"}, "right": {"status": "4"}}), "ringing"),
        (result("2800", extra={"left": {"status": "2"}, "right": {"status": "2"}}), "idle"),
        (result("2800"), None),
        (result("2800", extra="garbage"), None),
    ],
)
def test_ring_state(operation, expected):
    assert ring_state(operation) == expected


async def test_switch_only_for_other_devices(hass, mock_devices):
    await _setup(hass, mock_devices)
    state = hass.states.get(SWITCH)
    assert state.state == "off"
    assert state.attributes["friendly_name"] == "Galaxy S Ring"
    # tags keep their ring button; other devices get no tracker, sensors or buttons
    assert hass.states.async_entity_ids("switch") == [SWITCH]
    assert hass.states.async_entity_ids("device_tracker") == ["device_tracker.keys"]
    assert not [e for e in hass.states.async_entity_ids() if e.startswith(("sensor.galaxy", "button.galaxy"))]


async def test_turn_on_and_follow_until_stopped(hass, mock_devices, aioclient_mock):
    await _setup(hass, mock_devices)
    aioclient_mock.post(ADD, json={"oprnType": "RING", "reqId": "r1", "resultCode": "00"})
    results = AsyncMock(side_effect=[[result("1000")], [RINGING]])
    with patch(RESULTS, results):
        await _switch(hass, "turn_on")

    ((_, _, data, _),) = aioclient_mock.mock_calls
    assert data == {"dvceId": "p", "operation": "RING", "usrId": 12345, "status": "start"}
    results.assert_awaited_with("p", 12345, "RING")
    assert hass.states.get(SWITCH).state == "on"

    # stays on while ringing, and turns off once the phone stopped on its own
    with patch(RESULTS, AsyncMock(return_value=[RINGING])):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
        await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == "on"
    with patch(RESULTS, AsyncMock(return_value=[IDLE])):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
        await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == "off"


async def test_turn_on_waits_for_the_request(hass, mock_devices, aioclient_mock):
    """The result of an earlier ring is not mistaken for the answer."""
    await _setup(hass, mock_devices)
    aioclient_mock.post(ADD, json={"reqId": "r2", "resultCode": "00"})
    results = AsyncMock(side_effect=[[IDLE], [IDLE, result("2900", "r2", oprnResultCode="507")]])
    with patch(RESULTS, results), pytest.raises(HomeAssistantError) as err:
        await _switch(hass, "turn_on")
    assert err.value.translation_key == "ring_on_call"
    assert err.value.translation_placeholders["name"] == "Galaxy S"
    assert hass.states.get(SWITCH).state == "off"


@pytest.mark.parametrize(
    ("code", "translation_key"),
    [("1452", "ring_find_my_mobile_off"), ("3009", "ring_earbuds_worn"), ("9999", "ring_failed")],
)
async def test_turn_on_failed(hass, mock_devices, aioclient_mock, code, translation_key):
    await _setup(hass, mock_devices)
    aioclient_mock.post(ADD, json={"reqId": "r1", "resultCode": "00"})
    with (
        patch(RESULTS, AsyncMock(return_value=[result("2900", oprnResultCode=code)])),
        pytest.raises(HomeAssistantError) as err,
    ):
        await _switch(hass, "turn_on")
    assert err.value.translation_key == translation_key
    assert err.value.translation_placeholders["code"] == code


async def test_turn_on_without_answer(hass, mock_devices, aioclient_mock, freezer):
    """A device that doesn't answer in time is shown as ringing until polling gives up."""
    await _setup(hass, mock_devices)
    aioclient_mock.post(ADD, json={"reqId": "r1", "resultCode": "00"})
    with patch(RESULTS, AsyncMock(return_value=[])):
        await _switch(hass, "turn_on")
        freezer.tick(timedelta(seconds=3))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert hass.states.get(SWITCH).state == "on"
        freezer.tick(timedelta(seconds=200))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == "off"


async def test_turn_off(hass, mock_devices, aioclient_mock):
    await _setup(hass, mock_devices, results=[RINGING])
    assert hass.states.get(SWITCH).state == "on"
    aioclient_mock.post(ADD, json={"reqId": "r2", "resultCode": "00"})
    with patch(RESULTS, AsyncMock(return_value=[result("2800", "r2", extra={"status": "0"})])):
        await _switch(hass, "turn_off")
    assert aioclient_mock.mock_calls[0][2]["status"] == "stop"
    assert hass.states.get(SWITCH).state == "off"


async def test_turn_off_when_not_ringing(hass, mock_devices, aioclient_mock):
    await _setup(hass, mock_devices)
    aioclient_mock.post(ADD, json={"reqId": "r2", "resultCode": "00"})
    with patch(RESULTS, AsyncMock(return_value=[result("2900", "r2", oprnResultCode="3008")])):
        await _switch(hass, "turn_off")
    assert hass.states.get(SWITCH).state == "off"


async def test_ringing_started_elsewhere(hass, mock_devices):
    """A ring started from the SmartThings app shows up with the next update and is followed."""
    entry = await _setup(hass, mock_devices)
    with patch(RESULTS, AsyncMock(return_value=[RINGING])):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == "on"
    with patch(RESULTS, AsyncMock(return_value=[IDLE])):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
        await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == "off"


async def test_unchanged_result_is_not_applied_again(hass, mock_devices, aioclient_mock):
    """Another device's update doesn't bring back a ring that was stopped since."""
    entry = await _setup(hass, mock_devices, results=[RINGING])
    aioclient_mock.post(ADD, json={"reqId": "r2", "resultCode": "00"})
    with patch(RESULTS, AsyncMock(return_value=[result("2800", "r2", extra={"status": "0"})])):
        await _switch(hass, "turn_off")
    coordinator = entry.runtime_data
    coordinator.async_set_updated_data({**coordinator.data, "a": {**coordinator.data["a"], "battery": "LOW"}})
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == "off"
