#!/usr/bin/env python3
"""Wallpaper Picker — KDE Plasma daemon with CoverFlow carousel and categories."""

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
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QWidget, QGraphicsScene, QGraphicsBlurEffect
from PyQt6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, pyqtProperty,
    QFileSystemWatcher, QThread, pyqtSignal, pyqtSlot, QObject, QRectF, QTimer, QEvent
)
from PyQt6.QtGui import QPixmap, QPainter, QTransform, QColor, QImage, QFont, QPainterPath

WALLPAPER_DIR = Path(os.environ.get("WALLPAPER_DIR", str(Path.home() / "wallpapers")))
THUMB_CACHE_DIR = Path(os.environ.get(
    "XDG_CACHE_HOME", str(Path.home() / ".cache")
)) / "wallpaper-picker" / "thumbnails"
SOCKET_PATH = Path(os.environ.get(
    "XDG_RUNTIME_DIR", f"/tmp/user-{os.getuid()}"
)) / "wallpaper_picker.sock"
STATE_FILE = Path.home() / ".config" / "wallpaper-picker" / "state.json"
BASE_W, BASE_H = 350, 220
SPREAD = 230
MAX_DIST = 3.0
PAGE_SIZE = 24
FILTER_ALL = 'all'
FILTER_IMAGES = 'images'
FILTER_VIDEOS = 'videos'
FILTER_FAVORITES = 'favorites'
HEADER_H = 50
WIN_W, WIN_H = 1100, 340
BLUR_RADIUS = 30
BLUR_OVERLAY_ALPHA = 100  # dark overlay on top of blurred bg
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
    STATE_FILE.write_text(json.dumps(data))


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


