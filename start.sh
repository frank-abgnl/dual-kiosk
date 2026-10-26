#!/usr/bin/env bash
# ==============================================================================
# XInput Pseudo-Seating & Dual Firefox Kiosk Launcher
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH}"
export PATH="${HOME}/.local/bin:${PATH}"
export LD_LIBRARY_PATH="${HOME}/.local/lib:${LD_LIBRARY_PATH}"

# Dependency check
MISSING=()
command -v xinput >/dev/null 2>&1 || MISSING+=("xorg-xinput")
command -v xrandr >/dev/null 2>&1 || MISSING+=("xorg-xrandr")
command -v firefox >/dev/null 2>&1 || MISSING+=("firefox")
command -v wmctrl >/dev/null 2>&1 || MISSING+=("wmctrl")
command -v xdotool >/dev/null 2>&1 || MISSING+=("xdotool")

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "[!] Warning: The following dependencies are missing: ${MISSING[*]}"
    echo "    On Arch/CachyOS: sudo pacman -S --needed ${MISSING[*]}"
    echo "    On Debian/Ubuntu: sudo apt-get install -y ${MISSING[*]}"
    echo ""
fi

exec python3 "${SCRIPT_DIR}/main.py" "$@"
