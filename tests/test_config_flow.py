"""Tests for the reconfigure and options flows and the region default."""
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from custom_components.smarttags_nextgen.const import DOMAIN

from .common import TAG_A, create_entry

API = "custom_components.smarttags_nextgen.api.SmartTagsAPI"


@pytest.mark.parametrize(("server_region", "expected"), [("prd-us", "prd-us"), ("prd-xx", "prd-eu"), (None, "prd-eu")])
async def test_user_form_preselects_server_region(hass, mock_devices, server_region, expected):
    with patch(
        "custom_components.smarttags_nextgen.config_flow.async_get_server_region",
        AsyncMock(return_value=server_region),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] == FlowResultType.FORM
    assert result["data_schema"]({"jsession_id": "y"})["region"] == expected


async def _setup(hass, mock_devices, region="prd-eu"):
    mock_devices.append(TAG_A)
    entry = create_entry(hass, unique_id="12345")
    hass.config_entries.async_update_entry(entry, data={**entry.data, "region": region})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_reconfigure(hass, mock_devices):
    entry = await _setup(hass, mock_devices, region="prd-zz")
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    # a custom region is shown as "custom" with the value prefilled
    defaults = result["data_schema"]({})
    assert (defaults["jsession_id"], defaults["region"], defaults["custom_region"]) == ("x", "custom", "prd-zz")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"jsession_id": " new ", "region": "prd-us", "custom_region": ""}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {"jsession_id": "new", "region": "prd-us"}
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.LOADED


async def test_reconfigure_errors(hass, mock_devices):
    entry = await _setup(hass, mock_devices)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"jsession_id": "new", "region": "custom", "custom_region": " "}
    )
    assert result["errors"] == {"custom_region": "empty_custom_region"}

    from custom_components.smarttags_nextgen.api import SmartTagsAuthError

    with patch(f"{API}.refresh_csrf_token", AsyncMock(side_effect=SmartTagsAuthError)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"jsession_id": "bad", "region": "prd-eu", "custom_region": ""}
        )
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data["jsession_id"] == "x"


async def test_reconfigure_wrong_account(hass, mock_devices):
    entry = await _setup(hass, mock_devices)
    mock_devices[0] = {**TAG_A, "usrId": 999}
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"jsession_id": "other", "region": "prd-eu", "custom_region": ""}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data["jsession_id"] == "x"


async def test_options_scan_interval(hass, mock_devices):
    entry = await _setup(hass, mock_devices)
    assert entry.runtime_data.update_interval == timedelta(minutes=5)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"scan_interval": 15})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert entry.options == {"scan_interval": 15}
    # the entry is reloaded with the new interval
    assert entry.runtime_data.update_interval == timedelta(minutes=15)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["data_schema"]({})["scan_interval"] == 15
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(result["flow_id"], {"scan_interval": 0})