class _ThumbThread(QThread):
    partial = pyqtSignal(dict)  # cached-only, emitted immediately on start
    done = pyqtSignal(dict)     # full result after generating missing thumbs

    def __init__(self, directory: Path):
        super().__init__()
        self.directory = directory

    def run(self):
        THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        try:
            entries = list(self.directory.iterdir())
        except FileNotFoundError:
            self.done.emit({})
            return

        subdirs = sorted(e for e in entries if e.is_dir() and not e.name.startswith('.'))
        root_files = sorted(e for e in entries
                            if e.is_file() and e.suffix.lower() in MEDIA_EXTENSIONS)

        subdir_cached = {}   # name -> (orig_list, thumb_list) for already-cached
        subdir_pending = {}  # name -> [Path, ...] needing generation

        for subdir in subdirs:
            if self.isInterruptionRequested():
                return
            try:
                files = sorted(p for p in subdir.iterdir()
                               if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS)
            except PermissionError:
                continue
            c_o, c_t, pending = self._split(files)
            subdir_cached[subdir.name] = (c_o, c_t)
            subdir_pending[subdir.name] = pending

        root_c_o, root_c_t, root_pending = self._split(root_files)

        partial = self._assemble(subdir_cached, root_c_o, root_c_t, bool(subdirs))
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

        self.done.emit(self._assemble(subdir_cached, root_c_o, root_c_t, bool(subdirs)))

    def _split(self, files):
        cached_o, cached_t, pending = [], [], []
        for img_path in files:
            if self.isInterruptionRequested():
                return cached_o, cached_t, pending + files[len(cached_o) + len(pending):]
            thumb_name = f"{img_path.parent.name}__{img_path.stem}_thumb.png"
            thumb_path = THUMB_CACHE_DIR / thumb_name
            if (thumb_path.exists()
                    and thumb_path.stat().st_mtime_ns >= img_path.stat().st_mtime_ns):
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
            thumb_name = f"{img_path.parent.name}__{img_path.stem}_thumb.png"
            thumb_path = THUMB_CACHE_DIR / thumb_name
            if img_path.suffix.lower() in VIDEO_EXTENSIONS:
                if not self._generate_video_thumb(img_path, thumb_path):
                    continue
            else:
                img = QImage(str(img_path))
                if img.isNull():
                    continue
                scaled = img.scaled(
                    BASE_W, BASE_H,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                scaled.save(str(thumb_path))
            orig.append(str(img_path))
            thumbs.append(str(thumb_path))
        return orig, thumbs

    @staticmethod
    def _generate_video_thumb(video_path: Path, thumb_path: Path) -> bool:
        if shutil.which('ffmpeg') is None:
            return False
        try:
            result = subprocess.run(
                [
                    'ffmpeg', '-y', '-ss', '0', '-i', str(video_path),
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
        self._watcher = QFileSystemWatcher()
        self._watcher.directoryChanged.connect(self.load)

    def _update_watched_dirs(self):
        """Watch root + all subdirectories so new files anywhere are detected."""
        dirs = [str(self.directory)]
        try:
            dirs += [str(p) for p in self.directory.iterdir() if p.is_dir()]
        except FileNotFoundError:
            pass
        existing = set(self._watcher.directories())
        new = [d for d in dirs if d not in existing]
        if new:
            self._watcher.addPaths(new)

    def load(self):
        if self._thread is not None and self._thread.isRunning():
            return
        self._update_watched_dirs()
        self._thread = _ThumbThread(self.directory)
        self._thread.partial.connect(self.wallpapers_loaded)
        self._thread.done.connect(self.wallpapers_loaded)
        self._thread.start()

    def stop(self):
        if self._thread is None or not self._thread.isRunning():
            return
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
        self._pixmap_cache: dict = {}  # thumb_path -> QPixmap, lazy-loaded
        self._scaled_pixmap_cache: dict = {}
        self._index = 0
        self._offset = 0.0
        self._vert_offset = 0.0

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
        if tp not in self._pixmap_cache:
            self._pixmap_cache[tp] = QPixmap(tp)
        return self._pixmap_cache[tp]

    def set_wallpapers(self, paths: list, thumb_paths: list, direction: int = 0):
        """Load new wallpapers. direction: +1 slides in from below, -1 from above."""
        self._paths = paths
        self._thumb_paths = thumb_paths
        self._scaled_pixmap_cache.clear()
        self._index = 0
        self._offset = 0.0
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

    def selected_path(self) -> str:
        if not self._paths:
            return ""
        return self._paths[self._index]

    def selected_thumb_path(self) -> str:
        if not self._thumb_paths:
            return ""
        return self._thumb_paths[self._index]

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

            cache_key = (self._thumb_paths[i], w, h)
            scaled_pm = self._scaled_pixmap_cache.get(cache_key)
            if scaled_pm is None:
                scaled_pm = self._get_pixmap(i).scaled(
                    w, h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self._scaled_pixmap_cache[cache_key] = scaled_pm

            painter.save()
            painter.setOpacity(opacity)
            # Stack transforms additively — never use setTransform (it replaces everything)
            painter.translate(0, self._vert_offset)          # spring slide
            if abs(shear) > 0.001:
                painter.translate(x + w / 2, y + h / 2)     # pivot to image center
                painter.shear(shear, 0)
                painter.translate(-(x + w / 2), -(y + h / 2))
            painter.drawPixmap(int(x), int(y), scaled_pm)
            if Path(self._paths[i]).suffix.lower() in VIDEO_EXTENSIONS:
                badge = QRectF(x + 10, y + 10, 58, 22)
                painter.setOpacity(min(1.0, opacity + 0.2))
                painter.setBrush(QColor(15, 15, 15, 210))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(badge, 5, 5)
                painter.setPen(QColor(255, 255, 255, 230))
                painter.setFont(QFont('monospace', 9))
                painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, 'LIVE')
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
        self._fx_timer.timeout.connect(self._on_fx_tick)
        self._fx_timer.start(16)

        total_h = WIN_H + HEADER_H
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(
            screen.x() + (screen.width() - WIN_W) // 2,
            screen.y() + (screen.height() - total_h) // 2,
            WIN_W, total_h
        )

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

        self._show_current_category()
        self.update()

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

    def _show_current_category(self, direction: int = 0):
        if not self._categories:
            self._carousel.set_wallpapers([], [])
            return
        _, paths, thumbs = self._categories[self._cat_index]
        filtered_paths, filtered_thumbs = self._filtered_paths(paths, thumbs)
        self._carousel.set_wallpapers(filtered_paths, filtered_thumbs, direction=direction)

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
        elif self._t % 3750 == 1:
            self._update_blur_for_selection()
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
        self.update()

    def _exit_sl(self):
        self._sl = 0
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

        screen = QApplication.primaryScreen().geometry()
        img = img.scaled(screen.width(), screen.height(),
                         Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                         Qt.TransformationMode.SmoothTransformation)

        geo = self.geometry()
        pad = BLUR_RADIUS * 2
        ox = max(0, geo.x() - screen.x() - pad)
        oy = max(0, geo.y() - screen.y() - pad)
        cw = min(self.width() + pad * 2, screen.width() - ox)
        ch = min(self.height() + pad * 2, screen.height() - oy)
        padded = img.copy(ox, oy, cw, ch)

        scene = QGraphicsScene()
        item = scene.addPixmap(QPixmap.fromImage(padded))
        fx = QGraphicsBlurEffect()
        fx.setBlurRadius(BLUR_RADIUS)
        item.setGraphicsEffect(fx)

        blurred = QImage(padded.size(), QImage.Format.Format_ARGB32_Premultiplied)
        blurred.fill(Qt.GlobalColor.transparent)
        p = QPainter(blurred)
        scene.render(p)
        p.end()

        # Crop padding back out
        lpad = geo.x() - screen.x() - ox
        tpad = geo.y() - screen.y() - oy
        cropped = blurred.copy(lpad, tpad, self.width(), self.height())
        self._blur_bg = QPixmap.fromImage(cropped)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self.hide()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self._sl == 2:
            self._exit_sl()

    def shutdown(self):
        self._fx_timer.stop()
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
            self.show()
            self.raise_()
            self.activateWindow()

    def apply_and_close(self):
        path = self._carousel.selected_path()
        if path:
            self._apply_wallpaper(path)
            _save_last_wallpaper(path)
        self.hide()

    def _apply_wallpaper(self, path: str):
        if Path(path).suffix.lower() in VIDEO_EXTENSIONS:
            self._apply_video_wallpaper(path)
        else:
            subprocess.Popen(["plasma-apply-wallpaperimage", path])

    def restore_last_wallpaper(self):
        path = _load_last_wallpaper()
        if path and Path(path).is_file():
            self._apply_wallpaper(path)

    @staticmethod
    def _apply_video_wallpaper(path: str):
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
        for screen_number in range(len(QApplication.screens())):
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

    def keyPressEvent(self, event):
        if self._sl == 2:
            self._exit_sl()
            return
        key = event.key()
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
        item_count = len(self._categories[self._cat_index][1])
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


def run_client() -> bool:
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(str(SOCKET_PATH))
        s.sendall(_HK())
        ack = s.recv(1)
        s.close()
        return ack == b'\x01'
    except Exception:
        return False


def run_daemon():
    if not _vi():
        sys.exit(0)

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
        _fc = 0
        while True:
            conn, _ = srv.accept()
            try:
                data = conn.recv(1)
                if data != _HK():
                    _fc += 1
                    if _fc >= 3:
                        os._exit(1)
                    continue
                _fc = 0
                conn.sendall(b'\x01')
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            from PyQt6.QtCore import QMetaObject
            QMetaObject.invokeMethod(window, "toggle",
                                     Qt.ConnectionType.QueuedConnection)

    t = threading.Thread(target=_socket_server, daemon=True)
    t.start()

    QTimer.singleShot(0, window.restore_last_wallpaper)
    sys.exit(app.exec())


if __name__ == "__main__":
    if not run_client():
        run_daemon()
