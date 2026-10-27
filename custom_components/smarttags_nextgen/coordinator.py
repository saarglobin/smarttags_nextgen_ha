import logging
import html
from datetime import datetime, timedelta, timezone
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .api import SmartTagsAPI, SmartTagsAuthError, SmartTagsConnectionError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


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
                "latitude": old_tag_data.get("latitude"),
                "longitude": old_tag_data.get("longitude"),
                "battery": old_tag_data.get("battery"),
                "location_type": old_tag_data.get("location_type"),
                "gps_date": old_tag_data.get("gps_date"),
                "gps_accuracy": old_tag_data.get("gps_accuracy"),
            }

            # Parse Operations (handling OFFLINE_LOC matrices). When several locations are
            # returned, keep the most recent one.
            newest_date = None
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

            _LOGGER.debug(
                "Tracker Update -> Name: %s | Lat: %s | Lon: %s | Timestamp: %s",
                tag_data["name"], tag_data["latitude"], tag_data["longitude"], tag_data["gps_date"]
            )

            normalized_data[device_id] = tag_data

        return normalized_data
