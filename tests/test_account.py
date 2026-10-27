"""Tests for the Samsung account sign-in against mocked Samsung endpoints."""
import base64
import json
import urllib.parse
from unittest.mock import patch

import aiohttp

import pytest
from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from custom_components.smarttags_nextgen.account import (
    ENTRY_POINT_URL,
    REDIRECT_URI,
    InvalidRedirectError,
    PendingSignIn,
    SamsungSignInError,
    async_complete_sign_in,
    async_create_web_session,
    async_start_sign_in,
    is_redirect_uri,
)
from custom_components.smarttags_nextgen.api import SmartTagsAuthError, SmartTagsConnectionError

from .common import record_cookies

AUTH_SERVER = "https://eu-auth2.samsungosp.com"
FIND = "https://smartthingsfind.samsung.com"
CREDENTIALS = {
    "userauth_token": "master-token",
    "user_id": "user",
    "login_id": "me@example.com",
    "device_id": "abcdef",
    "auth_server_url": AUTH_SERVER,
}


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _decrypt_svc_param(svc_param, private_key):
    """Reverse the sign-in parameter encryption like Samsung's server does."""
    envelope = json.loads(base64.b64decode(urllib.parse.unquote(svc_param)))
    aes_key = base64.b64decode(private_key.decrypt(base64.b64decode(envelope["svcEncKY"]), asym_padding.PKCS1v15()))
    decryptor = Cipher(algorithms.AES(aes_key), modes.CBC(bytes.fromhex(envelope["svcEncIV"]))).decryptor()
    padded = decryptor.update(base64.b64decode(envelope["svcEncParam"])) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return json.loads(unpadder.update(padded) + unpadder.finalize())


def _encrypt_value(value, key):
    """Encrypt a redirect field like Samsung does (AES-ECB with the key's first 16 bytes)."""
    padder = padding.PKCS7(128).padder()
    data = padder.update(value.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key.encode()[:16].ljust(16, b"\0")), modes.ECB()).encryptor()
    return (encryptor.update(data) + encryptor.finalize()).hex()


def _redirect(pending, auth_server=AUTH_SERVER, response_key="response-key-123"):
    fields = {
        "state": _encrypt_value(response_key, pending.state),
        "auth_server_url": _encrypt_value(auth_server, response_key),
        "code": _encrypt_value("auth-code", response_key),
        "retValue": _encrypt_value("me@example.com", response_key),
    }
    return f"{REDIRECT_URI}?{urllib.parse.urlencode(fields)}"


def _pending():
    return PendingSignIn(url="https://example", state="pending-state-1234", code_verifier="verifier", device_id="abcdef")


async def test_start_sign_in(hass, aioclient_mock, rsa_key):
    public_key = rsa_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    aioclient_mock.get(
        ENTRY_POINT_URL,
        json={
            "chkDoNum": "1000",
            "pkiPublicKey": base64.b64encode(public_key).decode(),
            "signInURI": "https://account.samsung.com/signin",
        },
    )
    hass.config.country = "DE"

    pending = await async_start_sign_in(hass, device_id="abcdef")

    assert pending.url.startswith("https://account.samsung.com/signin?locale=en-US&svcParam=")
    assert pending.device_id == "abcdef"
    svc_param = urllib.parse.parse_qs(urllib.parse.urlparse(pending.url).query)["svcParam"][0]
    payload = _decrypt_svc_param(urllib.parse.quote(svc_param, safe=""), rsa_key)
    assert payload["state"] == pending.state
    assert payload["countryCode"] == "de"
    assert payload["deviceUniqueID"] == "abcdef"
    assert payload["redirect_uri"] == REDIRECT_URI
    assert payload["code_challenge_method"] == "S256"

    # without a device id a new random one is generated
    assert (await async_start_sign_in(hass)).device_id != "abcdef"


async def test_start_sign_in_unavailable(hass, aioclient_mock):
    aioclient_mock.get(ENTRY_POINT_URL, status=503)
    with pytest.raises(SamsungSignInError):
        await async_start_sign_in(hass)


def test_is_redirect_uri():
    assert is_redirect_uri(f" {REDIRECT_URI}?state=x ")
    assert not is_redirect_uri("https://smartthingsfind.samsung.com/?state=x")
    assert not is_redirect_uri("ms-app://other?state=x")


async def test_complete_sign_in(hass, aioclient_mock):
    aioclient_mock.post(f"{AUTH_SERVER}/auth/oauth2/authenticate", json={"userauth_token": "T", "userId": 42})
    pending = _pending()

    credentials = await async_complete_sign_in(hass, pending, _redirect(pending))

    assert credentials == {
        "userauth_token": "T",
        "user_id": "42",
        "login_id": "me@example.com",
        "device_id": "abcdef",
        "auth_server_url": AUTH_SERVER,
    }
    posted = aioclient_mock.mock_calls[0][2]
    assert posted["code"] == "auth-code"
    assert posted["code_verifier"] == "verifier"
    assert posted["physical_address_text"] == "abcdef"


