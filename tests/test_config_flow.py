"""Tests for the reconfigure and options flows and the region default."""
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from custom_components.smarttags_nextgen.account import InvalidRedirectError, PendingSignIn, SamsungSignInError
from custom_components.smarttags_nextgen.api import SmartTagsAuthError, SmartTagsConnectionError
from custom_components.smarttags_nextgen.const import DOMAIN

from .common import (
    ACCOUNT_CREDENTIALS,
    TAG_A,
    create_account_entry,
    create_entry,
    start_reconfigure_flow,
    start_user_flow,
)

API = "custom_components.smarttags_nextgen.api.SmartTagsAPI"


@pytest.mark.parametrize(("server_region", "expected"), [("prd-us", "prd-us"), ("prd-xx", "prd-eu"), (None, "prd-eu")])
async def test_user_form_preselects_server_region(hass, mock_devices, server_region, expected):
    with patch(
        "custom_components.smarttags_nextgen.config_flow.async_get_server_region",
        AsyncMock(return_value=server_region),
    ):
        result = await start_user_flow(hass, "cookie")
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
    result = await start_reconfigure_flow(hass, entry, "reconfigure_cookie")
    assert result["step_id"] == "reconfigure_cookie"
    # a custom region is shown as "custom" with the value prefilled
    defaults = result["data_schema"]({})
    assert (defaults["jsession_id"], defaults["region"], defaults["custom_region"]) == ("x", "custom", "prd-zz")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"jsession_id": " new ", "region": "prd-us", "custom_region": ""}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data == {"jsession_id": "new", "region": "prd-us"}
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.LOADED


async def test_reconfigure_errors(hass, mock_devices):
    entry = await _setup(hass, mock_devices)
    result = await start_reconfigure_flow(hass, entry, "reconfigure_cookie")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"jsession_id": "new", "region": "custom", "custom_region": " "}
    )
    assert result["errors"] == {"custom_region": "empty_custom_region"}

    with patch(f"{API}.refresh_csrf_token", AsyncMock(side_effect=SmartTagsAuthError)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"jsession_id": "bad", "region": "prd-eu", "custom_region": ""}
        )
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data["jsession_id"] == "x"


async def test_reconfigure_wrong_account(hass, mock_devices):
    entry = await _setup(hass, mock_devices)
    mock_devices[0] = {**TAG_A, "usrId": 999}
    result = await start_reconfigure_flow(hass, entry, "reconfigure_cookie")
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


CF = "custom_components.smarttags_nextgen.config_flow"
REDIRECT = "ms-app://s-1-15-2-4027708247-2189610-1983755848-2937435718-1578786913-2158692839-1974417358?state=x"


@pytest.fixture
def mock_sign_in():
    """Mock the Samsung account sign-in used by the config flow."""
    with (
        patch(
            f"{CF}.async_start_sign_in",
            AsyncMock(side_effect=lambda hass, device_id=None: PendingSignIn(
                f"https://sign-in/{device_id}", "state", "verifier", device_id or "new-device"
            )),
        ) as start,
        patch(f"{CF}.async_complete_sign_in", AsyncMock(return_value=dict(ACCOUNT_CREDENTIALS))) as complete,
        patch(f"{CF}.async_create_web_session", AsyncMock(return_value="created-session")) as create_session,
    ):
        yield start, complete, create_session


async def test_account_sign_in(hass, mock_devices, mock_sign_in):
    start, complete, create_session = mock_sign_in
    mock_devices.append(TAG_A)
    result = await start_user_flow(hass, "account")
    assert result["step_id"] == "account"
    assert result["description_placeholders"] == {"login_url": "https://sign-in/None"}

    with patch("custom_components.smarttags_nextgen.async_setup_entry", AsyncMock(return_value=True)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"redirect_url": REDIRECT, "region": "prd-eu", "custom_region": ""}
        )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {"auth_method": "account", "region": "prd-eu", **ACCOUNT_CREDENTIALS}
    assert result["result"].unique_id == "12345"
    complete.assert_awaited_once()
    assert complete.await_args.args[2] == REDIRECT
    # the account token was checked by creating a session with it
    create_session.assert_awaited_once()


async def test_account_sign_in_wrong_paste_keeps_sign_in(hass, mock_devices, mock_sign_in):
    start, complete, _ = mock_sign_in
    mock_devices.append(TAG_A)
    complete.side_effect = [InvalidRedirectError, dict(ACCOUNT_CREDENTIALS)]
    result = await start_user_flow(hass, "account")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": "https://wrong", "region": "prd-eu", "custom_region": ""}
    )
    assert result["errors"] == {"redirect_url": "invalid_redirect"}
    with patch("custom_components.smarttags_nextgen.async_setup_entry", AsyncMock(return_value=True)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"redirect_url": REDIRECT, "region": "prd-eu", "custom_region": ""}
        )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    # the same sign-in link stayed valid
    assert start.await_count == 1


