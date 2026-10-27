"""Tests for the locate and refresh actions."""
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr

from custom_components.smarttags_nextgen.api import SmartTagsConnectionError
from custom_components.smarttags_nextgen.const import DOMAIN

from .common import OPERATIONS, PHONE, TAG_A, TAG_B, create_entry

FIND = "https://smartthingsfind.samsung.com"
ADD = f"{FIND}/dm/addOperation.do"
TAG_LOCATION = f"{FIND}/dm/getTagLocation.do"
WAIT = "custom_components.smarttags_nextgen.coordinator.LOCATION_REQUEST_WAIT"
API = "custom_components.smarttags_nextgen.api.SmartTagsAPI"


async def _setup(hass, mock_devices):
    mock_devices.extend([TAG_A, TAG_B, PHONE])
    entry = create_entry(hass, unique_id="12345")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.runtime_data.api.csrf_token = "token"
    return entry


async def _locate(hass, target):
    await hass.services.async_call(DOMAIN, "locate", target=target, blocking=True)


def _requested(aioclient_mock):
    return [data["dvceId"] for _, url, data, _ in aioclient_mock.mock_calls if str(url).startswith(ADD)]


async def test_locate_entity(hass, mock_devices, aioclient_mock):
    await _setup(hass, mock_devices)
    aioclient_mock.post(ADD, json={"oprnType": "CHECK_CONNECTION_WITH_LOCATION", "resultCode": "00"})
    aioclient_mock.post(
        TAG_LOCATION,
        json={"operation": [{"oprnType": "LOCATION", "latitude": "5.5", "longitude": "6.5", "extra": {"gpsUtcDt": "20260110120000"}}]},
    )

    with patch(WAIT, 0):
        await _locate(hass, {"entity_id": "device_tracker.keys"})
        await hass.async_block_till_done(wait_background_tasks=True)

    assert _requested(aioclient_mock) == ["a"]
    state = hass.states.get("device_tracker.keys")
    assert (state.attributes["latitude"], state.attributes["longitude"]) == (5.5, 6.5)


async def test_locate_devices(hass, mock_devices, aioclient_mock):
    entry = await _setup(hass, mock_devices)
    aioclient_mock.post(ADD, json={"resultCode": "00"})
    aioclient_mock.post(TAG_LOCATION, json={"operation": []})
    registry = dr.async_get(hass)
    device_ids = [registry.async_get_device_by_identifier((DOMAIN, tag), entry.entry_id).id for tag in ("a", "b")]

    with patch(WAIT, 0):
        # several entities of the same tag request its location once
        await _locate(hass, {"device_id": device_ids, "entity_id": ["button.keys_ring", "device_tracker.keys"]})
        await hass.async_block_till_done(wait_background_tasks=True)

    assert sorted(_requested(aioclient_mock)) == ["a", "b"]


async def test_locate_without_tags(hass, mock_devices, aioclient_mock):
    entry = await _setup(hass, mock_devices)
    phone = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "p"), entry.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await _locate(hass, {"device_id": phone.id})
    assert err.value.translation_key == "no_tags_targeted"
    assert aioclient_mock.mock_calls == []


async def test_locate_rejected(hass, mock_devices, aioclient_mock):
    await _setup(hass, mock_devices)
    aioclient_mock.post(ADD, json={"resultCode": "01", "faultCode": "BIZ-3100"})
    with pytest.raises(HomeAssistantError) as err:
        await _locate(hass, {"entity_id": "device_tracker.keys"})
    assert err.value.translation_key == "operation_failed"


async def test_refresh(hass, mock_devices):
    await _setup(hass, mock_devices)
    operations = {**OPERATIONS, "a": [{"oprnType": "LOCATION", "latitude": "7", "longitude": "8"}]}
    with patch(f"{API}.set_last_select", AsyncMock(side_effect=operations.get)):
        await hass.services.async_call(DOMAIN, "refresh", blocking=True)

    state = hass.states.get("device_tracker.keys")
    assert (state.attributes["latitude"], state.attributes["longitude"]) == (7.0, 8.0)


async def test_refresh_failed(hass, mock_devices):
    await _setup(hass, mock_devices)
    with (
        patch(f"{API}.get_devices", AsyncMock(side_effect=SmartTagsConnectionError("Samsung is down"))),
        pytest.raises(HomeAssistantError) as err,
    ):
        await hass.services.async_call(DOMAIN, "refresh", blocking=True)
    assert err.value.translation_key == "refresh_failed"
    assert err.value.translation_placeholders["error"] == "Samsung is down"
