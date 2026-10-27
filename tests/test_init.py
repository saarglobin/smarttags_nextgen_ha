"""Tests for setting up the integration and its entities."""
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.smarttags_nextgen.const import DOMAIN
from custom_components.smarttags_nextgen.coordinator import parse_stf_date

from .common import PHONE, TAG_A, TAG_B, create_entry


def test_parse_stf_date():
    assert parse_stf_date("20260102120000").isoformat() == "2026-01-02T12:00:00+00:00"
    assert parse_stf_date(None) is None
    assert parse_stf_date("garbage") is None


async def test_setup_entities(hass, mock_devices):
    mock_devices.extend([TAG_A, PHONE])
    entry = create_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    (state,) = hass.states.async_all("device_tracker")
    assert state.name == "Keys"
    assert state.attributes["latitude"] == 3.0
    assert state.attributes["longitude"] == 4.0
    assert state.attributes["gps_accuracy"] == 5.0
    assert state.attributes["location_type"] == "offline"
    assert state.attributes["battery_level"] == 50
    assert state.attributes["last_seen"] == "2026-01-02T12:00:00+00:00"

    entity_registry = er.async_get(hass)
    device = dr.async_get(hass).async_get(entity_registry.async_get(state.entity_id).device_id)
    assert (device.name, device.manufacturer, device.model) == ("Keys", "Samsung", "Galaxy SmartTag2")

    battery = hass.states.get("sensor.keys_battery")
    assert battery.state == "50"
    assert battery.attributes["unit_of_measurement"] == "%"
    assert battery.attributes["device_class"] == "battery"
    last_seen = hass.states.get("sensor.keys_last_seen")
    assert last_seen.state == "2026-01-02T12:00:00+00:00"
    assert last_seen.attributes["friendly_name"] == "Keys Last seen"
    for entity_id in ("sensor.keys_battery", "sensor.keys_last_seen"):
        assert entity_registry.async_get(entity_id).device_id == device.id

    # the unique id is filled in from the account id
    assert entry.unique_id == "12345"


async def test_tags_added_and_removed(hass, mock_devices):
    mock_devices.append(TAG_A)
    entry = create_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data

    # a tag added to the account later appears without a reload
    mock_devices.append(TAG_B)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert sorted(s.name for s in hass.states.async_all("device_tracker")) == ["Bag", "Keys"]
    assert hass.states.get("sensor.bag_battery").state == "unknown"
    assert hass.states.get("sensor.bag_last_seen").state == "unknown"

    # a removed tag becomes unavailable
    mock_devices.remove(TAG_A)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    keys = [s for s in hass.states.async_all("device_tracker") if s.name == "Keys"][0]
    assert keys.state == "unavailable"
    assert hass.states.get("sensor.keys_battery").state == "unavailable"


async def test_unique_id_not_filled_in_when_account_already_configured(hass, mock_devices):
    mock_devices.append(TAG_A)
    create_entry(hass, unique_id="12345")
    entry = create_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.unique_id is None


async def test_unload(hass, mock_devices):
    mock_devices.append(TAG_A)
    entry = create_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.NOT_LOADED


async def test_config_flow_creates_entry(hass, mock_devices):
    mock_devices.append(TAG_A)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"jsession_id": "y", "region": "prd-eu", "custom_region": ""}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {"jsession_id": "y", "region": "prd-eu"}
    assert result["result"].unique_id == "12345"


async def test_config_flow_custom_region(hass, mock_devices):
    mock_devices.append(TAG_A)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"jsession_id": "y", "region": "custom", "custom_region": ""}
    )
    assert result["errors"] == {"custom_region": "empty_custom_region"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"jsession_id": "y", "region": "custom", "custom_region": " prd-xx "}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["region"] == "prd-xx"


async def test_config_flow_aborts_duplicate_account(hass, mock_devices):
    mock_devices.append(TAG_A)
    create_entry(hass, unique_id="12345")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"jsession_id": "y", "region": "prd-eu", "custom_region": ""}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
