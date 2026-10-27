from unittest.mock import AsyncMock, patch

import pytest

from .common import OPERATIONS

pytest_plugins = "pytest_homeassistant_custom_component"

API = "custom_components.smarttags_nextgen.api.SmartTagsAPI"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def mock_devices():
    """Mock the Samsung API client. Tests fill and modify the returned device list."""
    devices = []
    with (
        patch(f"{API}.refresh_csrf_token", AsyncMock(return_value=None)),
        patch(f"{API}.get_devices", AsyncMock(side_effect=lambda: list(devices))),
        patch(f"{API}.set_last_select", AsyncMock(side_effect=lambda device_id: OPERATIONS.get(device_id))),
    ):
        yield devices
