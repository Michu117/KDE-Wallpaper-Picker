# Wallpaper Picker

A lightweight wallpaper picker daemon for **KDE Plasma** (Wayland & X11), built with Python and PyQt6.

Press a shortcut — a frosted-glass CoverFlow window appears in the center of your screen. Browse your wallpapers with arrow keys or scroll wheel, apply with Enter or a click.

---

## Features

- **Frosted-glass background** — blurs your current wallpaper behind the window
- **CoverFlow carousel** with smooth 200ms animations
- **Category support** — organizes wallpapers by subfolder, switch with ↑/↓
- **Daemon mode** — starts once, opens/closes instantly via shortcut
- **Thumbnail cache** at `~/.cache/wallpaper-picker/thumbnails/` — instant display after first launch
- **Auto-reload** — new wallpapers in your folder are detected automatically
- **Live wallpapers** — previews and applies local live backgrounds through the KDE plugin
- **All monitors** are set simultaneously
- **State persistence** — remembers your last category and applied wallpaper
- **Hidden startup** — starts as a background daemon after login
- **Large collections** — use `PageUp` and `PageDown` to jump 24 items
- **Media filters** — press `A` for all, `S` for static, `D` for live, or `Z` for favorites
- **Search** — press `/` and type a filename fragment
- **Favorites** — press `F` to save or remove the selected item

---

## Requirements

- KDE Plasma (Wayland or X11)
- Python 3.10+
- PyQt6
- `ffmpeg` for video thumbnails
- **Smart Video Wallpaper Reborn** Plasma wallpaper plugin for video playback

```bash
sudo dnf install python3-qt6 plasma-workspace ffmpeg curl glib2
```

`plasma-apply-wallpaperimage` is included with KDE Plasma.
`install.sh` installs **Smart Video Wallpaper Reborn** automatically for the
current user when it is missing. It downloads the official release and checks
its SHA-256 before installing it.

---

## Easy installation

On Nobara with KDE Plasma, run:

```bash
sudo dnf install python3-qt6 plasma-workspace ffmpeg curl glib2
git clone https://github.com/Michu117/KDE-Wallpaper-Picker.git
cd KDE-Wallpaper-Picker
bash install.sh
mkdir -p ~/wallpapers
```

The installer checks the dependencies, installs **Smart Video Wallpaper Reborn**
for the current user when needed, verifies its SHA-256 checksum, and creates a
hidden **autostart** daemon entry. The picker starts automatically at the next
KDE login.

To start it immediately without logging out:

```bash
python3 "$PWD/wallpaper_picker.py" &
```

Place static and Live backgrounds in `~/wallpapers`. Subfolders become
categories automatically.

If the project is already cloned, only run:

```bash
cd KDE-Wallpaper-Picker
bash install.sh
```

The installer is safe to run again and does not reinstall the Live plugin when
it is already available.

## Installation details

The script checks dependencies, installs the Live wallpaper plugin when needed,
and sets up **autostart** (daemon launches automatically on KDE login).

---

## Shortcut Setup

One manual step required:

1. **System Settings** → **Shortcuts** → **Custom Shortcuts**
2. Edit → New → **Global Shortcut → Command/URL**
3. Settings:
   - **Name:** `Wallpaper Picker`
   - **Trigger:** `Shift+Alt+W` (or whatever you prefer)
   - **Action:** `python3 "/PATH/TO/wallpaper_picker.py"`

---

## Start Without Restarting

```bash
python3 "/PATH/TO/wallpaper_picker.py" &
```

The shortcut works immediately after.

---

## Usage

| Key / Action | Function |
|---|---|
| `←` / `→` | Previous / next wallpaper |
| `PageUp` / `PageDown` | Jump 24 wallpapers |
| `A` | Show all media |
| `S` | Show static only |
| `D` | Show live only |
| `Z` | Show favorites only |
| `/` | Start filename search |
| `Backspace` | Remove the last search character |
| `F` | Toggle favorite |
| Scroll wheel | Navigate |
| `↑` / `↓` | Previous / next category |
| `Enter` | Apply wallpaper + close |
| Click center image | Apply wallpaper + close |
| Click side image | Navigate to that image |
| `Escape` | Close without change |

---

## Folder Structure

Wallpapers are organized by subfolders inside your wallpaper directory:

```
~/wallpapers/
├── nature/
│   ├── forest.jpg
│   └── mountains.png
├── abstract/
│   └── waves.webp
└── loose_wallpaper.jpg      ← goes into "Unsorted"
```

If no subfolders exist, all images are shown in a single "All" category.

---

## Configuration

The default wallpaper directory is `~/wallpapers`. To use another directory, set
`WALLPAPER_DIR` before starting the picker:

```bash
WALLPAPER_DIR="$HOME/Imágenes/Fondos" python3 "/PATH/TO/wallpaper_picker.py"
```

Other display settings can be edited at the top of `wallpaper_picker.py`:

```python
BASE_W, BASE_H = 350, 220                            # center image size
SPREAD = 230                                         # spacing between images
BLUR_RADIUS = 30                                     # frosted glass blur strength
BLUR_OVERLAY_ALPHA = 100                             # dark overlay opacity (0–255)
```

---

## Supported Formats

`.jpg` `.jpeg` `.png` `.webp` `.bmp` `.mp4` `.mpg` `.mpeg` `.ogg` `.mov` `.webm`
`.flv` `.mkv` `.avi` `.wmv` `.gif`

---

## Project Structure

```
wallpaper-picker/
├── wallpaper_picker.py   # complete daemon + UI (single file)
└── install.sh            # installer + autostart setup
```

---
## Troubleshooting

¯\\_(ツ)\_/¯

## License

MIT
