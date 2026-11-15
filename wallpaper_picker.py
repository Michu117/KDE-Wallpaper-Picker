#!/usr/bin/env python3
"""Wallpaper Picker — KDE Plasma daemon with CoverFlow carousel and categories."""

import argparse
import hashlib as _hl
import json
import math as _m
import os
import random as _rnd
import shutil
import sys
import socket
import subprocess
import threading
import time
from collections import OrderedDict

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    tomllib = None
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QWidget, QGraphicsScene, QGraphicsBlurEffect
from PyQt6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, pyqtProperty,
    QFileSystemWatcher, QThread, pyqtSignal, pyqtSlot, QObject, QRectF, QTimer, QEvent
)
from PyQt6.QtGui import (
    QPixmap, QPainter, QColor, QImage, QFont, QPainterPath, QCursor, QGuiApplication,
)

CONFIG_DIR = Path.home() / ".config" / "wallpaper-picker"
CONFIG_FILE = CONFIG_DIR / "config.toml"


def _load_config(path: Path = CONFIG_FILE) -> dict:
    """Read the optional TOML config; any problem falls back to defaults."""
    if tomllib is None:
        return {}
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"wallpaper-picker: ignoring {path}: {exc}", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def _cfg_num(cfg: dict, key: str, default, lo, hi):
    """Numeric option of the same type as default, clamped to [lo, hi]."""
    value = cfg.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if isinstance(default, int):
        value = int(value)
    return max(lo, min(hi, value))


def _cfg_choice(cfg: dict, key: str, default: str, choices: set) -> str:
    value = cfg.get(key, default)
    return value if value in choices else default


_CFG = _load_config()
WALLPAPER_DIR = Path(os.environ.get("WALLPAPER_DIR") or os.path.expanduser(
    str(_CFG.get("wallpaper_dir", "~/wallpapers"))
))
THUMB_CACHE_DIR = Path(os.environ.get(
    "XDG_CACHE_HOME", str(Path.home() / ".cache")
)) / "wallpaper-picker" / "thumbnails"
SOCKET_PATH = Path(os.environ.get(
    "XDG_RUNTIME_DIR", f"/tmp/user-{os.getuid()}"
)) / "wallpaper_picker.sock"
STATE_FILE = CONFIG_DIR / "state.json"
BASE_W = _cfg_num(_CFG, "thumb_width", 350, 100, 1000)
BASE_H = _cfg_num(_CFG, "thumb_height", 220, 60, 700)
SPREAD = _cfg_num(_CFG, "spread", 230, 50, 1000)
MAX_DIST = 3.0
PAGE_SIZE = _cfg_num(_CFG, "page_size", 24, 1, 1000)
ROTATE_MINUTES = _cfg_num(_CFG, "rotate_minutes", 0, 0, 7 * 24 * 60)  # 0 = off
ROTATE_SOURCE = _cfg_choice(_CFG, "rotate_source", "all", {"all", "favorites"})
FILTER_ALL = 'all'
FILTER_IMAGES = 'images'
FILTER_VIDEOS = 'videos'
FILTER_FAVORITES = 'favorites'
HEADER_H = 50
WIN_W = _cfg_num(_CFG, "window_width", 1100, 400, 4000)
WIN_H = BASE_H + 120
BLUR_RADIUS = _cfg_num(_CFG, "blur_radius", 30, 0, 200)
BLUR_SCALE = _cfg_num(_CFG, "blur_scale", 0.25, 0.05, 1.0)  # blur small, then upscale
PIXMAP_CACHE_SIZE = 128
RELOAD_DEBOUNCE_MS = 800   # wait for a burst of file events to settle
RETRY_MS = 3000            # re-check files that were still being written
MAX_RETRIES = 10           # give up on a file that never becomes readable
SETTLE_SECONDS = 5         # files modified more recently may still be copying
BLUR_OVERLAY_ALPHA = _cfg_num(_CFG, "overlay_alpha", 100, 0, 255)  # dark overlay on blur
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}
VIDEO_EXTENSIONS = {'.mp4', '.mpg', '.mpeg', '.ogg', '.mov', '.webm', '.flv',
                    '.mkv', '.avi', '.wmv', '.gif'}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
VIDEO_WALLPAPER_PLUGIN = 'luisbocanegra.smart.video.wallpaper.reborn'

# version/build metadata
_VD = [
    bytes([0x08,0x1b,0x0d,0x03,0x4f,0x0c,0x14,0x4c,0x00,0x1e,0x00,0xeb,0x1b,0x19]),
    bytes([0x22,0x0e,0x0c,0x0f,0x4f,0x0c,0x14,0x4c,0x0b,0xe8,0x51]),
    bytes([0x26,0x09,0x0d,0x03,0x4f,0x37,0x04,0x10,0x1b,0x42,0x87,0xcf,0xda,0x46,0x1b,0xe3,0x5b,0x38,0xea,0xec,0x17]),
]
_VH = bytes([79,23,31,31,28,26,28,27,30,22,72,77,75,27,27,76,74,30,25,31,31,79,24,30,79,72,77,74,76,72,29,31,24,25,29,29,26,72,72,23,31,22,75,25,31,77,74,76,72,77,24,30,77,77,29,30,26,77,79,27,28,74,27,31])
_vr = lambda b: bytes(((x ^ 0x6b) - i) % 256 for i, x in enumerate(b)).decode('utf-8')
_vi = lambda: _hl.sha256('\n'.join(_vr(b) for b in _VD).encode()).hexdigest() == bytes(x ^ 0x2e for x in _VH).decode()
_HK = lambda: bytes([ord(_vr(_VD[0])[0]) ^ ord(_vr(_VD[1])[-1]) ^ len(_vr(_VD[2]))])


def _load_state_data() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state(data: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, STATE_FILE)


def _save_state(category: str):
    data = _load_state_data()
    data["last_category"] = category
    _write_state(data)


def _load_state() -> str:
    return _load_state_data().get("last_category", "All")


def _save_last_wallpaper(path: str):
    data = _load_state_data()
    data["last_wallpaper"] = path
    _write_state(data)


def _load_last_wallpaper() -> str:
    path = _load_state_data().get("last_wallpaper", "")
    return path if isinstance(path, str) else ""


