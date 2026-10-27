import aiohttp
import logging
from typing import Awaitable, Callable, Dict, Any, Optional, List

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


def describe_error(err: Exception) -> str:
    """Describe a request error without its URL and headers, which can contain the session or tokens."""
    if isinstance(err, aiohttp.ClientResponseError):
        return f"{type(err).__name__} (status {err.status})"
    if isinstance(err, aiohttp.ClientError):
        return type(err).__name__
    return f"{type(err).__name__}: {err}"


class SmartTagsAuthError(Exception):
    """Raised when Samsung rejects the JSESSIONID (expired or invalid session)."""


class SmartTagsConnectionError(Exception):
    """Raised when SmartThings Find cannot be reached or answers unexpectedly."""


async def async_get_server_region(session: aiohttp.ClientSession) -> Optional[str]:
    """Return the region Samsung routes this client to (x-fmm-orgin header), or None if unavailable.

    This reflects the server Samsung picks for the client's location and does not require a
    session, so it is only a suggestion for the account's region.
    """
    try:
        async with session.get(
            "https://smartthingsfind.samsung.com/chkLogin.do", timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            return resp.headers.get("x-fmm-orgin") or resp.headers.get("x-fmm-origin")
    except (aiohttp.ClientError, TimeoutError) as err:
        _LOGGER.debug("Could not determine the SmartThings Find server region: %r", err)
        return None


class SmartTagsOperationError(Exception):
    """Raised when Samsung rejects an operation such as ringing a tag."""


class SmartTagsAPI:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        jsession_id: Optional[str],
        region: str,
        session_factory: Optional[Callable[[], Awaitable[str]]] = None,
    ):
        self.session = session
        self.jsession_id = jsession_id
        self.region = region  # Capture the region selected dynamically during config flow execution
        self.csrf_token: Optional[str] = None
        # Creates a new JSESSIONID when the current one expires (Samsung account sign-in)
        self._session_factory = session_factory

    @property
    def cookies(self) -> Dict[str, str]:
        """The session cookie, passed per request.

        A Cookie header would be overridden by a JSESSIONID in the cookie jar of Home
        Assistant's shared session (e.g. an anonymous one Samsung set earlier), while
        cookies passed with the request take precedence over the jar.
        """
        return {"JSESSIONID": self.jsession_id or ""}

    @property
    def headers(self) -> Dict[str, str]:
        """Dynamically build HTTP headers to ensure the current region is evaluated on every request."""
        return {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9,he;q=0.8,ja;q=0.7",
            "origin": "https://smartthingsfind.samsung.com",
            "priority": "u=1, i",
            "referer": "https://smartthingsfind.samsung.com/",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
            "x-fmm-origin": self.region,
            "x-fmm-orgin": self.region  # Maintain the structural typo fallback as discovered natively
        }

    async def refresh_csrf_token(self) -> None:
        """Fetch a fresh CSRF token, creating a new session first if needed and possible."""
        if self._session_factory is None:
            await self._fetch_csrf_token()
            return
        if self.jsession_id is None:
            self.jsession_id = await self._session_factory()
        try:
            await self._fetch_csrf_token()
        except SmartTagsAuthError:
            _LOGGER.debug("SmartThings Find session expired, creating a new one")
            self.jsession_id = await self._session_factory()
            await self._fetch_csrf_token()

    async def _fetch_csrf_token(self) -> None:
        """Fetch a fresh CSRF token from the chkLogin endpoint."""
        url = "https://smartthingsfind.samsung.com/chkLogin.do"
        try:
            async with self.session.get(url, headers=self.headers, cookies=self.cookies, timeout=REQUEST_TIMEOUT) as resp:
                csrf = resp.headers.get("_csrf") or resp.headers.get("X-CSRF-TOKEN")
                if csrf:
                    self.csrf_token = csrf
                    _LOGGER.debug("SmartThings Find: Successfully refreshed CSRF token dynamically")
                    return
                body = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SmartTagsConnectionError(f"Error requesting CSRF token: {describe_error(err)}") from err

        # An unknown or expired session answers 200 with the body "fail" (and no _csrf header)
        if resp.status == 401 or (resp.status == 200 and body.strip() == "fail"):
            raise SmartTagsAuthError("chkLogin rejected the JSESSIONID")
        raise SmartTagsConnectionError(f"chkLogin answered with status {resp.status} but without a CSRF token")

    async def get_devices(self) -> List[Dict[str, Any]]:
        """Fetch the list of all registered devices, creating a new session once if it was rejected."""
        try:
            return await self._fetch_devices()
        except SmartTagsAuthError:
            if self._session_factory is None:
                raise
            _LOGGER.debug("SmartThings Find rejected the session for the device list, creating a new one")
            self.jsession_id = await self._session_factory()
            await self._fetch_csrf_token()
            return await self._fetch_devices()

    async def _fetch_devices(self) -> List[Dict[str, Any]]:
        """Fetch the list of all registered devices."""
        if not self.csrf_token:
            raise SmartTagsConnectionError("Cannot fetch devices: CSRF token is missing or uninitialized")

        url = f"https://smartthingsfind.samsung.com/device/getDeviceList.do?_csrf={self.csrf_token}"
        headers = {**self.headers, "content-type": "application/json"}

        try:
            async with self.session.post(
                url, headers=headers, cookies=self.cookies, json={}, timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status == 401:
                    raise SmartTagsAuthError("getDeviceList rejected the JSESSIONID")
                if resp.status != 200:
                    raise SmartTagsConnectionError(f"getDeviceList answered with status {resp.status}")
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise SmartTagsConnectionError(f"Error fetching device list: {describe_error(err)}") from err

        if not isinstance(data, dict):
            raise SmartTagsConnectionError("getDeviceList returned an unexpected response")
        device_list = data.get("deviceList", [])
        _LOGGER.debug("SmartThings Find: Found %s total devices in Samsung account", len(device_list))
        return device_list

    async def set_last_select(self, device_id: str) -> Optional[List[Dict[str, Any]]]:
        """Fetch baseline state state updates for tracking entities."""
        if not self.csrf_token:
            return None

        url = f"https://smartthingsfind.samsung.com/device/setLastSelect.do?_csrf={self.csrf_token}"
        headers = {**self.headers, "content-type": "application/json"}
        payload = {"dvceId": device_id}

        try:
            async with self.session.post(
                url, headers=headers, cookies=self.cookies, json=payload, timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status != 200:
                    _LOGGER.debug("setLastSelect for %s answered with status %s", device_id, resp.status)
                    return None
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            # A single tag failing keeps its previous state instead of failing the whole update
            _LOGGER.debug("Error fetching state of %s: %s", device_id, describe_error(err))
            return None
        return data.get("operation", []) if isinstance(data, dict) else None

    async def _post_operation_request(self, path: str, payload: Dict[str, Any], step: str) -> Dict[str, Any]:
        if not self.csrf_token:
            raise SmartTagsConnectionError(f"Cannot {step}: CSRF token is missing or uninitialized")
        url = f"https://smartthingsfind.samsung.com{path}?_csrf={self.csrf_token}"
        headers = {**self.headers, "content-type": "application/json"}
        try:
            async with self.session.post(
                url, headers=headers, cookies=self.cookies, json=payload, timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status == 401:
                    raise SmartTagsAuthError(f"{step} rejected the JSESSIONID")
                if resp.status != 200:
                    raise SmartTagsConnectionError(f"{step} answered with status {resp.status}")
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise SmartTagsConnectionError(f"Error during {step}: {describe_error(err)}") from err
        if not isinstance(data, dict):
            raise SmartTagsConnectionError(f"{step} returned an unexpected response")
        return data

    async def add_operation(
        self, device_id: str, user_id: Optional[str], operation: str, extra: Optional[Dict[str, Any]] = None
    ) -> None:
        """Ask a device to perform an operation, e.g. RING or CHECK_CONNECTION_WITH_LOCATION."""
        payload = {"dvceId": device_id, "operation": operation, "usrId": user_id, **(extra or {})}
        data = await self._post_operation_request("/dm/addOperation.do", payload, f"{operation} request")
        # Like the SmartThings Find website, which also accepts requests for devices in power saving mode
        if data.get("resultCode") != "00" and data.get("powerSavingResultCode") != "00":
            raise SmartTagsOperationError(
                f"Samsung rejected the {operation} request ({data.get('resultCode')}, {data.get('faultCode')})"
            )

    async def get_tag_location(self, device_id: str, latest_time: str) -> List[Dict[str, Any]]:
        """Fetch the locations a tag reported, as the website does after a location request.

        latest_time is the time of the newest known location (YYYYMMDDHHMMSS, "00000000" if none).
        """
        payload = {"dvceId": device_id, "latestTime": latest_time}
        data = await self._post_operation_request("/dm/getTagLocation.do", payload, "tag location")
        operations = data.get("operation")
        return operations if isinstance(operations, list) else []