async def test_complete_sign_in_bare_auth_server_host(hass, aioclient_mock):
    """Samsung sends the auth server as a bare host name in the redirect."""
    aioclient_mock.post(f"{AUTH_SERVER}/auth/oauth2/authenticate", json={"userauth_token": "T", "userId": 42})
    pending = _pending()

    credentials = await async_complete_sign_in(hass, pending, _redirect(pending, auth_server="eu-auth2.samsungosp.com"))

    assert credentials["auth_server_url"] == AUTH_SERVER
    assert str(aioclient_mock.mock_calls[0][1]) == f"{AUTH_SERVER}/auth/oauth2/authenticate"


@pytest.mark.parametrize("auth_server", ["https://evil.example.com", "evil.example.com", "http://eu-auth2.samsungosp.com"])
async def test_complete_sign_in_untrusted_server(hass, aioclient_mock, auth_server):
    pending = _pending()
    with pytest.raises(SamsungSignInError):
        await async_complete_sign_in(hass, pending, _redirect(pending, auth_server=auth_server))
    # the sign-in code is never sent to another server
    assert not aioclient_mock.mock_calls


async def test_complete_sign_in_invalid_redirect(hass, aioclient_mock):
    pending = _pending()
    with pytest.raises(InvalidRedirectError):
        await async_complete_sign_in(hass, pending, "https://example.com/?state=1")
    # a redirect of another sign-in cannot be decrypted
    other = PendingSignIn(url="", state="another-state-5678", code_verifier="", device_id="")
    with pytest.raises(InvalidRedirectError):
        await async_complete_sign_in(hass, pending, _redirect(other))
    assert not aioclient_mock.mock_calls


async def test_complete_sign_in_rejected(hass, aioclient_mock):
    aioclient_mock.post(f"{AUTH_SERVER}/auth/oauth2/authenticate", status=400)
    pending = _pending()
    with pytest.raises(SamsungSignInError):
        await async_complete_sign_in(hass, pending, _redirect(pending))


def _mock_web_login(aioclient_mock, login_status=302, location=f"{FIND}/"):
    aioclient_mock.get(f"{FIND}/getState.do", json={"state": "server-state"}, cookies={"JSESSIONID": "bootstrap"})
    aioclient_mock.get(
        f"{FIND}/login.do", status=login_status, headers={"Location": location}, cookies={"JSESSIONID": "new-session"}
    )


async def test_create_web_session(hass, aioclient_mock):
    cookies = record_cookies(aioclient_mock)
    aioclient_mock.get(f"{AUTH_SERVER}/auth/oauth2/v2/authorize", json={"code": "web-code"})
    _mock_web_login(aioclient_mock)

    assert await async_create_web_session(hass, CREDENTIALS) == "new-session"

    authorize, _, login = aioclient_mock.mock_calls
    assert authorize[1].query["userauth_token"] == "master-token"
    assert authorize[1].query["client_id"] == "ntly6zvfpn"
    assert login[1].query["state"] == "server-state"
    assert login[1].query["code"] == "web-code"
    assert login[1].query["auth_server_url"] == "eu-auth2.samsungosp.com"
    # the login has to use the cookie of getState.do
    assert cookies[2] == (f"{FIND}/login.do", {"JSESSIONID": "bootstrap"})


async def test_create_web_session_privacy_retry(hass, aioclient_mock):
    """Without accepted privacy terms Samsung wants the authorization without login_id."""
    aioclient_mock.get(
        f"{AUTH_SERVER}/auth/oauth2/v2/authorize?login_id=me%40example.com", json={"privacyAccepted": "N"}
    )
    aioclient_mock.get(f"{AUTH_SERVER}/auth/oauth2/v2/authorize", json={"code": "web-code"})
    _mock_web_login(aioclient_mock)

    assert await async_create_web_session(hass, CREDENTIALS) == "new-session"
    assert "login_id" not in aioclient_mock.mock_calls[1][1].query


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_create_web_session_token_rejected(hass, aioclient_mock, status):
    aioclient_mock.get(f"{AUTH_SERVER}/auth/oauth2/v2/authorize", status=status)
    with pytest.raises(SmartTagsAuthError):
        await async_create_web_session(hass, CREDENTIALS)


async def test_create_web_session_errors(hass, aioclient_mock):
    aioclient_mock.get(f"{AUTH_SERVER}/auth/oauth2/v2/authorize", status=503)
    with pytest.raises(SmartTagsConnectionError):
        await async_create_web_session(hass, CREDENTIALS)

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{AUTH_SERVER}/auth/oauth2/v2/authorize", json={"code": "web-code"})
    _mock_web_login(aioclient_mock, location="https://evil.example.com/")
    with pytest.raises(SmartTagsConnectionError):
        await async_create_web_session(hass, CREDENTIALS)

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{AUTH_SERVER}/auth/oauth2/v2/authorize", exc=TimeoutError())
    with pytest.raises(SmartTagsConnectionError):
        await async_create_web_session(hass, CREDENTIALS)


