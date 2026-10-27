import aiohttp
import logging
from typing import Dict, Any, Optional, List

_LOGGER = logging.getLogger(__name__)


class SmartTagsAuthError(Exception):
    """Raised when Samsung rejects the JSESSIONID (expired or invalid session)."""


class SmartTagsAPI:
    def __init__(self, session: aiohttp.ClientSession, jsession_id: str, region: str):
        self.session = session
        self.jsession_id = jsession_id
        self.region = region  # Capture the region selected dynamically during config flow execution
        self.csrf_token: Optional[str] = None

    @property
    def headers(self) -> Dict[str, str]:
        """Dynamically build HTTP headers to ensure the current region is evaluated on every request."""
        return {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9,he;q=0.8,ja;q=0.7",
            "Cookie": f"JSESSIONID={self.jsession_id}",
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

    async def refresh_csrf_token(self) -> bool:
        """Fetch a fresh CSRF token from the chkLogin endpoint."""
        url = "https://smartthingsfind.samsung.com/chkLogin.do"
        try:
            async with self.session.get(url, headers=self.headers) as resp:
                csrf = resp.headers.get("_csrf") or resp.headers.get("X-CSRF-TOKEN")
                if csrf:
                    self.csrf_token = csrf
                    _LOGGER.info("SmartThings Find: Successfully refreshed CSRF token dynamically")
                    return True

                # An unknown or expired session answers 200 with the body "fail" (and no _csrf header)
                body = await resp.text()
                if resp.status == 401 or (resp.status == 200 and body.strip() == "fail"):
                    raise SmartTagsAuthError("chkLogin rejected the JSESSIONID")

                _LOGGER.error("SmartThings Find: chkLogin responded with status %s but '_csrf' header was missing.", resp.status)
                return False
        except SmartTagsAuthError:
            raise
        except Exception as e:
            _LOGGER.error("Network error attempting to refresh CSRF token: %s", e)
            return False

    async def get_devices(self) -> Optional[List[Dict[str, Any]]]:
        """Fetch the list of all registered devices."""
        if not self.csrf_token:
            _LOGGER.error("Cannot fetch devices: CSRF token is missing or uninitialized")
            return None

        url = f"https://smartthingsfind.samsung.com/device/getDeviceList.do?_csrf={self.csrf_token}"
        headers = {**self.headers, "content-type": "application/json"}
        
        #-- check outgoing request ---
        # _LOGGER.critical("QA DIAGNOSTIC - OUTGOING REQUEST HEADERS: %s", headers)
        # -----------------------------------------------------

        try:
            async with self.session.post(url, headers=headers, json={}) as resp:
                if resp.status == 401:
                    raise SmartTagsAuthError("getDeviceList rejected the JSESSIONID")
                if resp.status != 200:
                    _LOGGER.error("Failed to fetch device list. Status: %s", resp.status)
                    return None
                    
                data = await resp.json()
                if data:
                    device_list = data.get("deviceList", [])
                    _LOGGER.info("SmartThings Find: Found %s total devices in Samsung account", len(device_list))
                    return device_list
                return None
        except SmartTagsAuthError:
            raise
        except Exception as e:
            _LOGGER.error("Network error fetching device list: %s", e)
            return None

    async def set_last_select(self, device_id: str) -> Optional[List[Dict[str, Any]]]:
        """Fetch baseline state state updates for tracking entities."""
        if not self.csrf_token:
            return None

        url = f"https://smartthingsfind.samsung.com/device/setLastSelect.do?_csrf={self.csrf_token}"
        headers = {**self.headers, "content-type": "application/json"}
        payload = {"dvceId": device_id}

        try:
            async with self.session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("operation", []) if data else None
        except Exception:
            return None

    async def get_device_locations(self, device_id: str, latest_time: str) -> Optional[List[Dict[str, Any]]]:
        """Fetch live coordinate telemetry attributes."""
        if not self.csrf_token:
            return None

        url = f"https://smartthingsfind.samsung.com/dm/getTagLocation.do?_csrf={self.csrf_token}"
        headers = {**self.headers, "content-type": "application/json"}
        payload = {"dvceId": device_id, "latestTime": latest_time}

        try:
            async with self.session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("operation", []) if data else None
        except Exception:
            return None
