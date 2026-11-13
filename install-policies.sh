#!/usr/bin/env bash
# ==============================================================================
# Install Enterprise Policies for Firefox Dual Kiosk
# Must be run with root / sudo permissions
# ==============================================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "[!] Error: install-policies.sh must be run with sudo or as root." >&2
    exit 1
fi

POLICIES_CONTENT='{
  "policies": {
    "DisableDeveloperTools": true,
    "BlockAboutConfig": true,
    "DisableAppUpdate": true,
    "DontCheckDefaultBrowser": true,
    "DisableFirefoxStudies": true,
    "DisableTelemetry": true,
    "NoDefaultBookmarks": true,
    "PromptForDownloadLocation": false
  }
}'

TARGET_DIRS=(
    "/etc/firefox/policies"
)

if [ -d "/usr/lib/firefox" ]; then
    TARGET_DIRS+=("/usr/lib/firefox/distribution")
fi
if [ -d "/usr/lib64/firefox" ]; then
    TARGET_DIRS+=("/usr/lib64/firefox/distribution")
fi

for dir in "${TARGET_DIRS[@]}"; do
    mkdir -p "$dir"
    echo "$POLICIES_CONTENT" > "$dir/policies.json"
    chmod 644 "$dir/policies.json"
    echo "[✓] Enterprise policies installed to $dir/policies.json"
done

echo "[✓] Firefox enterprise lockdown successfully installed."
