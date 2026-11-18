"""Check cleanup compatibility with persistent headless homes."""

import contextlib
import fcntl
import importlib.machinery
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / "sandkasten-cleanup"
loader = importlib.machinery.SourceFileLoader("cleanup", str(path))
spec = importlib.util.spec_from_loader(loader.name, loader)
cleanup = importlib.util.module_from_spec(spec)
loader.exec_module(cleanup)


class CleanupTests(unittest.TestCase):
    def test_removes_persistent_and_legacy_homes(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory).resolve()
            for name in ("project-" + "a" * 12, "project-" + "a" * 8, "2026-11-16_0123456789abcdef"):
                home = cache / name
                home.mkdir()
                (home / "state").write_text("saved")
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertTrue(cleanup.remove_sandbox(name, cache))
                self.assertFalse(home.exists())

    def test_refuses_active_home(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory).resolve()
            name = "project-" + "b" * 8
            home = cache / name
            home.mkdir()
            with (cache / (name + ".lock")).open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertFalse(cleanup.remove_sandbox(name, cache))
            self.assertTrue(home.exists())

    def test_does_not_follow_linked_parent_of_subvolume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cache = root / "cache"
            home = cache / ("project-" + "c" * 8)
            home.mkdir(parents=True)
            host_tectonic = root / "host-cache/Tectonic"
            host_tectonic.mkdir(parents=True)
            (home / ".cache").symlink_to(root / "host-cache", target_is_directory=True)
            with patch("subprocess.run") as run, patch("shutil.which", return_value="/usr/bin/btrfs"):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertTrue(cleanup.remove_sandbox(home.name, cache))
            run.assert_not_called()
            self.assertTrue(host_tectonic.is_dir())
            self.assertFalse(home.exists())


if __name__ == "__main__":
    unittest.main()
