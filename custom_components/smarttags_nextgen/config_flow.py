"""Config flow for SmartThings Find NextGen integration."""
import logging
from functools import partial
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional
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
    AUTH_METHOD_ACCOUNT,
    CONF_AUTH_METHOD,
    CONF_JSESSION_ID,
    CONF_REGION,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    REGION_EUROPE,
    REGION_US_GENERAL,
    REGION_ASIA,
    REGION_ASIA_2,
)
from .account import (
    CONF_DEVICE_ID,
    InvalidRedirectError,
    PendingSignIn,
    SamsungSignInError,
    async_complete_sign_in,
    async_create_web_session,
    async_start_sign_in,
)
from .api import SmartTagsAPI, SmartTagsAuthError, SmartTagsConnectionError, async_get_server_region

_LOGGER = logging.getLogger(__name__)

CONF_CUSTOM_REGION = "custom_region"
CONF_REDIRECT_URL = "redirect_url"
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

FinishCallback = Callable[[Dict[str, Any], Dict[str, Any]], Awaitable[Any]]


def _form_defaults(jsession_id: str, region: str) -> Dict[str, str]:
    """Form defaults for a stored region, selecting "custom" for regions not in the list."""
    if region in REGION_OPTIONS:
        return {CONF_JSESSION_ID: jsession_id, CONF_REGION: region, CONF_CUSTOM_REGION: ""}
    return {CONF_JSESSION_ID: jsession_id, CONF_REGION: REGION_CUSTOM, CONF_CUSTOM_REGION: region}


def _region_fields(defaults: Mapping[str, Any]) -> Dict[Any, Any]:
    return {
        vol.Required(CONF_REGION, default=defaults.get(CONF_REGION, REGION_EUROPE)): vol.In(REGION_OPTIONS),
        vol.Optional(CONF_CUSTOM_REGION, default=defaults.get(CONF_CUSTOM_REGION, "")): str,
    }


def _credentials_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_JSESSION_ID, default=defaults.get(CONF_JSESSION_ID, "")): str,
        **_region_fields(defaults),
    })


def _parse_region(user_input: Mapping[str, Any], errors: Dict[str, str]) -> Optional[str]:
    """Return the selected region, or record an error and return None."""
    region = user_input[CONF_REGION]
    if region == REGION_CUSTOM:
        region = user_input.get(CONF_CUSTOM_REGION, "").strip()
        if not region:
            errors[CONF_CUSTOM_REGION] = "empty_custom_region"
            return None
    return region


def _parse_credentials(user_input: Mapping[str, Any], errors: Dict[str, str]) -> Optional[Dict[str, str]]:
    """Turn the submitted JSESSIONID form into entry data, or record an error and return None."""
    region = _parse_region(user_input, errors)
    if region is None:
        return None
    return {CONF_JSESSION_ID: user_input[CONF_JSESSION_ID].strip(), CONF_REGION: region}


