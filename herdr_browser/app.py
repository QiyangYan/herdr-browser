"""GTK/VTE terminal with one disposable WebKit view, bound to client-local focus."""
import json
import os
from pathlib import Path
import signal
import tempfile
import uuid
import time
import subprocess
import hashlib
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gtk, GLib, Vte, Pango, Gdk, Gio

from .store import (Store, atomic_json, task_key, task_home, checked_url,
                    status_label, task_caption, find_page, url_identity, ZOOM_LIMITS)
from .router import process_token, sync_defaults, open_external, firefox_required, open_firefox
from .reports import ReportServer, session_id, session_snapshot, conclusion_token
from .desktop import APP_ID
from .recency import grouped_tasks


def connection_path(session):
    socket = os.environ.get("HERDR_SOCKET_PATH")
    if not session and socket:
        return str(Path(socket).resolve())
    session = session or os.environ.get("HERDR_SESSION")
    config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "herdr"
    if session and session != "default":
        config = config / "sessions" / session
    return str((config / "herdr.sock").resolve())


class BrowserWindow(Gtk.Window):
    def __init__(self, args, application=None):
        super().__init__(title="Herdr · optional task browser")
        if application is not None:
            self.set_application(application)
        self.set_wmclass(APP_ID, APP_ID)
        self.set_icon_name(APP_ID)
        self.set_default_size(1480, 920)
        self.store = Store()
        self.zoom_levels = {pane: self.store.zoom_percent(pane) for pane in ZOOM_LIMITS}
        self.zoom_widgets = {}
        self.args = args
        self.connection = connection_path(args.session)
        socket_parent = Path(self.connection).parent
        self.session_label = args.session or (
            socket_parent.name if socket_parent.parent.name == "sessions" else "default")
        self.launch_tab = getattr(args, "restore_tab", None) or (
            os.environ.get("HERDR_TAB_ID") if not args.session else None)
        source = Path(__file__).resolve().parent
        self.loaded_sources = {source / name: hashlib.sha256((source / name).read_bytes()).digest()
                               for name in ("app.py", "store.py", "router.py", "cli.py",
                                            "reports.py", "report_view.js", "session_view.js",
                                            "review_state.js", "serve_reports.py", "recency.py", "desktop.py")}
        manifest = source.parent / "installation.json"
        if manifest.is_file():
            self.loaded_sources[manifest] = hashlib.sha256(manifest.read_bytes()).digest()
        self.update_stamps = {}
        self.next_update_check = 0
        self.update_available = False
        self.runtime = tempfile.TemporaryDirectory(prefix="herdr-browser-")
        self.context_path = Path(self.runtime.name) / "context.json"
        self.inbox = Path(self.runtime.name) / "urls"
        self.inbox.mkdir(mode=0o700)
        self.inbox_stamp = None
        self.pending_route = None
        self.focused_at = time.time_ns()
        self.context = {}
        self.key = None
        self.view = None
        self.report_location = None
        self.showing_home = False
        self.home_html = None
        self.engine = None
        self.client_pid = None
        self.client_exited = False
        self.closed = False
        self.last_stamp = None
        self.store_stamp = None
        self.task_ids = []
        self.task_menu_signature = None
        self.next_task_menu_check = 0
        self.links = []
        self.section = "General"
        self.active_page = None
        self.section_names = []
        self.page_signature = None
        self.page_buttons = []
        self.set_name("herdr-companion")
        self.css = Gtk.CssProvider()
        self.css.load_from_data(b"""
        #herdr-companion { background: #f2f2f4; color: #30343b; }
        #herdr-companion .chrome { background: #ececef; padding: 6px; }
        #herdr-companion .chrome button { background-image: none; background: transparent;
          border: 1px solid transparent; box-shadow: none; border-radius: 7px;
          min-height: 24px; padding: 3px 7px; color: #4b505a; }
        #herdr-companion .chrome button:hover { background: #dedee4; }
        #herdr-companion .chrome button:checked { background: #fff; border-color: #d4d5db; }
        #herdr-companion .chrome button:focus, #herdr-companion entry:focus {
          border-color: #6b90bd; }
        #herdr-companion entry { background: #fafafb; color: #30343b;
          border: 1px solid #d5d6dc; border-radius: 8px; box-shadow: none;
          min-height: 28px; padding: 2px 9px; }
        #herdr-companion .page-tabs button { background: #e6e6eb;
          border: 1px solid transparent; border-radius: 7px; }
        #herdr-companion .page-tabs button.active-page { background: #fff;
          border-color: #cbd1dc; color: #244f82; }
        #herdr-companion .page-tabs .page-tab { background: #e6e6eb;
          border: 1px solid transparent; border-radius: 7px; }
        #herdr-companion .page-tabs .page-tab.active-page { background: #fff;
          border-color: #cbd1dc; }
        #herdr-companion .page-tabs .page-tab button { background: transparent;
          border: 0; }
        #herdr-companion .page-tabs .page-tab button:hover { background: #dedee4; }
        #herdr-companion .page-tabs .tab-close { padding: 3px; min-width: 20px; }
        #herdr-companion .quiet-label { color: #6b707a; font-size: 11px; }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        instances = self.store.home / "instances"
        instances.mkdir(mode=0o700, exist_ok=True)
        self.instance = instances / f"{os.getpid()}-{uuid.uuid4().hex}.json"
        self.instance_metadata = {
            "pid": os.getpid(), "connection": self.connection,
            "application_id": application.get_application_id() if application else None,
            "context": str(self.context_path),
            "process_token": process_token(os.getpid()), "inbox": str(self.inbox),
            "routing_enabled": False, "focused_at": self.focused_at,
        }
        atomic_json(self.instance, self.instance_metadata)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(root)
        header = Gtk.Box(spacing=12)
        header.get_style_context().add_class("chrome")
        header.set_border_width(2)
        self.heading = Gtk.Label(label="Waiting for this window's Herdr tab…")
        self.heading.set_xalign(0)
        self.heading.set_ellipsize(Pango.EllipsizeMode.END)
        header.pack_start(self.heading, True, True, 0)
        self.update_button = Gtk.Button(label="Apply update")
        self.update_button.set_sensitive(False)
        self.update_button.set_tooltip_text("Up to date. Installed updates will appear here.")
        self.update_button.connect("clicked", self.apply_update)
        header.pack_end(self.update_button, False, False, 0)
        self.toggle = Gtk.ToggleButton(label="Browser")
        enabled = not args.no_browser and (
            args.browser or self.store.read().get("browser_enabled", True))
        self.toggle.set_active(enabled)
        header.pack_end(self.toggle, False, False, 0)
        root.pack_start(header, False, False, 0)
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        root.pack_start(self.paned, True, True, 0)
        self.terminal = Vte.Terminal()
        self.terminal.set_font(Pango.FontDescription("Monospace 12"))
        self.terminal.set_font_scale(self.zoom_levels["terminal"] / 100)
        self.terminal.set_scrollback_lines(10000)
        self.terminal.set_allow_hyperlink(True)
        # OSC 8 links carry a URL behind a descriptive label. Also recognize
        # printed addresses, which do not carry hyperlink escape sequences.
        url_regex = Vte.Regex.new_for_match(r'https?://[^\s<>"`]+', -1,
                                           0x00000400 | 0x00020000 | 0x00080000)
        tag = self.terminal.match_add_regex(url_regex, 0)
        self.terminal.match_set_cursor_name(tag, "pointer")
        self.terminal_link_press = None
        self.terminal.connect("button-press-event", self.terminal_pressed)
        self.terminal.connect("button-release-event", self.terminal_released)
        self.terminal.connect("motion-notify-event", self.terminal_motion)
        self.terminal.connect("key-press-event", self.terminal_keys)
        self.terminal.connect("button-press-event", self.terminal_clipboard_pressed)
        self.terminal.connect("popup-menu", self.terminal_popup)
        self.terminal_menu = Gtk.Menu()
        self.terminal_menu.attach_to_widget(self.terminal, None)
        self.copy_item = Gtk.MenuItem(label="Copy · Ctrl+Shift+C")
        self.copy_item.connect("activate", self.copy_terminal)
        self.terminal_menu.append(self.copy_item)
        self.paste_item = Gtk.MenuItem(label="Paste text · Ctrl+Shift+V")
        self.paste_item.connect("activate", self.paste_terminal)
        self.terminal_menu.append(self.paste_item)
        self.terminal_menu.show_all()
        self.terminal.connect("child-exited", self.child_exited)
        self.terminal_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.terminal_box.pack_start(self.zoom_controls("terminal", "Terminal text"), False, False, 0)
        self.terminal_box.pack_start(self.terminal, True, True, 0)
        self.paned.pack1(self.terminal_box, True, False)
        self.browser_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.browser_box.set_size_request(460, -1)
        self.paned.pack2(self.browser_box, True, False)
        controls = Gtk.Box(spacing=3)
        controls.get_style_context().add_class("chrome")
        self.tool_button(controls, "go-previous-symbolic", "Back", self.back)
        self.tool_button(controls, "go-next-symbolic", "Forward", self.forward)
        self.tool_button(controls, "go-home-symbolic", "Current task page", self.home)
        self.tool_button(controls, "view-list-symbolic", "Session overview", self.session_overview)
        self.address = Gtk.Entry()
        self.address.set_sensitive(False)
        self.address.set_placeholder_text("Enter a website address")
        self.address.connect("activate", self.navigate)
        self.address.connect("key-press-event", self.browser_keys)
        controls.pack_start(self.address, True, True, 5)
        self.tool_button(controls, "view-refresh-symbolic", "Reload", self.reload)
        self.tool_button(controls, "emblem-favorite-symbolic", "Save page title", self.save_page)
        self.tool_button(controls, "document-edit-symbolic", "Next action", self.next_action)
        self.browser_box.pack_start(controls, False, False, 0)
        self.tool_button(controls, "edit-find-symbolic", "Find in page (Ctrl+F)", self.open_find)
        self.find_bar = Gtk.Box(spacing=5)
        self.find_bar.get_style_context().add_class("chrome")
        self.find_entry = Gtk.SearchEntry()
        self.find_entry.set_placeholder_text("Find in this page")
        self.find_entry.connect("search-changed", self.search_page)
        self.find_entry.connect("activate", lambda *_: self.find_step())
        self.find_bar.pack_start(self.find_entry, True, True, 5)
        self.find_count = Gtk.Label(label="")
        self.find_bar.pack_start(self.find_count, False, False, 0)
        self.tool_button(self.find_bar, "go-up-symbolic", "Previous match (Shift+F3)",
                         lambda *_: self.find_step(backward=True))
        self.tool_button(self.find_bar, "go-down-symbolic", "Next match (F3 / Enter)",
                         lambda *_: self.find_step())
        self.tool_button(self.find_bar, "window-close-symbolic", "Close find (Esc)", self.close_find)
        self.browser_box.pack_start(self.find_bar, False, False, 0)
        self.find_bar.show_all()
        self.find_bar.hide()
        self.find_bar.set_no_show_all(True)
        self.connect("key-press-event", self.find_keys)
        ownership = Gtk.Box(spacing=12)
        ownership.get_style_context().add_class("chrome")
        task_label = Gtk.Label(label="Task:")
        task_label.get_style_context().add_class("quiet-label")
        ownership.pack_start(task_label, False, False, 0)
        self.task_picker = Gtk.ComboBoxText()
        self.task_picker.set_sensitive(False)
        self.task_picker.connect("changed", self.select_task)
        self.task_picker.set_tooltip_text(
            "Last recorded use: under 7 days, 7–30 days, earlier. "
            "Undated older tasks appear under Earlier. Changes only this window's task.")
        renderer = self.task_picker.get_cells()[0]
        renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
        renderer.set_property("max-width-chars", 18)
        self.task_picker.set_cell_data_func(renderer, self.render_task_row)
        ownership.pack_start(self.task_picker, True, True, 0)
        section_label = Gtk.Label(label="Section:")
        section_label.get_style_context().add_class("quiet-label")
        ownership.pack_start(section_label, False, False, 0)
        self.section_picker = Gtk.ComboBoxText()
        self.section_picker.set_tooltip_text("Optional subplan section")
        renderer = self.section_picker.get_cells()[0]
        renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
        renderer.set_property("max-width-chars", 18)
        self.section_picker.connect("changed", self.select_section)
        ownership.pack_start(self.section_picker, False, False, 0)
        self.tool_button(ownership, "folder-new-symbolic", "Add subplan section", self.add_section)
        self.browser_box.pack_start(ownership, False, False, 0)
        identity = Gtk.Box(spacing=20)
        identity.get_style_context().add_class("chrome")
        self.workspace_label = Gtk.Label(label="Workspace: Unavailable")
        self.workspace_label.set_xalign(0)
        self.workspace_label.set_ellipsize(Pango.EllipsizeMode.END)
        identity.pack_start(self.workspace_label, True, True, 0)
        self.status_label = Gtk.Label(label="Status: Unavailable")
        self.status_label.set_xalign(1)
        identity.pack_end(self.status_label, False, False, 0)
        self.browser_box.pack_start(identity, False, False, 0)
        tabs_row = Gtk.Box(spacing=3)
        tabs_row.get_style_context().add_class("chrome")
        self.tabs_box = Gtk.Box(spacing=3)
        self.tabs_box.get_style_context().add_class("page-tabs")
        tabs_row.pack_start(self.tabs_box, True, True, 0)
        # At most three visible page buttons; the picker handles overflow.
        self.page_picker = Gtk.ComboBoxText()
        renderer = self.page_picker.get_cells()[0]
        renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
        renderer.set_property("max-width-chars", 12)
        self.page_picker.set_tooltip_text("All page tabs in this section")
        self.page_picker.connect("changed", self.select_page)
        tabs_row.pack_start(self.page_picker, False, False, 0)
        self.tool_button(tabs_row, "list-add-symbolic", "New page tab", self.new_page)
        self.tool_button(tabs_row, "window-close-symbolic", "Close selected page tab", self.close_page)
        self.browser_box.pack_start(tabs_row, False, False, 0)
        self.browser_box.pack_start(self.zoom_controls("browser", "Page zoom"), False, False, 0)
        self.web_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.browser_box.pack_start(self.web_holder, True, True, 0)
        self.message = Gtk.Label(label="")
        self.message.set_xalign(0)
        self.message.set_line_wrap(True)
        self.message.get_style_context().add_class("quiet-label")
        self.browser_box.pack_start(self.message, False, False, 0)
        self.report_server = ReportServer(self.store)
        try:
            self.report_server.ensure()
        except (OSError, ValueError) as error:
            self.report(f"Task-page server unavailable: {error}")
        self.toggle.connect("toggled", self.toggle_browser)
        self.connect("delete-event", self.on_close)
        self.connect("focus-in-event", self.focus_received)
        self.show_all()
        self.paned.set_position(740)
        geometry = os.environ.pop("HERDR_BROWSER_RESTORE_WINDOW", None)
        if geometry:
            try:
                saved = json.loads(geometry)
                self.resize(int(saved["width"]), int(saved["height"]))
                self.move(int(saved["x"]), int(saved["y"]))
                self.paned.set_position(int(saved["split"]))
            except (ValueError, KeyError, TypeError):
                pass
        if enabled:
            self.enable_browser()
        else:
            self.browser_box.hide()
        self.poll_source = GLib.timeout_add(150, self.poll_context)
        env = dict(os.environ)
        for name in ("HERDR_TAB_ID", "HERDR_PANE_ID", "HERDR_WORKSPACE_ID",
                     "HERDR_CLIENT_SOCKET_PATH", "HERDR_ENV",
                     "KITTY_WINDOW_ID", "WEZTERM_PANE", "TERM_PROGRAM"):
            env.pop(name, None)
        if args.session:
            env.pop("HERDR_SOCKET_PATH", None)
        env["HERDR_BROWSER_CONTEXT_PATH"] = str(self.context_path)
        env["HERDR_BROWSER_HOME"] = str(self.store.home)
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        launcher = Path.home() / ".local/bin/herdr-browser"
        if launcher.is_file():
            import shlex
            env["BROWSER"] = shlex.quote(str(launcher)) + " open %s"
        command = [str(args.client)]
        if args.session:
            command += ["--session", args.session]
        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT, os.getcwd(), command,
            [f"{key}={value}" for key, value in env.items()],
            # VTE normally MERGES envv with the parent. Use only the complete
            # environment above, so removed pane/nesting markers stay absent.
            GLib.SpawnFlags(Vte.SPAWN_NO_PARENT_ENVV), None, None, -1, None,
            self.spawn_finished, None)

    def tool_button(self, box, icon, tooltip, callback):
        button = Gtk.Button()
        button.add(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU))
        button.set_tooltip_text(tooltip)
        button.get_accessible().set_name(tooltip)
        button.connect("clicked", callback)
        box.pack_start(button, False, False, 0)
        return button

    def find_keys(self, widget, event):
        # Keep terminal/editor Ctrl+F and Ctrl+G untouched.
        focus = self.get_focus()
        if not focus or not (focus is self.browser_box or focus.is_ancestor(self.browser_box)):
            return False
        modifiers = event.state & Gtk.accelerator_get_default_mod_mask()
        ctrl, shift = Gdk.ModifierType.CONTROL_MASK, Gdk.ModifierType.SHIFT_MASK
        key = Gdk.keyval_to_lower(event.keyval)
        if modifiers == ctrl and key == Gdk.KEY_f:
            return self.open_find()
        if ((key == Gdk.KEY_F3 and modifiers in (0, shift))
                or (key == Gdk.KEY_g and modifiers in (ctrl, ctrl | shift))):
            return self.find_step(backward=bool(modifiers & shift))
        if not self.find_bar.get_visible():
            return False
        if key == Gdk.KEY_Escape and modifiers == 0:
            return self.close_find()
        if focus is self.find_entry and key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and modifiers == shift:
            return self.find_step(backward=True)
        return False

    def open_find(self, *args):
        if self.view is None:
            return False
        self.find_bar.show()
        self.find_entry.grab_focus()
        self.find_entry.select_region(0, -1)
        self.search_page()
        return True

    def search_page(self, *args):
        if self.view is None or not self.find_bar.get_visible():
            return
        controller = self.view.get_find_controller()
        query = self.find_entry.get_text()
        controller.search_finish()
        if not query:
            self.find_count.set_text("")
            return
        self.find_count.set_text("Searching…")
        options = self.webkit.FindOptions.CASE_INSENSITIVE | self.webkit.FindOptions.WRAP_AROUND
        controller.search(query, options, 100000)

    def find_step(self, backward=False):
        if self.view is None:
            return False
        if not self.find_bar.get_visible():
            return self.open_find()
        if not self.find_entry.get_text():
            self.find_entry.grab_focus()
            return True
        controller = self.view.get_find_controller()
        if backward:
            controller.search_previous()
        else:
            controller.search_next()
        return True

    def close_find(self, *args, focus_page=True):
        if self.view is not None:
            self.view.get_find_controller().search_finish()
        self.find_bar.hide()
        self.find_count.set_text("")
        if focus_page and self.view is not None:
            self.view.grab_focus()
        return True

    def found_page_text(self, controller, count):
        if (self.view is not None and controller is self.view.get_find_controller()
                and self.find_bar.get_visible()):
            self.find_count.set_text(str(count) + (" match" if count == 1 else " matches"))

    def failed_page_find(self, controller):
        self.found_page_text(controller, 0)

    def browser_keys(self, entry, event):
        # Browser shortcuts apply only while its address field has focus.
        if event.state & Gdk.ModifierType.CONTROL_MASK:
            if event.keyval in (Gdk.KEY_t, Gdk.KEY_T):
                self.new_page()
                self.address.grab_focus()
                return True
            if event.keyval in (Gdk.KEY_w, Gdk.KEY_W):
                self.close_page()
                return True
        return False

    def zoom_controls(self, pane, title):
        row = Gtk.Box(spacing=3)
        row.get_style_context().add_class("chrome")
        label = Gtk.Label(label=title)
        label.set_xalign(0)
        row.pack_start(label, True, True, 0)
        buttons = []
        for text, action, tooltip in (
                ("−", -10, "Zoom out · Ctrl+minus"),
                (f"{self.zoom_levels[pane]}%", 0, "Reset to 100% · Ctrl+0"),
                ("+", 10, "Zoom in · Ctrl+plus (or Ctrl+equal)")):
            button = Gtk.Button(label=text)
            button.set_focus_on_click(False)
            button.set_tooltip_text(tooltip)
            button.get_accessible().set_name(f"{title}: {tooltip}")
            button.connect("clicked", lambda _button, delta=action: self.change_zoom(pane, delta))
            row.pack_start(button, False, False, 0)
            buttons.append(button)
        self.zoom_widgets[pane] = buttons
        self.update_zoom_controls(pane)
        return row

    def update_zoom_controls(self, pane):
        out, reset, zoom_in = self.zoom_widgets[pane]
        value = self.zoom_levels[pane]
        minimum, maximum = ZOOM_LIMITS[pane]
        reset.set_label(f"{value}%")
        out.set_sensitive(value > minimum)
        zoom_in.set_sensitive(value < maximum)

    def change_zoom(self, pane, delta):
        minimum, maximum = ZOOM_LIMITS[pane]
        value = 100 if delta == 0 else self.zoom_levels[pane] + delta
        value = max(minimum, min(maximum, value))
        try:
            self.store.set_zoom_percent(pane, value)
        except (OSError, ValueError) as error:
            self.report(f"Zoom applied, but could not save it: {error}")
        self.zoom_levels[pane] = value
        if pane == "terminal":
            self.terminal.set_font_scale(value / 100)
        elif self.view is not None:
            self.view.set_zoom_level(value / 100)
        self.update_zoom_controls(pane)

    def do_key_press_event(self, event):
        # Capture before Gtk.Window forwards keys to VTE/WebKit. Otherwise
        # the terminal child may consume them as application input.
        if hasattr(self, "terminal_box"):
            modifiers = event.state & Gtk.accelerator_get_default_mod_mask()
            ctrl, shift = Gdk.ModifierType.CONTROL_MASK, Gdk.ModifierType.SHIFT_MASK
            actions = {Gdk.KEY_plus: 10, Gdk.KEY_equal: 10, Gdk.KEY_KP_Add: 10,
                       Gdk.KEY_minus: -10, Gdk.KEY_KP_Subtract: -10,
                       Gdk.KEY_0: 0, Gdk.KEY_KP_0: 0}
            if modifiers in (ctrl, ctrl | shift) and event.keyval in actions:
                focus = self.get_focus()
                if focus is not None:
                    if focus == self.terminal_box or focus.is_ancestor(self.terminal_box):
                        self.change_zoom("terminal", actions[event.keyval])
                        return True
                    if focus == self.browser_box or focus.is_ancestor(self.browser_box):
                        self.change_zoom("browser", actions[event.keyval])
                        return True
        return Gtk.Window.do_key_press_event(self, event)

    def copy_terminal(self, *args):
        if self.terminal.get_has_selection():
            self.terminal.copy_clipboard_format(Vte.Format.TEXT)

    def paste_terminal(self, *args):
        self.terminal.paste_clipboard()

    def terminal_keys(self, terminal, event):
        self.acknowledge_terminal_view()
        modifiers = event.state & Gtk.accelerator_get_default_mod_mask()
        ctrl = Gdk.ModifierType.CONTROL_MASK
        shift = Gdk.ModifierType.SHIFT_MASK
        key = Gdk.keyval_to_lower(event.keyval)
        if ((modifiers == (ctrl | shift) and key == Gdk.KEY_c)
                or (modifiers == ctrl and key == Gdk.KEY_Insert)):
            self.copy_terminal()
            return True
        if ((modifiers == (ctrl | shift) and key == Gdk.KEY_v)
                or (modifiers == shift and key == Gdk.KEY_Insert)):
            self.paste_terminal()
            return True
        if modifiers == ctrl and key == Gdk.KEY_v:
            # Raw Ctrl+V reaches Herdr's image-paste handler. Consume it only
            # for text; image-only clipboard requests still reach the client.
            clipboard = terminal.get_clipboard(Gdk.SELECTION_CLIPBOARD)
            if clipboard.wait_is_text_available():
                self.paste_terminal()
                return True
        return False

    def terminal_popup(self, terminal, event=None):
        self.terminal_link_press = None
        clipboard = terminal.get_clipboard(Gdk.SELECTION_CLIPBOARD)
        self.copy_item.set_sensitive(terminal.get_has_selection())
        self.paste_item.set_sensitive(clipboard.wait_is_text_available())
        if event is None:
            self.terminal_menu.popup_at_widget(
                terminal, Gdk.Gravity.CENTER, Gdk.Gravity.CENTER, None)
        else:
            self.terminal_menu.popup_at_pointer(event)
        return True

    def terminal_clipboard_pressed(self, terminal, event):
        if event.get_button()[1] == 3 and event.type == Gdk.EventType.BUTTON_PRESS:
            return self.terminal_popup(terminal, event)
        return False

    def terminal_url(self, event):
        url = self.terminal.hyperlink_check_event(event)
        if not url:
            url, _ = self.terminal.match_check_event(event)
            if url:
                # Printed prose/Markdown can put punctuation just after a URL.
                url = url.rstrip(".,;!")
                for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
                    while url.endswith(closing) and url.count(closing) > url.count(opening):
                        url = url[:-1]
        if url:
            try:
                return checked_url(url)
            except ValueError:
                pass
        return None

    def terminal_pressed(self, terminal, event):
        self.acknowledge_terminal_view()
        self.terminal_link_press = None
        # Leave selection gestures, other buttons and non-website links alone.
        if (event.get_button()[1] == 1 and event.type == Gdk.EventType.BUTTON_PRESS
                and not event.state & (Gdk.ModifierType.SHIFT_MASK |
                                       Gdk.ModifierType.MOD1_MASK)):
            url = self.terminal_url(event)
            if url:
                self.terminal_link_press = (url, event.x, event.y, self.key, event.copy())
                # This gesture belongs to the companion. Passing the press to
                # the child TUI lets it launch the desktop browser as well.
                return True
        return False

    def terminal_motion(self, terminal, event):
        if self.terminal_link_press:
            _, x, y, _, original = self.terminal_link_press
            threshold = Gtk.Settings.get_default().get_property("gtk-dnd-drag-threshold")
            if abs(event.x - x) > threshold or abs(event.y - y) > threshold:
                self.terminal_link_press = None
                # A drag belongs to VTE, so replay the withheld press and let
                # normal selection/mouse reporting handle the rest.
                Vte.Terminal.do_button_press_event(terminal, original.button)
                return False
            return True
        return False

    def terminal_released(self, terminal, event):
        pressed, self.terminal_link_press = self.terminal_link_press, None
        if pressed and event.get_button()[1] == 1:
            url, x, y, key, original = pressed
            threshold = Gtk.Settings.get_default().get_property("gtk-dnd-drag-threshold")
            if abs(event.x - x) > threshold or abs(event.y - y) > threshold:
                Vte.Terminal.do_button_press_event(terminal, original.button)
                return False
            if (key == self.key
                    and self.terminal_url(event) == url):
                # Let VTE finish the mouse gesture before changing the browser.
                GLib.idle_add(self.open_terminal_url, url, key)
            return True
        return False

    def open_terminal_url(self, url, key):
        if self.closed:
            return False
        self.poll_context()
        if key != self.key:
            return False
        try:
            url = checked_url(url)
            if firefox_required(url):
                open_firefox(url)
                self.report("Opened S3 Browser in Firefox.")
            elif not self.toggle.get_active():
                open_external(url)
            elif self.key and self.engine is not None:
                row = self.store.task(key)
                known = find_page(row, url)
                section = known["section"] if known else self.section
                page = self.store.save_link(key, url, known["title"] if known else "", section)
                self.store.activate_page(key, section, page)
                same = self.section == section and self.active_page == page and self.view is not None
                self.section = section
                if not same:
                    self.restore_view(False)
                self.refresh_link_location(url)
                self.report("")
            else:
                self.report("Waiting for this window's Herdr task before opening the website.")
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            self.report(f"Website could not open: {error}")
        return False

    def refresh_link_location(self, url):
        """Apply anchors/report refresh versions inside the existing tab."""
        if self.view:
            current = self.view.get_uri() or ""
            if current and current != url and url_identity(current) == url_identity(url):
                self.view.load_uri(url)

    def report(self, message):
        self.message.set_text(message)

    def check_updates(self):
        if time.monotonic() < self.next_update_check:
            return
        self.next_update_check = time.monotonic() + 2
        changed = False
        try:
            for path, loaded in self.loaded_sources.items():
                stat = path.stat()
                stamp = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
                previous = self.update_stamps.get(path)
                if previous is None or previous[0] != stamp:
                    self.update_stamps[path] = (stamp, hashlib.sha256(path.read_bytes()).digest())
                changed |= self.update_stamps[path][1] != loaded
        except OSError:
            return
        self.update_available = changed
        self.update_button.set_sensitive(changed)
        self.update_button.set_label("Apply update" if changed else "Up to date")
        self.update_button.set_tooltip_text(
            "Restart this interface to apply installed updates. Herdr tasks keep running; "
            "browser logins and unsaved page state may reset." if changed else
            "Up to date. Installed updates will appear here.")

    def apply_update(self, *args):
        if not self.update_available or self.closed:
            return
        entry = Path(__file__).resolve().parent.parent / "launch.py"
        command = [sys.executable]
        if entry.is_file():
            command.append(str(entry))
        else:
            command += ["-m", __package__]
        command += ["--client", str(self.args.client),
                    "--browser" if self.toggle.get_active() else "--no-browser"]
        if self.args.session:
            command += ["--session", self.args.session]
        if self.context.get("available") and self.context.get("endpoint") == "local":
            command += ["--restore-tab", self.context["focused_tab_id"]]
        x, y = self.get_position()
        width, height = self.get_size()
        env = dict(os.environ, HERDR_SOCKET_PATH=self.connection,
                   HERDR_BROWSER_HOME=str(self.store.home),
                   HERDR_BROWSER_RESTORE_WINDOW=json.dumps({
                       "x": x, "y": y, "width": width, "height": height,
                       "split": self.paned.get_position()}))
        try:
            replacement = subprocess.Popen(command, env=env, start_new_session=True)
        except OSError as error:
            self.report(f"Update could not start: {error}")
            return
        self.on_close()
        self.destroy()
        return replacement

    def focus_received(self, *args):
        self.focused_at = time.time_ns()
        if self.key:
            self.store.touch_task(self.key)
        self.publish_instance()
        return False

    def publish_instance(self):
        enabled = bool(not self.closed and not self.client_exited and self.key
                       and self.toggle.get_active() and self.engine is not None)
        previous = self.instance_metadata["routing_enabled"]
        metadata = dict(self.instance_metadata, routing_enabled=enabled,
                        focused_at=self.focused_at, reports_url=self.report_server.base)
        if metadata != self.instance_metadata:
            atomic_json(self.instance, metadata)
            self.instance_metadata = metadata
        if enabled != previous:
            try:
                sync_defaults(self.store.home)
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                self.report(f"Desktop browser could not be restored: {error}")

    def spawn_finished(self, terminal, pid, error, data=None):
        if error:
            self.client_exited = True
            self.heading.set_text(f"Herdr could not start: {error}")
        else:
            self.client_pid = pid

    def child_exited(self, terminal, status):
        self.client_pid = None
        self.client_exited = True
        self.key = None
        self.destroy_view()
        self.heading.set_text("Herdr client exited · tasks remain in the Herdr session")
        self.address.set_sensitive(False)
        self.task_picker.set_sensitive(False)
        self.workspace_label.set_text("Workspace: Unavailable")
        self.status_label.set_text("Status: Unavailable")
        self.refresh_pages()
        self.publish_instance()

    def enable_browser(self):
        try:
            if self.engine is None:
                # --no-browser never imports or initializes WebKit.
                gi.require_version("WebKit2", "4.0")
                from gi.repository import WebKit2
                self.webkit = WebKit2
                # Each window owns cookies, so simultaneous windows never fight
                # over a disk profile. Logins last for this window's lifetime.
                self.engine = WebKit2.WebContext.new_ephemeral()
            self.browser_box.show_all()
            self.restore_view()
        except Exception as error:
            self.report(f"Browser unavailable: {error}. The terminal is still available.")
            self.browser_box.show_all()

    def destroy_view(self):
        # Detach first: WebKit can emit termination signals synchronously.
        # Intentional suspension must not look like a crash or write stale URLs.
        self.close_find(focus_page=False)
        view, self.view = self.view, None
        self.report_location = None
        if view is not None:
            view.stop_loading()
            view.terminate_web_process()
            view.destroy()
        self.home_html = None

    def toggle_browser(self, toggle):
        enabled = toggle.get_active()
        try:
            self.store.preference(enabled)
        except (ValueError, OSError) as error:
            self.report(str(error))
        if enabled:
            self.enable_browser()
        else:
            self.destroy_view()
            self.engine = None
            self.browser_box.hide()
            self.terminal.grab_focus()
        self.publish_instance()

    def restore_view(self, restore_section=True):
        self.destroy_view()
        self.page_signature = None
        if not self.key or not self.toggle.get_active() or self.engine is None:
            self.refresh_pages()
            return
        row = self.store.task(self.key)
        if restore_section:
            self.section = row["active_section"]
        state = row["sections"][self.section]
        if not state["last_url"] and restore_section:
            current = self.report_server.reports.primary(self.key)
            if current and current["status"] not in ("done", "archived"):
                url = self.report_server.task_url(self.key, current["id"])
                page = self.store.save_link(self.key, url, current["title"], "Task pages")
                self.store.activate_page(self.key, "Task pages", page)
                row = self.store.task(self.key)
                self.section = row["active_section"]
                state = row["sections"][self.section]
        self.active_page = state["active_page"]
        key = self.key
        view = self.webkit.WebView.new_with_context(self.engine)
        view.set_zoom_level(self.zoom_levels["browser"] / 100)
        manager = view.get_user_content_manager()
        manager.register_script_message_handler("herdrReview")
        manager.connect("script-message-received::herdrReview", self.conclusion_message, view)
        view.connect("load-changed", self.navigation_loaded)
        view.get_settings().set_enable_developer_extras(False)
        view.connect("notify::uri", self.uri_changed, key)
        view.connect("notify::title", self.title_changed, key)
        view.connect("load-failed", self.load_failed, key)
        view.connect("create", self.new_window, key)
        view.connect("decide-policy", self.navigation_policy)
        view.connect("web-process-terminated", self.web_terminated, key)
        self.view = view
        controller = view.get_find_controller()
        controller.connect("found-text", self.found_page_text)
        controller.connect("failed-to-find-text", self.failed_page_find)
        self.web_holder.pack_start(view, True, True, 0)
        view.show()
        if state["last_url"]:
            self.showing_home = False
            self.report_location = self.report_server.parse_url(state["last_url"])
            view.load_uri(state["last_url"])
        else:
            self.showing_home = True
            self.refresh_home()
        self.refresh_pages()

    def conclusion_message(self, manager, result, view):
        if view is not self.view:
            return
        nonce = None
        try:
            message = json.loads(result.get_js_value().to_string())
            nonce = message.get("nonce")
            location = self.report_server.parse_url(view.get_uri() or "")
            if (not isinstance(nonce, str) or len(nonce) > 100 or not location
                    or location["kind"] != "task" or message["owner"] != location["owner"]
                    or message["goal"] != location["goal"]):
                raise ValueError("Review must come from its displayed task report.")
            state = self.report_server.reports.get(message["owner"], message["goal"])
            if (not state or state["status"] == "archived" or not (state["summary"] or state["results"])
                    or conclusion_token(state) != message["token"]):
                raise ValueError("The conclusion changed; reopen its latest report.")
            self.store.mark_conclusion(message["owner"], message["goal"], message["token"], message["mode"])
            mark = self.store.task(message["owner"])["conclusion_marks"][message["goal"]]
            response = {"mark": mark, "persistent": True}
            view.run_javascript("window.HerdrReview?.nativeResult(" + json.dumps(nonce)
                                + "," + json.dumps(response) + ",null)", None, None, None)
            self.push_navigation_state(force=True)
        except (ValueError, TypeError, KeyError, OSError) as error:
            if isinstance(nonce, str):
                view.run_javascript("window.HerdrReview?.nativeResult(" + json.dumps(nonce)
                                    + ",null," + json.dumps(str(error)) + ")", None, None, None)

    def navigation_loaded(self, view, event):
        if view is self.view and event == self.webkit.LoadEvent.FINISHED:
            self.push_navigation_state(force=True)
            if self.find_bar.get_visible():
                self.search_page()

    def push_navigation_state(self, force=False):
        """Tiny shared read model; no extra process or live browser view."""
        if not self.key:
            return
        now = time.monotonic()
        if not force and now < getattr(self, "next_navigation_state", 0):
            return
        self.next_navigation_state = now + 2
        rows = self.report_server.navigation_rows(session_id(self.store.task(self.key))) or []
        spaces = session_snapshot(rows, include_completed=True)
        self.task_attention = {task["owner"]: (task["color"], task["attention"])
                               for space in spaces for task in space["tasks"]}
        self.unseen_finishes = {task["owner"] for space in spaces for task in space["tasks"]
                               if (task.get("finished_at") or 0) > (task.get("finished_viewed_at") or 0)}
        self.refresh_task_menu()
        selected = next((task for task in self.context.get("tasks", [])
                         if task["tab_id"] == self.context.get("focused_tab_id")), None)
        if selected and self.key:
            attention = self.task_attention.get(self.key)
            if attention:
                circle = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[attention[0]]
                self.status_label.set_text(circle + " " + attention[1])
                self.status_label.set_tooltip_text("Green: in progress. Yellow: new conclusion or input needed. Red: viewed, reviewed or idle.")
        location = self.report_server.parse_url(self.view.get_uri() or "") if self.view else None
        if not location:
            return
        displayed_session = location.get("session")
        if location["kind"] == "task":
            displayed_owner = self.store.task(location["owner"])
            displayed_session = session_id(displayed_owner) if displayed_owner.get("connection") else None
        own_session = session_id(self.store.task(self.key))
        if displayed_session and displayed_session != own_session:
            spaces = session_snapshot(self.report_server.navigation_rows(displayed_session) or [], include_completed=True)
        value = json.dumps({"spaces": spaces, "base": self.report_server.base}, ensure_ascii=True)
        if not force and value == getattr(self, "navigation_signature", None):
            return
        self.navigation_signature = value
        self.view.run_javascript("window.HerdrNativeSnapshot=" + value
            + ";window.HerdrReview?.nativeSnapshot(window.HerdrNativeSnapshot.spaces);"
              "dispatchEvent(new Event('herdr-session-snapshot'));", None, None, None)

    def acknowledge_terminal_view(self):
        if self.key and self.key in getattr(self, "unseen_finishes", set()):
            self.store.mark_task_viewed(self.key)
            self.unseen_finishes.discard(self.key)
            self.push_navigation_state(force=True)

    def refresh_home(self):
        if self.view and self.key and self.showing_home:
            content = task_home(self.store.task(self.key), self.section)
            if content != self.home_html:
                self.home_html = content
                self.view.load_html(content, None)

    def open_firefox_url(self, url):
        try:
            open_firefox(checked_url(url))
            self.report("Opened S3 Browser in Firefox.")
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            self.report(f"Firefox could not open: {error}")
        return False

    def navigation_policy(self, view, decision, policy_type):
        if policy_type in (self.webkit.PolicyDecisionType.NAVIGATION_ACTION,
                           self.webkit.PolicyDecisionType.NEW_WINDOW_ACTION):
            action = decision.get_navigation_action()
            uri = action.get_request().get_uri() or ""
            if firefox_required(uri):
                decision.ignore()
                GLib.idle_add(self.open_firefox_url, uri)
                return True
            if not uri.startswith(("http://", "https://", "about:blank")):
                decision.ignore()
                self.report("This minimal browser opens HTTP and HTTPS pages.")
                return True
            source = self.report_location
            if source:
                parsed = self.report_server.parse_url(uri)
                if parsed and parsed["kind"] == "task" and parsed != source:
                    decision.ignore()
                    GLib.idle_add(self.open_report_link, uri, parsed)
                    return True
                if not parsed and uri.startswith(("http://", "https://")):
                    decision.ignore()
                    GLib.idle_add(self.open_terminal_url, uri, self.key)
                    return True
        return False

    def uri_changed(self, view, param, key):
        if view is not self.view or key != self.key:
            return
        uri = view.get_uri() or ""
        if firefox_required(uri):
            return
        if uri.startswith(("http://", "https://")):
            self.showing_home = False
            self.address.set_text(uri)
            try:
                parsed = self.report_server.parse_url(uri)
                # Provisional URI notifications can precede navigation policy.
                # Keep the report's ownership until its routed page is selected.
                if self.report_location and parsed != self.report_location:
                    return
                if parsed and parsed["kind"] == "session":
                    self.active_page = None
                    self.refresh_pages()
                    return
                if self.active_page is None:
                    self.active_page = self.store.save_link(key, uri, "", self.section)
                    page = next(page for page in self.store.task(key)["links"]
                                if page["id"] == self.active_page)
                    self.section = page["section"]
                page = self.store.record_location(key, self.section, self.active_page, uri)
                if page:
                    self.active_page, self.section = page["id"], page["section"]
                    self.store.activate_page(key, self.section, self.active_page)
                self.refresh_pages()
            except (ValueError, OSError) as error:
                self.report(str(error))
        else:
            self.address.set_text("")

    def title_changed(self, view, param, key):
        if view is self.view and key == self.key and self.active_page:
            uri = view.get_uri() or ""
            if self.report_location and self.report_server.parse_url(uri) != self.report_location:
                return
            if uri.startswith(("http://", "https://")):
                try:
                    self.store.record_location(
                        key, self.section, self.active_page, uri, view.get_title())
                    self.refresh_pages()
                except (ValueError, OSError) as error:
                    self.report(str(error))

    def new_window(self, view, action, key):
        if view is self.view and key == self.key:
            uri = action.get_request().get_uri()
            try:
                parsed = self.report_server.parse_url(uri)
                if parsed and parsed["kind"] == "task":
                    GLib.idle_add(self.open_report_link, checked_url(uri), parsed)
                else:
                    GLib.idle_add(self.open_terminal_url, checked_url(uri), key)
            except ValueError as error:
                self.report(str(error))
        return None

    def load_failed(self, view, event, uri, error, key):
        if view is self.view and key == self.key:
            self.report(f"Page could not load: {error.message}")
        return False

    def web_terminated(self, view, reason, key):
        if view is self.view and key == self.key:
            self.report("Browser process stopped. Use Reload or turn Browser off and on.")

    def back(self, *args):
        if self.view:
            self.view.go_back()

    def forward(self, *args):
        if self.view:
            self.view.go_forward()

    def reload(self, *args):
        if self.view:
            self.report("")
            self.view.reload()
        elif self.key:
            self.restore_view()

    def home(self, *args):
        if self.key and self.view:
            current = self.report_server.reports.primary(self.key)
            if current:
                self.open_terminal_url(self.report_server.task_url(self.key, current["id"]), self.key)
                return
        self.new_page()

    def session_overview(self, *args):
        if self.key and self.view:
            url = self.report_server.session_url(self.store.task(self.key))
            self.report_location = self.report_server.parse_url(url)
            self.view.load_uri(url)

    def open_report_link(self, url, parsed):
        owner = parsed["owner"]
        known = self.report_server.reports.get(owner, parsed["goal"])
        target = next((task for task in self.context.get("tasks", [])
                       if task_key(self.connection, self.context, task["tab_id"]) == owner), None)
        if not target or (not known and parsed["goal"] != "start"):
            self.report("This report's Herdr task is no longer available in this session.")
            return False
        self.pending_route = {
            "schema": 1, "nonce": uuid.uuid4().hex, "created_at": time.time(),
            "endpoint": self.context["endpoint"], "boot_id": self.context["boot_id"],
            "tab_id": target["tab_id"], "key": owner, "url": url,
            "title": known["title"] if known else target["label"],
            "section": "Task pages", "focus": True, "background": False,
        }
        self.poll_routes()
        return False

    def new_page(self, *args):
        if self.view and self.key:
            try:
                self.store.activate_page(self.key, self.section)
                self.restore_view(False)
            except (ValueError, OSError) as error:
                self.report(str(error))

    def navigate(self, entry):
        if self.view and self.key:
            try:
                self.report("")
                url = checked_url(entry.get_text())
                if find_page(self.store.task(self.key), url):
                    self.open_terminal_url(url, self.key)
                    return
                self.showing_home = False
                self.view.load_uri(url)
            except ValueError as error:
                self.report(str(error))

    def save_page(self, *args):
        if self.view and self.key:
            try:
                uri = checked_url(self.view.get_uri() or "")
                if self.active_page is None:
                    self.active_page = self.store.save_link(
                        self.key, uri, self.view.get_title() or "", self.section)
                self.store.record_location(self.key, self.section, self.active_page,
                                           uri, self.view.get_title())
                self.refresh_pages()
                self.report(f"Page saved · {self.section}.")
            except (ValueError, OSError) as error:
                self.report(str(error))

    def next_action(self, *args):
        if not self.key:
            return
        key = self.key
        goal = (self.report_server.reports.get(key, self.report_location["goal"])
                if self.report_location and self.report_location.get("owner") == key
                else self.report_server.reports.primary(key))
        dialog = Gtk.Dialog(title="Next action for this task", transient_for=self,
                            flags=Gtk.DialogFlags.MODAL)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                           "Save", Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_text(goal["next_action"] if goal else self.store.task(key).get("next_action", ""))
        entry.set_size_request(450, -1)
        dialog.get_content_area().pack_start(entry, True, True, 15)
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            try:
                self.store.set_value(key, "next_action", entry.get_text())
                if goal:
                    self.report_server.reports.update(key, {"next_action": entry.get_text()}, goal=goal["id"])
                if key == self.key:
                    self.refresh_home()
            except (ValueError, OSError) as error:
                self.report(str(error))
        dialog.destroy()

    def refresh_pages(self):
        row = self.store.task(self.key) if self.key else {}
        self.section_picker.set_sensitive(bool(self.key))
        self.page_picker.set_sensitive(bool(self.key))
        self.section_names = list(row.get("sections", {}))
        self.section_picker.handler_block_by_func(self.select_section)
        self.section_picker.remove_all()
        for name in self.section_names:
            self.section_picker.append_text(name)
        if self.section in self.section_names:
            self.section_picker.set_active(self.section_names.index(self.section))
            self.section_picker.set_tooltip_text(f"Subplan section · {self.section}")
        self.section_picker.handler_unblock_by_func(self.select_section)
        links = [link for link in row.get("links", [])
                 if link["section"] == self.section]
        signature = (self.key, self.section, self.active_page,
                     tuple((link["id"], link["title"], link["url"]) for link in links),
                     self.showing_home)
        if signature == self.page_signature:
            return
        self.page_signature = signature
        self.links = links
        self.page_picker.handler_block_by_func(self.select_page)
        self.page_picker.remove_all()
        self.page_picker.append_text(f"All pages ({len(links)})")
        for link in links:
            self.page_picker.append_text(link["title"])
        self.page_picker.set_active(0)
        self.page_picker.handler_unblock_by_func(self.select_page)
        for child in self.tabs_box.get_children():
            child.destroy()
        self.page_buttons = []
        if not links or self.showing_home:
            home = Gtk.Button(label="Start page")
            home.get_style_context().add_class("active-page")
            home.connect("clicked", self.home)
            self.tabs_box.pack_start(home, True, True, 0)
        active_index = next((i for i, link in enumerate(links)
                             if link["id"] == self.active_page), 0)
        # Keep the active page visible without an unbounded horizontal strip.
        start = max(0, min(active_index - 1, len(links) - 3))
        limit = 2 if self.showing_home else 3
        for link in links[start:start + limit]:
            tab = Gtk.Box(spacing=0)
            tab.get_style_context().add_class("page-tab")
            button = Gtk.Button()
            label = Gtk.Label(label=link["title"])
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(12)
            button.add(label)
            button.set_tooltip_text(link["title"] + "\n" + link["url"])
            if link["id"] == self.active_page and not self.showing_home:
                button.get_style_context().add_class("active-page")
                tab.get_style_context().add_class("active-page")
            button.connect("clicked", self.open_page, link["id"])
            tab.pack_start(button, True, True, 0)
            close = Gtk.Button()
            close.add(Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU))
            close.get_style_context().add_class("tab-close")
            close.set_tooltip_text("Close " + link["title"])
            close.get_accessible().set_name("Close " + link["title"])
            close.connect("clicked", self.close_tab, self.key, link["id"])
            tab.pack_end(close, False, False, 0)
            self.tabs_box.pack_start(tab, True, True, 0)
            self.page_buttons.append(button)
        self.tabs_box.show_all()

    def select_page(self, picker):
        index = picker.get_active() - 1
        if self.view and 0 <= index < len(self.links):
            self.open_page(None, self.links[index]["id"])

    def open_page(self, button, page_id):
        if self.view and self.key and page_id != self.active_page:
            try:
                self.store.activate_page(self.key, self.section, page_id)
                self.restore_view(False)
            except (ValueError, OSError) as error:
                self.report(str(error))

    def close_page(self, *args):
        self.close_tab(None, self.key, self.active_page)

    def close_tab(self, button, key, page_id):
        if key and key == self.key and page_id:
            try:
                selected = page_id == self.active_page
                self.store.close_page(key, page_id)
                if selected:
                    self.store.activate_page(key, self.section)
                    self.restore_view(False)
                else:
                    self.refresh_pages()
                    self.refresh_home()
            except (ValueError, OSError) as error:
                self.report(str(error))

    def select_section(self, picker):
        index = picker.get_active()
        if self.key and 0 <= index < len(self.section_names):
            name = self.section_names[index]
            if name != self.section:
                try:
                    self.store.select_section(self.key, name)
                    self.section = name
                    self.restore_view(False)
                except (ValueError, OSError) as error:
                    self.report(str(error))

    def add_section(self, *args):
        if not self.key:
            return
        key = self.key
        dialog = Gtk.Dialog(title="Add subplan section", transient_for=self,
                            flags=Gtk.DialogFlags.MODAL)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Add", Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_placeholder_text("e.g. Research, Implementation, Review")
        entry.set_size_request(360, -1)
        entry.set_activates_default(True)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.get_content_area().pack_start(entry, True, True, 15)
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            try:
                name = self.store.add_section(key, entry.get_text())
                self.store.select_section(key, name)
                if key == self.key:
                    self.section = name
                    self.restore_view(False)
            except (ValueError, OSError) as error:
                self.report(str(error))
        dialog.destroy()

    def render_task_row(self, picker, renderer, model, iterator, data=None):
        header = model.get_value(iterator, 1) is None
        renderer.set_property("sensitive", not header)
        renderer.set_property("weight", Pango.Weight.BOLD if header else Pango.Weight.NORMAL)
        renderer.set_property("foreground", "#626771")
        renderer.set_property("foreground-set", header)

    def refresh_task_menu(self):
        context = self.context
        records = self.store.read()["tasks"]
        groups = grouped_tasks(context.get("tasks", []), records,
                               lambda task: task_key(self.connection, context, task["tab_id"]))
        labels = {row["workspace_id"]: row["label"] for row in context.get("workspaces", [])}
        rows = []
        for name, tasks in groups:
            rows.append((None, name if tasks else name + " · none"))
            for task, recorded in tasks:
                identity = task_key(self.connection, context, task["tab_id"])
                caption = task_caption(task, labels.get(task["workspace_id"], ""),
                                       getattr(self, "task_attention", {}).get(identity))
                rows.append((task["tab_id"], caption +
                             (" · date unavailable" if recorded is None else "")))
        signature = (context.get("focused_tab_id"), tuple(rows))
        if signature == self.task_menu_signature:
            return
        self.task_menu_signature = signature
        self.task_picker.handler_block_by_func(self.select_task)
        try:
            self.task_picker.remove_all()
            self.task_ids = [identity for identity, _ in rows]
            for identity, text in rows:
                self.task_picker.append(identity, text)
            selected = context.get("focused_tab_id")
            if selected in self.task_ids and selected is not None:
                self.task_picker.set_active(self.task_ids.index(selected))
        finally:
            self.task_picker.handler_unblock_by_func(self.select_task)

    def select_task(self, picker):
        index = picker.get_active()
        if not self.context.get("available") or not 0 <= index < len(self.task_ids):
            return
        tab_id = self.task_ids[index]
        if tab_id is None:
            self.task_menu_signature = None
            self.refresh_task_menu()
            return
        if tab_id == self.context.get("focused_tab_id"):
            return
        try:
            atomic_json(self.context_path.with_suffix(".request"), {
                "endpoint": self.context["endpoint"], "boot_id": self.context["boot_id"],
                "tab_id": tab_id, "nonce": uuid.uuid4().hex,
            })
            self.report("Switching this window's Herdr task…")
        except OSError as error:
            self.report(f"Could not switch the Herdr task: {error}")

    def poll_context(self):
        if self.closed:
            return False
        self.check_updates()
        try:
            self.report_server.ensure()
        except (OSError, ValueError) as error:
            self.report(f"Task-page server unavailable: {error}")
        if self.client_exited:
            return True
        try:
            if time.monotonic() >= self.next_task_menu_check:
                self.refresh_task_menu()
                self.next_task_menu_check = time.monotonic() + 60
            stat = self.context_path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            if stamp != self.last_stamp:
                context = json.loads(self.context_path.read_text())
                if context.get("schema") != 1:
                    raise ValueError("Unsupported client context")
                self.last_stamp = stamp
                self.context = context
                self.apply_context()
            stamp = self.store.path.stat().st_mtime_ns if self.store.path.exists() else 0
            if stamp != self.store_stamp:
                self.store_stamp = stamp
                self.refresh_pages()
                self.refresh_home()
                self.refresh_task_menu()
            self.poll_routes()
            self.push_navigation_state()
        except FileNotFoundError:
            if self.last_stamp is not None:
                self.last_stamp = None
                self.context = {}
                self.key = None
                self.destroy_view()
                self.address.set_sensitive(False)
                self.task_picker.set_sensitive(False)
                self.workspace_label.set_text("Workspace: Unavailable")
                self.status_label.set_text("Status: Unavailable")
                self.heading.set_text("Task selection unavailable")
                self.report("The client selection signal stopped. Relaunch this optional window.")
        except (OSError, ValueError, KeyError) as error:
            self.key = None
            self.destroy_view()
            self.address.set_sensitive(False)
            self.task_picker.set_sensitive(False)
            self.workspace_label.set_text("Workspace: Unavailable")
            self.status_label.set_text("Status: Unavailable")
            self.report(f"Task binding unavailable: {error}")
        self.publish_instance()
        return True

    def poll_routes(self):
        if not self.key or not self.toggle.get_active() or self.engine is None:
            return
        if self.pending_route is None:
            stamp = self.inbox.stat().st_mtime_ns
            if stamp == self.inbox_stamp:
                return
            self.inbox_stamp = stamp
            files = sorted(self.inbox.glob("*.json"))
            if not files:
                return
            path = files[0]
            try:
                self.pending_route = json.loads(path.read_text())
                if not isinstance(self.pending_route, dict):
                    raise ValueError("Invalid website request.")
            except (OSError, ValueError) as error:
                self.pending_route = None
                self.report(f"Website could not open: {error}")
                return
            finally:
                path.unlink(missing_ok=True)
            self.inbox_stamp = None
        request = self.pending_route
        context = self.context
        try:
            if request.get("schema") != 1 or (
                    request["endpoint"] != context["endpoint"]
                    or request["boot_id"] != context["boot_id"]
                    or request["key"] != task_key(self.connection, context, request["tab_id"])
                    or not any(task["tab_id"] == request["tab_id"] for task in context["tasks"])
                    or time.time() - request["created_at"] > 60):
                raise ValueError("Website request has expired or its task is unavailable.")
            url = checked_url(request["url"])
            if firefox_required(url):
                self.pending_route = None
                if not request.get("background"):
                    self.open_firefox_url(url)
                return
            key, section = request["key"], request["section"]
            if request["focus"] and key != self.key:
                if not request.get("focus_requested"):
                    atomic_json(self.context_path.with_suffix(".request"), {
                        "endpoint": context["endpoint"], "boot_id": context["boot_id"],
                        "tab_id": request["tab_id"], "nonce": request["nonce"],
                    })
                    request["focus_requested"] = True
                return
            self.pending_route = None
            page = self.store.save_link(key, url, request["title"], section)
            saved = next(link for link in self.store.task(key)["links"] if link["id"] == page)
            section = saved["section"]
            if request["background"]:
                self.refresh_pages()
                self.refresh_home()
                return
            self.store.activate_page(key, section, page)
            if key == self.key:
                same = (self.section == section and self.active_page == page
                        and self.view is not None)
                self.section = section
                if not same:
                    self.restore_view(False)
                self.refresh_link_location(url)
                if request["focus"]:
                    self.present()
                self.report("")
            # Background task URLs restore when that task is next selected.
        except (ValueError, KeyError, TypeError, OSError) as error:
            self.pending_route = None
            self.report(f"Website could not open: {error}")

    def apply_context(self):
        context = self.context
        available = context.get("available", False)
        if available and self.launch_tab:
            tab_id, self.launch_tab = self.launch_tab, None
            if context.get("endpoint") == "local" and any(
                    row["tab_id"] == tab_id for row in context.get("tasks", [])):
                if context.get("focused_tab_id") != tab_id:
                    atomic_json(self.context_path.with_suffix(".request"), {
                        "endpoint": context["endpoint"], "boot_id": context["boot_id"],
                        "tab_id": tab_id, "nonce": uuid.uuid4().hex,
                    })
                    self.report("Opening the task that launched this window…")
                    return
        selected = next((task for task in context.get("tasks", [])
                         if task["tab_id"] == context.get("focused_tab_id")), None)
        key = task_key(self.connection, context, selected["tab_id"]) if available and selected else None
        self.address.set_sensitive(key is not None)
        self.task_picker.set_sensitive(available)
        workspace_labels = {row["workspace_id"]: row["label"]
                            for row in context.get("workspaces", [])}
        if key:
            self.store.register_context(self.connection, context)
            if key != self.key:
                self.store.touch_task(key)
        self.refresh_task_menu()
        workspace = workspace_labels.get(selected["workspace_id"], "") if key else ""
        self.workspace_label.set_text(f'Workspace: {workspace or "Unavailable"}')
        self.workspace_label.set_tooltip_text(self.workspace_label.get_text())
        self.status_label.set_text(
            f'Status: {status_label(selected.get("agent_status") if key else None)}')
        self.status_label.set_tooltip_text("Agent activity reported by Herdr; experiment progress is on its report.")
        self.heading.set_text(task_caption(selected, workspace,
                              getattr(self, "task_attention", {}).get(key)) if key
                              else "Task unavailable · waiting for this Herdr client")
        self.heading.set_tooltip_text(
            self.heading.get_text() +
            f'   |   Machine: {context.get("endpoint_label", "")}   |   Session: {self.session_label}')
        self.set_title(self.heading.get_text() + " — Herdr")
        if key != self.key:
            self.key = key
            self.active_page = None
            self.report("")
            self.restore_view()
        else:
            self.refresh_pages()
            self.refresh_home()
        self.publish_instance()

    def on_close(self, *args):
        if self.closed:
            return False
        self.closed = True
        GLib.source_remove(self.poll_source)
        self.destroy_view()
        if self.client_pid:
            try:
                os.kill(self.client_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        self.instance.unlink(missing_ok=True)
        self.report_server.close()
        try:
            sync_defaults(self.store.home)
        except (ValueError, OSError, subprocess.SubprocessError):
            pass  # The handler also recovers after an interrupted close.
        self.runtime.cleanup()
        Gtk.StyleContext.remove_provider_for_screen(self.get_screen(), self.css)
        if Gtk.main_level():
            Gtk.main_quit()
        return False


def run(args):
    GLib.set_prgname(APP_ID)
    GLib.set_application_name("Herdr Browser")
    Gdk.set_program_class(APP_ID)
    if not Gtk.init_check()[0]:
        raise ValueError("A graphical desktop is required (GTK + VTE + system WebKit).")
    application = Gtk.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
    application.register(None)
    BrowserWindow(args, application)
    Gtk.main()
    return 0
