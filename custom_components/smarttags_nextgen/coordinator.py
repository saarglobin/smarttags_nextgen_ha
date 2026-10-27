import asyncio
import logging
import html
from datetime import datetime, timedelta, timezone
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .api import (
    SmartTagsAPI,
    SmartTagsAuthError,
    SmartTagsConnectionError,
    SmartTagsOperationError,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# How long the SmartThings Find website waits for a tag to report its location after a request
LOCATION_REQUEST_WAIT = 30


def calc_gps_accuracy(horizontal, vertical):
    """Combine the horizontal and vertical uncertainty (meters) into one accuracy value."""
    try:
        return round((float(horizontal) ** 2 + float(vertical) ** 2) ** 0.5, 1)
    except (TypeError, ValueError):
        return None


def parse_stf_date(value):
    """Parse a SmartThings Find timestamp (YYYYMMDDHHMMSS, UTC), returning None if it is missing or malformed."""
    try:
        return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def apply_operations(tag_data, operations, name, newest_date=None):
    """Update tag_data from the operations of a SmartThings Find response.

    When several locations are returned the most recent one is used; a location
    not newer than newest_date is ignored.
    """
    for oprn in operations or []:
        oprn_type = oprn.get("oprnType")

        if oprn_type in ["LOCATION", "LASTLOC", "OFFLINE_LOC"]:
            try:
                latitude = float(oprn["latitude"])
                longitude = float(oprn["longitude"])
            except (KeyError, TypeError, ValueError):
                _LOGGER.debug("Skipping %s operation without coordinates for %s", oprn_type, name)
                continue

            gps_date = parse_stf_date((oprn.get("extra") or {}).get("gpsUtcDt"))
            if gps_date is None:
                gps_date = parse_stf_date((oprn.get("encLocation") or {}).get("gpsUtcDt"))
            if newest_date is not None and gps_date is not None and gps_date <= newest_date:
                continue
            newest_date = gps_date or newest_date

            tag_data["latitude"] = latitude
            tag_data["longitude"] = longitude
            tag_data["gps_date"] = gps_date
            tag_data["gps_accuracy"] = calc_gps_accuracy(
                oprn.get("horizontalUncertainty"), oprn.get("verticalUncertainty")
            )

            # Assign location type based on the operation matrix
            if oprn_type == "OFFLINE_LOC":
                tag_data["location_type"] = "offline"
            else:
                tag_data["location_type"] = oprn.get("locationType", "gps")

        elif oprn_type == "CHECK_CONNECTION":
            tag_data["battery"] = oprn.get("battery")


class SmartTagCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, config_entry, jsession_id, region, update_interval, session_factory=None):
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=update_interval,
            always_update=False
        )
        # Transmitting the dynamic operational region variable natively into the API setup orchestrator
        self.api = SmartTagsAPI(async_get_clientsession(hass), jsession_id, region, session_factory)
        # Samsung account id (usrId) of the devices, used as the config entry unique id
        self.account_id = None
        # Last raw operations per tag, kept for diagnostics
        self.last_operations = {}

    async def _async_update_data(self):
        """Refresh CSRF, fetch device list, initialize new tags, and synchronize states."""
        try:
            await self.api.refresh_csrf_token()
            devices = await self.api.get_devices()
        except SmartTagsAuthError as err:
            # Makes Home Assistant start the reauth flow so the user can paste a new JSESSIONID
            raise ConfigEntryAuthFailed("SmartThings Find session expired, please provide a new JSESSIONID") from err
        except SmartTagsConnectionError as err:
            raise UpdateFailed(str(err)) from err

        for device in devices:
            if device.get("usrId"):
                self.account_id = str(device["usrId"])
                break

        tags = [d for d in devices if d.get("deviceType") == "TAG"]
        _LOGGER.debug("SmartThings Find: Identified %s tracking tags to process", len(tags))

        old_data = self.data if self.data else {}
        normalized_data = {}
        self.last_operations = {}

        for tag in tags:
            device_id = tag.get("dvceID")
            # Prefer the user-given nickname so several tags of the same model get distinct names
            raw_name = tag.get("nickName") or tag.get("modelName") or "SmartTag"
            name = html.unescape(html.unescape(raw_name))
            old_tag_data = old_data.get(device_id, {})

            # Force baseline fetch on every poll to bypass Samsung's static state cache
            _LOGGER.debug("Fetching fresh state updates for %s", name)
            operations = await self.api.set_last_select(device_id)
            self.last_operations[device_id] = operations
            if operations is None:
                _LOGGER.debug("No state update received for %s, keeping the previous state", name)

            tag_data = {
                "device_id": device_id,
                "name": name,
                "model": html.unescape(html.unescape(tag["modelName"])) if tag.get("modelName") else None,
                # Account the tag belongs to, which differs for tags shared with this account
                "user_id": tag.get("usrId"),
                "latitude": old_tag_data.get("latitude"),
                "longitude": old_tag_data.get("longitude"),
                "battery": old_tag_data.get("battery"),
                "location_type": old_tag_data.get("location_type"),
                "gps_date": old_tag_data.get("gps_date"),
                "gps_accuracy": old_tag_data.get("gps_accuracy"),
            }

            apply_operations(tag_data, operations, name)

            _LOGGER.debug(
                "Tracker Update -> Name: %s | Lat: %s | Lon: %s | Timestamp: %s",
                tag_data["name"], tag_data["latitude"], tag_data["longitude"], tag_data["gps_date"]
            )

            normalized_data[device_id] = tag_data

        return normalized_data

    async def async_start_operation(self, device_id, operation, extra=None):
        """Ask a tag to perform an operation, e.g. RING."""
        user_id = (self.data or {}).get(device_id, {}).get("user_id")
        try:
            # Also creates a new session if it expired and the account sign-in is used
            await self.api.refresh_csrf_token()
            await self.api.add_operation(device_id, user_id, operation, extra)
        except SmartTagsAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="session_expired"
            ) from err
        except (SmartTagsConnectionError, SmartTagsOperationError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="operation_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    async def async_request_location(self, device_id):
        """Ask a tag for its current location and update it once reported.

        Like the SmartThings Find website: request CHECK_CONNECTION_WITH_LOCATION, give the
        tag some time to be found by nearby Galaxy devices, then fetch its locations.
        """
        await self.async_start_operation(device_id, "CHECK_CONNECTION_WITH_LOCATION")
        self.config_entry.async_create_background_task(
            self.hass,
            self._async_fetch_requested_location(device_id),
            name=f"{DOMAIN} location request {device_id}",
        )

    async def _async_fetch_requested_location(self, device_id):
        await asyncio.sleep(LOCATION_REQUEST_WAIT)
        tag_data = (self.data or {}).get(device_id)
        if tag_data is None:
            return
        known_date = tag_data.get("gps_date")
        try:
            operations = await self.api.get_tag_location(
                device_id, known_date.strftime("%Y%m%d%H%M%S") if known_date else "00000000"
            )
        except (SmartTagsAuthError, SmartTagsConnectionError) as err:
            _LOGGER.debug("Could not fetch the requested location of %s: %s", device_id, err)
            return

        updated = dict(tag_data)
        apply_operations(updated, operations, tag_data.get("name"), newest_date=known_date)
        _LOGGER.debug(
            "Requested location of %s: %s", device_id, "updated" if updated != tag_data else "no newer location"
        )
        if updated != tag_data:
            self.async_set_updated_data({**self.data, device_id: updated})
