"""Protect recipient instructions and an existing install when installation fails."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .install_bundle import BEGIN, END, install, workflow_text, write_atomic


class WorkflowTests(unittest.TestCase):
    def test_reinstall_preserves_surrounding_instructions_without_duplicates(self):
        old = "My instructions\n\n" + BEGIN + "\nold\n" + END + "\nOther instructions\n"
        updated = workflow_text(old, "new")
        self.assertEqual(workflow_text(updated, "new"), updated)
        self.assertTrue(updated.startswith("My instructions\n\n"))
        self.assertTrue(updated.endswith("\nOther instructions\n"))
        self.assertEqual(updated.count(BEGIN), 1)
        self.assertIn("\nnew\n", updated)

    def test_partial_marker_does_not_overwrite_user_text(self):
        for old in (BEGIN, END, END + BEGIN, BEGIN + BEGIN + END):
            with self.subTest(old=old), self.assertRaises(ValueError):
                workflow_text(old, "new")

    def test_atomic_write_keeps_executable_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bin/tool"
            write_atomic(path, b"old", 0o755)
            write_atomic(path, b"new", 0o755)
            self.assertEqual(path.read_bytes(), b"new")
            self.assertEqual(path.stat().st_mode & 0o777, 0o755)

    def test_failed_activation_restores_previous_application_and_launcher(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "bundle"
            package = bundle / "herdr_browser"
            package.mkdir(parents=True)
            (package / "provenance.json").write_text('{"version":"v0"}')
            (bundle / "client").mkdir()
            (bundle / "client/herdr").write_bytes(b"test client")
            for name in ("LICENSE", "NOTICE", "README.md", "DEVELOPMENT.md"):
                (bundle / name).write_text("test documentation")
            for name in ("docs", "patches"):
                (bundle / name).mkdir()
            prefix = root / "prefix"
            previous = prefix / "share/herdr-browser"
            previous.mkdir(parents=True)
            (previous / "old").write_text("keep this")
            launcher = prefix / "bin/herdr-browser"
            write_atomic(launcher, b"previous launcher", 0o755)
            with patch("herdr_browser.desktop.install_app_launcher",
                       side_effect=OSError("simulated desktop installation failure")):
                with self.assertRaisesRegex(OSError, "simulated"):
                    install(bundle, prefix)
            self.assertEqual((previous / "old").read_text(), "keep this")
            self.assertEqual(launcher.read_bytes(), b"previous launcher")
            self.assertEqual(launcher.stat().st_mode & 0o777, 0o755)
            self.assertFalse((prefix / "share/applications/herdr-browser.desktop").exists())


if __name__ == "__main__":
    unittest.main()