async def test_create_web_session_relative_redirect(hass, aioclient_mock):
    aioclient_mock.get(f"{AUTH_SERVER}/auth/oauth2/v2/authorize", json={"code": "web-code"})
    _mock_web_login(aioclient_mock, location="/")
    assert await async_create_web_session(hass, CREDENTIALS) == "new-session"


async def test_create_web_session_error_hides_token(hass, aioclient_mock):
    """Errors are logged by the config flow, so they must not contain the token from the URL."""
    import aiohttp

    aioclient_mock.get(
        f"{AUTH_SERVER}/auth/oauth2/v2/authorize",
        exc=aiohttp.ClientResponseError(
            aiohttp.RequestInfo(
                url=aiohttp.client.URL(f"{AUTH_SERVER}/auth/oauth2/v2/authorize?userauth_token=master-token"),
                method="GET",
                headers={},
                real_url=aiohttp.client.URL(f"{AUTH_SERVER}/auth/oauth2/v2/authorize?userauth_token=master-token"),
            ),
            (),
            status=500,
        ),
    )
    with pytest.raises(SmartTagsConnectionError) as err:
        await async_create_web_session(hass, CREDENTIALS)
    assert "master-token" not in str(err.value)
    assert "status 500" in str(err.value)


async def test_create_web_session_unexpected_status_names_step(hass, aioclient_mock):
    aioclient_mock.get(
        f"{AUTH_SERVER}/auth/oauth2/v2/authorize", status=302, headers={"Location": "https://account.samsung.com/consent?c=secret"}
    )
    with pytest.raises(SmartTagsConnectionError) as err:
        await async_create_web_session(hass, CREDENTIALS)
    assert str(err.value) == (
        "Samsung authorization answered with status 302 (redirect: https://account.samsung.com/consent)"
    )


async def test_create_web_session_ignores_cookie_deletion(hass, aioclient_mock):
    """login.do sets the new session, then deletes a .samsung.com JSESSIONID with an empty value."""
    aioclient_mock.get(f"{AUTH_SERVER}/auth/oauth2/v2/authorize", json={"code": "web-code"})
    aioclient_mock.get(f"{FIND}/getState.do", json={"state": "server-state"}, cookies={"JSESSIONID": "bootstrap"})
    aioclient_mock.get(
        f"{FIND}/login.do",
        status=302,
        headers=[
            ("Location", "https://smartthingsfind.samsung.com"),
            ("Set-Cookie", "JSESSIONID=new-session; Path=/; HttpOnly;HttpOnly;Secure"),
            (
                "Set-Cookie",
                'JSESSIONID=""; Domain=.samsung.com; Expires=Thu, 01 Jan 1970 00:00:10 GMT; Path=/;HttpOnly;Secure',
            ),
        ],
    )
    assert await async_create_web_session(hass, CREDENTIALS) == "new-session"


async def test_web_login_uses_own_cookie_jar(hass, aioclient_mock):
    """The shared session may hold an old SmartThings Find cookie, which stops getState.do from starting a session."""
    from homeassistant.helpers import aiohttp_client

    from custom_components.smarttags_nextgen import account

    aioclient_mock.get(f"{AUTH_SERVER}/auth/oauth2/v2/authorize", json={"code": "web-code"})
    _mock_web_login(aioclient_mock)
    sessions = []

    def create(hass, **kwargs):
        session = aiohttp_client.async_create_clientsession(hass, **kwargs)
        sessions.append((session, kwargs))
        return session

    with patch.object(account, "async_create_clientsession", create):
        assert await async_create_web_session(hass, CREDENTIALS) == "new-session"
    ((session, kwargs),) = sessions
    assert kwargs["auto_cleanup"] is False
    assert isinstance(kwargs["cookie_jar"], aiohttp.CookieJar)
    assert kwargs["cookie_jar"] is not aiohttp_client.async_get_clientsession(hass).cookie_jar
    # detached again, without closing Home Assistant's shared connection pool
    assert session.connector is None
    assert not aiohttp_client.async_get_clientsession(hass).closed


async def test_create_web_session_without_new_session(hass, aioclient_mock):
    aioclient_mock.get(f"{AUTH_SERVER}/auth/oauth2/v2/authorize", json={"code": "web-code"})
    aioclient_mock.get(f"{FIND}/getState.do", json={"state": "server-state"})
    with pytest.raises(SmartTagsConnectionError, match="getState did not start a new session"):
        await async_create_web_session(hass, CREDENTIALS)
