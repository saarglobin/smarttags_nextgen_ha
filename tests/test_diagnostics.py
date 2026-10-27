import json

from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry

from .common import TAG_A, create_entry


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
    assert "3.0" not in dumped and "4.0" not in dumped
