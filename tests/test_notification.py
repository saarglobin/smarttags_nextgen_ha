"""Tests for the notification about an expired session."""
from unittest.mock import AsyncMock, patch

from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import async_get_persistent_notifications

from custom_components.smarttags_nextgen.api import SmartTagsAuthError

from .common import TAG_A, create_entry

API = "custom_components.smarttags_nextgen.api.SmartTagsAPI"
NOTIFICATION = "smarttags_nextgen_session_expired"
ADD = "https://smartthingsfind.samsung.com/dm/addOperation.do"


async def _setup(hass, mock_devices, unique_id="12345"):
    mock_devices.append(TAG_A)
    entry = create_entry(hass, unique_id=unique_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _expire(entry):
    with patch(f"{API}.get_devices", AsyncMock(side_effect=SmartTagsAuthError)):
        await entry.runtime_data.async_refresh()


async def test_expired_session_notifies_until_updated(hass, mock_devices):
    entry = await _setup(hass, mock_devices)
    assert NOTIFICATION not in async_get_persistent_notifications(hass)

    await _expire(entry)
    notification = async_get_persistent_notifications(hass)[NOTIFICATION]
    assert notification["title"] == "SmartThings Find session expired"
    assert entry.title in notification["message"]

    # e.g. after signing in again
    await entry.runtime_data.async_refresh()
    assert NOTIFICATION not in async_get_persistent_notifications(hass)


async def test_expired_session_on_setup_notifies(hass, mock_devices):
    with patch(f"{API}.get_devices", AsyncMock(side_effect=SmartTagsAuthError)):
        entry = create_entry(hass)
        assert not await hass.config_entries.async_setup(entry.entry_id)
    assert NOTIFICATION in async_get_persistent_notifications(hass)


async def test_expired_session_on_ring_notifies(hass, mock_devices, aioclient_mock):
    entry = await _setup(hass, mock_devices)
    entry.runtime_data.api.csrf_token = "token"
    aioclient_mock.post(ADD, status=401)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("button", "press", {"entity_id": "button.keys_ring"}, blocking=True)
    assert NOTIFICATION in async_get_persistent_notifications(hass)


async def test_notification_stays_while_another_session_is_expired(hass, mock_devices):
    first = await _setup(hass, mock_devices, unique_id="1")
    second = create_entry(hass, unique_id="2")
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    await _expire(first)
    await _expire(second)
    await first.runtime_data.async_refresh()
    assert NOTIFICATION in async_get_persistent_notifications(hass)

    # removing the entry dismisses it too
    await hass.config_entries.async_remove(second.entry_id)
    assert NOTIFICATION not in async_get_persistent_notifications(hass)
