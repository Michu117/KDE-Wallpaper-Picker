#!/usr/bin/env bash
# Wallpaper Picker installer. Safe to run again.
#
# Optional environment variables:
#   WALLPAPER_DIR  wallpaper folder (default: ~/wallpapers; saved to config.toml)
#   SHORTCUT       global shortcut (default: Shift+Alt+W)
#   PYTHON_BIN     Python interpreter (default: python3)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$SCRIPT_DIR/wallpaper_picker.py"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SHORTCUT="${SHORTCUT:-Shift+Alt+W}"
WP_DIR="${WALLPAPER_DIR:-$HOME/wallpapers}"
CONFIG_DIR="$HOME/.config/wallpaper-picker"
CONFIG_FILE="$CONFIG_DIR/config.toml"
AUTOSTART_FILE="$HOME/.config/autostart/wallpaper-picker.desktop"
APPS_DIR="$HOME/.local/share/applications"
SHORTCUT_DESKTOP_NAME="wallpaper-picker.desktop"
SHORTCUT_FILE="$APPS_DIR/$SHORTCUT_DESKTOP_NAME"
VIDEO_PLUGIN_ID="luisbocanegra.smart.video.wallpaper.reborn"
VIDEO_PLUGIN_VERSION="2.16.0"
VIDEO_PLUGIN_URL="https://github.com/luisbocanegra/plasma-smart-video-wallpaper-reborn/releases/download/v${VIDEO_PLUGIN_VERSION}/plasma-smart-video-wallpaper-reborn-v${VIDEO_PLUGIN_VERSION}.tar.gz"
VIDEO_PLUGIN_SHA256="4f144fbe6b29b2aaad173ce9c989c411f59463e520daf9fd59b3211bfdc7b7c2"

ok()   { echo "[OK] $*"; }
warn() { echo "[WARN] $*"; }
die()  { echo "ERROR: $*" >&2; exit 1; }

