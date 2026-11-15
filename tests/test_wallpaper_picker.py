"""Tests for wallpaper_picker. Run with: python3 -m unittest discover -s tests

Runs headless (QT_QPA_PLATFORM=offscreen) against a temporary HOME, so it
never touches your real wallpapers, cache, state or Plasma session.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.TemporaryDirectory()
_ROOT = Path(_TMP.name)
os.environ.update({
    "QT_QPA_PLATFORM": "offscreen",
    "HOME": str(_ROOT / "home"),
    "XDG_CACHE_HOME": str(_ROOT / "cache"),
    "XDG_RUNTIME_DIR": str(_ROOT / "run"),
    "WALLPAPER_DIR": str(_ROOT / "wp"),
})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import wallpaper_picker as wp  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _make_image(path: Path, color: str = "red"):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = QImage(64, 40, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    assert img.save(str(path))


def _scan(directory: Path) -> dict:
    result = {}
    thread = wp._ThumbThread(directory)
    thread.done.connect(result.update)
    thread.run()  # synchronous: run in this thread
    return result


class _TempDirCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory(dir=_ROOT)
        self.root = Path(self._dir.name)
        self.wallpapers = self.root / "wp"
        self.wallpapers.mkdir()

    def tearDown(self):
        self._dir.cleanup()


class ScanTests(_TempDirCase):
    def test_same_name_different_extension_and_folder_get_unique_thumbs(self):
        for rel in ("a/x.png", "a/x.jpg", "b/x.png"):
            _make_image(self.wallpapers / rel)
        thumbs = _scan(self.wallpapers)["All"][1]
        self.assertEqual(len(thumbs), 3)
        self.assertEqual(len(set(thumbs)), 3)

    def test_nested_folders_become_categories(self):
        _make_image(self.wallpapers / "nature" / "a.png")
        _make_image(self.wallpapers / "nature" / "forest" / "b.png")
        _make_image(self.wallpapers / "loose.png")
        _make_image(self.wallpapers / ".hidden" / "c.png")
        result = _scan(self.wallpapers)
        self.assertEqual(set(result), {"All", "nature", "nature/forest", "Unsorted"})
        self.assertEqual(len(result["All"][0]), 3)

    def test_flat_folder_is_single_all_category(self):
        _make_image(self.wallpapers / "a.png")
        self.assertEqual(set(_scan(self.wallpapers)), {"All"})

    def test_missing_folder_gives_empty_result(self):
        self.assertEqual(_scan(self.root / "does-not-exist"), {})

    def test_stale_thumbnails_are_removed(self):
        _make_image(self.wallpapers / "a.png")
        _make_image(self.wallpapers / "b.png")
        _scan(self.wallpapers)
        (self.wallpapers / "b.png").unlink()
        stale = wp.THUMB_CACHE_DIR / "old__name_thumb.png"
        stale.write_bytes(b"x")
        result = _scan(self.wallpapers)
        remaining = {str(p) for p in wp.THUMB_CACHE_DIR.glob("*.png")}
        self.assertEqual(remaining, set(result["All"][1]))
        self.assertFalse(stale.exists())


class WatcherTests(_TempDirCase):
    def _run_loop(self, ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def test_file_copied_slowly_is_detected(self):
        """The file appears half-written (one directoryChanged event) and is
        completed later (only fileChanged). It must still show up."""
        source = self.root / "full.png"
        _make_image(source)
        data = source.read_bytes()
        loader = wp.WallpaperLoader(self.wallpapers)
        seen = []
        loader.wallpapers_loaded.connect(
            lambda d: seen.append(len(d.get("All", ([], []))[0])))
        loader.load()
        self._run_loop(300)
        target = self.wallpapers / "copied.png"
        with open(target, "wb") as fh:
            fh.write(data[: len(data) // 3])
            fh.flush()
            self._run_loop(1200)  # scanned while incomplete
            fh.write(data[len(data) // 3:])
        self._run_loop(2500)
        loader.stop()
        self.assertEqual(seen[-1:], [1])

    def test_unchanged_rescan_does_not_emit(self):
        _make_image(self.wallpapers / "a.png")
        loader = wp.WallpaperLoader(self.wallpapers)
        seen = []
        loader.wallpapers_loaded.connect(seen.append)
        for _ in range(3):
            loader.load()
            loader._thread.wait()
            QApplication.processEvents()
        loader.stop()
        self.assertEqual(len(seen), 1)


class StateTests(unittest.TestCase):
    def test_state_roundtrip_is_atomic(self):
        wp._save_favorites({"/a", "/b"})
        wp._save_last_wallpaper("/a")
        self.assertEqual(wp._load_favorites(), {"/a", "/b"})
        self.assertEqual(wp._load_last_wallpaper(), "/a")
        self.assertFalse(wp.STATE_FILE.with_name("state.json.tmp").exists())

    def test_corrupt_state_falls_back_to_defaults(self):
        wp.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        wp.STATE_FILE.write_text("{not json")
        self.assertEqual(wp._load_favorites(), set())
        self.assertEqual(wp._load_filter(), wp.FILTER_ALL)


class ConfigTests(_TempDirCase):
    def test_missing_and_invalid_config(self):
        self.assertEqual(wp._load_config(self.root / "nope.toml"), {})
        bad = self.root / "bad.toml"
        bad.write_text("this is = = not toml")
        self.assertEqual(wp._load_config(bad), {})

    def test_values_are_type_checked_and_clamped(self):
        good = self.root / "c.toml"
        good.write_text('page_size = 5000\nblur_scale = "big"\nrotate_source = "favorites"\n')
        cfg = wp._load_config(good)
        self.assertEqual(wp._cfg_num(cfg, "page_size", 24, 1, 1000), 1000)
        self.assertEqual(wp._cfg_num(cfg, "blur_scale", 0.25, 0.05, 1.0), 0.25)
        self.assertEqual(wp._cfg_num({"x": True}, "x", 3, 0, 9), 3)
        self.assertEqual(wp._cfg_choice(cfg, "rotate_source", "all",
                                        {"all", "favorites"}), "favorites")


class CarouselTests(unittest.TestCase):
    def setUp(self):
        self.carousel = wp.CarouselWidget()
        self.paths = [f"/w/{i}.png" for i in range(10)]
        self.carousel.set_wallpapers(self.paths, self.paths)

    def test_reload_keeps_selection(self):
        self.carousel.navigate(4)
        self.carousel.set_wallpapers(self.paths, self.paths,
                                     select_path=self.carousel.selected_path())
        self.assertEqual(self.carousel.selected_path(), "/w/4.png")

    def test_reload_without_selected_item_starts_at_first(self):
        self.carousel.set_wallpapers(self.paths, self.paths, select_path="/gone.png")
        self.assertEqual(self.carousel.selected_path(), "/w/0.png")

    def test_jump_to(self):
        self.assertTrue(self.carousel.jump_to("/w/7.png"))
        self.assertEqual(self.carousel.selected_path(), "/w/7.png")
        self.assertFalse(self.carousel.jump_to("/elsewhere.png"))

    def test_navigate_random_changes_selection(self):
        for _ in range(20):
            before = self.carousel.selected_path()
            self.carousel.navigate_random()
            self.assertNotEqual(self.carousel.selected_path(), before)


class RandomKeyTests(unittest.TestCase):
    def test_r_applies_random_wallpaper_from_current_view(self):
        win = wp.WallpaperPickerWindow()
        win._loader.stop()
        paths = [f"/w/{i}.png" for i in range(5)]
        win._categories = [("All", paths, paths)]
        win._cat_index = 0
        win._filter_mode = wp.FILTER_ALL
        win._show_current_category()
        applied = []
        original = wp._apply_wallpaper
        wp._apply_wallpaper = lambda path, screens, wait=False: applied.append(path)
        try:
            win._apply_random_from_view()
            win._apply_random_from_view()
        finally:
            wp._apply_wallpaper = original
        self.assertEqual(len(applied), 2)
        self.assertNotEqual(applied[0], applied[1])
        self.assertEqual(wp._load_last_wallpaper(), applied[1])
        self.assertEqual(win._carousel._active_path, applied[1])
        win.shutdown()


class RandomAndProtocolTests(_TempDirCase):
    def test_pick_random_avoids_current_and_respects_filter(self):
        a, b = self.wallpapers / "a.png", self.wallpapers / "b.png"
        _make_image(a)
        _make_image(b)
        paths = [str(a), str(b), str(self.wallpapers / "missing.png")]
        for _ in range(20):
            self.assertEqual(wp._pick_random(paths, current=str(a)), str(b))
        self.assertEqual(wp._pick_random(paths, only={str(a)}), str(a))
        self.assertEqual(wp._pick_random([], ""), "")

    def test_parse_command(self):
        key = wp._HK()
        self.assertEqual(wp._parse_command(key), wp.CMD_TOGGLE)  # old client
        self.assertEqual(wp._parse_command(key + wp.CMD_RANDOM), wp.CMD_RANDOM)
        self.assertIsNone(wp._parse_command(b""))
        self.assertIsNone(wp._parse_command(bytes([key[0] ^ 1])))
        self.assertIsNone(wp._parse_command(key + b"?"))


if __name__ == "__main__":
    unittest.main()
