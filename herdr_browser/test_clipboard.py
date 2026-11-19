"""Exercise the real desktop clipboard and PTY on a private Xvfb display."""
import argparse
import ctypes
from ctypes.util import find_library
import os
from pathlib import Path
import tempfile
import time
import unittest

from .test_app import pump
from .app import BrowserWindow, Gdk, Gtk


def press_keys(*keyvals):
    """Deliver native keyboard input only to the explicitly private display."""
    display_name = os.environ["DISPLAY"]
    if (os.environ.get("HERDR_BROWSER_TEST_DISPLAY") != "private"
            or Gdk.Display.get_default().get_name() != display_name):
        raise RuntimeError("Refusing keyboard input outside the private display")
    x11 = ctypes.CDLL(find_library("X11"))
    xtest = ctypes.CDLL(find_library("Xtst"))
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XKeysymToKeycode.restype = ctypes.c_uint
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xtest.XTestFakeKeyEvent.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
    display = x11.XOpenDisplay(display_name.encode())
    if not display:
        raise RuntimeError("Could not connect to private display")
    try:
        codes = [x11.XKeysymToKeycode(display, key) for key in keyvals]
        if not all(codes):
            raise RuntimeError("A test key has no keycode")
        for code in codes:
            xtest.XTestFakeKeyEvent(display, code, 1, 0)
        for code in reversed(codes):
            xtest.XTestFakeKeyEvent(display, code, 0, 0)
        x11.XFlush(display)
        pump()
    finally:
        x11.XCloseDisplay(display)


class ClipboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="herdr-clipboard-test-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.previous_home = os.environ.get("HERDR_BROWSER_HOME")
        os.environ["HERDR_BROWSER_HOME"] = str(root / "state")
        self.capture = root / "received"
        ready = root / "ready"
        client = root / "capture-client"
        client.write_text(
            "#!/usr/bin/python3\n"
            "import os, tty\n"
            "from pathlib import Path\n"
            "tty.setraw(0)\n"
            "os.write(1, b'\\x1b[?2004h')\n"
            f"Path({str(ready)!r}).touch()\n"
            f"with open({str(self.capture)!r}, 'wb', buffering=0) as stream:\n"
            "    while True:\n"
            "        data = os.read(0, 4096)\n"
            "        if not data: break\n"
            "        stream.write(data)\n"
        )
        client.chmod(0o700)
        args = argparse.Namespace(client=client, session="clipboard-test",
                                  no_browser=True, browser=False)
        self.window = BrowserWindow(args)
        self.addCleanup(self.close)
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            pump(.05)
        self.assertTrue(ready.exists(), "Capture client did not start")
        self.clipboard = self.window.terminal.get_clipboard(Gdk.SELECTION_CLIPBOARD)
        self.window.present()
        self.window.terminal.grab_focus()
        self.window.get_window().focus(Gdk.CURRENT_TIME)
        pump()

    def close(self):
        self.window.on_close()
        self.window.destroy()
        pump(.1)
        if self.previous_home is None:
            os.environ.pop("HERDR_BROWSER_HOME", None)
        else:
            os.environ["HERDR_BROWSER_HOME"] = self.previous_home

    def received(self):
        return self.capture.read_bytes() if self.capture.exists() else b""

    def test_ctrl_v_pastes_unicode_multiline_text_with_bracketed_paste(self):
        text = "Copied text, not an image.\nSecond line: café 🐑"
        self.clipboard.set_text(text, -1)
        press_keys(Gdk.KEY_Control_L, Gdk.KEY_v)
        self.assertEqual(
            self.received(), b"\x1b[200~" + text.replace("\n", "\r").encode()
            + b"\x1b[201~")

    def test_standard_paste_shortcut_and_shift_insert_send_text(self):
        self.clipboard.set_text("terminal paste", -1)
        press_keys(Gdk.KEY_Control_L, Gdk.KEY_Shift_L, Gdk.KEY_v)
        press_keys(Gdk.KEY_Shift_L, Gdk.KEY_Insert)
        self.assertEqual(
            self.received(), b"\x1b[200~terminal paste\x1b[201~" * 2)

    def test_copy_selection_uses_real_clipboard_and_ctrl_c_is_interrupt(self):
        self.window.terminal.feed(b"Selected terminal text")
        pump()
        self.window.terminal.select_all()
        press_keys(Gdk.KEY_Control_L, Gdk.KEY_Shift_L, Gdk.KEY_c)
        self.assertIn("Selected terminal text", self.clipboard.wait_for_text())
        self.assertEqual(self.received(), b"")
        press_keys(Gdk.KEY_Control_L, Gdk.KEY_c)
        self.assertEqual(self.received(), b"\x03")

    def test_image_only_clipboard_preserves_raw_ctrl_v(self):
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 2, 2)
        image.fill(0x446688ff)
        self.clipboard.set_image(image)
        self.assertFalse(self.clipboard.wait_is_text_available())
        press_keys(Gdk.KEY_Control_L, Gdk.KEY_v)
        self.assertEqual(self.received(), b"\x16")

    def test_menu_pastes_text_and_disables_copy_without_selection(self):
        self.clipboard.set_text("menu paste", -1)
        event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        event.window = self.window.terminal.get_window()
        event.button = 3
        event.x, event.y = 20, 20
        event.time = Gdk.CURRENT_TIME
        event.set_device(Gdk.Display.get_default().get_default_seat().get_pointer())
        self.assertTrue(self.window.terminal.emit("button-press-event", event))
        self.assertTrue(self.window.terminal_menu.get_visible())
        self.assertFalse(self.window.copy_item.get_sensitive())
        self.assertTrue(self.window.paste_item.get_sensitive())
        self.window.terminal_menu.popdown()
        self.window.paste_item.activate()
        pump()
        self.assertEqual(self.received(), b"\x1b[200~menu paste\x1b[201~")
