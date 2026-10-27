"""Config flow for SmartThings Find NextGen integration."""
import logging
from typing import Any, Dict, Mapping, Optional
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


# Fixed: Added REGION_ASIA_2 to the source import parameters mapping
from .const import DOMAIN, CONF_JSESSION_ID, CONF_REGION, REGION_EUROPE, REGION_US_GENERAL, REGION_ASIA, REGION_ASIA_2
from .api import SmartTagsAPI, SmartTagsAuthError

_LOGGER = logging.getLogger(__name__)

async def validate_input(hass: HomeAssistant, data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the user input by attempting a login with the selected region."""
    session = async_get_clientsession(hass)
    
    # Instantiate the API orchestrator with the chosen region from the dropdown form
    api = SmartTagsAPI(session, data[CONF_JSESSION_ID], data[CONF_REGION])
    
    # Pre-flight validation check executing a dynamic CSRF exchange
    try:
        success = await api.refresh_csrf_token()
        if not success:
            raise CannotConnect

        devices = await api.get_devices()
    except SmartTagsAuthError as err:
        raise InvalidAuth from err
    if devices is None:
        raise CannotConnect
        
    account_id = next((str(d["usrId"]) for d in devices if d.get("usrId")), None)
    return {"title": "SmartThings Find Account", "account_id": account_id}

class SmartTagsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SmartThings Find NextGen."""

    VERSION = 1

    _reauth_entry: Optional[config_entries.ConfigEntry] = None

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> Any:
        """Handle the initial step creating the interactive UI configurations."""
        errors: Dict[str, str] = {}
        
        if user_input is not None:
            region_selection = user_input.get(CONF_REGION)
            custom_region_val = user_input.get("custom_region")
            
            actual_region = region_selection
            if region_selection == "custom":
                if not custom_region_val or not custom_region_val.strip():
                    errors["custom_region"] = "empty_custom_region"
                else:
                    actual_region = custom_region_val.strip()

            if not errors:
                validation_data = {
                    CONF_JSESSION_ID: user_input[CONF_JSESSION_ID],
                    CONF_REGION: actual_region
                }
                try:
                    info = await validate_input(self.hass, validation_data)
                except InvalidAuth:
                    errors["base"] = "invalid_auth"
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Unexpected exception occurred during validation")
                    errors["base"] = "unknown"
                else:
                    # Prevent adding the same Samsung account twice
                    if info["account_id"]:
                        await self.async_set_unique_id(info["account_id"])
                        self._abort_if_unique_id_configured()
                    return self.async_create_entry(title=info["title"], data=validation_data)

        # Explicit key-value mapping dict linking internal region values to friendly readable UI names
        region_options = {
            REGION_EUROPE: "Europe (prd-eu)",
            REGION_US_GENERAL: "General / US (prd-us)",
            REGION_ASIA: "Asia / Pacific (prd-ap)",
            REGION_ASIA_2: "Asia / Pacific 2 (prd-ap2)",
            "custom": "Other / Custom..."
        }

        jsession_default = ""
        region_default = REGION_EUROPE
        custom_region_default = ""
        if user_input is not None:
            jsession_default = user_input.get(CONF_JSESSION_ID, "")
            region_default = user_input.get(CONF_REGION, REGION_EUROPE)
            custom_region_default = user_input.get("custom_region", "")

        data_schema = vol.Schema({
            vol.Required(CONF_JSESSION_ID, default=jsession_default): str,
            vol.Required(CONF_REGION, default=region_default): vol.In(region_options),
            vol.Optional("custom_region", default=custom_region_default): str
        })

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={"url": "https://smartthingsfind.samsung.com"},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> Any:
        """Start reauth when the stored JSESSIONID is rejected by Samsung."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: Optional[Dict[str, Any]] = None) -> Any:
        """Ask for a fresh JSESSIONID, keeping the configured region."""
        errors: Dict[str, str] = {}
        entry = self._reauth_entry

        if user_input is not None:
            validation_data = {
                **entry.data,
                CONF_JSESSION_ID: user_input[CONF_JSESSION_ID].strip(),
            }
            try:
                info = await validate_input(self.hass, validation_data)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception occurred during reauth")
                errors["base"] = "unknown"
            else:
                if entry.unique_id and info["account_id"] and info["account_id"] != entry.unique_id:
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(entry, data=validation_data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_JSESSION_ID): str}),
            errors=errors,
            description_placeholders={"url": "https://smartthingsfind.samsung.com"},
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
        errors: Dict[str, str] = {}

        if user_input is not None:
            region_selection = user_input.get(CONF_REGION)
            custom_region_val = user_input.get("custom_region")
            
            actual_region = region_selection
            if region_selection == "custom":
                if not custom_region_val or not custom_region_val.strip():
                    errors["custom_region"] = "empty_custom_region"
                else:
                    actual_region = custom_region_val.strip()

            if not errors:
                validation_data = {
                    CONF_JSESSION_ID: user_input[CONF_JSESSION_ID],
                    CONF_REGION: actual_region
                }
                try:
                    await validate_input(self.hass, validation_data)
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data=validation_data
                    )
                    return self.async_create_entry(title="", data={})
                except InvalidAuth:
                    errors["base"] = "invalid_auth"
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Unexpected exception occurred during validation")
                    errors["base"] = "unknown"

        # Explicit key-value mapping dict linking internal region values to friendly readable UI names
        region_options = {
            REGION_EUROPE: "Europe (prd-eu)",
            REGION_US_GENERAL: "General / US (prd-us)",
            REGION_ASIA: "Asia / Pacific (prd-ap)",
            REGION_ASIA_2: "Asia / Pacific 2 (prd-ap2)",
            "custom": "Other / Custom..."
        }

        if user_input is not None:
            jsession_default = user_input.get(CONF_JSESSION_ID, "")
            region_default = user_input.get(CONF_REGION, REGION_EUROPE)
            custom_region_default = user_input.get("custom_region", "")
        else:
            jsession_default = self.config_entry.data.get(CONF_JSESSION_ID, "")
            current_region = self.config_entry.data.get(CONF_REGION, REGION_EUROPE)
            
            region_default = current_region
            custom_region_default = ""
            if current_region not in [REGION_EUROPE, REGION_US_GENERAL, REGION_ASIA, REGION_ASIA_2]:
                region_default = "custom"
                custom_region_default = current_region

        data_schema = vol.Schema({
            vol.Required(CONF_JSESSION_ID, default=jsession_default): str,
            vol.Required(CONF_REGION, default=region_default): vol.In(region_options),
            vol.Optional("custom_region", default=custom_region_default): str
        })

        return self.async_show_form(
            step_id="init",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={"url": "https://smartthingsfind.samsung.com"},
        )
