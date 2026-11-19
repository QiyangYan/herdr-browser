"""Zoom unit tests use the existing private-display native fixture."""
import ctypes
from ctypes.util import find_library
import json
import os
import unittest
from unittest.mock import patch

from .test_app import NativeTests, pump, Gdk
from .app import Gtk
from .store import Store


def send_key(window, key, ctrl=True, shift=False):
    """Send actual X11 keys only to the private test window."""
    display_name = os.environ["DISPLAY"]
    if os.environ.get("HERDR_BROWSER_TEST_DISPLAY") != "private" or display_name == ":1":
        raise RuntimeError("Refusing to send test input to the user's display.")
    import gi
    gi.require_version("GdkX11", "3.0")
    from gi.repository import GdkX11
    x11 = ctypes.CDLL(find_library("X11"))
    xtest = ctypes.CDLL(find_library("Xtst"))
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XKeysymToKeycode.restype = ctypes.c_uint
    x11.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xtest.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
    display = x11.XOpenDisplay(display_name.encode())
    if not display:
        raise RuntimeError("Private test display unavailable.")
    try:
        x11.XSetInputFocus(display, GdkX11.X11Window.get_xid(window.get_window()), 1, 0)
        keys = ([Gdk.KEY_Control_L] if ctrl else []) + ([Gdk.KEY_Shift_L] if shift else []) + [key]
        for pressed in keys:
            xtest.XTestFakeKeyEvent(display, x11.XKeysymToKeycode(display, pressed), 1, 0)
        for released in reversed(keys):
            xtest.XTestFakeKeyEvent(display, x11.XKeysymToKeycode(display, released), 0, 0)
        x11.XFlush(display)
        pump()
    finally:
        x11.XCloseDisplay(display)


class ZoomTests(NativeTests):
    def test_zoom_native_terminal_keys_are_consumed(self):
        self.window.terminal.grab_focus()
        forwarded = []
        self.window.terminal.connect("key-press-event", lambda _, event: forwarded.append(event.keyval) or False)
        send_key(self.window, Gdk.KEY_equal)
        self.assertAlmostEqual(self.window.terminal.get_font_scale(), 1.1)
        self.assertNotIn(Gdk.KEY_equal, forwarded)
        send_key(self.window, Gdk.KEY_equal, shift=True)
        self.assertAlmostEqual(self.window.terminal.get_font_scale(), 1.2)
        send_key(self.window, Gdk.KEY_minus)
        self.assertAlmostEqual(self.window.terminal.get_font_scale(), 1.1)
        send_key(self.window, Gdk.KEY_0)
        self.assertAlmostEqual(self.window.terminal.get_font_scale(), 1)
        send_key(self.window, Gdk.KEY_a, ctrl=False)
        self.assertIn(Gdk.KEY_a, forwarded)
        self.assertAlmostEqual(self.window.view.get_zoom_level(), 1)

    def test_zoom_native_browser_and_address_keys(self):
        for target in (self.window.view, self.window.address):
            target.grab_focus()
            send_key(self.window, Gdk.KEY_equal)
            self.assertAlmostEqual(self.window.view.get_zoom_level(), 1.1)
            send_key(self.window, Gdk.KEY_0)
            self.assertAlmostEqual(self.window.view.get_zoom_level(), 1)
        self.assertAlmostEqual(self.window.terminal.get_font_scale(), 1)

    def test_zoom_buttons_bounds_and_reset(self):
        for pane, minimum in (("terminal", 50), ("browser", 25)):
            out, reset, zoom_in = self.window.zoom_widgets[pane]
            zoom_in.clicked()
            self.assertEqual(self.window.zoom_levels[pane], 110)
            self.assertEqual(reset.get_label(), "110%")
            for _ in range(100):
                self.window.change_zoom(pane, -10)
            self.assertEqual(self.window.zoom_levels[pane], minimum)
            self.assertFalse(out.get_sensitive())
            for _ in range(100):
                self.window.change_zoom(pane, 10)
            self.assertEqual(self.window.zoom_levels[pane], 300)
            self.assertFalse(zoom_in.get_sensitive())
            reset.clicked()
            self.assertEqual(self.window.zoom_levels[pane], 100)

    def test_zoom_persists_and_survives_view_replacement(self):
        self.window.change_zoom("browser", 30)
        self.window.change_zoom("terminal", 20)
        self.assertEqual(Store().zoom_percent("browser"), 130)
        self.assertEqual(Store().zoom_percent("terminal"), 120)
        self.window.restore_view()
        pump()
        self.assertAlmostEqual(self.window.view.get_zoom_level(), 1.3)
        self.window.toggle.set_active(False)
        self.window.toggle.set_active(True)
        pump()
        self.assertAlmostEqual(self.window.view.get_zoom_level(), 1.3)
        from .app import BrowserWindow
        another = BrowserWindow(self.args)
        try:
            self.assertAlmostEqual(another.terminal.get_font_scale(), 1.2)
            self.assertEqual(another.zoom_levels["browser"], 130)
        finally:
            another.on_close()
            another.destroy()
            pump()

    def test_zoom_corrupt_settings_and_save_failure(self):
        with self.window.store.transaction() as data:
            data["zoom_percent"] = {"terminal": "large", "browser": 900}
        self.assertEqual(self.window.store.zoom_percent("terminal"), 100)
        self.assertEqual(self.window.store.zoom_percent("browser"), 300)
        with patch.object(self.window.store, "set_zoom_percent", side_effect=OSError("test failure")):
            self.window.change_zoom("terminal", 10)
        self.assertAlmostEqual(self.window.terminal.get_font_scale(), 1.1)
        self.assertIn("could not save", self.window.message.get_text())


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ZoomTests(name) for name in loader.getTestCaseNames(ZoomTests)
                             if name.startswith("test_zoom_"))
