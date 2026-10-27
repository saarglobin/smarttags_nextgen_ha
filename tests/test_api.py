"""Tests running the real API client against mocked Samsung endpoints."""
import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType

from custom_components.smarttags_nextgen.api import (
    SmartTagsAPI,
    SmartTagsAuthError,
    SmartTagsConnectionError,
)
from custom_components.smarttags_nextgen.const import DOMAIN

from .common import TAG_A, create_entry

BASE = "https://smartthingsfind.samsung.com"
CHK = f"{BASE}/chkLogin.do"
LIST = f"{BASE}/device/getDeviceList.do"
SELECT = f"{BASE}/device/setLastSelect.do"


def ok_login(aioclient_mock):
    aioclient_mock.get(CHK, text="", headers={"_csrf": "token"})


def api(hass):
    return SmartTagsAPI(async_get_clientsession(hass), "session", "prd-eu")


async def test_csrf_ok(hass, aioclient_mock):
    ok_login(aioclient_mock)
    client = api(hass)
    await client.refresh_csrf_token()
    assert client.csrf_token == "token"
    headers = aioclient_mock.mock_calls[0][3]
    assert headers["Cookie"] == "JSESSIONID=session"


async def test_csrf_rejected_session(hass, aioclient_mock):
    aioclient_mock.get(CHK, text="fail")
    with pytest.raises(SmartTagsAuthError):
        await api(hass).refresh_csrf_token()


@pytest.mark.parametrize("kwargs", [{"exc": TimeoutError()}, {"status": 503, "text": "down"}])
async def test_csrf_connection_errors(hass, aioclient_mock, kwargs):
    aioclient_mock.get(CHK, **kwargs)
    with pytest.raises(SmartTagsConnectionError):
        await api(hass).refresh_csrf_token()


async def test_devices_errors(hass, aioclient_mock):
    client = api(hass)
    client.csrf_token = "token"
    aioclient_mock.post(LIST, status=401, text="Logout")
    with pytest.raises(SmartTagsAuthError):
        await client.get_devices()
    aioclient_mock.clear_requests()
    aioclient_mock.post(LIST, status=500)
    with pytest.raises(SmartTagsConnectionError):
        await client.get_devices()
    aioclient_mock.clear_requests()
    aioclient_mock.post(LIST, text="<html>not json</html>")
    with pytest.raises(SmartTagsConnectionError):
        await client.get_devices()


async def test_set_last_select_failure_returns_none(hass, aioclient_mock):
    client = api(hass)
    client.csrf_token = "token"
    aioclient_mock.post(SELECT, exc=TimeoutError())
    assert await client.set_last_select("a") is None


async def test_expired_session_starts_reauth(hass, aioclient_mock):
    aioclient_mock.get(CHK, text="fail")
    entry = create_entry(hass, unique_id="12345")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_outage_retries_setup(hass, aioclient_mock):
    aioclient_mock.get(CHK, exc=TimeoutError())
    entry = create_entry(hass, unique_id="12345")
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def _start_reauth(hass, entry):
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id, "unique_id": entry.unique_id},
        data=entry.data,
    )


async def test_reauth_success(hass, aioclient_mock):
    ok_login(aioclient_mock)
    aioclient_mock.post(LIST, json={"deviceList": [TAG_A]})
    aioclient_mock.post(SELECT, json={"operation": []})
    entry = create_entry(hass, unique_id="12345")
    result = await _start_reauth(hass, entry)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"jsession_id": " new "})
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == {"jsession_id": "new", "region": "prd-eu"}


async def test_reauth_wrong_account(hass, aioclient_mock):
    ok_login(aioclient_mock)
    aioclient_mock.post(LIST, json={"deviceList": [{**TAG_A, "usrId": 999}]})
    entry = create_entry(hass, unique_id="12345")
    result = await _start_reauth(hass, entry)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"jsession_id": "new"})
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data["jsession_id"] == "x"


async def test_reauth_invalid_session_shows_error(hass, aioclient_mock):
    aioclient_mock.get(CHK, text="fail")
    entry = create_entry(hass, unique_id="12345")
    result = await _start_reauth(hass, entry)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"jsession_id": "bad"})
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