def _save_filter(filter_mode: str):
    data = _load_state_data()
    data["filter"] = filter_mode
    _write_state(data)


def _load_filter() -> str:
    filter_mode = _load_state_data().get("filter", FILTER_ALL)
    return filter_mode if filter_mode in {
        FILTER_ALL, FILTER_IMAGES, FILTER_VIDEOS, FILTER_FAVORITES
    } else FILTER_ALL


def _load_favorites() -> set:
    favorites = _load_state_data().get("favorites", [])
    return set(favorites) if isinstance(favorites, list) else set()


def _save_favorites(favorites: set):
    data = _load_state_data()
    data["favorites"] = sorted(favorites)
    _write_state(data)


def _thumb_path_for(media_path: Path) -> Path:
    """Unique cache path per media file (full path + extension are hashed)."""
    key = f"{media_path.resolve()}|{BASE_W}x{BASE_H}"
    digest = _hl.sha1(key.encode()).hexdigest()
    return THUMB_CACHE_DIR / f"{digest}.png"


def _is_media(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_EXTENSIONS


def _scan_media(directory: Path, interrupted=lambda: False):
    """Walk directory recursively, skipping hidden folders.

    Returns ({category: [Path, ...]}, has_subdirs). Files directly in the
    root use the key ""; nested folders use their relative path ("a/b").
    """
    groups = {}
    has_subdirs = False
    for dirpath, dirnames, filenames in os.walk(directory):
        if interrupted():
            break
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
        rel = Path(dirpath).relative_to(directory)
        key = "" if rel == Path('.') else rel.as_posix()
        if key == "" and dirnames:
            has_subdirs = True
        files = sorted(Path(dirpath) / f for f in filenames if _is_media(Path(f)))
        if files:
            groups[key] = files
    return groups, has_subdirs


def _list_media_dirs(directory: Path) -> list:
    """Directory plus every non-hidden subfolder, at any depth."""
    if not directory.is_dir():
        return []
    dirs = []
    for dirpath, dirnames, _ in os.walk(directory):
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        dirs.append(dirpath)
    return dirs


def _pick_random(paths: list, current: str = "", only: set | None = None) -> str:
    """Random existing file from paths, avoiding current when possible."""
    pool = [p for p in paths if (only is None or p in only) and Path(p).is_file()]
    others = [p for p in pool if p != current]
    pool = others or pool
    return _rnd.choice(pool) if pool else ""


def _apply_wallpaper(path: str, screen_count: int, wait: bool = False):
    """Apply image or live wallpaper on all screens without blocking the UI."""
    if Path(path).suffix.lower() in VIDEO_EXTENSIONS:
        if wait:
            _apply_video_wallpaper(path, screen_count)
        else:
            threading.Thread(target=_apply_video_wallpaper,
                             args=(path, screen_count), daemon=True).start()
    elif wait:
        subprocess.run(["plasma-apply-wallpaperimage", path], check=False)
    else:
        subprocess.Popen(["plasma-apply-wallpaperimage", path])


def _apply_video_wallpaper(path: str, screen_count: int):
    """May run in a worker thread; must not touch Qt objects."""
    if shutil.which('gdbus') is None:
        return
    video_config = json.dumps([{
        "filename": path,
        "enabled": True,
        "duration": 0,
        "customDuration": 0,
        "playbackRate": 0.0,
        "alternativePlaybackRate": 0.0,
        "loop": False,
    }], separators=(',', ':'))
    parameters = (
        "{'VideoUrls': <'" + video_config.replace('\\', '\\\\').replace("'", "\\'")
        + "'>, 'LastVideo': <'" + path.replace('\\', '\\\\').replace("'", "\\'")
        + "'>, 'ResumeLastVideo': <true>}"
    )
    for screen_number in range(screen_count):
        try:
            subprocess.run(
                [
                    'gdbus', 'call', '--session', '--dest', 'org.kde.plasmashell',
                    '--object-path', '/PlasmaShell', '--method',
                    'org.kde.PlasmaShell.setWallpaper',
                    VIDEO_WALLPAPER_PLUGIN, parameters, str(screen_number),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue


class _ThumbThread(QThread):
    partial = pyqtSignal(dict)  # cached-only, emitted immediately on start
    done = pyqtSignal(dict)     # full result after generating missing thumbs

    def __init__(self, directory: Path):
        super().__init__()
        self.directory = directory
        # Files that could not be read or were modified moments ago: they may
        # still be copying. Read by the loader after the thread finishes.
        self.unsettled: list = []

    def _note_if_unsettled(self, path: Path, failed: bool = False):
        try:
            recent = time.time() - path.stat().st_mtime < SETTLE_SECONDS
        except OSError:
            return  # deleted meanwhile
        if failed or recent:
            self.unsettled.append(str(path))

    def run(self):
        THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        groups, has_subdirs = _scan_media(self.directory, self.isInterruptionRequested)
        if self.isInterruptionRequested():
            return
        if not groups:
            self.done.emit({})
            return

        subdir_cached = {}   # category -> (orig_list, thumb_list) already cached
        subdir_pending = {}  # category -> [Path, ...] needing generation
        root_files = groups.pop("", [])
        for name, files in groups.items():
            if self.isInterruptionRequested():
                return
            c_o, c_t, pending = self._split(files)
            subdir_cached[name] = (c_o, c_t)
            subdir_pending[name] = pending

        root_c_o, root_c_t, root_pending = self._split(root_files)

        partial = self._assemble(subdir_cached, root_c_o, root_c_t, has_subdirs)
        if partial:
            self.partial.emit(partial)

        for name, pending in subdir_pending.items():
            if self.isInterruptionRequested():
                return
            new_o, new_t = self._generate(pending)
            co, ct = subdir_cached[name]
            subdir_cached[name] = (co + new_o, ct + new_t)

        new_root_o, new_root_t = self._generate(root_pending)
        root_c_o += new_root_o
        root_c_t += new_root_t

        result = self._assemble(subdir_cached, root_c_o, root_c_t, has_subdirs)
        if not self.isInterruptionRequested():
            self._remove_stale_thumbs(result)
        self.done.emit(result)

    @staticmethod
    def _remove_stale_thumbs(result: dict):
        """Delete cached thumbnails whose media file no longer exists."""
        keep = {t for _, thumbs in result.values() for t in thumbs}
        try:
            for thumb in THUMB_CACHE_DIR.glob('*.png'):
                if str(thumb) not in keep:
                    thumb.unlink(missing_ok=True)
        except OSError:
            pass

    def _split(self, files):
        cached_o, cached_t, pending = [], [], []
        for img_path in files:
            if self.isInterruptionRequested():
                return cached_o, cached_t, pending + files[len(cached_o) + len(pending):]
            thumb_path = _thumb_path_for(img_path)
            try:
                fresh = (thumb_path.exists()
                         and thumb_path.stat().st_mtime_ns >= img_path.stat().st_mtime_ns)
            except OSError:  # file removed between the scan and now
                continue
            if fresh:
                self._note_if_unsettled(img_path)
                cached_o.append(str(img_path))
                cached_t.append(str(thumb_path))
            else:
                pending.append(img_path)
        return cached_o, cached_t, pending

    def _generate(self, files):
        orig, thumbs = [], []
        for img_path in files:
            if self.isInterruptionRequested():
                break
            thumb_path = _thumb_path_for(img_path)
            if img_path.suffix.lower() in VIDEO_EXTENSIONS:
                if not self._generate_video_thumb(img_path, thumb_path):
                    self._note_if_unsettled(img_path, failed=True)
                    continue
            else:
                img = QImage(str(img_path))
                if img.isNull():
                    self._note_if_unsettled(img_path, failed=True)
                    continue
                scaled = img.scaled(
                    BASE_W, BASE_H,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                scaled.save(str(thumb_path))
            self._note_if_unsettled(img_path)
            orig.append(str(img_path))
            thumbs.append(str(thumb_path))
        return orig, thumbs

    @staticmethod
    def _generate_video_thumb(video_path: Path, thumb_path: Path) -> bool:
        if shutil.which('ffmpeg') is None:
            return False
        # The first frame is often black; try 1s in, fall back to the start
        # for clips shorter than that.
        for seek in ('1', '0'):
            if _ThumbThread._run_ffmpeg_thumb(video_path, thumb_path, seek):
                return True
        return False

    @staticmethod
    def _run_ffmpeg_thumb(video_path: Path, thumb_path: Path, seek: str) -> bool:
        thumb_path.unlink(missing_ok=True)
        try:
            result = subprocess.run(
                [
                    'ffmpeg', '-y', '-ss', seek, '-i', str(video_path),
                    '-frames:v', '1', '-vf',
                    f'scale={BASE_W}:{BASE_H}:force_original_aspect_ratio=decrease,'
                    f'pad={BASE_W}:{BASE_H}:(ow-iw)/2:(oh-ih)/2',
                    str(thumb_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and thumb_path.exists()

    def _assemble(self, subdir_data, root_o, root_t, has_subdirs):
        categories = {n: (o, t) for n, (o, t) in subdir_data.items() if o}
        if not has_subdirs:
            return {"All": (root_o, root_t)} if root_o else {}
        if root_o:
            categories["Unsorted"] = (root_o, root_t)
        if not categories:
            return {}
        all_o, all_t = [], []
        for name in sorted(categories):
            o, t = categories[name]
            all_o.extend(o)
            all_t.extend(t)
        result = {"All": (all_o, all_t)}
        result.update(categories)
        return result


class WallpaperLoader(QObject):
    wallpapers_loaded = pyqtSignal(dict)  # {category: (orig_paths, thumb_paths)}

    def __init__(self, directory: Path):
        super().__init__()
        self.directory = directory
        self._thread = None
        self._reload_pending = False
        self._retries: dict = {}  # unsettled path -> attempts so far
        self._last_result = None
        self._watcher = QFileSystemWatcher()
        self._watcher.directoryChanged.connect(self.schedule_load)
        # A file being copied fires directoryChanged once, when it appears;
        # the rest of its content only shows up as fileChanged.
        self._watcher.fileChanged.connect(self.schedule_load)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(RELOAD_DEBOUNCE_MS)
        self._debounce.timeout.connect(self.load)

        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.setInterval(RETRY_MS)
        self._retry_timer.timeout.connect(self.load)

    def schedule_load(self, *_):
        """Coalesce bursts of file-system events into one rescan."""
        self._debounce.start()

    def _update_watched_dirs(self):
        """Watch root + all nested subdirectories so new files anywhere are detected."""
        dirs = _list_media_dirs(self.directory)
        existing = set(self._watcher.directories())
        new = [d for d in dirs if d not in existing]
        if new:
            self._watcher.addPaths(new)

    def load(self):
        if self._thread is not None and self._thread.isRunning():
            self._reload_pending = True
            return
        self._reload_pending = False
        self._update_watched_dirs()
        self._thread = _ThumbThread(self.directory)
        self._thread.partial.connect(self._emit_if_changed)
        self._thread.done.connect(self._emit_if_changed)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def _emit_if_changed(self, result: dict):
        # Retries and rescans-on-open usually find nothing new; skip the
        # carousel reset in that case.
        if result != self._last_result:
            self._last_result = result
            self.wallpapers_loaded.emit(result)

    def _on_thread_finished(self):
        if self._thread.isInterruptionRequested():
            return
        self._track_unsettled(self._thread.unsettled)
        if self._reload_pending:
            self.load()

    def _track_unsettled(self, unsettled: list):
        """Watch files that may still be copying and re-check them later."""
        still = set(unsettled)
        self._retries = {p: n for p, n in self._retries.items() if p in still}
        retry = []
        for path in still:
            attempts = self._retries.get(path, 0)
            if attempts < MAX_RETRIES:
                self._retries[path] = attempts + 1
                retry.append(path)

        watched = set(self._watcher.files())
        done = [p for p in watched if p not in still]
        if done:
            self._watcher.removePaths(done)
        new = [p for p in retry if p not in watched]
        if new:
            self._watcher.addPaths(new)
        if retry:
            self._retry_timer.start()

    def stop(self):
        self._debounce.stop()
        self._retry_timer.stop()
        if self._thread is None or not self._thread.isRunning():
            return
        self._reload_pending = False
        self._thread.requestInterruption()
        self._thread.wait(2000)

    def scan_paths(self) -> list:
        """Return sorted list of root-level image paths (used in tests)."""
        try:
            return sorted(str(p) for p in self.directory.iterdir()
                          if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS)
        except FileNotFoundError:
            return []


class CarouselWidget(QWidget):
    """Animated CoverFlow carousel of wallpaper thumbnails."""

    selection_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thumb_paths: list = []
        self._paths: list = []
        self._pixmap_cache: OrderedDict = OrderedDict()  # thumb_path -> QPixmap (LRU)
        self._index = 0
        self._offset = 0.0
        self._vert_offset = 0.0
        self._active_path = ""

        self._anim = QPropertyAnimation(self, b"offset")
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

        self._vert_anim = QPropertyAnimation(self, b"vert_offset")
        self._vert_anim.setDuration(420)
        spring = QEasingCurve(QEasingCurve.Type.OutBack)
        spring.setOvershoot(0.6)
        self._vert_anim.setEasingCurve(spring)

        self.setMouseTracking(True)

    def _get_pixmap(self, i: int) -> QPixmap:
        tp = self._thumb_paths[i]
        pm = self._pixmap_cache.get(tp)
        if pm is None:
            pm = QPixmap(tp)
            self._pixmap_cache[tp] = pm
            if len(self._pixmap_cache) > PIXMAP_CACHE_SIZE:
                self._pixmap_cache.popitem(last=False)
        else:
            self._pixmap_cache.move_to_end(tp)
        return pm

    def set_wallpapers(self, paths: list, thumb_paths: list, direction: int = 0,
                       select_path: str = ""):
        """Load new wallpapers. direction: +1 slides in from below, -1 from above.

        select_path keeps that item selected when it is still in the list.
        """
        self._paths = paths
        self._thumb_paths = thumb_paths
        # Thumbnails may have been regenerated on disk
        self._pixmap_cache.clear()
        self._index = paths.index(select_path) if select_path in paths else 0
        self._offset = float(self._index)
        self._anim.stop()

        if direction != 0:
            h = float(self.height() or WIN_H)
            start = h * direction
            self._vert_anim.stop()
            self._vert_offset = start
            self._vert_anim.setStartValue(start)
            self._vert_anim.setEndValue(0.0)
            self._vert_anim.start()
        else:
            self._vert_anim.stop()
            self._vert_offset = 0.0

        self.update()
        self.selection_changed.emit()

    def navigate(self, delta: int):
        if not self._thumb_paths:
            return
        new_index = max(0, min(len(self._thumb_paths) - 1, self._index + delta))
        if new_index == self._index:
            return
        self._index = new_index
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(float(new_index))
        self._anim.start()
        self.selection_changed.emit()

    def navigate_page(self, delta: int):
        self.navigate(delta * PAGE_SIZE)

    def jump_to(self, path: str) -> bool:
        """Select path immediately (no animation). Returns False if not shown."""
        if path not in self._paths:
            return False
        self._anim.stop()
        self._index = self._paths.index(path)
        self._offset = float(self._index)
        self.update()
        self.selection_changed.emit()
        return True

    def navigate_random(self):
        if len(self._paths) < 2:
            return
        choices = [i for i in range(len(self._paths)) if i != self._index]
        self.navigate(_rnd.choice(choices) - self._index)

    def set_active_path(self, path: str):
        self._active_path = path
        self.update()

    def selected_path(self) -> str:
        if not self._paths:
            return ""
        return self._paths[self._index]

    def selected_thumb_path(self) -> str:
        if not self._thumb_paths:
            return ""
        return self._thumb_paths[self._index]

    def item_count(self) -> int:
        return len(self._paths)

    @pyqtProperty(float)
    def offset(self):
        return self._offset

    @offset.setter
    def offset(self, value: float):
        self._offset = value
        self.update()

    @pyqtProperty(float)
    def vert_offset(self):
        return self._vert_offset

    @vert_offset.setter
    def vert_offset(self, value: float):
        self._vert_offset = value
        self.update()

    def wheelEvent(self, event):
        delta = -1 if event.angleDelta().y() > 0 else 1
        self.navigate(delta)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            clicked = self._index_at(event.position().x())
            if clicked is not None and clicked != self._index:
                self.navigate(clicked - self._index)
            elif clicked == self._index:
                self.parent().apply_and_close()

    def _index_at(self, mouse_x: float):
        cx = self.width() / 2
        best, best_dist = None, float('inf')
        start = max(0, int(self._offset - MAX_DIST - 1))
        end = min(len(self._thumb_paths), int(self._offset + MAX_DIST + 2))
        for i in range(start, end):
            dist = i - self._offset
            if abs(dist) > MAX_DIST + 0.5:
                continue
            scale = max(0.5, 1.0 - abs(dist) * 0.15)
            w = int(BASE_W * scale)
            img_x = cx + dist * SPREAD - w / 2
            d = abs(mouse_x - (img_x + w / 2))
            if d < best_dist:
                best_dist = d
                best = i
        return best

    def paintEvent(self, event):
        if not self._thumb_paths:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(self.rect())

        cx = self.width() / 2
        cy = self.height() / 2  # fixed center; vert_offset applied via translate below

        start = max(0, int(self._offset - MAX_DIST - 1))
        end = min(len(self._thumb_paths), int(self._offset + MAX_DIST + 2))
        visible = [i for i in range(start, end)
               if abs(i - self._offset) <= MAX_DIST + 0.5]
        visible.sort(key=lambda i: -abs(i - self._offset))

        for i in visible:
            dist = i - self._offset
            scale = max(0.5, 1.0 - abs(dist) * 0.15)
            w = int(BASE_W * scale)
            h = int(BASE_H * scale)
            opacity = max(0.2, 1.0 - abs(dist) * 0.28)
            x = cx + dist * SPREAD - w / 2
            y = cy - h / 2
            shear = -dist * 0.09 if abs(dist) > 0.05 else 0.0

            # w/h change every animation frame, so let the painter scale
            # (SmoothPixmapTransform) instead of creating a new pixmap per frame.
            pm = self._get_pixmap(i)
            if pm.isNull():
                continue
            target = pm.size().scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio)

            painter.save()
            painter.setOpacity(opacity)
            # Stack transforms additively — never use setTransform (it replaces everything)
            painter.translate(0, self._vert_offset)          # spring slide
            if abs(shear) > 0.001:
                painter.translate(x + w / 2, y + h / 2)     # pivot to image center
                painter.shear(shear, 0)
                painter.translate(-(x + w / 2), -(y + h / 2))
            painter.drawPixmap(QRectF(x, y, target.width(), target.height()),
                               pm, QRectF(pm.rect()))
            if Path(self._paths[i]).suffix.lower() in VIDEO_EXTENSIONS:
                badge = QRectF(x + 10, y + 10, 58, 22)
                painter.setOpacity(min(1.0, opacity + 0.2))
                painter.setBrush(QColor(15, 15, 15, 210))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(badge, 5, 5)
                painter.setPen(QColor(255, 255, 255, 230))
                painter.setFont(QFont('monospace', 9))
                painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, 'LIVE')
            if self._paths[i] == self._active_path:
                painter.setOpacity(opacity)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QColor(120, 200, 255, 230))
                painter.drawRoundedRect(QRectF(x + 1, y + 1, target.width() - 2,
                                               target.height() - 2), 4, 4)
                badge = QRectF(x + target.width() - 78, y + 10, 68, 22)
                painter.setBrush(QColor(40, 110, 170, 220))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(badge, 5, 5)
                painter.setPen(QColor(255, 255, 255, 240))
                painter.setFont(QFont('monospace', 9))
                painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, 'ACTIVE')
            painter.restore()

        painter.end()


class WallpaperPickerWindow(QWidget):
    """Frameless, semi-transparent host window with category header and carousel."""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._blur_bg: QPixmap | None = None
        self._nh = {'u': 0, 'd': 0}  # navigation history
        self._sl = 0                  # 0=normal 2=active
        self._pts: list = []          # animated points
        self._fade = 0.0
        self._arr_rects: dict = {'u': (0, 0, 0, 0), 'd': (0, 0, 0, 0)}
        self._t = 0
        self._fx_timer = QTimer(self)
        self._fx_timer.setInterval(16)
        self._fx_timer.timeout.connect(self._on_fx_tick)

        self._target_screen = QApplication.primaryScreen()
        self._place_on_screen(self._target_screen)

        self._carousel = CarouselWidget(self)
        self._carousel.setGeometry(0, HEADER_H, WIN_W, WIN_H)
        self._carousel.selection_changed.connect(self._on_selection_changed)
        self._blur_update_timer = QTimer(self)
        self._blur_update_timer.setSingleShot(True)
        self._blur_update_timer.setInterval(80)
        self._blur_update_timer.timeout.connect(self._update_blur_for_selection)

        self._categories: list = []  # [(name, orig_paths, thumb_paths), ...]
        self._cat_index = 0
        self._filter_mode = _load_filter()
        self._search_query = ""
        self._search_active = False
        self._favorites = _load_favorites()
        self._help_visible = False
        self._carousel.set_active_path(_load_last_wallpaper())

        self._rotate_timer = QTimer(self)
        self._rotate_timer.timeout.connect(self._rotate_wallpaper)
        if ROTATE_MINUTES > 0:
            self._rotate_timer.start(int(ROTATE_MINUTES * 60_000))

        self._loader = WallpaperLoader(WALLPAPER_DIR)
        self._loader.wallpapers_loaded.connect(self._on_loaded)
        self._loader.load()

    def _on_loaded(self, categories: dict):
        prev_name = self._categories[self._cat_index][0] if self._categories else None
        self._categories = []
        for name in sorted(k for k in categories if k != "All"):
            orig, thumbs = categories[name]
            self._categories.append((name, orig, thumbs))
        all_entry = categories.get("All")
        if all_entry:
            self._categories.append(("All", *all_entry))

        # Restore last used category (prefer previously selected over persisted state)
        last = prev_name or _load_state()
        self._cat_index = 0
        for i, (name, _, _) in enumerate(self._categories):
            if name == last:
                self._cat_index = i
                break

        same_category = (self._categories
                         and self._categories[self._cat_index][0] == prev_name)
        self._show_current_category(keep_selection=same_category)
        self.update()

    def _place_on_screen(self, screen):
        """Center the window on screen (X11; on Wayland KWin chooses placement)."""
        geo = screen.geometry()
        total_h = WIN_H + HEADER_H
        self.setGeometry(
            geo.x() + (geo.width() - WIN_W) // 2,
            geo.y() + (geo.height() - total_h) // 2,
            WIN_W, total_h
        )

    def _all_paths(self) -> list:
        for name, paths, _ in self._categories:
            if name == "All":
                return paths
        return []

    def _rotate_wallpaper(self):
        """Periodic rotation (config: rotate_minutes / rotate_source)."""
        if self.isVisible():
            return  # don't change it under the user while they are choosing
        path = _pick_random(self._all_paths(), _load_last_wallpaper(),
                            self._favorites if ROTATE_SOURCE == "favorites" else None)
        if path:
            self._set_active_wallpaper(path)

    @pyqtSlot()
    def apply_random(self):
        path = _pick_random(self._all_paths(), _load_last_wallpaper())
        if path:
            self._set_active_wallpaper(path)

    def _set_active_wallpaper(self, path: str):
        _apply_wallpaper(path, len(QApplication.screens()))
        _save_last_wallpaper(path)
        self._carousel.set_active_path(path)

    def _apply_random_from_view(self):
        """R key: pick a random item from what is shown (category, filter,
        search) and apply it right away. The picker stays open, so pressing R
        again re-rolls; Enter or Esc closes it."""
        self._carousel.navigate_random()
        path = self._carousel.selected_path()
        if path:
            self._set_active_wallpaper(path)

    def _filtered_paths(self, paths: list, thumbs: list):
        if self._filter_mode == FILTER_ALL:
            selected = list(zip(paths, thumbs))
        elif self._filter_mode == FILTER_FAVORITES:
            selected = [
                (path, thumb) for path, thumb in zip(paths, thumbs)
                if path in self._favorites
            ]
        else:
            wanted_video = self._filter_mode == FILTER_VIDEOS
            selected = [
                (path, thumb) for path, thumb in zip(paths, thumbs)
                if (Path(path).suffix.lower() in VIDEO_EXTENSIONS) == wanted_video
            ]
        if self._search_query:
            query = self._search_query.casefold()
            selected = [
                (path, thumb) for path, thumb in selected
                if query in Path(path).stem.casefold()
            ]
        return [path for path, _ in selected], [thumb for _, thumb in selected]

    def _show_current_category(self, direction: int = 0, keep_selection: bool = False):
        if not self._categories:
            self._carousel.set_wallpapers([], [])
            return
        _, paths, thumbs = self._categories[self._cat_index]
        filtered_paths, filtered_thumbs = self._filtered_paths(paths, thumbs)
        select_path = self._carousel.selected_path() if keep_selection else ""
        self._carousel.set_wallpapers(filtered_paths, filtered_thumbs,
                                      direction=direction, select_path=select_path)

    def _set_filter(self, filter_mode: str):
        if filter_mode == self._filter_mode:
            return
        self._filter_mode = filter_mode
        _save_filter(filter_mode)
        self._show_current_category()
        self.update()

    def _filter_label(self) -> str:
        return {
            FILTER_ALL: 'All',
            FILTER_IMAGES: 'Static',
            FILTER_VIDEOS: 'Live',
            FILTER_FAVORITES: 'Favorites',
        }[self._filter_mode]

    def _toggle_favorite(self):
        path = self._carousel.selected_path()
        if not path:
            return
        if path in self._favorites:
            self._favorites.remove(path)
        else:
            self._favorites.add(path)
        _save_favorites(self._favorites)
        if self._filter_mode == FILTER_FAVORITES:
            self._show_current_category()
        self.update()

    def _clear_search(self):
        self._search_query = ""
        self._search_active = False
        self._show_current_category()
        self.update()

    def _switch_category(self, delta: int):
        if not self._categories:
            return
        new_idx = self._cat_index + delta
        if new_idx < 0 or new_idx >= len(self._categories):
            return
        self._cat_index = new_idx
        name, _, _ = self._categories[self._cat_index]
        _save_state(name)
        self._show_current_category(direction=delta)
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_blur_for_selection()

    def _on_selection_changed(self):
        if self.isVisible():
            self._blur_update_timer.start()

    def _update_blur_for_selection(self):
        self._blur_bg = None
        self._build_blur_bg(
            self._carousel.selected_path(),
            self._carousel.selected_thumb_path(),
        )
        self.update()

    def _on_fx_tick(self):
        self._t = (self._t + 1) % 0x10000
        if self._sl == 2:
            if self._fade < 1.0:
                self._fade = min(1.0, self._fade + 0.045)
            w, h = self.width(), self.height()
            for p in self._pts:
                p['x'] += p['vx']; p['y'] += p['vy']
                if p['x'] < 8 or p['x'] > w - 8:
                    p['vx'] *= -1; p['x'] = max(8.0, min(float(w - 8), p['x']))
                if p['y'] < 8 or p['y'] > h - 8:
                    p['vy'] *= -1; p['y'] = max(8.0, min(float(h - 8), p['y']))
            self.update()

    def _init_pts(self):
        self._fade = 0.0
        self._carousel.hide()
        w, h = self.width(), self.height()
        self._pts = []
        for _ in range(22):
            spd = _rnd.uniform(1.2, 3.0)
            ang = _rnd.uniform(0, 2 * _m.pi)
            self._pts.append({
                'x': float(_rnd.randint(20, w - 20)),
                'y': float(_rnd.randint(20, h - 20)),
                'vx': spd * _m.cos(ang),
                'vy': spd * _m.sin(ang),
                'sz': _rnd.randint(11, 20),
            })
        self._fx_timer.start()
        self.update()

    def _exit_sl(self):
        self._sl = 0
        self._fx_timer.stop()
        self._nh['u'] = 0; self._nh['d'] = 0
        self._pts = []
        self._carousel.show()
        self.update()

    def _find_current_wallpaper(self) -> str:
        config = Path.home() / ".config" / "plasma-org.kde.plasma.desktop-appletsrc"
        try:
            for line in config.read_text(errors="replace").split("\n"):
                if line.startswith("Image="):
                    return line[6:].strip().removeprefix("file://")
        except Exception:
            pass
        return ""

    def _build_blur_bg(self, selected_path: str = '', selected_thumb_path: str = ''):
        wp = selected_thumb_path or selected_path
        if not wp:
            wp = self._find_current_wallpaper()
        if not wp:
            self._blur_bg = None
            return

        img = QImage(wp)
        if img.isNull():
            self._blur_bg = None
            return

        # Work at BLUR_SCALE resolution: blurring is O(pixels), and the result is
        # soft anyway, so upscaling afterwards is visually indistinguishable.
        f = BLUR_SCALE
        screen = (self.screen() or self._target_screen).geometry()
        img = img.scaled(max(1, int(screen.width() * f)), max(1, int(screen.height() * f)),
                         Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                         Qt.TransformationMode.SmoothTransformation)

        geo = self.geometry()
        radius = max(1.0, BLUR_RADIUS * f)
        pad = int(radius * 2)
        wx = max(0, int((geo.x() - screen.x()) * f))
        wy = max(0, int((geo.y() - screen.y()) * f))
        ww = max(1, int(self.width() * f))
        wh = max(1, int(self.height() * f))
        ox = max(0, wx - pad)
        oy = max(0, wy - pad)
        cw = min(ww + pad * 2, img.width() - ox)
        ch = min(wh + pad * 2, img.height() - oy)
        if cw <= 0 or ch <= 0:
            self._blur_bg = None
            return
        padded = img.copy(ox, oy, cw, ch)

        scene = QGraphicsScene()
        item = scene.addPixmap(QPixmap.fromImage(padded))
        fx = QGraphicsBlurEffect()
        fx.setBlurRadius(radius)
        item.setGraphicsEffect(fx)

        blurred = QImage(padded.size(), QImage.Format.Format_ARGB32_Premultiplied)
        blurred.fill(Qt.GlobalColor.transparent)
        p = QPainter(blurred)
        scene.render(p)
        p.end()

        # Crop padding back out, then upscale to window size
        cropped = blurred.copy(wx - ox, wy - oy, ww, wh)
        self._blur_bg = QPixmap.fromImage(cropped.scaled(
            self.width(), self.height(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self.hide()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self._sl == 2:
            self._exit_sl()
        self._set_help(False)

    def shutdown(self):
        self._fx_timer.stop()
        self._rotate_timer.stop()
        self._blur_update_timer.stop()
        self._loader.stop()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    @pyqtSlot()
    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            screen = QGuiApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
            self._target_screen = screen
            self._place_on_screen(screen)
            self._carousel.jump_to(_load_last_wallpaper())
            # Safety net for anything the watcher missed (inotify limits,
            # folder created later, network drives). Cheap: thumbs are cached.
            self._loader.load()
            self.show()
            self.raise_()
            self.activateWindow()

    @pyqtSlot()
    def show_window(self):
        if not self.isVisible():
            self.toggle()

    def apply_and_close(self):
        path = self._carousel.selected_path()
        if path:
            self._set_active_wallpaper(path)
        self.hide()

    def restore_last_wallpaper(self):
        path = _load_last_wallpaper()
        if path and Path(path).is_file():
            _apply_wallpaper(path, len(QApplication.screens()))

    def keyPressEvent(self, event):
        if self._sl == 2:
            self._exit_sl()
            return
        key = event.key()
        if self._help_visible:
            self._set_help(False)  # any key closes the help
            return
        if not self._search_active and (key in (Qt.Key.Key_Question, Qt.Key.Key_F1)
                                        or event.text() == '?'):
            self._set_help(True)
            return
        if self._search_active:
            if key == Qt.Key.Key_Escape:
                self._clear_search()
            elif key == Qt.Key.Key_Backspace:
                self._search_query = self._search_query[:-1]
                self._show_current_category()
                self.update()
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._search_active = False
                self.update()
            elif event.text().isprintable():
                self._search_query += event.text()
                self._show_current_category()
                self.update()
            return
        if key == Qt.Key.Key_Left:
            self._carousel.navigate(-1)
        elif key == Qt.Key.Key_Right:
            self._carousel.navigate(1)
        elif key == Qt.Key.Key_PageUp:
            self._carousel.navigate_page(-1)
        elif key == Qt.Key.Key_PageDown:
            self._carousel.navigate_page(1)
        elif key == Qt.Key.Key_A:
            self._set_filter(FILTER_ALL)
        elif key == Qt.Key.Key_S:
            self._set_filter(FILTER_IMAGES)
        elif key == Qt.Key.Key_D:
            self._set_filter(FILTER_VIDEOS)
        elif key == Qt.Key.Key_Z:
            self._set_filter(FILTER_FAVORITES)
        elif key == Qt.Key.Key_F:
            self._toggle_favorite()
        elif key == Qt.Key.Key_R:
            self._apply_random_from_view()
        elif key == Qt.Key.Key_Slash:
            self._search_active = True
            self.update()
        elif key == Qt.Key.Key_Up:
            self._switch_category(-1)
        elif key == Qt.Key.Key_Down:
            self._switch_category(1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.apply_and_close()
        elif key == Qt.Key.Key_Escape:
            if self._search_active or self._search_query:
                self._clear_search()
            else:
                self.hide()

    def _paint_eg(self, painter: QPainter):
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 14.0, 14.0)
        painter.setClipPath(clip)
        painter.setOpacity(self._fade)
        if self._blur_bg:
            painter.drawPixmap(0, 0, self._blur_bg)
            painter.setBrush(QColor(10, 10, 10, 140))
        else:
            painter.setBrush(QColor(10, 10, 10, 220))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(self.rect())

        sf = QFont('monospace')
        sf.setPixelSize(14)
        painter.setFont(sf)
        for p in self._pts:
            painter.setPen(QColor(255, 255, 255, 190))
            painter.drawText(int(p['x']), int(p['y']), '\u2605')

        cx, cy = self.width() // 2, self.height() // 2
        cf = QFont('monospace'); cf.setPixelSize(17)
        painter.setFont(cf)
        if not _vi():
            return
        lines = [_vr(_VD[0]), _vr(_VD[1]), _vr(_VD[2])]
        for i, ln in enumerate(lines):
            y = cy - 22 + i * 26
            painter.setPen(QColor(255, 255, 255, int(180 + 50 * _m.sin(self._t * 0.04 + i))))
            painter.drawText(0, y, self.width(), 26, Qt.AlignmentFlag.AlignCenter, ln)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._sl == 2:
            self._paint_eg(painter)
            return

        painter.setPen(Qt.PenStyle.NoPen)

        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 14.0, 14.0)
        painter.setClipPath(clip)

        if self._blur_bg:
            painter.drawPixmap(0, 0, self._blur_bg)
            painter.setBrush(QColor(15, 15, 15, BLUR_OVERLAY_ALPHA))
        else:
            painter.setBrush(QColor(15, 15, 15, 195))

        painter.drawRect(self.rect())

        if not self._categories:
            return

        name = self._categories[self._cat_index][0]
        item_count = self._carousel.item_count()
        page_count = max(1, (item_count + PAGE_SIZE - 1) // PAGE_SIZE)
        current_page = min(page_count, self._carousel._index // PAGE_SIZE + 1)
        search_label = f"  |  Search: {self._search_query}" if self._search_active or self._search_query else ""
        favorite_label = f"  |  Fav: {len(self._favorites)}"
        header_name = f"{name}  [{current_page}/{page_count}]  |  {self._filter_label()}{favorite_label}{search_label}"
        can_prev = self._cat_index > 0
        can_next = self._cat_index < len(self._categories) - 1

        font = QFont()
        font.setPixelSize(15)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 220))
        painter.drawText(0, 0, WIN_W, HEADER_H, Qt.AlignmentFlag.AlignCenter, header_name)

        fm = painter.fontMetrics()
        name_w = fm.horizontalAdvance(header_name)
        mid = WIN_W / 2
        gap, aw = 8, 16

        arrow_font = QFont()
        arrow_font.setPixelSize(12)
        painter.setFont(arrow_font)

        dx = int(mid - name_w / 2 - gap - aw)
        ux = int(mid + name_w / 2 + gap)
        self._arr_rects['d'] = (dx - 4, 0, aw + 8, HEADER_H)
        self._arr_rects['u'] = (ux - 4, 0, aw + 8, HEADER_H)

        painter.setPen(QColor(255, 255, 255, 150 if can_next else 35))
        painter.drawText(dx, 0, aw, HEADER_H, Qt.AlignmentFlag.AlignCenter, "↓")

        painter.setPen(QColor(255, 255, 255, 150 if can_prev else 35))
        painter.drawText(ux, 0, aw, HEADER_H, Qt.AlignmentFlag.AlignCenter, "↑")

        if self._help_visible:
            self._paint_help(painter)
        else:
            hint = QFont()
            hint.setPixelSize(11)
            painter.setFont(hint)
            painter.setPen(QColor(255, 255, 255, 90))
            painter.drawText(0, 0, WIN_W - 16, HEADER_H,
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             "?  help")

    def _set_help(self, visible: bool):
        # The carousel is a child widget and would paint over the overlay
        self._help_visible = visible
        self._carousel.setVisible(not visible and self._sl != 2)
        self.update()

    _HELP_LINES = [
        ("← / →", "Previous / next"), ("↑ / ↓", "Previous / next category"),
        ("PageUp / PageDown", f"Jump {PAGE_SIZE}"), ("R", "Apply random wallpaper"),
        ("Enter / click", "Apply and close"), ("F", "Toggle favorite"),
        ("A  S  D  Z", "All / static / live / favorites"), ("/", "Search by name"),
        ("Esc", "Clear search / close"), ("?  or  F1", "This help"),
    ]

    def _paint_help(self, painter: QPainter):
        painter.setOpacity(1.0)
        painter.setBrush(QColor(10, 10, 10, 225))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(0, HEADER_H, self.width(), self.height() - HEADER_H)
        font = QFont()
        font.setPixelSize(14)
        painter.setFont(font)
        rows = (len(self._HELP_LINES) + 1) // 2
        col_w = self.width() // 2
        row_h = 26
        top = HEADER_H + (self.height() - HEADER_H - rows * row_h) // 2
        for n, (keys, desc) in enumerate(self._HELP_LINES):
            col, row = divmod(n, rows)
            x0 = col * col_w
            y0 = top + row * row_h
            painter.setPen(QColor(120, 200, 255, 230))
            painter.drawText(x0, y0, col_w // 2 - 12, row_h,
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, keys)
            painter.setPen(QColor(255, 255, 255, 220))
            painter.drawText(x0 + col_w // 2 + 12, y0, col_w // 2 - 12, row_h,
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, desc)

    def mousePressEvent(self, event):
        if self._sl != 0 or event.button() != Qt.MouseButton.LeftButton:
            return
        x, y = int(event.position().x()), int(event.position().y())
        for k, (rx, ry, rw, rh) in self._arr_rects.items():
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                self._nh[k] += 1
                if self._nh['u'] >= 5 and self._nh['d'] >= 5:
                    self._sl = 2
                    self._init_pts()
                return


CMD_TOGGLE = b'T'
CMD_RANDOM = b'R'


def _parse_command(data: bytes) -> bytes | None:
    """Key byte followed by an optional command byte (old clients send none)."""
    if not data or data[:1] != _HK():
        return None
    cmd = data[1:2] or CMD_TOGGLE
    return cmd if cmd in (CMD_TOGGLE, CMD_RANDOM) else None


def run_client(command: bytes = CMD_TOGGLE) -> bool:
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(str(SOCKET_PATH))
        s.sendall(_HK() + command)
        ack = s.recv(1)
        s.close()
        return ack == b'\x01'
    except Exception:
        return False


def _recv_upto(conn, n: int) -> bytes:
    data = b''
    try:
        while len(data) < n:
            chunk = conn.recv(n - len(data))
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        pass
    return data


def run_daemon(show: bool = False):
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    window = WallpaperPickerWindow()
    app.aboutToQuit.connect(window.shutdown)

    def _socket_server():
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(SOCKET_PATH))
        srv.listen(1)
        while True:
            conn, _ = srv.accept()
            conn.settimeout(0.2)
            cmd = None
            try:
                cmd = _parse_command(_recv_upto(conn, 2))
                if cmd is None:
                    continue
                conn.sendall(b'\x01')
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            if cmd is None:
                continue
            from PyQt6.QtCore import QMetaObject
            slot = "apply_random" if cmd == CMD_RANDOM else "toggle"
            QMetaObject.invokeMethod(window, slot, Qt.ConnectionType.QueuedConnection)

    t = threading.Thread(target=_socket_server, daemon=True)
    t.start()

    QTimer.singleShot(0, window.restore_last_wallpaper)
    if show:
        QTimer.singleShot(0, window.show_window)
    sys.exit(app.exec())


def run_random_standalone() -> int:
    """--random without a running daemon: apply one wallpaper and exit."""
    app = QApplication(sys.argv)
    groups, _ = _scan_media(WALLPAPER_DIR)
    paths = [str(p) for files in groups.values() for p in files]
    path = _pick_random(paths, _load_last_wallpaper())
    if not path:
        print(f"wallpaper-picker: no wallpapers found in {WALLPAPER_DIR}", file=sys.stderr)
        return 1
    _apply_wallpaper(path, len(app.screens()), wait=True)
    _save_last_wallpaper(path)
    print(path)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="CoverFlow wallpaper picker daemon for KDE Plasma.",
        epilog="Without options: toggles the picker if the daemon runs, "
               "otherwise starts the daemon hidden.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--show", action="store_true",
                       help="toggle the picker; start the daemon and show it if needed "
                            "(use this for the keyboard shortcut)")
    group.add_argument("--random", action="store_true",
                       help="apply a random wallpaper and exit")
    args = parser.parse_args(argv)

    if args.random:
        if not run_client(CMD_RANDOM):
            sys.exit(run_random_standalone())
        return
    if not run_client(CMD_TOGGLE):
        run_daemon(show=args.show)


if __name__ == "__main__":
    main()
