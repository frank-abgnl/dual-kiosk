#!/usr/bin/env bash
# Installs (or with --remove, removes) the "XInput Kiosk Launcher" menu entry and desktop shortcut.
set -e
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="xinput-kiosk-launcher.desktop"
MENU_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESK_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"

if [ "$1" = "--remove" ]; then
    rm -f "$MENU_DIR/$NAME" "$DESK_DIR/$NAME"
    command -v update-desktop-database >/dev/null && update-desktop-database "$MENU_DIR" || true
    echo "[✓] Launcher shortcuts removed."
    exit 0
fi

chmod +x "$APP_DIR/launch.sh" "$APP_DIR/launcher.py"
mkdir -p "$MENU_DIR"
sed "s|@APP_DIR@|$APP_DIR|g" "$APP_DIR/$NAME" > "$MENU_DIR/$NAME"
chmod +x "$MENU_DIR/$NAME"
if [ -d "$DESK_DIR" ]; then
    cp "$MENU_DIR/$NAME" "$DESK_DIR/$NAME"
    chmod +x "$DESK_DIR/$NAME"
    command -v gio >/dev/null && gio set "$DESK_DIR/$NAME" metadata::trusted true 2>/dev/null || true
fi
command -v update-desktop-database >/dev/null && update-desktop-database "$MENU_DIR" || true
echo "[✓] Installed: application menu entry${DESK_DIR:+ and $DESK_DIR/$NAME}"
echo "    Right-click the icon for: re-detect hardware / stop kiosks / show Control Board."