async def validate_input(hass: HomeAssistant, data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the entry data by fetching the device list with the selected region."""
    try:
        if data.get(CONF_AUTH_METHOD) == AUTH_METHOD_ACCOUNT:
            jsession_id = await async_create_web_session(hass, data)
        else:
            jsession_id = data[CONF_JSESSION_ID]
        api = SmartTagsAPI(async_get_clientsession(hass), jsession_id, data[CONF_REGION])

        # Pre-flight validation check executing a dynamic CSRF exchange
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
    except InvalidAuth as err:
        _LOGGER.warning("SmartThings Find rejected the login: %s", err.__cause__)
        errors["base"] = "invalid_auth"
    except CannotConnect as err:
        _LOGGER.warning("Could not connect to SmartThings Find: %s", err.__cause__)
        errors["base"] = "cannot_connect"
    except Exception:  # pylint: disable=broad-except
        _LOGGER.exception("Unexpected exception occurred during validation")
        errors["base"] = "unknown"
    return None


def _is_other_account(entry: config_entries.ConfigEntry, info: Dict[str, Any]) -> bool:
    return bool(entry.unique_id and info["account_id"] and info["account_id"] != entry.unique_id)


def _is_account_entry(entry: config_entries.ConfigEntry) -> bool:
    return entry.data.get(CONF_AUTH_METHOD) == AUTH_METHOD_ACCOUNT


class SmartTagsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SmartThings Find NextGen."""

    VERSION = 1

    def __init__(self) -> None:
        self._pending_sign_in: Optional[PendingSignIn] = None

    # Adding an account

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> Any:
        """Let the user choose how to authenticate."""
        return self.async_show_menu(step_id="user", menu_options=["account", "cookie"])

    async def async_step_account(self, user_input: Optional[Dict[str, Any]] = None) -> Any:
        """Sign in with a Samsung account."""
        return await self._async_account_step("account", user_input, self._async_create_entry)

    async def async_step_cookie(self, user_input: Optional[Dict[str, Any]] = None) -> Any:
        """Authenticate with a JSESSIONID copied from the browser."""
        return await self._async_cookie_step("cookie", user_input, self._async_create_entry)

    async def _async_create_entry(self, data: Dict[str, Any], info: Dict[str, Any]) -> Any:
        # Prevent adding the same Samsung account twice
        if info["account_id"]:
            await self.async_set_unique_id(info["account_id"])
            self._abort_if_unique_id_configured()
        return self.async_create_entry(title=info["title"], data=data)

    # Reauthentication

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> Any:
        """Start reauth when Samsung rejects the session or the account token."""
        if entry_data.get(CONF_AUTH_METHOD) == AUTH_METHOD_ACCOUNT:
            return await self.async_step_reauth_account()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: Optional[Dict[str, Any]] = None) -> Any:
        """Ask for a fresh JSESSIONID, keeping the configured region."""
        errors: Dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            data = {**entry.data, CONF_JSESSION_ID: user_input[CONF_JSESSION_ID].strip()}
            info = await _async_try_validate(self.hass, data, errors)
            if info:
                return await self._async_update_entry(entry, data, info)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_JSESSION_ID): str}),
            errors=errors,
            description_placeholders=DESCRIPTION_PLACEHOLDERS,
        )

    async def async_step_reauth_account(self, user_input: Optional[Dict[str, Any]] = None) -> Any:
        """Sign in with the Samsung account again, keeping the configured region."""
        entry = self._get_reauth_entry()
        return await self._async_account_step(
            "reauth_account",
            user_input,
            partial(self._async_update_entry, entry),
            fixed_region=entry.data[CONF_REGION],
            device_id=entry.data.get(CONF_DEVICE_ID),
        )

    # Reconfiguration

    async def async_step_reconfigure(self, user_input: Optional[Dict[str, Any]] = None) -> Any:
        """Let the user change the region or the way the entry authenticates."""
        menu_options = ["reconfigure_account", "reconfigure_cookie"]
        if _is_account_entry(self._get_reconfigure_entry()):
            menu_options.insert(0, "reconfigure_region")
        return self.async_show_menu(step_id="reconfigure", menu_options=menu_options)

    async def async_step_reconfigure_account(self, user_input: Optional[Dict[str, Any]] = None) -> Any:
        """Sign in with a Samsung account, also to switch an entry from a JSESSIONID."""
        entry = self._get_reconfigure_entry()
        return await self._async_account_step(
            "reconfigure_account",
            user_input,
            partial(self._async_update_entry, entry),
            region_default=entry.data.get(CONF_REGION, REGION_EUROPE),
            device_id=entry.data.get(CONF_DEVICE_ID),
        )

    async def async_step_reconfigure_cookie(self, user_input: Optional[Dict[str, Any]] = None) -> Any:
        """Change the JSESSIONID and region, also to switch an entry from the account sign-in."""
        entry = self._get_reconfigure_entry()
        jsession_id = "" if _is_account_entry(entry) else entry.data.get(CONF_JSESSION_ID, "")
        return await self._async_cookie_step(
            "reconfigure_cookie",
            user_input,
            partial(self._async_update_entry, entry),
            defaults=_form_defaults(jsession_id, entry.data.get(CONF_REGION, REGION_EUROPE)),
        )

    async def async_step_reconfigure_region(self, user_input: Optional[Dict[str, Any]] = None) -> Any:
        """Change the region of an entry using the account sign-in."""
        errors: Dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            region = _parse_region(user_input, errors)
            if region:
                data = {**entry.data, CONF_REGION: region}
                info = await _async_try_validate(self.hass, data, errors)
                if info:
                    return await self._async_update_entry(entry, data, info)
            defaults = user_input
        else:
            defaults = _form_defaults("", entry.data.get(CONF_REGION, REGION_EUROPE))

        return self.async_show_form(
            step_id="reconfigure_region", data_schema=vol.Schema(_region_fields(defaults)), errors=errors
        )

    async def _async_update_entry(
        self, entry: config_entries.ConfigEntry, data: Dict[str, Any], info: Dict[str, Any]
    ) -> Any:
        if _is_other_account(entry, info):
            return self.async_abort(reason="wrong_account")
        return self.async_update_reload_and_abort(entry, data=data)

    # Shared steps

    async def _async_default_region(self) -> str:
        # Pre-select the region Samsung routes this Home Assistant instance to
        region = await async_get_server_region(async_get_clientsession(self.hass))
        return region if region in REGION_OPTIONS else REGION_EUROPE

    async def _async_cookie_step(
        self,
        step_id: str,
        user_input: Optional[Dict[str, Any]],
        finish: FinishCallback,
        defaults: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        """Form for a JSESSIONID and region."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            data = _parse_credentials(user_input, errors)
            info = data and await _async_try_validate(self.hass, data, errors)
            if info:
                return await finish(data, info)
            defaults = user_input
        elif defaults is None:
            defaults = _form_defaults("", await self._async_default_region())

        return self.async_show_form(
            step_id=step_id,
            data_schema=_credentials_schema(defaults),
            errors=errors,
            description_placeholders=DESCRIPTION_PLACEHOLDERS,
        )

    async def _async_account_step(
        self,
        step_id: str,
        user_input: Optional[Dict[str, Any]],
        finish: FinishCallback,
        *,
        fixed_region: Optional[str] = None,
        region_default: Optional[str] = None,
        device_id: Optional[str] = None,
    ) -> Any:
        """Form with the Samsung sign-in link, a field for the redirect and, unless fixed, the region."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            region = fixed_region or _parse_region(user_input, errors)
            credentials = region and await self._async_complete_sign_in(user_input[CONF_REDIRECT_URL], errors)
            if credentials:
                data = {CONF_AUTH_METHOD: AUTH_METHOD_ACCOUNT, CONF_REGION: region, **credentials}
                info = await _async_try_validate(self.hass, data, errors)
                if info:
                    return await finish(data, info)
                # The sign-in code has been used, so the next attempt needs a new sign-in
                self._pending_sign_in = None
            defaults = user_input
        else:
            defaults = _form_defaults("", region_default or fixed_region or await self._async_default_region())

        if self._pending_sign_in is None:
            try:
                self._pending_sign_in = await async_start_sign_in(self.hass, device_id)
            except SamsungSignInError as err:
                _LOGGER.warning("Could not start the Samsung account sign-in: %s", err)
                return self.async_abort(reason="sign_in_unavailable")

        fields = {} if fixed_region else _region_fields(defaults)
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({vol.Required(CONF_REDIRECT_URL): str, **fields}),
            errors=errors,
            description_placeholders={"login_url": self._pending_sign_in.url},
        )

    async def _async_complete_sign_in(self, redirect_url: str, errors: Dict[str, str]) -> Optional[Dict[str, str]]:
        """Exchange the pasted redirect for the account credentials, or record an error."""
        try:
            return await async_complete_sign_in(self.hass, self._pending_sign_in, redirect_url)
        except InvalidRedirectError:
            # Pasting the wrong text does not use up the sign-in, the user can paste again
            errors[CONF_REDIRECT_URL] = "invalid_redirect"
        except SamsungSignInError as err:
            _LOGGER.warning("Samsung account sign-in failed: %s", err)
            errors["base"] = "sign_in_failed"
            # The sign-in code can only be used once, so the user needs to sign in again
            self._pending_sign_in = None
        return None

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
