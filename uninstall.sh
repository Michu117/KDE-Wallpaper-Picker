#!/usr/bin/env bash
# Wallpaper Picker uninstaller.
#   bash uninstall.sh          remove daemon, autostart and shortcut
#   bash uninstall.sh --purge  also remove thumbnails, state and config
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$SCRIPT_DIR/wallpaper_picker.py"
AUTOSTART_FILE="$HOME/.config/autostart/wallpaper-picker.desktop"
SHORTCUT_DESKTOP_NAME="wallpaper-picker.desktop"
SHORTCUT_FILE="$HOME/.local/share/applications/$SHORTCUT_DESKTOP_NAME"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/wallpaper-picker"
CONFIG_DIR="$HOME/.config/wallpaper-picker"

PURGE=0
case "${1:-}" in
    --purge) PURGE=1 ;;
    "") ;;
    *) echo "Usage: bash uninstall.sh [--purge]" >&2; exit 2 ;;
esac

echo "=== Wallpaper Picker Uninstaller ==="

if pkill -f "$SCRIPT" 2>/dev/null; then
    echo "[OK] Daemon stopped"
fi

rm -f "$AUTOSTART_FILE" && echo "[OK] Autostart entry removed"

if [ -f "$SHORTCUT_FILE" ]; then
    rm -f "$SHORTCUT_FILE"
    if command -v kwriteconfig6 &>/dev/null; then
        kwriteconfig6 --file kglobalshortcutsrc --group services \
            --group "$SHORTCUT_DESKTOP_NAME" --key _launch --delete
    fi
    echo "[OK] Shortcut removed (fully released after the next login)"
fi

if [ "$PURGE" -eq 1 ]; then
    rm -rf "$CACHE_DIR" "$CONFIG_DIR"
    echo "[OK] Thumbnails, state, favorites and config removed"
else
    echo "Kept: $CACHE_DIR and $CONFIG_DIR (use --purge to delete them)"
fi

echo ""
echo "Your wallpapers and the Smart Video Wallpaper Reborn plugin were not touched."
echo "To remove the plugin: kpackagetool6 --type Plasma/Wallpaper --remove luisbocanegra.smart.video.wallpaper.reborn"