async def test_account_sign_in_failed_starts_new_sign_in(hass, mock_devices, mock_sign_in):
    start, complete, _ = mock_sign_in
    complete.side_effect = SamsungSignInError
    result = await start_user_flow(hass, "account")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": REDIRECT, "region": "prd-eu", "custom_region": ""}
    )
    assert result["errors"] == {"base": "sign_in_failed"}
    assert start.await_count == 2


async def test_account_session_rejected_starts_new_sign_in(hass, mock_devices, mock_sign_in):
    start, _, create_session = mock_sign_in
    create_session.side_effect = SmartTagsAuthError
    result = await start_user_flow(hass, "account")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": REDIRECT, "region": "prd-eu", "custom_region": ""}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    assert start.await_count == 2


async def test_account_sign_in_unavailable(hass, mock_devices, mock_sign_in):
    start, _, _ = mock_sign_in
    start.side_effect = SamsungSignInError
    result = await start_user_flow(hass, "account")
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "sign_in_unavailable"


async def test_reauth_account(hass, mock_devices, mock_sign_in):
    start, complete, _ = mock_sign_in
    mock_devices.append(TAG_A)
    entry = create_account_entry(hass)
    complete.return_value = {**ACCOUNT_CREDENTIALS, "userauth_token": "renewed-token"}
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_account"
    # the device id bound to the stored token is reused
    start.assert_awaited_once_with(hass, "abcdef")

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"redirect_url": REDIRECT})
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.data["userauth_token"] == "renewed-token"
    assert entry.data["region"] == "prd-eu"


async def test_reauth_account_wrong_account(hass, mock_devices, mock_sign_in):
    mock_devices.append({**TAG_A, "usrId": 999})
    entry = create_account_entry(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"redirect_url": REDIRECT})
    assert result["reason"] == "wrong_account"
    assert entry.data["userauth_token"] == "master-token"


async def test_reconfigure_menu(hass, mock_devices):
    cookie_entry = create_entry(hass, unique_id="1")
    result = await cookie_entry.start_reconfigure_flow(hass)
    assert result["menu_options"] == ["reconfigure_account", "reconfigure_cookie"]
    account_entry = create_account_entry(hass, unique_id="2")
    result = await account_entry.start_reconfigure_flow(hass)
    assert result["menu_options"] == ["reconfigure_region", "reconfigure_account", "reconfigure_cookie"]


async def test_reconfigure_switch_cookie_to_account(hass, mock_devices, mock_sign_in):
    mock_devices.append(TAG_A)
    entry = create_entry(hass, unique_id="12345")
    result = await start_reconfigure_flow(hass, entry, "reconfigure_account")
    assert result["data_schema"]({"redirect_url": REDIRECT})["region"] == "prd-eu"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": REDIRECT, "region": "prd-us", "custom_region": ""}
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data == {"auth_method": "account", "region": "prd-us", **ACCOUNT_CREDENTIALS}


async def test_reconfigure_switch_account_to_cookie(hass, mock_devices):
    mock_devices.append(TAG_A)
    entry = create_account_entry(hass)
    result = await start_reconfigure_flow(hass, entry, "reconfigure_cookie")
    assert result["data_schema"]({})["jsession_id"] == ""
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"jsession_id": "copied", "region": "prd-eu", "custom_region": ""}
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    # the account token is removed
    assert entry.data == {"jsession_id": "copied", "region": "prd-eu"}


async def test_reconfigure_region(hass, mock_devices, mock_sign_in):
    mock_devices.append(TAG_A)
    entry = create_account_entry(hass)
    result = await start_reconfigure_flow(hass, entry, "reconfigure_region")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"region": "custom", "custom_region": "prd-xx"}
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data == {"auth_method": "account", "region": "prd-xx", **ACCOUNT_CREDENTIALS}


async def test_account_connection_error_is_logged(hass, mock_devices, mock_sign_in, caplog):
    _, _, create_session = mock_sign_in
    create_session.side_effect = SmartTagsConnectionError("getState answered with status 503")
    result = await start_user_flow(hass, "account")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": REDIRECT, "region": "prd-eu", "custom_region": ""}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert "Could not connect to SmartThings Find: getState answered with status 503" in caplog.text
