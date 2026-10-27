import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DOMAIN, CONF_JSESSION_ID, CONF_REGION, REGION_EUROPE
from .coordinator import SmartTagCoordinator

# We load the platforms definition directly from const to match your original architecture
PLATFORMS = ["device_tracker"]

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SmartTags from a config entry."""
    # Extract the region selected in the UI form, fallback to prd-eu if missing
    region = entry.data.get(CONF_REGION, REGION_EUROPE)

    coordinator = SmartTagCoordinator(hass, entry.data[CONF_JSESSION_ID], region)

    await coordinator.async_config_entry_first_refresh()

    # Entries created before the unique id was introduced get the Samsung account id,
    # unless another entry already claims that account
    account_id = coordinator.account_id
    if entry.unique_id is None and account_id and not any(
        other.unique_id == account_id for other in hass.config_entries.async_entries(DOMAIN)
    ):
        hass.config_entries.async_update_entry(entry, unique_id=account_id)

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle config entry update."""
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
