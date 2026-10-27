"""Shared test data and helpers."""
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smarttags_nextgen.const import DOMAIN

TAG_A = {"dvceID": "a", "deviceType": "TAG", "modelName": "Galaxy SmartTag2", "nickName": "Keys", "usrId": 12345}
TAG_B = {"dvceID": "b", "deviceType": "TAG", "modelName": "Galaxy SmartTag2", "nickName": "Bag", "usrId": 12345}
PHONE = {"dvceID": "p", "deviceType": "PHONE", "modelName": "Galaxy S", "usrId": 12345}

OPERATIONS = {
    "a": [
        {"oprnType": "LOCATION", "latitude": "1.0", "longitude": "2.0", "extra": {"gpsUtcDt": "20260101120000"}},
        # a newer fix listed after an older one wins
        {
            "oprnType": "OFFLINE_LOC",
            "latitude": "3.0",
            "longitude": "4.0",
            "extra": {"gpsUtcDt": "20260102120000"},
            "horizontalUncertainty": "3",
            "verticalUncertainty": "4",
        },
        # an older one after it is ignored
        {"oprnType": "LOCATION", "latitude": "9.0", "longitude": "9.0", "extra": {"gpsUtcDt": "20251231120000"}},
        # missing coordinates must not break the update
        {"oprnType": "LOCATION", "extra": {"gpsUtcDt": "20260103120000"}},
        {"oprnType": "LOCATION", "latitude": None, "longitude": "4.0"},
        {"oprnType": "CHECK_CONNECTION", "battery": "MEDIUM"},
    ],
    "b": [{"oprnType": "LOCATION", "latitude": "5.0", "longitude": "6.0", "locationType": "gps"}],
}


def create_entry(hass, unique_id=None):
    """Add a config entry for the integration to hass."""
    entry = MockConfigEntry(domain=DOMAIN, data={"jsession_id": "x", "region": "prd-eu"}, unique_id=unique_id)
    entry.add_to_hass(hass)
    return entry


async def start_user_flow(hass, method):
    """Start adding the integration and pick "account" or "cookie" in the menu."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] == FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": method})


async def start_reconfigure_flow(hass, entry, step):
    """Start reconfiguring the entry and pick the step in the menu."""
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] == FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": step})


ACCOUNT_CREDENTIALS = {
    "userauth_token": "master-token",
    "user_id": "user",
    "login_id": "me@example.com",
    "device_id": "abcdef",
    "auth_server_url": "https://eu-auth2.samsungosp.com",
}


def create_account_entry(hass, unique_id="12345"):
    """Add a config entry that uses the Samsung account sign-in."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"auth_method": "account", "region": "prd-eu", **ACCOUNT_CREDENTIALS},
        unique_id=unique_id,
    )
    entry.add_to_hass(hass)
    return entry


def record_cookies(aioclient_mock):
    """Record the cookies passed with each mocked request (the mock itself only records headers)."""
    calls = []
    match_request = aioclient_mock.match_request

    async def record(method, url, **kwargs):
        calls.append((str(url), kwargs.get("cookies")))
        return await match_request(method, url, **kwargs)

    aioclient_mock.match_request = record
    return calls
