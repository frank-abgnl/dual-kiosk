#!/usr/bin/env bash
# Installs (or with --remove, removes) the "XInput Kiosk Launcher" menu entry and desktop shortcut.
set -e
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_FILES=("xinput-kiosk-launcher.desktop" "xinput-multiseat.desktop")
MENU_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESK_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"

if [ "$1" = "--remove" ]; then
    for f in "${DESKTOP_FILES[@]}"; do
        rm -f "$MENU_DIR/$f" "$DESK_DIR/$f"
    done
    command -v update-desktop-database >/dev/null && update-desktop-database "$MENU_DIR" || true
    echo "[✓] Launcher shortcuts removed."
    exit 0
fi

chmod +x "$APP_DIR/launch.sh" "$APP_DIR/launcher.py" "$APP_DIR/start.sh"
mkdir -p "$MENU_DIR"

for f in "${DESKTOP_FILES[@]}"; do
    if [ -f "$APP_DIR/$f" ]; then
        sed "s|@APP_DIR@|$APP_DIR|g" "$APP_DIR/$f" > "$MENU_DIR/$f"
        chmod +x "$MENU_DIR/$f"
        if [ -d "$DESK_DIR" ]; then
            cp "$MENU_DIR/$f" "$DESK_DIR/$f"
            chmod +x "$DESK_DIR/$f"
            command -v gio >/dev/null && gio set "$DESK_DIR/$f" metadata::trusted true 2>/dev/null || true
        fi
    fi
done

command -v update-desktop-database >/dev/null && update-desktop-database "$MENU_DIR" || true
echo "[✓] Installed: application menu entry${DESK_DIR:+ and $DESK_DIR}"
echo "    Right-click the icon for: re-detect hardware / stop kiosks / show Control Board."
