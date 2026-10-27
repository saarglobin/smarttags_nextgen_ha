"""Config flow for SmartThings Find NextGen integration."""
import logging
from typing import Any, Dict, Mapping, Optional
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


from .const import (
    DOMAIN,
    CONF_JSESSION_ID,
    CONF_REGION,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    REGION_EUROPE,
    REGION_US_GENERAL,
    REGION_ASIA,
    REGION_ASIA_2,
)
from .api import SmartTagsAPI, SmartTagsAuthError, SmartTagsConnectionError, async_get_server_region

_LOGGER = logging.getLogger(__name__)

CONF_CUSTOM_REGION = "custom_region"
REGION_CUSTOM = "custom"

# Explicit key-value mapping dict linking internal region values to friendly readable UI names
REGION_OPTIONS = {
    REGION_EUROPE: "Europe (prd-eu)",
    REGION_US_GENERAL: "General / US (prd-us)",
    REGION_ASIA: "Asia / Pacific (prd-ap)",
    REGION_ASIA_2: "Asia / Pacific 2 (prd-ap2)",
    REGION_CUSTOM: "Other / Custom...",
}

DESCRIPTION_PLACEHOLDERS = {"url": "https://smartthingsfind.samsung.com"}


def _form_defaults(jsession_id: str, region: str) -> Dict[str, str]:
    """Form defaults for a stored region, selecting "custom" for regions not in the list."""
    if region in REGION_OPTIONS:
        return {CONF_JSESSION_ID: jsession_id, CONF_REGION: region, CONF_CUSTOM_REGION: ""}
    return {CONF_JSESSION_ID: jsession_id, CONF_REGION: REGION_CUSTOM, CONF_CUSTOM_REGION: region}


def _credentials_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_JSESSION_ID, default=defaults.get(CONF_JSESSION_ID, "")): str,
        vol.Required(CONF_REGION, default=defaults.get(CONF_REGION, REGION_EUROPE)): vol.In(REGION_OPTIONS),
        vol.Optional(CONF_CUSTOM_REGION, default=defaults.get(CONF_CUSTOM_REGION, "")): str,
    })


def _parse_credentials(user_input: Mapping[str, Any], errors: Dict[str, str]) -> Optional[Dict[str, str]]:
    """Turn the submitted form into entry data, or record an error and return None."""
    region = user_input[CONF_REGION]
    if region == REGION_CUSTOM:
        region = user_input.get(CONF_CUSTOM_REGION, "").strip()
        if not region:
            errors[CONF_CUSTOM_REGION] = "empty_custom_region"
            return None
    return {CONF_JSESSION_ID: user_input[CONF_JSESSION_ID].strip(), CONF_REGION: region}


async def validate_input(hass: HomeAssistant, data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the user input by attempting a login with the selected region."""
    api = SmartTagsAPI(async_get_clientsession(hass), data[CONF_JSESSION_ID], data[CONF_REGION])

    # Pre-flight validation check executing a dynamic CSRF exchange
    try:
        await api.refresh_csrf_token()
        devices = await api.get_devices()
    except SmartTagsAuthError as err:
        raise InvalidAuth from err
    except SmartTagsConnectionError as err:
        raise CannotConnect from err

    account_id = next((str(d["usrId"]) for d in devices if d.get("usrId")), None)
    return {"title": "SmartThings Find Account", "account_id": account_id}


async def _async_try_validate(
    hass: HomeAssistant, data: Dict[str, Any], errors: Dict[str, str]
) -> Optional[Dict[str, Any]]:
    """Validate the data, recording a form error and returning None on failure."""
    try:
        return await validate_input(hass, data)
    except InvalidAuth:
        errors["base"] = "invalid_auth"
    except CannotConnect:
        errors["base"] = "cannot_connect"
    except Exception:  # pylint: disable=broad-except
        _LOGGER.exception("Unexpected exception occurred during validation")
        errors["base"] = "unknown"
    return None


def _is_other_account(entry: config_entries.ConfigEntry, info: Dict[str, Any]) -> bool:
    return bool(entry.unique_id and info["account_id"] and info["account_id"] != entry.unique_id)


class SmartTagsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SmartThings Find NextGen."""

    VERSION = 1

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> Any:
        """Handle the initial step creating the interactive UI configurations."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            data = _parse_credentials(user_input, errors)
            info = data and await _async_try_validate(self.hass, data, errors)
            if info:
                # Prevent adding the same Samsung account twice
                if info["account_id"]:
                    await self.async_set_unique_id(info["account_id"])
                    self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=data)
            defaults = user_input
        else:
            # Pre-select the region Samsung routes this Home Assistant instance to
            region = await async_get_server_region(async_get_clientsession(self.hass))
            defaults = _form_defaults("", region if region in REGION_OPTIONS else REGION_EUROPE)

        return self.async_show_form(
            step_id="user",
            data_schema=_credentials_schema(defaults),
            errors=errors,
            description_placeholders=DESCRIPTION_PLACEHOLDERS,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> Any:
        """Start reauth when the stored JSESSIONID is rejected by Samsung."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: Optional[Dict[str, Any]] = None) -> Any:
        """Ask for a fresh JSESSIONID, keeping the configured region."""
        errors: Dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            data = {**entry.data, CONF_JSESSION_ID: user_input[CONF_JSESSION_ID].strip()}
            info = await _async_try_validate(self.hass, data, errors)
            if info:
                if _is_other_account(entry, info):
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_JSESSION_ID): str}),
            errors=errors,
            description_placeholders=DESCRIPTION_PLACEHOLDERS,
        )

    async def async_step_reconfigure(self, user_input: Optional[Dict[str, Any]] = None) -> Any:
        """Change the JSESSIONID or region of an existing entry."""
        errors: Dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            data = _parse_credentials(user_input, errors)
            info = data and await _async_try_validate(self.hass, data, errors)
            if info:
                if _is_other_account(entry, info):
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(entry, data_updates=data)
            defaults = user_input
        else:
            defaults = _form_defaults(
                entry.data.get(CONF_JSESSION_ID, ""), entry.data.get(CONF_REGION, REGION_EUROPE)
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_credentials_schema(defaults),
            errors=errors,
            description_placeholders=DESCRIPTION_PLACEHOLDERS,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return SmartTagsOptionsFlowHandler()


class SmartTagsOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for SmartThings Find NextGen."""

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> Any:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        scan_interval = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_SCAN_INTERVAL, default=scan_interval): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=60)
                ),
            }),
        )
