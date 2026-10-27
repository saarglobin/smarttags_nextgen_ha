"""Shared test data."""
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
