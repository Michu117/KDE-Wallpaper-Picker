#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$SCRIPT_DIR/wallpaper_picker.py"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DESKTOP_DIR="$HOME/.config/autostart"
DESKTOP_FILE="$DESKTOP_DIR/wallpaper-picker.desktop"
VIDEO_PLUGIN_ID="luisbocanegra.smart.video.wallpaper.reborn"
VIDEO_PLUGIN_VERSION="2.16.0"
VIDEO_PLUGIN_URL="https://github.com/luisbocanegra/plasma-smart-video-wallpaper-reborn/releases/download/v${VIDEO_PLUGIN_VERSION}/plasma-smart-video-wallpaper-reborn-v${VIDEO_PLUGIN_VERSION}.tar.gz"
VIDEO_PLUGIN_SHA256="4f144fbe6b29b2aaad173ce9c989c411f59463e520daf9fd59b3211bfdc7b7c2"

echo "=== Wallpaper Picker Installer ==="

# Check PyQt6
if ! "$PYTHON_BIN" -c "from PyQt6.QtWidgets import QApplication" 2>/dev/null; then
    echo "ERROR: PyQt6 nicht gefunden. Installieren mit: sudo dnf install python3-qt6"
    exit 1
fi
echo "[OK] PyQt6 gefunden"

# Check plasma-apply-wallpaperimage
if ! command -v plasma-apply-wallpaperimage &>/dev/null; then
    echo "ERROR: plasma-apply-wallpaperimage nicht gefunden. Installieren mit: sudo dnf install plasma-workspace"
    exit 1
fi
echo "[OK] plasma-apply-wallpaperimage gefunden"

if ! command -v ffmpeg &>/dev/null; then
    echo "ERROR: ffmpeg nicht gefunden. Installieren mit: sudo dnf install ffmpeg"
    exit 1
else
    echo "[OK] ffmpeg gefunden"
fi

if ! command -v gdbus &>/dev/null; then
    echo "ERROR: gdbus nicht gefunden. Installieren mit: sudo dnf install glib2"
    exit 1
else
    echo "[OK] gdbus gefunden"
fi

if ! command -v kpackagetool6 &>/dev/null; then
    echo "ERROR: kpackagetool6 nicht gefunden. Instala KDE Plasma 6 antes de continuar."
    exit 1
fi

if kpackagetool6 --list --type Plasma/Wallpaper 2>/dev/null | grep -q "$VIDEO_PLUGIN_ID"; then
    echo "[OK] Smart Video Wallpaper Reborn encontrado"
else
    echo "Instalando Smart Video Wallpaper Reborn v$VIDEO_PLUGIN_VERSION..."
    PLUGIN_ARCHIVE="$(mktemp)"
    trap 'rm -f "$PLUGIN_ARCHIVE"' EXIT
    if ! curl --fail --location --proto '=https' --tlsv1.2 --retry 3 \
        "$VIDEO_PLUGIN_URL" --output "$PLUGIN_ARCHIVE"; then
        echo "ERROR: no se pudo descargar el plugin de fondos Live."
        exit 1
    fi
    if ! printf '%s  %s\n' "$VIDEO_PLUGIN_SHA256" "$PLUGIN_ARCHIVE" | sha256sum --check --status; then
        echo "ERROR: la verificación del plugin falló."
        exit 1
    fi
    if ! kpackagetool6 --type Plasma/Wallpaper --install "$PLUGIN_ARCHIVE"; then
        echo "ERROR: no se pudo instalar el plugin de fondos Live."
        exit 1
    fi
    echo "[OK] Plugin de fondos Live instalado"
fi

# Check wallpaper directory
if [ ! -d "$HOME/wallpapers" ]; then
    echo "WARNUNG: $HOME/wallpapers existiert nicht. Ordner anlegen und Bilder reinkopieren."
fi

# Create autostart entry
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Name=Wallpaper Picker
Exec=$PYTHON_BIN "$SCRIPT"
Type=Application
X-KDE-AutostartEnabled=true
EOF
echo "[OK] Autostart-Eintrag erstellt: $DESKTOP_FILE"

echo ""
echo "=== Manueller Schritt: Tastenkürzel eintragen ==="
echo "1. Systemeinstellungen → Tastenkürzel → Eigene Tastenkürzel"
echo "2. Bearbeiten → Neu → Globales Tastenkürzel → Befehl/URL"
echo "3. Name: Wallpaper Picker"
echo "4. Auslöser: Shift+Alt+W"
echo "5. Aktion: $PYTHON_BIN \"$SCRIPT\""
echo ""
echo "Installation abgeschlossen. Jetzt starten mit:"
echo "  $PYTHON_BIN \"$SCRIPT\" &"
