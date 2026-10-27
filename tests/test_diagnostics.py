import json

from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry

from .common import TAG_A, create_account_entry, create_entry


def _values(data):
    """All leaf values of nested dicts and lists."""
    if isinstance(data, dict):
        for value in data.values():
            yield from _values(value)
    elif isinstance(data, list):
        for value in data:
            yield from _values(value)
    else:
        yield data


async def test_diagnostics_redacts_private_data(hass, hass_client, mock_devices):
    assert await async_setup_component(hass, "diagnostics", {})
    mock_devices.append(TAG_A)
    entry = create_entry(hass, unique_id="12345")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    dumped = json.dumps(diag)
    assert "12345" not in dumped  # account id
    assert '"x"' not in dumped  # session id
    assert diag["entry"]["data"]["jsession_id"] == "**REDACTED**"
    assert diag["last_update_success"] is True

    (tag,) = diag["tags"]
    assert tag["data"]["latitude"] == "**REDACTED**"
    assert tag["data"]["longitude"] == "**REDACTED**"
    assert tag["data"]["name"] == "Keys"
    assert tag["data"]["battery"] == "MEDIUM"
    # structure only, no values
    assert tag["last_operations"][1] == {
        "oprnType": "OFFLINE_LOC",
        "keys": ["extra", "horizontalUncertainty", "latitude", "longitude", "oprnType", "verticalUncertainty"],
        "extra_keys": ["gpsUtcDt"],
    }
    # the coordinates (3.0, 4.0) don't appear anywhere, also not in the raw operations
    assert not {3.0, 4.0, "3.0", "4.0"} & set(_values(diag))


async def test_diagnostics_redacts_account_credentials(hass, hass_client, mock_devices):
    assert await async_setup_component(hass, "diagnostics", {})
    mock_devices.append(TAG_A)
    entry = create_account_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    dumped = json.dumps(diag)
    for secret in ("master-token", "me@example.com", "abcdef"):
        assert secret not in dumped
    for key in ("userauth_token", "user_id", "login_id", "device_id"):
        assert diag["entry"]["data"][key] == "**REDACTED**"
    assert diag["entry"]["data"]["auth_method"] == "account"
    assert diag["entry"]["data"]["auth_server_url"] == "https://eu-auth2.samsungosp.com"
