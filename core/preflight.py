"""
Environment preflight checks for XInput Pseudo-Seating.
Validates session type, display availability, required binary tools,
XInput 2 support, and Firefox packaging restrictions.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import List, NamedTuple, Optional

logger = logging.getLogger("xinput_seating.preflight")

REQUIRED_TOOLS = ["xinput", "xrandr", "firefox", "wmctrl", "xdotool", "xkbcomp"]


class PreflightResult(NamedTuple):
    errors: List[str]
    warnings: List[str]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def check_xfixes_version(run_cmd_fn=None) -> Optional[tuple]:
    """Attempts to determine XFixes version via Xlib if available."""
    try:
        from Xlib import display  # type: ignore
        d = display.Display()
        if hasattr(d, "xfixes_query_version"):
            v = d.xfixes_query_version(5, 0)
            return (getattr(v, "major_version", 0), getattr(v, "minor_version", 0))
    except Exception:
        pass
    return None


def preflight(
    env: Optional[dict] = None,
    which_fn=None,
    run_cmd_fn=None,
    readlink_fn=None,
    check_xfixes: bool = False,
) -> PreflightResult:
    """
    Validates runtime environment for pseudo-seating.
    Returns PreflightResult with blocking errors and advisory warnings.
    """
    if env is None:
        env = os.environ
    if which_fn is None:
        which_fn = shutil.which
    if run_cmd_fn is None:
        def _default_run(cmd):
            return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        run_cmd_fn = _default_run
    if readlink_fn is None:
        readlink_fn = os.path.realpath

    errors: List[str] = []
    warnings: List[str] = []

    # 1. DISPLAY check
    display = env.get("DISPLAY", "")
    session_type = env.get("XDG_SESSION_TYPE", "").lower()

    if session_type == "wayland":
        if not display:
            errors.append("Wayland session detected without DISPLAY set. Pseudo-seating requires X11 or XWayland with XI2.")
        else:
            # Check if XI2 is operational through XWayland
            xinput_bin = which_fn("xinput")
            if xinput_bin:
                try:
                    res = run_cmd_fn(["xinput", "list"])
                    if getattr(res, "returncode", 0) != 0:
                        errors.append("Wayland session detected and XInput 2 is unavailable (xinput list failed). Pseudo-seating requires X11.")
                except Exception as exc:
                    errors.append(f"Wayland session detected and XInput 2 check failed: {exc}")
            else:
                errors.append("Wayland session detected without xinput tool. Pseudo-seating requires X11.")
    elif not display:
        errors.append("DISPLAY environment variable is not set. An active X11 display is required.")

    # 2. Required tools check
    missing_tools = []
    for tool in REQUIRED_TOOLS:
        if not which_fn(tool):
            missing_tools.append(tool)
    if missing_tools:
        errors.append(f"Missing required system tools: {', '.join(missing_tools)}")

    # 3. python-xlib check
    try:
        import Xlib  # noqa: F401
    except ImportError:
        warnings.append("python-xlib is not installed. Kiosk shield window policing will be disabled.")

    # 4. XFixes version check
    if check_xfixes:
        ver = check_xfixes_version(run_cmd_fn)
        if ver is not None and ver[0] < 5:
            warnings.append(f"XFixes version {ver[0]}.{ver[1]} is less than 5.0. Pointer barrier support may be limited.")

    # 5. Firefox packaging check (snap / flatpak)
    firefox_bin = which_fn("firefox")
    if firefox_bin:
        try:
            resolved = str(readlink_fn(firefox_bin)).lower()
            if "/snap/" in resolved or "snap" in resolved or "flatpak" in resolved:
                warnings.append(
                    f"Firefox appears to be packaged via snap or flatpak ({resolved}). "
                    "Container sandboxing may restrict kiosk profile directories or X11 socket access."
                )
        except Exception:
            pass

    return PreflightResult(errors=errors, warnings=warnings)