# True if another kglobalshortcutsrc entry (not ours) is bound to $1.
# Values look like "Meta+E,Meta+E,Description" or "_launch=Ctrl+Alt+T\tMeta+K".
shortcut_in_use() {
    awk -v sc="$1" -v own="[services][$SHORTCUT_DESKTOP_NAME]" '
        /^\[/ { group = $0; next }
        group == own { next }
        /=/ {
            sub(/^[^=]*=/, "")
            split($0, fields, ",")
            n = split(fields[1], keys, "\\\\t")
            for (i = 1; i <= n; i++) if (keys[i] == sc) found = 1
        }
        END { exit !found }
    ' "${XDG_CONFIG_HOME:-$HOME/.config}/kglobalshortcutsrc" 2>/dev/null
}

require() {  # require <command> <install hint>
    command -v "$1" &>/dev/null || die "$1 not found. Install it with: $2"
    ok "$1 found"
}

echo "=== Wallpaper Picker Installer ==="

# --- Dependencies -----------------------------------------------------------
"$PYTHON_BIN" -c "from PyQt6.QtWidgets import QApplication" 2>/dev/null \
    || die "PyQt6 not found. Install it with: sudo dnf install python3-qt6"
ok "PyQt6 found"
"$PYTHON_BIN" -c "import tomllib" 2>/dev/null \
    || warn "Python 3.11+ is needed to read config.toml; defaults will be used"

require plasma-apply-wallpaperimage "sudo dnf install plasma-workspace"
require ffmpeg "sudo dnf install ffmpeg"
require gdbus "sudo dnf install glib2"
require kpackagetool6 "KDE Plasma 6 (plasma-workspace)"
require kwriteconfig6 "sudo dnf install kf6-kconfig"

# --- Live wallpaper plugin --------------------------------------------------
if kpackagetool6 --list --type Plasma/Wallpaper 2>/dev/null | grep -q "$VIDEO_PLUGIN_ID"; then
    ok "Smart Video Wallpaper Reborn found"
else
    require curl "sudo dnf install curl"
    echo "Installing Smart Video Wallpaper Reborn v$VIDEO_PLUGIN_VERSION..."
    PLUGIN_ARCHIVE="$(mktemp)"
    trap 'rm -f "$PLUGIN_ARCHIVE"' EXIT
    curl --fail --location --proto '=https' --tlsv1.2 --retry 3 \
        "$VIDEO_PLUGIN_URL" --output "$PLUGIN_ARCHIVE" \
        || die "could not download the live wallpaper plugin"
    printf '%s  %s\n' "$VIDEO_PLUGIN_SHA256" "$PLUGIN_ARCHIVE" | sha256sum --check --status \
        || die "live wallpaper plugin checksum verification failed"
    kpackagetool6 --type Plasma/Wallpaper --install "$PLUGIN_ARCHIVE" \
        || die "could not install the live wallpaper plugin"
    ok "Live wallpaper plugin installed"
fi

# --- Wallpaper folder + config ----------------------------------------------
mkdir -p "$WP_DIR"
ok "Wallpaper folder: $WP_DIR"

mkdir -p "$CONFIG_DIR"
if [ -n "${WALLPAPER_DIR:-}" ]; then
    if [ ! -f "$CONFIG_FILE" ]; then
        printf 'wallpaper_dir = "%s"\n' "$WP_DIR" > "$CONFIG_FILE"
        ok "Saved wallpaper_dir to $CONFIG_FILE"
    elif ! grep -q '^wallpaper_dir' "$CONFIG_FILE"; then
        printf 'wallpaper_dir = "%s"\n' "$WP_DIR" >> "$CONFIG_FILE"
        ok "Added wallpaper_dir to $CONFIG_FILE"
    else
        warn "$CONFIG_FILE already sets wallpaper_dir; left unchanged"
    fi
fi

# --- Autostart (daemon starts hidden at login) -----------------------------
mkdir -p "$(dirname "$AUTOSTART_FILE")"
cat > "$AUTOSTART_FILE" <<EOF
[Desktop Entry]
Name=Wallpaper Picker
Exec=$PYTHON_BIN "$SCRIPT"
Type=Application
NoDisplay=true
X-KDE-AutostartEnabled=true
EOF
ok "Autostart entry: $AUTOSTART_FILE"

# --- Global shortcut --------------------------------------------------------
# Plasma 6 stores command shortcuts as a .desktop file in applications/ plus a
# [services][<file>.desktop] _launch=<keys> entry in kglobalshortcutsrc.
existing="$(grep -lsF "$SCRIPT" "$APPS_DIR"/*.desktop 2>/dev/null \
    | xargs -r grep -lsx 'X-KDE-GlobalAccel-CommandShortcut=true' \
    | grep -vx "$SHORTCUT_FILE" | head -n1 || true)"

if [ -n "$existing" ]; then
    ok "A shortcut for this script already exists ($existing); not adding another"
else
    if shortcut_in_use "$SHORTCUT"; then
        warn "$SHORTCUT is already used by another shortcut; pick another with SHORTCUT=... bash install.sh"
    fi
    mkdir -p "$APPS_DIR"
    cat > "$SHORTCUT_FILE" <<EOF
[Desktop Entry]
Name=Wallpaper Picker
Exec=$PYTHON_BIN "$SCRIPT" --show
Type=Application
NoDisplay=true
StartupNotify=false
X-KDE-GlobalAccel-CommandShortcut=true
EOF
    kwriteconfig6 --file kglobalshortcutsrc --group services \
        --group "$SHORTCUT_DESKTOP_NAME" --key _launch "$SHORTCUT"
    ok "Shortcut $SHORTCUT registered (active after the next login, or change it in"
    echo "     System Settings → Keyboard → Shortcuts → Wallpaper Picker)"
fi

echo ""
echo "Installation complete. Start the daemon now with:"
echo "  $PYTHON_BIN \"$SCRIPT\" &"
echo "Uninstall with: bash \"$SCRIPT_DIR/uninstall.sh\""
