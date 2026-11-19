"""Run each native UI contract once, including tests using inherited fixtures."""
import importlib
import os
from pathlib import Path
import sys
import unittest

if os.environ.get("HERDR_BROWSER_TEST_DISPLAY") != "private":
    raise SystemExit("Run this under a private Xvfb display.")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
suite = unittest.TestSuite()
for name in ("test_app", "test_zoom", "test_clipboard"):
    module = importlib.import_module("herdr_browser." + name)
    for cls in vars(module).values():
        if (isinstance(cls, type) and issubclass(cls, unittest.TestCase)
                and cls.__module__ == module.__name__):
            for method in sorted(cls.__dict__):
                if method.startswith("test_"):
                    suite.addTest(cls(method))
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
