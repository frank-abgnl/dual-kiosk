#!/usr/bin/env bash
# ==============================================================================
# XInput Pseudo-Seating — Uninstaller
# Stops everything, resets XInput devices to Virtual core, removes configs.
#
# Usage:
#   ./uninstall.sh                  Interactive uninstall
#   ./uninstall.sh --yes            No confirmation prompt
#   ./uninstall.sh --keep-config    Keep ~/.config/xinput-seats
#   ./uninstall.sh --dry-run        Show what would happen
#   ./uninstall.sh --remove-project Also delete this project folder (asks first)
# ==============================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOVE_PROJECT=0
PASS=()
ASSUME_YES=0
DRY=0

for a in "$@"; do
    case "$a" in
        --remove-project) REMOVE_PROJECT=1 ;;
        --yes|-y) ASSUME_YES=1; PASS+=("$a") ;;
        --dry-run) DRY=1; PASS+=("$a") ;;
        *) PASS+=("$a") ;;
    esac
done

main() {
    export PYTHONPATH="${SCRIPT_DIR}${PYTHONPATH:+:$PYTHONPATH}"
    python3 "${SCRIPT_DIR}/main.py" --uninstall ${PASS[@]+"${PASS[@]}"} || return $?

    if [ "$REMOVE_PROJECT" -eq 1 ] && [ "$DRY" -eq 0 ]; then
        if [ "$ASSUME_YES" -eq 1 ]; then
            ans="y"
        else
            read -r -p "Delete project folder ${SCRIPT_DIR}? [y/N] " ans
        fi
        if [[ "$ans" =~ ^[Yy]$ ]]; then
            cd "$HOME" && rm -rf "${SCRIPT_DIR}"
            echo "[✓] Removed ${SCRIPT_DIR}"
        fi
    fi
}

main
exit $?
