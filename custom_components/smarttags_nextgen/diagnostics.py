from homeassistant.components.diagnostics import async_redact_data
from .account import CONF_DEVICE_ID, CONF_LOGIN_ID, CONF_USER_ID, CONF_USERAUTH_TOKEN
from .const import CONF_JSESSION_ID

TO_REDACT = {
    CONF_JSESSION_ID,
    CONF_USERAUTH_TOKEN,
    CONF_USER_ID,
    CONF_LOGIN_ID,
    CONF_DEVICE_ID,
    "unique_id",
    "latitude",
    "longitude",
}


def _describe_operation(operation):
    """Describe the structure of a raw operation (field names only, no values) to debug parsing issues."""
    if not isinstance(operation, dict):
        return type(operation).__name__
    return {
        "oprnType": operation.get("oprnType"),
        "keys": sorted(operation),
        "extra_keys": sorted(operation["extra"]) if isinstance(operation.get("extra"), dict) else None,
    }


async def async_get_config_entry_diagnostics(hass, entry):
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    tags = []
    for device_id, tag_data in (coordinator.data or {}).items():
        operations = coordinator.last_operations.get(device_id)
        tags.append(
            {
                "data": async_redact_data(tag_data, TO_REDACT),
                "last_operations": None if operations is None else [_describe_operation(op) for op in operations],
            }
        )
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "tags": tags,
    }
