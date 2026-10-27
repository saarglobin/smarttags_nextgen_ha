"""Samsung account sign-in that can create new SmartThings Find web sessions.

Signing in once through Samsung's app login yields a long-lived account token
(``userauth_token``). With it, a new JSESSIONID can be created whenever the web
session expires, without asking for the password or second factor again.

The protocol is ported from samsung-re-find (MIT License, Copyright (c) Charles Bel),
https://github.com/charlesbel/samsung-re-find, which builds on the authentication
research of uTag (https://github.com/KieronQuinn/uTag/wiki/Authentication).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
from http.cookies import CookieError, SimpleCookie
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, Mapping

import aiohttp
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import load_der_public_key
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession, async_get_clientsession

from .api import SmartTagsAuthError, SmartTagsConnectionError, describe_error

_LOGGER = logging.getLogger(__name__)

AUTH_CLIENT_ID = "yfrtglt53o"
WEB_FIND_CLIENT_ID = "ntly6zvfpn"
WEB_FIND_SCOPE = "iot.client"
REDIRECT_URI = "ms-app://s-1-15-2-4027708247-2189610-1983755848-2937435718-1578786913-2158692839-1974417358"
ENTRY_POINT_URL = "https://account.samsung.com/accounts/ANDROIDSDK/getEntryPoint"
FIND_URL = "https://smartthingsfind.samsung.com"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Config entry keys of the account credentials
CONF_USERAUTH_TOKEN = "userauth_token"
CONF_USER_ID = "user_id"
CONF_LOGIN_ID = "login_id"
CONF_DEVICE_ID = "device_id"
CONF_AUTH_SERVER_URL = "auth_server_url"


class SamsungSignInError(Exception):
    """Raised when the Samsung account sign-in could not be completed."""


class InvalidRedirectError(SamsungSignInError):
    """Raised when the pasted text is not the Samsung sign-in redirect."""


@dataclass
class PendingSignIn:
    """A started sign-in, kept by the config flow until the user pastes the redirect."""

    url: str
    state: str
    code_verifier: str
    device_id: str


def _random_urlsafe(byte_count: int) -> str:
    return base64.urlsafe_b64encode(os.urandom(byte_count)).rstrip(b"=").decode()


def _code_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _encrypt_svc_param(payload: Mapping[str, Any], chk_do_num: int, public_key_b64: str) -> str:
    """Encrypt the sign-in parameters the way Samsung's app SDK does (PBKDF2 + RSA + AES-CBC)."""
    chk_text = str(chk_do_num)
    hashed = base64.b64encode(hashlib.sha256(chk_text.encode()).digest())
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", hashed, salt, chk_do_num, dklen=16)
    public_key = load_der_public_key(base64.b64decode(public_key_b64))
    encrypted_key = public_key.encrypt(base64.b64encode(derived), asym_padding.PKCS1v15())

    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(json.dumps(payload, separators=(",", ":")).encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(derived), modes.CBC(iv)).encryptor()
    encrypted_payload = encryptor.update(padded) + encryptor.finalize()

    envelope = {
        "chkDoNum": chk_text,
        "svcEncParam": base64.b64encode(encrypted_payload).decode(),
        "svcEncKY": base64.b64encode(encrypted_key).decode(),
        "svcEncIV": iv.hex(),
    }
    return urllib.parse.quote(base64.b64encode(json.dumps(envelope).encode()).decode(), safe="")


def _decrypt_auth_value(value: str, key: str) -> str:
    """Decrypt a field of the sign-in redirect (protocol-defined AES-ECB)."""
    key_bytes = key.encode()[:16].ljust(16, b"\0")
    decryptor = Cipher(algorithms.AES(key_bytes), modes.ECB()).decryptor()
    padded = decryptor.update(bytes.fromhex(value)) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode()


