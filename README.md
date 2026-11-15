# Wallpaper Picker

A lightweight wallpaper picker daemon for **KDE Plasma** (Wayland & X11), built with Python and PyQt6.

Press a shortcut — a frosted-glass CoverFlow window appears in the center of your screen. Browse your wallpapers with arrow keys or scroll wheel, apply with Enter or a click.

---

## Features

- **Frosted-glass background** — blurs your current wallpaper behind the window
- **CoverFlow carousel** with smooth 200ms animations
- **Category support** — every subfolder (nested ones too) is a category, switch with ↑/↓
- **Daemon mode** — starts once, opens/closes instantly via shortcut
- **Thumbnail cache** at `~/.cache/wallpaper-picker/thumbnails/` — instant display after first launch
- **Auto-reload** — new wallpapers are detected automatically, even while they are
  still being copied or downloaded (the folder is also re-checked every time the picker opens)
- **Live wallpapers** — previews and applies local live backgrounds through the KDE plugin
- **All monitors** are set simultaneously
- **State persistence** — remembers your last category and applied wallpaper
- **Hidden startup** — starts as a background daemon after login
- **Large collections** — use `PageUp` and `PageDown` to jump 24 items
- **Media filters** — press `A` for all, `S` for static, `D` for live, or `Z` for favorites
- **Search** — press `/` and type a filename fragment
- **Favorites** — press `F` to save or remove the selected item
- **Random wallpaper** — `R` in the picker applies a random one from the current view
  (press again to re-roll), or `wallpaper_picker.py --random` from a script
- **Automatic rotation** — optional, every N minutes (see Configuration)
- **Active wallpaper marked** — the picker opens on it and shows an `ACTIVE` badge
- **Built-in help** — press `?` or `F1`

---

## Requirements

- KDE Plasma (Wayland or X11)
- Python 3.11+ (3.10 works, but `config.toml` is ignored)
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
for the current user when needed, verifies its SHA-256 checksum, creates a
hidden **autostart** daemon entry and registers the global shortcut
**Shift+Alt+W**. The picker and the shortcut are active from the next KDE login.

Installer options (environment variables):

```bash
WALLPAPER_DIR="$HOME/Pictures/Wallpapers" SHORTCUT="Meta+W" bash install.sh
```

If a shortcut for `wallpaper_picker.py` already exists, the installer keeps it
and does not add a second one. It also warns when the chosen keys are already
taken by another shortcut.

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

## Uninstall

```bash
bash uninstall.sh           # stop daemon, remove autostart and shortcut
bash uninstall.sh --purge   # also delete thumbnails, favorites, state and config
```

Your wallpapers and the Live plugin are left untouched.

---

## Shortcut Setup

`install.sh` registers the shortcut for you. To change the keys, use
**System Settings → Keyboard → Shortcuts → Wallpaper Picker**.

To set it up by hand instead: **System Settings → Keyboard → Shortcuts →
Add New → Command**, with the command:

```bash
python3 "/PATH/TO/wallpaper_picker.py" --show
```

---

## Command line

| Command | Effect |
|---|---|
| `wallpaper_picker.py` | Toggle the picker if the daemon runs, otherwise start the daemon hidden |
| `wallpaper_picker.py --show` | Toggle the picker; start the daemon **and show it** if needed |
| `wallpaper_picker.py --random` | Apply a random wallpaper (through the daemon, or standalone) |

`--random` works well from a cron job or a systemd timer.

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
| `R` | Apply a random wallpaper from the current view (press again to re-roll) |
| `?` / `F1` | Show the key help |
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

Nested folders are categories too and use their relative path, for example
`nature/forest`. Folders whose names start with `.` are ignored. If no
subfolders exist, all images are shown in a single "All" category.

---

## Configuration

Optional settings live in `~/.config/wallpaper-picker/config.toml`. Every key
is optional; invalid values fall back to the default. Restart the daemon after
editing it.

```toml
wallpaper_dir  = "~/wallpapers"   # folder to browse
thumb_width    = 350              # center image size
thumb_height   = 220
spread         = 230              # spacing between images
window_width   = 1100
page_size      = 24               # PageUp / PageDown jump
blur_radius    = 30               # frosted glass blur strength
blur_scale     = 0.25             # blur resolution (lower = faster)
overlay_alpha  = 100              # dark overlay opacity (0–255)
rotate_minutes = 0                # automatic rotation, 0 = off
rotate_source  = "all"            # "all" or "favorites"
```

The `WALLPAPER_DIR` environment variable overrides `wallpaper_dir`:

```bash
WALLPAPER_DIR="$HOME/Imágenes/Fondos" python3 "/PATH/TO/wallpaper_picker.py"
```

The picker opens centered on the active screen (on X11, the screen under the
mouse).

---

## Supported Formats

`.jpg` `.jpeg` `.png` `.webp` `.bmp` `.mp4` `.mpg` `.mpeg` `.ogg` `.mov` `.webm`
`.flv` `.mkv` `.avi` `.wmv` `.gif`

---

## Project Structure

```
wallpaper-picker/
├── wallpaper_picker.py   # complete daemon + UI (single file)
├── install.sh            # installer: dependencies, autostart, shortcut
├── uninstall.sh          # removes autostart and shortcut (--purge: data too)
└── tests/                # python3 -m unittest discover -s tests
```

---
## Troubleshooting

**Opening the picker brings back the windows hidden by Show Desktop (Meta+D).**
On Wayland the picker uses a layer-shell "dock" surface so KWin keeps Show
Desktop active. This needs `layer-shell-qt` (installed with Plasma) and the
distribution's PyQt6 (`python3-qt6`). A PyQt6 installed with pip bundles its own
Qt; the picker then falls back to a normal window and prints a warning.

**Live wallpaper stays frozen after Show Desktop (Meta+D) until a window is
minimized.** Smart Video Wallpaper Reborn older than 2.15.0 keeps pausing while
windows are only hidden by Show Desktop. Run `bash install.sh` again (it upgrades
outdated versions) and restart Plasma:

```bash
systemctl --user restart plasma-plasmashell
```

As a workaround on old versions, set *Pause video* to *Never* in the wallpaper
settings.

## License

MIT
