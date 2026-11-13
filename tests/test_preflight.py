import subprocess
import pytest
from core.preflight import preflight, PreflightResult, REQUIRED_TOOLS


def test_preflight_healthy_x11():
    env = {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}
    which_fn = lambda tool: f"/usr/bin/{tool}"
    readlink_fn = lambda path: "/usr/lib/firefox/firefox"
    res = preflight(env=env, which_fn=which_fn, readlink_fn=readlink_fn)
    assert res.ok is True
    assert len(res.errors) == 0


def test_preflight_wayland_without_display():
    env = {"XDG_SESSION_TYPE": "wayland", "DISPLAY": ""}
    which_fn = lambda tool: f"/usr/bin/{tool}"
    res = preflight(env=env, which_fn=which_fn)
    assert res.ok is False
    assert any("Wayland session detected without DISPLAY" in e for e in res.errors)


def test_preflight_wayland_xinput_fails():
    env = {"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"}
    which_fn = lambda tool: f"/usr/bin/{tool}"

    class FakeCompleted:
        returncode = 1

    run_cmd_fn = lambda cmd: FakeCompleted()
    res = preflight(env=env, which_fn=which_fn, run_cmd_fn=run_cmd_fn)
    assert res.ok is False
    assert any("XInput 2 is unavailable" in e for e in res.errors)


def test_preflight_missing_display():
    env = {"XDG_SESSION_TYPE": "x11"}  # no DISPLAY
    which_fn = lambda tool: f"/usr/bin/{tool}"
    res = preflight(env=env, which_fn=which_fn)
    assert res.ok is False
    assert any("DISPLAY environment variable is not set" in e for e in res.errors)


def test_preflight_missing_tools():
    env = {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}
    # Only xinput exists, others missing
    which_fn = lambda tool: f"/usr/bin/{tool}" if tool == "xinput" else None
    res = preflight(env=env, which_fn=which_fn)
    assert res.ok is False
    assert any("Missing required system tools" in e for e in res.errors)
    assert any("firefox" in e for e in res.errors)


def test_preflight_snap_flatpak_warning():
    env = {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}
    which_fn = lambda tool: f"/usr/bin/{tool}"
    readlink_fn = lambda path: "/snap/firefox/current/usr/lib/firefox/firefox"
    res = preflight(env=env, which_fn=which_fn, readlink_fn=readlink_fn)
    assert res.ok is True  # Warning, not blocking error
    assert any("snap or flatpak" in w for w in res.warnings)


def test_preflight_unpacking():
    env = {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}
    which_fn = lambda tool: f"/usr/bin/{tool}"
    errors, warnings = preflight(env=env, which_fn=which_fn)
    assert isinstance(errors, list)
    assert isinstance(warnings, list)