def _trusted_auth_server_url(value: str) -> str:
    """Only accept Samsung account servers, so the account token is never sent elsewhere."""
    # The sign-in redirect contains the bare host name, e.g. eu-auth2.samsungosp.com
    if "://" not in value:
        value = f"https://{value}"
    try:
        parsed = urllib.parse.urlparse(value)
        port = parsed.port
    except ValueError as err:
        raise SamsungSignInError("Invalid Samsung authentication server") from err
    hostname = (parsed.hostname or "").lower()
    trusted = hostname == "account.samsung.com" or hostname == "samsungosp.com" or hostname.endswith(".samsungosp.com")
    if (
        parsed.scheme != "https"
        or not trusted
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise SamsungSignInError(f"Untrusted Samsung authentication server: {hostname or 'unknown'}")
    return f"https://{hostname}"


def is_redirect_uri(value: str) -> bool:
    """Whether the text is the ms-app:// redirect Samsung sends at the end of the sign-in."""
    try:
        actual = urllib.parse.urlsplit(value.strip())
    except ValueError:
        return False
    expected = urllib.parse.urlsplit(REDIRECT_URI)
    return (actual.scheme, actual.netloc, actual.path) == (expected.scheme, expected.netloc, expected.path)


async def _async_json(resp: aiohttp.ClientResponse, step: str) -> Dict[str, Any]:
    if 300 <= resp.status < 400:
        raise SamsungSignInError(f"Samsung {step} redirected unexpectedly")
    if resp.status != 200:
        raise SamsungSignInError(f"Samsung {step} failed with status {resp.status}")
    data = await resp.json(content_type=None)
    if not isinstance(data, dict):
        raise SamsungSignInError(f"Samsung {step} returned an unexpected response")
    return data


async def async_start_sign_in(hass: HomeAssistant, device_id: str | None = None) -> PendingSignIn:
    """Create the Samsung sign-in URL the user opens in their browser."""
    session = async_get_clientsession(hass)
    try:
        async with session.get(ENTRY_POINT_URL, allow_redirects=False, timeout=REQUEST_TIMEOUT) as resp:
            entry = await _async_json(resp, "sign-in entry point")
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise SamsungSignInError(f"Could not reach Samsung account: {describe_error(err)}") from err

    # A random identifier Samsung associates with this Home Assistant "device"; it is kept
    # with the credentials because the account token is bound to it
    device_id = device_id or secrets.token_hex(16)
    state = _random_urlsafe(15)[:20]
    verifier = _random_urlsafe(32)[:43]
    payload = {
        "clientId": AUTH_CLIENT_ID,
        "code_challenge": _code_challenge(verifier),
        "code_challenge_method": "S256",
        "competitorDeviceYNFlag": "Y",
        "countryCode": (hass.config.country or "us").lower(),
        "deviceInfo": "Google|com.android.chrome",
        "deviceModelID": "Pixel 8 Pro",
        "deviceName": "Google Pixel 8 Pro",
        "deviceOSVersion": "35",
        "devicePhysicalAddressText": f"ANID:{device_id}",
        "deviceType": "APP",
        "deviceUniqueID": device_id,
        "redirect_uri": REDIRECT_URI,
        "replaceableClientConnectYN": "N",
        "replaceableClientId": "",
        "replaceableDevicePhysicalAddressText": "",
        "responseEncryptionType": "1",
        "responseEncryptionYNFlag": "Y",
        "scope": "",
        "state": state,
        "svcIptLgnID": "",
        "iosYNFlag": "Y",
    }
    try:
        # PBKDF2 with Samsung's iteration count is CPU heavy
        encrypted = await hass.async_add_executor_job(
            _encrypt_svc_param, payload, int(entry["chkDoNum"]), entry["pkiPublicKey"]
        )
        sign_in_uri = entry["signInURI"]
    except (KeyError, TypeError, ValueError) as err:
        raise SamsungSignInError("Samsung sign-in entry point returned unexpected data") from err

    url = f"{sign_in_uri}?locale={urllib.parse.quote('en-US')}&svcParam={encrypted}&mode=C"
    return PendingSignIn(url=url, state=state, code_verifier=verifier, device_id=device_id)


async def async_complete_sign_in(hass: HomeAssistant, pending: PendingSignIn, redirect_uri: str) -> Dict[str, str]:
    """Exchange the ms-app:// redirect for the account credentials stored in the config entry."""
    redirect_uri = redirect_uri.strip()
    if not is_redirect_uri(redirect_uri):
        raise InvalidRedirectError("Not the Samsung sign-in redirect")

    parsed = urllib.parse.urlparse(redirect_uri)
    params = urllib.parse.parse_qs(parsed.query)
    if parsed.fragment:
        params.update(urllib.parse.parse_qs(parsed.fragment))

    def one(name: str) -> str:
        return params.get(name, [""])[0]

    try:
        response_key = _decrypt_auth_value(one("state"), pending.state)
        auth_server = _decrypt_auth_value(one("auth_server_url"), response_key)
        code = _decrypt_auth_value(one("code"), response_key)
        login_id = _decrypt_auth_value(one("retValue"), response_key)
    except (ValueError, UnicodeDecodeError) as err:
        # Also raised for a redirect of an older sign-in attempt
        raise InvalidRedirectError("Unable to decrypt the Samsung sign-in redirect") from err
    auth_server = _trusted_auth_server_url(auth_server)

    session = async_get_clientsession(hass)
    try:
        async with session.post(
            f"{auth_server}/auth/oauth2/authenticate",
            data={
                "grant_type": "authorization_code",
                "serviceType": "M",
                "client_id": AUTH_CLIENT_ID,
                "code": code,
                "code_verifier": pending.code_verifier,
                "username": login_id,
                "physical_address_text": pending.device_id,
            },
            allow_redirects=False,
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            result = await _async_json(resp, "account authentication")
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise SamsungSignInError(f"Could not reach Samsung account: {describe_error(err)}") from err

    userauth_token = result.get("userauth_token") or result.get("userAuthToken")
    user_id = result.get("userId") or result.get("user_id")
    if not userauth_token or not user_id:
        raise SamsungSignInError("Samsung did not return an account token")
    return {
        CONF_USERAUTH_TOKEN: userauth_token,
        CONF_USER_ID: str(user_id),
        CONF_LOGIN_ID: login_id,
        CONF_DEVICE_ID: pending.device_id,
        CONF_AUTH_SERVER_URL: auth_server,
    }


def _describe_location(resp: aiohttp.ClientResponse) -> str:
    """Host and path of a redirect, without the query that can contain codes."""
    location = urllib.parse.urlparse(resp.headers.get("Location", ""))
    return f"{location.scheme}://{location.netloc}{location.path}" if location.netloc else location.path or "none"


def _session_cookie(resp: aiohttp.ClientResponse) -> str | None:
    """The JSESSIONID the response sets.

    login.do sets the new session and then deletes a JSESSIONID on .samsung.com with an
    empty value; resp.cookies only keeps the last one, so all Set-Cookie headers are read.
    """
    for header in reversed(resp.headers.getall("Set-Cookie", [])):
        cookie = SimpleCookie()
        try:
            cookie.load(header)
        except CookieError:
            continue
        if "JSESSIONID" in cookie and cookie["JSESSIONID"].value:
            return cookie["JSESSIONID"].value
    cookie = resp.cookies.get("JSESSIONID")
    return cookie.value if cookie and cookie.value else None


async def _async_web_login(hass: HomeAssistant, auth_server: str, code: str, code_expires_in: Any) -> str | None:
    """Exchange the authorization code for a SmartThings Find JSESSIONID.

    This runs in its own HTTP session with an empty cookie jar: Home Assistant's shared
    session keeps cookies, and if it already has a SmartThings Find cookie (e.g. from
    the region lookup), getState.do does not issue the new session the login needs.
    """
    web = async_create_clientsession(hass, auto_cleanup=False, cookie_jar=aiohttp.CookieJar())
    try:
        # The web login requires the state and cookie from getState.do of the same session
        async with web.get(
            f"{FIND_URL}/getState.do", params={"payload": "hound"}, allow_redirects=False, timeout=REQUEST_TIMEOUT
        ) as resp:
            if resp.status != 200:
                raise SmartTagsConnectionError(f"getState answered with status {resp.status}")
            state_data = await resp.json(content_type=None)
            login_state = state_data.get("state") if isinstance(state_data, dict) else None
            bootstrap_cookie = _session_cookie(resp)
        if not login_state:
            raise SmartTagsConnectionError("getState did not return a login state")
        if not bootstrap_cookie:
            raise SmartTagsConnectionError("getState did not start a new session")

        auth_host = urllib.parse.urlparse(auth_server).netloc
        async with web.get(
            f"{FIND_URL}/login.do",
            params={
                "auth_server_url": auth_host,
                "api_server_url": auth_host,
                "code": code,
                "code_expires_in": str(code_expires_in),
                "state": login_state,
            },
            cookies={"JSESSIONID": bootstrap_cookie},
            allow_redirects=False,
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            if resp.status in (301, 302, 303, 307):
                # A relative redirect stays on SmartThings Find
                location = urllib.parse.urlparse(urllib.parse.urljoin(f"{FIND_URL}/", resp.headers.get("Location", "")))
                if location.scheme != "https" or location.hostname != "smartthingsfind.samsung.com":
                    raise SmartTagsConnectionError(
                        f"SmartThings Find login redirected to an unexpected destination: {_describe_location(resp)}"
                    )
            elif resp.status != 200:
                raise SmartTagsConnectionError(f"SmartThings Find login answered with status {resp.status}")
            return _session_cookie(resp)
    finally:
        # Only detach: the session shares Home Assistant's connection pool
        web.detach()


async def async_create_web_session(hass: HomeAssistant, credentials: Mapping[str, Any]) -> str:
    """Create a new SmartThings Find JSESSIONID from the stored account token.

    Raises SmartTagsAuthError if Samsung no longer accepts the account token, so the
    user is asked to sign in again, and SmartTagsConnectionError for other failures.
    """
    session = async_get_clientsession(hass)
    auth_server = _trusted_auth_server_url(credentials[CONF_AUTH_SERVER_URL])
    params = {
        "response_type": "code",
        "serviceType": "M",
        "client_id": WEB_FIND_CLIENT_ID,
        "childAccountSupported": "Y",
        "userauth_token": credentials[CONF_USERAUTH_TOKEN],
        "physical_address_text": credentials[CONF_DEVICE_ID],
        "scope": WEB_FIND_SCOPE,
        "login_id": credentials[CONF_LOGIN_ID],
    }

    async def authorize(query: Mapping[str, str]) -> Dict[str, Any]:
        async with session.get(
            f"{auth_server}/auth/oauth2/v2/authorize", params=query, allow_redirects=False, timeout=REQUEST_TIMEOUT
        ) as resp:
            if resp.status in (400, 401, 403):
                raise SmartTagsAuthError(f"Samsung rejected the account token ({resp.status})")
            if resp.status != 200:
                raise SmartTagsConnectionError(
                    f"Samsung authorization answered with status {resp.status} (redirect: {_describe_location(resp)})"
                )
            data = await resp.json(content_type=None)
            if not isinstance(data, dict):
                raise SmartTagsConnectionError("Samsung authorization returned an unexpected response")
            return data

    try:
        auth_data = await authorize(params)
        if not auth_data.get("code") and auth_data.get("privacyAccepted") == "N":
            auth_data = await authorize({k: v for k, v in params.items() if k != "login_id"})
        code = auth_data.get("code")
        if not code:
            raise SmartTagsAuthError("Samsung did not return an authorization code")

        jsession_id = await _async_web_login(hass, auth_server, code, auth_data.get("code_expires_in", 300))
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise SmartTagsConnectionError(f"Error creating a SmartThings Find session: {describe_error(err)}") from err

    if not jsession_id:
        raise SmartTagsConnectionError("SmartThings Find login did not issue a session")
    _LOGGER.debug("Created a new SmartThings Find web session from the Samsung account token")
    return jsession_id
