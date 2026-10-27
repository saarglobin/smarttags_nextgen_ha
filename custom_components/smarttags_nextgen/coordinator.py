import logging
import html
from datetime import timedelta
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .api import SmartTagsAPI, SmartTagsAuthError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class SmartTagCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, jsession_id, region):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=5),
            always_update=False
        )
        # Transmitting the dynamic operational region variable natively into the API setup orchestrator
        self.api = SmartTagsAPI(async_get_clientsession(hass), jsession_id, region)
        self.last_known_timestamps = {}

    async def _async_update_data(self):
        """Refresh CSRF, fetch device list, initialize new tags, and synchronize states."""
        try:
            token_success = await self.api.refresh_csrf_token()
            if not token_success:
                raise UpdateFailed("Failed to dynamically acquire operational CSRF token.")

            devices = await self.api.get_devices()
        except SmartTagsAuthError as err:
            # Makes Home Assistant start the reauth flow so the user can paste a new JSESSIONID
            raise ConfigEntryAuthFailed("SmartThings Find session expired, please provide a new JSESSIONID") from err

        if devices is None:
            raise UpdateFailed("Failed to communicate with Samsung Device List endpoint.")

        tags = [d for d in devices if d.get("deviceType") == "TAG"]
        _LOGGER.info("SmartThings Find: Identified %s tracking tags to process", len(tags))
        
        old_data = self.data if self.data else {}
        normalized_data = {}

        for tag in tags:
            device_id = tag.get("dvceID")
            raw_name = tag.get("modelName") or tag.get("nickName") or "SmartTag"
            name = html.unescape(html.unescape(raw_name))
            old_tag_data = old_data.get(device_id, {})
            
            operations = None

            # Force baseline fetch on every poll to bypass Samsung's static state cache
            _LOGGER.info("Fetching fresh state updates for %s", name)
            operations = await self.api.set_last_select(device_id)

            tag_data = {
                "device_id": device_id,
                "name": name,
                "latitude": old_tag_data.get("latitude"),
                "longitude": old_tag_data.get("longitude"),
                "battery": old_tag_data.get("battery"),
                "location_type": old_tag_data.get("location_type")
            }

            # 3. Parse Operations (Now handling OFFLINE_LOC matrices)
            if operations:
                for oprn in operations:
                    oprn_type = oprn.get("oprnType")
                    
                    if oprn_type in ["LOCATION", "OFFLINE_LOC"]:
                        # Extract the location data
                        tag_data["latitude"] = float(oprn.get("latitude"))
                        tag_data["longitude"] = float(oprn.get("longitude"))
                        
                        # Assign location type based on the operation matrix
                        if oprn_type == "OFFLINE_LOC":
                            tag_data["location_type"] = "offline"
                        else:
                            tag_data["location_type"] = oprn.get("locationType", "gps")
                        
                        # Extract the critical dynamic timestamp to use on the next 5-minute poll
                        if "extra" in oprn and "gpsUtcDt" in oprn["extra"]:
                            self.last_known_timestamps[device_id] = oprn["extra"]["gpsUtcDt"]
                        elif "encLocation" in oprn and "gpsUtcDt" in oprn["encLocation"]:
                            self.last_known_timestamps[device_id] = oprn["encLocation"]["gpsUtcDt"]
                            
                    elif oprn_type == "CHECK_CONNECTION":
                        tag_data["battery"] = oprn.get("battery")
            
            _LOGGER.info(
                "Tracker Update -> Name: %s | Lat: %s | Lon: %s | Timestamp: %s",
                tag_data["name"], tag_data["latitude"], tag_data["longitude"], self.last_known_timestamps.get(device_id, "Unknown")
            )
                        
            normalized_data[device_id] = tag_data

        return normalized_data
