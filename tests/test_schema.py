import pytest
from core.schema import CONFIG_VERSION, get_default_config, validate, SchemaError
from core.config_manager import ConfigManager


def test_schema_default_config_valid():
    cfg = get_default_config()
    assert cfg["version"] == CONFIG_VERSION
    errors = validate(cfg)
    assert len(errors) == 0


def test_schema_root_validation():
    errors = validate("not a dict")
    assert len(errors) == 1
    assert errors[0].path == "root"


def test_schema_seats_validation():
    # Missing seats
    errors = validate({"version": "1.0"})
    assert any(e.path == "seats" for e in errors)

    # Duplicate seat ids
    bad_seats = {
        "version": "1.0",
        "seats": [
            {"id": "seat1", "is_primary": True},
            {"id": "seat1", "is_primary": False},
        ],
    }
    errors = validate(bad_seats)
    assert any("Duplicate seat id" in e.message for e in errors)

    # Invalid URL scheme
    bad_url = {
        "version": "1.0",
        "seats": [
            {"id": "seat1", "is_primary": True, "kiosk_url": "file:///etc/passwd"},
            {"id": "seat2", "is_primary": False, "kiosk_url": "https://duckduckgo.com"},
        ],
    }
    errors = validate(bad_url)
    assert any("seats[0].kiosk_url" in e.path for e in errors)


def test_reset_to_defaults_writes_backup(tmp_path):
    cfg_file = tmp_path / "config.json"
    bak_file = tmp_path / "config.json.bak"

    cm = ConfigManager(config_path=cfg_file)
    initial_custom = {
        "version": "1.0",
        "seats": [
            {"id": "seat1", "kiosk_url": "https://custom.com", "is_primary": True},
            {"id": "seat2", "kiosk_url": "https://custom2.com", "is_primary": False},
        ],
    }
    cm.save_config(initial_custom)
    assert cfg_file.exists()

    # Reset to defaults
    cm.reset_to_defaults()

    # .bak file must exist and contain the pre-reset config
    assert bak_file.exists()
    assert "https://custom.com" in bak_file.read_text()

    # Current config must have factory defaults
    loaded = cm.load_config()
    assert loaded["seats"][0]["kiosk_url"] == "https://duckduckgo.com"
