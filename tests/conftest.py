import pytest
from core.device_manager import DeviceManager
from core.autostart import _autostart_cache


@pytest.fixture(autouse=True)
def reset_device_manager_cache():
    DeviceManager.clear_cache()
    yield
    DeviceManager.clear_cache()
