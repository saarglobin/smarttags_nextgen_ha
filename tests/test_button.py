"""Tests for the ring and refresh location buttons."""
from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.exceptions import HomeAssistantError

from custom_components.smarttags_nextgen.const import DOMAIN

from .common import TAG_A, create_entry

FIND = "https://smartthingsfind.samsung.com"
ADD = f"{FIND}/dm/addOperation.do"
TAG_LOCATION = f"{FIND}/dm/getTagLocation.do"
WAIT = "custom_components.smarttags_nextgen.coordinator.LOCATION_REQUEST_WAIT"


async def _setup(hass, mock_devices):
    mock_devices.append(TAG_A)
    entry = create_entry(hass, unique_id="12345")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.runtime_data.api.csrf_token = "token"
    return entry


async def _press(hass, entity_id):
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)


async def test_buttons_created(hass, mock_devices):
    await _setup(hass, mock_devices)
    assert hass.states.get("button.keys_ring").attributes["friendly_name"] == "Keys Ring"
    assert hass.states.get("button.keys_refresh_location") is not None


async def test_ring(hass, mock_devices, aioclient_mock):
    await _setup(hass, mock_devices)
    # tags answer without a request id
    aioclient_mock.post(ADD, json={"oprnType": "RING", "resultCode": "00"})

    await _press(hass, "button.keys_ring")

    ((_, url, data, _),) = aioclient_mock.mock_calls
    assert url.query["_csrf"] == "token"
    assert data == {"dvceId": "a", "operation": "RING", "usrId": 12345, "status": "start"}


async def test_ring_power_saving(hass, mock_devices, aioclient_mock):
    await _setup(hass, mock_devices)
    aioclient_mock.post(ADD, json={"resultCode": "01", "powerSavingResultCode": "00"})
    await _press(hass, "button.keys_ring")


async def test_ring_rejected(hass, mock_devices, aioclient_mock):
    await _setup(hass, mock_devices)
    aioclient_mock.post(ADD, json={"resultCode": "01", "faultCode": "BIZ-3100"})
    with pytest.raises(HomeAssistantError) as err:
        await _press(hass, "button.keys_ring")
    assert err.value.translation_key == "operation_failed"
    assert err.value.translation_placeholders["error"] == "Samsung rejected the RING request (01, BIZ-3100)"


async def test_ring_expired_session_starts_reauth(hass, mock_devices, aioclient_mock):
    entry = await _setup(hass, mock_devices)
    aioclient_mock.post(ADD, status=401)
    with pytest.raises(HomeAssistantError) as err:
        await _press(hass, "button.keys_ring")
    assert err.value.translation_key == "session_expired"
    await hass.async_block_till_done()
    (flow,) = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flow["context"]["source"] == SOURCE_REAUTH
    assert flow["context"]["entry_id"] == entry.entry_id


async def test_refresh_location(hass, mock_devices, aioclient_mock):
    await _setup(hass, mock_devices)
    aioclient_mock.post(ADD, json={"oprnType": "CHECK_CONNECTION_WITH_LOCATION", "resultCode": "00"})
    aioclient_mock.post(
        TAG_LOCATION,
        json={
            "resultCode": "00",
            "operation": [
                {"oprnType": "OFFLINE_LOC", "latitude": "5.5", "longitude": "6.5", "extra": {"gpsUtcDt": "20260110120000"}},
                {"oprnType": "CHECK_CONNECTION", "battery": "LOW"},
            ],
        },
    )

    with patch(WAIT, 0):
        await _press(hass, "button.keys_refresh_location")
        # the location is fetched in a background task
        await hass.async_block_till_done(wait_background_tasks=True)

    add, location = aioclient_mock.mock_calls
    assert add[2] == {"dvceId": "a", "operation": "CHECK_CONNECTION_WITH_LOCATION", "usrId": 12345}
    # asks for locations newer than the one already known
    assert location[2] == {"dvceId": "a", "latestTime": "20260102120000"}
    state = hass.states.get("device_tracker.keys")
    assert (state.attributes["latitude"], state.attributes["longitude"]) == (5.5, 6.5)
    assert state.attributes["last_seen"] == "2026-01-10T12:00:00+00:00"
    assert hass.states.get("sensor.keys_battery").state == "10"


async def test_refresh_location_keeps_newer_location(hass, mock_devices, aioclient_mock):
    await _setup(hass, mock_devices)
    aioclient_mock.post(ADD, json={"resultCode": "00"})
    aioclient_mock.post(
        TAG_LOCATION,
        json={"operation": [{"oprnType": "LOCATION", "latitude": "9", "longitude": "9", "extra": {"gpsUtcDt": "20250101000000"}}]},
    )
    with patch(WAIT, 0):
        await _press(hass, "button.keys_refresh_location")
        await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get("device_tracker.keys")
    assert (state.attributes["latitude"], state.attributes["longitude"]) == (3.0, 4.0)


async def test_refresh_location_rejected(hass, mock_devices, aioclient_mock):
    await _setup(hass, mock_devices)
    aioclient_mock.post(ADD, json={"resultCode": "01", "faultCode": "BIZ-3100"})
    with pytest.raises(HomeAssistantError):
        await _press(hass, "button.keys_refresh_location")
    assert len(aioclient_mock.mock_calls) == 1
