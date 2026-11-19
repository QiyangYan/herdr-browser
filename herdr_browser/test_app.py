"""Run only on a private Xvfb display; never inject events into the user's desktop."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import signal
import subprocess
import ctypes
from ctypes.util import find_library
from unittest.mock import Mock, patch

if os.environ.get("HERDR_BROWSER_TEST_DISPLAY") != "private":
    raise RuntimeError("Use an isolated Xvfb display for these native UI tests.")

from .app import BrowserWindow, GLib, Gtk
from .store import atomic_json, task_key
from .router import deliver
import gi
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk

if Gdk.Display.get_default().get_name() != os.environ.get("DISPLAY"):
    raise RuntimeError("GTK must use the private Xvfb display; set GDK_BACKEND=x11.")


def pump(seconds=0.3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.01)

def click_native_terminal(event, drag_dx=0):
    """Real X11 input, explicitly connected to the private display only."""
    display_name = os.environ["DISPLAY"]
    if Gdk.Display.get_default().get_name() != display_name:
        raise RuntimeError("Refusing to inject events outside the private display.")
    x11 = ctypes.CDLL(find_library("X11"))
    xtest = ctypes.CDLL(find_library("Xtst"))
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xtest.XTestFakeMotionEvent.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
    xtest.XTestFakeButtonEvent.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
    display = x11.XOpenDisplay(display_name.encode())
    if not display:
        raise RuntimeError("Could not connect to private Xvfb.")
    try:
        x, y = event.get_window().get_origin()[-2:]
        if not xtest.XTestFakeMotionEvent(display, -1, x + int(event.x), y + int(event.y), 0):
            raise RuntimeError("Could not move the private pointer.")
        xtest.XTestFakeButtonEvent(display, 1, 1, 0)
        x11.XFlush(display)
        pump(.05)
        if drag_dx:
            xtest.XTestFakeMotionEvent(display, -1, x + int(event.x) + drag_dx,
                                      y + int(event.y), 0)
            x11.XFlush(display)
            pump(.05)
        xtest.XTestFakeButtonEvent(display, 1, 0, 0)
        x11.XFlush(display)
        pump()
    finally:
        x11.XCloseDisplay(display)


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_home = os.environ.get("HERDR_BROWSER_HOME")
        os.environ["HERDR_BROWSER_HOME"] = self.tmp.name
        fake_client = Path(self.tmp.name) / "fake-client"
        fake_client.write_text("#!/bin/sh\nexec /bin/cat\n")
        fake_client.chmod(0o700)
        self.args = argparse.Namespace(client=fake_client, session="ui-test",
                                       no_browser=False, browser=True)
        self.window = BrowserWindow(self.args)
        self.addCleanup(self.close)
        self.context = {
            "schema": 1, "available": True, "endpoint": "local",
            "endpoint_label": "Local", "boot_id": "test-boot",
            "focused_tab_id": "t1", "focused_workspace_id": "w1",
            "tasks": [
                {"tab_id": "t1", "workspace_id": "w1", "label": "Evaluation",
                 "agent_status": "working"},
                {"tab_id": "t2", "workspace_id": "w1", "label": "Training",
                 "agent_status": "blocked"},
            ],
            "workspaces": [{"workspace_id": "w1", "label": "Private UI test"}],
        }
        atomic_json(self.window.context_path, self.context)
        pump(1.0)

    def close(self):
        self.window.on_close()
        self.window.destroy()
        pump(0.1)
        if self.old_home is None:
            os.environ.pop("HERDR_BROWSER_HOME", None)
        else:
            os.environ["HERDR_BROWSER_HOME"] = self.old_home

    def test_switch_disposes_old_view_and_rejects_late_uri(self):
        first_key, old_view = self.window.key, self.window.view
        self.assertIsNotNone(old_view)
        # Keep page metadata deterministic; a live external site may update its
        # title before this assertion on a faster network.
        report = self.window.report_server.reports.update(first_key, {}, new_title="First")
        url = self.window.report_server.task_url(first_key, report["id"])
        self.window.store.save_link(first_key, url, "First")
        self.context["focused_tab_id"] = "t2"
        atomic_json(self.window.context_path, self.context)
        pump()
        self.assertNotEqual(self.window.key, first_key)
        self.assertIsNot(self.window.view, old_view)
        # A late URI signal from the old task cannot save into the new task.
        self.window.uri_changed(old_view, None, first_key)
        self.assertEqual(self.window.store.task(self.window.key)["last_url"], "")
        self.context["focused_tab_id"] = "t1"
        atomic_json(self.window.context_path, self.context)
        pump()
        self.assertEqual(self.window.key, first_key)
        self.assertEqual(self.window.store.task(first_key)["links"][0]["title"], "First")

    def test_toggle_frees_view_and_restores_terminal_width(self):
        before = self.window.terminal.get_allocated_width()
        self.window.toggle.set_active(False)
        pump()
        self.assertIsNone(self.window.view)
        self.assertFalse(self.window.browser_box.get_visible())
        self.assertGreater(self.window.terminal.get_allocated_width(), before)
        self.assertFalse(self.window.store.read()["browser_enabled"])
        self.window.toggle.set_active(True)
        pump()
        self.assertIsNotNone(self.window.view)

    def test_task_picker_uses_private_request_and_waits_for_client_focus(self):
        original = self.window.key
        self.window.task_picker.set_active(self.window.task_ids.index("t2"))
        request = json.loads(self.window.context_path.with_suffix(".request").read_text())
        self.assertEqual(request["tab_id"], "t2")
        self.assertEqual(request["boot_id"], "test-boot")
        self.assertEqual(self.window.key, original)
        self.context["focused_tab_id"] = "t2"
        atomic_json(self.window.context_path, self.context)
        pump()
        self.assertEqual(self.window.key,
                         task_key(self.window.connection, self.context, "t2"))

    def test_recency_headers_are_disabled_and_refresh_keeps_actual_selection(self):
        third = {"tab_id": "t3", "workspace_id": "w1", "label": "Earlier task",
                 "agent_status": "idle"}
        self.context["tasks"].append(third)
        self.window.context = self.context
        self.window.store.register_context(self.window.connection, self.context)
        now = time.time()
        with self.window.store.transaction() as state:
            for identity, age in (("t1", 1), ("t2", 10), ("t3", 45)):
                key = task_key(self.window.connection, self.context, identity)
                state["tasks"][key]["last_activity_at"] = now - age * 86400
        self.window.refresh_task_menu()
        model = self.window.task_picker.get_model()
        self.assertEqual([row[0] for row in model if row[1] is None],
                         ["Last 7 days", "Last 1 month", "Earlier"])
        self.assertEqual(self.window.task_picker.get_active_id(), "t1")
        request = self.window.context_path.with_suffix(".request")
        self.assertFalse(request.exists())
        renderer = self.window.task_picker.get_cells()[0]
        self.window.render_task_row(self.window.task_picker, renderer, model,
                                    model.get_iter_first())
        self.assertFalse(renderer.get_property("sensitive"))
        self.window.task_picker.set_active(0)
        self.assertEqual(self.window.task_picker.get_active_id(), "t1")
        self.assertFalse(request.exists())
        self.window.task_picker.set_active(self.window.task_ids.index("t2"))
        self.assertEqual(json.loads(request.read_text())["tab_id"], "t2")

    def test_disconnect_clears_binding_and_keeps_terminal(self):
        self.context["available"] = False
        atomic_json(self.window.context_path, self.context)
        pump()
        self.assertIsNone(self.window.key)
        self.assertIsNone(self.window.view)
        self.assertFalse(self.window.address.get_sensitive())
        self.assertTrue(self.window.terminal.get_visible())

    def test_missing_signal_clears_the_previous_task(self):
        self.window.context_path.unlink()
        pump()
        self.assertIsNone(self.window.key)
        self.assertIsNone(self.window.view)
        self.assertFalse(self.window.address.get_sensitive())

    def test_launching_task_is_requested_before_binding_another_task(self):
        self.window.destroy_view()
        self.window.key = None
        self.window.launch_tab = "t2"
        self.window.apply_context()
        request = json.loads(self.window.context_path.with_suffix(".request").read_text())
        self.assertEqual(request["tab_id"], "t2")
        self.assertIsNone(self.window.key)
        self.context["focused_tab_id"] = "t2"
        atomic_json(self.window.context_path, self.context)
        pump()
        self.assertIsNotNone(self.window.key)

    def test_client_exit_cannot_rebind_stale_context(self):
        os.kill(self.window.client_pid, signal.SIGTERM)
        pump()
        self.assertTrue(self.window.client_exited)
        atomic_json(self.window.context_path, self.context)
        pump()
        self.assertIsNone(self.window.key)
        self.assertIsNone(self.window.view)
        self.assertFalse(self.window.task_picker.get_sensitive())

    def test_rendered_native_home(self):
        self.window.move(0, 0)
        pump(1.0)
        output = Path(os.environ.get("HERDR_BROWSER_SCREENSHOT", "/tmp/herdr-browser-native.png"))
        width = self.window.get_window().get_width()
        height = self.window.get_window().get_height()
        image = Gdk.pixbuf_get_from_window(self.window.get_window(), 0, 0, width, height)
        self.assertIsNotNone(image)
        image.savev(str(output), "png", [], [])
        self.assertGreater(output.stat().st_size, 1000)

    def test_section_and_page_switches_keep_one_view_and_ignore_old_events(self):
        key = self.window.key
        first = self.window.store.save_link(key, "https://example.com/first", "First", "Research")
        second = self.window.store.save_link(key, "https://example.com/second", "Second", "Research")
        self.window.store.add_section(key, "Review")
        self.window.refresh_pages()
        old = self.window.view
        self.window.section_picker.set_active(self.window.section_names.index("Research"))
        self.assertEqual(self.window.section, "Research")
        self.assertEqual(self.window.active_page, first)
        self.assertIsNot(self.window.view, old)
        self.window.open_page(None, second)
        self.assertEqual(self.window.active_page, second)
        research_view = self.window.view
        self.window.section_picker.set_active(self.window.section_names.index("Review"))
        self.assertTrue(self.window.showing_home)
        self.assertEqual(len(self.window.web_holder.get_children()), 1)
        self.window.uri_changed(research_view, None, key)
        self.window.title_changed(research_view, None, key)
        self.assertEqual(self.window.store.task(key)["sections"]["Review"]["last_url"], "")
        self.window.section_picker.set_active(self.window.section_names.index("Research"))
        self.assertEqual(self.window.active_page, second)
        self.window.close_page()
        self.assertTrue(self.window.showing_home)
        self.assertNotIn(second, [link["id"] for link in self.window.store.task(key)["links"]])

    def test_intentional_disposal_ignores_synchronous_termination_signal(self):
        real_view = self.window.view
        fake_view = Mock()
        fake_view.terminate_web_process.side_effect = lambda: self.window.web_terminated(
            fake_view, None, self.window.key)
        self.window.view = fake_view
        self.window.report("")
        self.window.destroy_view()
        self.assertEqual(self.window.message.get_text(), "")
        self.assertIsNone(self.window.view)
        fake_view.destroy.assert_called_once()
        self.window.view = real_view

    def test_routed_website_is_registered_and_visualized_automatically(self):
        url = "http://127.0.0.1:9/routing-test"
        self.assertTrue(deliver(url, "New report", "Review", focus=True))
        pump()
        self.assertEqual(self.window.section, "Review")
        row = self.window.store.task(self.window.key)
        self.assertEqual(row["last_url"], url)
        self.assertEqual(row["sections"]["Review"]["active_page"], self.window.active_page)
        self.assertEqual(len(self.window.web_holder.get_children()), 1)

    def test_agent_output_in_background_task_does_not_steal_selection(self):
        key, view = self.window.key, self.window.view
        url = "http://127.0.0.1:9/background-report"
        with patch.dict(os.environ, HERDR_TAB_ID="t2", HERDR_SOCKET_PATH=self.window.connection):
            self.assertTrue(deliver(url, "Training report", "Results"))
        pump()
        self.assertEqual(self.window.key, key)
        self.assertIs(self.window.view, view)
        other = task_key(self.window.connection, self.context, "t2")
        self.assertEqual(self.window.store.task(other)["last_url"], url)
        self.context["focused_tab_id"] = "t2"
        atomic_json(self.window.context_path, self.context)
        pump()
        self.assertEqual(self.window.section, "Results")
        self.assertEqual(self.window.store.task(other)["last_url"], url)

    def test_clicked_background_task_url_waits_for_verified_client_selection(self):
        url = "http://127.0.0.1:9/clicked-report"
        other = task_key(self.window.connection, self.context, "t2")
        self.window.store.save_link(other, url, "Training report", "Results")
        old_key = self.window.key
        self.assertTrue(deliver(url, focus=True))
        pump()
        self.assertEqual(self.window.key, old_key)
        request = json.loads(self.window.context_path.with_suffix(".request").read_text())
        self.assertEqual(request["tab_id"], "t2")
        self.context["focused_tab_id"] = "t2"
        atomic_json(self.window.context_path, self.context)
        pump()
        self.assertEqual(self.window.key, other)
        self.assertEqual(self.window.section, "Results")
        self.assertIsNone(self.window.pending_route)

    def test_invalid_routing_payload_does_not_clear_task_binding(self):
        key = self.window.key
        atomic_json(self.window.inbox / "invalid.json", ["not", "a", "request"])
        pump()
        self.assertEqual(self.window.key, key)
        self.assertIsNotNone(self.window.view)

    def terminal_link_event(self, text, url):
        terminal = self.window.terminal
        terminal.reset(True, True)
        terminal.feed(("\x1b[2J\x1b[H" + text + "\r\n").encode())
        pump(0.2)
        event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        event.button = 1
        event.state = Gdk.ModifierType(0)
        # VTE receives events through its input-only child window, which is
        # distinct from get_window(). Match that actual event window.
        for window in terminal.get_window().get_children():
            event.window = window
            for y in range(2, terminal.get_char_height() * 2, 4):
                for x in range(2, min(terminal.get_allocated_width(), 160), 4):
                    event.x, event.y = x, y
                    if self.window.terminal_url(event) == url:
                        return event
        self.fail("The actual terminal did not detect the rendered URL")

    def click_terminal(self, event):
        pressed = self.window.terminal.emit("button-press-event", event)
        released = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
        released.window = event.window
        released.button, released.state = event.get_button()[1], event.state
        released.x, released.y = event.x, event.y
        handled = self.window.terminal.emit("button-release-event", released)
        pump()
        return pressed, handled

    def test_terminal_named_link_opens_beside_its_source_task(self):
        url = "http://127.0.0.1:9/named-report"
        text = f"\x1b]8;;{url}\x1b\\View the change summary\x1b]8;;\x1b\\"
        other = task_key(self.window.connection, self.context, "t2")
        self.window.store.save_link(other, url, "Other task's report", "Other section")
        key = self.window.key
        event = self.terminal_link_event(text, url)
        self.assertEqual(self.click_terminal(event), (True, True),
                         "Both click events must stop before reaching the child TUI")
        self.assertEqual(self.window.key, key)
        self.assertFalse(self.window.showing_home)
        self.assertEqual(self.window.store.task(key)["last_url"], url)
        self.assertFalse(self.window.context_path.with_suffix(".request").exists())
        self.assertEqual(len(self.window.web_holder.get_children()), 1)

    def javascript(self, expression):
        results = []
        def finished(view, result, _):
            results.append(view.run_javascript_finish(result).get_js_value().to_string())
        self.window.view.run_javascript(expression, None, finished, None)
        deadline = time.monotonic() + 5
        while not results and time.monotonic() < deadline:
            pump(.05)
        self.assertTrue(results, "Native report JavaScript completed")
        return results[0]

    def test_living_page_updates_without_reload_and_preserves_section_and_scroll(self):
        reports = self.window.report_server.reports
        text = "\n\n".join(f"Result {i}: " + "measured details " * 20 for i in range(30))
        figure = {"title": "Synthetic test figure", "unit": "Test episodes", "maximum": 2,
                  "values": [{"label": "Fixture", "value": 1, "note": "Synthetic fixture"}],
                  "caption": "Test data only."}
        curve = {"title": "Synthetic test curve", "caption": "Synthetic fixture only.",
                 "x_label": "Updates", "y_label": "Success (%)", "x_maximum": 200,
                 "y_maximum": 100, "gap": 100,
                 "series": [{"label": "Fixture", "points": [{"x": 100, "y": 50}]}]}
        state = reports.update(self.window.key, {"summary": "Initial", "results": text,
                                                "figures": [figure], "curves": [curve]},
                               new_title="Live report")
        url = self.window.report_server.task_url(self.window.key, state["id"])
        self.window.store.save_link(self.window.key, url, state["title"], "Task pages")
        self.window.open_terminal_url(url, self.window.key)
        pump(.5)
        self.javascript("""(() => {
          document.querySelector("#full-results").open = true;
          window.retained = {
            work:document.querySelector('#current-work'),
            curve:document.querySelector('.learning-curve'),
            holder:document.querySelector('.ic-holder'),
            stage:document.querySelector('.ic-stage'),
            input:document.querySelector('.ic-min'),
            ranges:document.querySelector('.ic-range-controls'),
            results:document.querySelector('#full-results'), disconnected:0
          };
          retained.ranges.open = true;
          retained.input.value = '7.25'; retained.input.focus({preventScroll:true});
          new MutationObserver(records => {
            for (const record of records) for (const node of record.removedNodes)
              if (node === retained.curve || node.contains?.(retained.curve)) retained.disconnected++;
          }).observe(document.querySelector('#task-content'),{childList:true,subtree:true});
          scrollTo(0,400); return 'ready';
        })()""")
        position = float(self.javascript("String(scrollY)"))
        view, page = self.window.view, self.window.active_page
        updated_figure = dict(figure, values=[{"label": "Fixture", "value": 2, "note": "Synthetic fixture"}])
        updated_curve = dict(curve, series=[{"label": "Fixture", "points": [{"x": 100, "y": 50},
                                                                         {"x": 200, "y": 75}]}])
        reports.update(self.window.key, {"summary": "Updated silently", "results": text + "\n\nNew result",
                                        "figures": [updated_figure], "curves": [updated_curve]})
        pump(3.4)
        self.assertIs(self.window.view, view)
        self.assertEqual(self.window.active_page, page)
        self.assertEqual(self.javascript("document.querySelector('#task-content').dataset.revision"), "2")
        self.assertEqual(self.javascript("String(document.querySelector('#full-results').open)"), "true")
        self.assertEqual(self.javascript("document.querySelector('.bar-value').textContent"), "2 / 2")
        self.assertEqual(self.javascript("String(document.querySelectorAll('.ic-series circle').length)"), "2")
        self.assertEqual(self.javascript("""String(
          retained.work === document.querySelector('#current-work') &&
          retained.curve === document.querySelector('.learning-curve') &&
          retained.holder === document.querySelector('.ic-holder') &&
          retained.stage === document.querySelector('.ic-stage') &&
          retained.ranges.open && retained.input === document.activeElement &&
          retained.input.value === '7.25' && retained.disconnected === 0 &&
          retained.results === document.querySelector('#full-results'))"""), "true",
                         "New measurements must preserve the chart, controls, draft input and open sections")
        self.assertAlmostEqual(float(self.javascript("String(scrollY)")), position, delta=2)
        self.assertEqual(len(self.window.web_holder.get_children()), 1)
        self.assertEqual(len(self.window.store.task(self.window.key)["links"]), 1)
        self.window.new_page()
        pump()
        self.assertTrue(self.window.showing_home)
        self.window.home()
        pump()
        self.assertEqual(self.window.view.get_uri(), url)

    def test_dashboard_home_and_history_use_one_view_without_extra_saved_pages(self):
        from .dashboard import Dashboard
        pump(.5)
        self.assertEqual(self.javascript("String(document.querySelectorAll('#session-dashboard').length)"), "1")
        self.assertFalse(self.window.store.task(self.window.key)["links"])
        reports = self.window.report_server.reports
        reports.update(self.window.key, {"title": "Dashboard test", "summary": "Recorded work"}, goal="dashboard")
        dashboard = Dashboard(reports)
        dashboard.upsert(self.window.key, "dashboard", "checks", {
            "agent_id": "/example/checks", "agent_label": "Checks",
            "task": "Keyboard verification", "status": "complete", "summary": "Passed."})
        self.window.home()
        pump(.5)
        view = self.window.view
        self.assertEqual(self.javascript("String(document.querySelectorAll('#session-dashboard tbody tr').length)"), "2")
        self.javascript("document.querySelector('[data-dashboard-id=\"checks\"] .sd-remove').click(); 'clicked'")
        pump(.4)
        self.assertEqual(len(dashboard.snapshot(self.window.key, "dashboard")["history"]), 1)
        self.window.view.reload()
        pump(.5)
        self.assertEqual(self.javascript("document.querySelector('#dashboard-history summary').textContent"), "Dashboard history (1)")
        self.javascript("document.querySelector('#dashboard-history').open=true; document.querySelector('[data-dashboard-focus=\"restore:checks\"]').click(); 'restored'")
        pump(.4)
        self.assertFalse(dashboard.snapshot(self.window.key, "dashboard")["history"])
        self.assertIs(self.window.view, view)
        self.assertEqual(len(self.window.store.task(self.window.key)["links"]), 1)

    def test_dashboard_refresh_keeps_controls_and_nested_charts_stable(self):
        from .dashboard import Dashboard
        reports = self.window.report_server.reports
        curve = {"title": "Recorded fixture", "caption": "Synthetic test values.",
                 "x_label": "Updates", "y_label": "Score", "x_maximum": 2, "y_maximum": 1, "gap": 1,
                 "series": [{"label": "Fixture", "points": [{"x": 1, "y": .5}]}]}
        reports.update(self.window.key, {
            "title": "Refresh fixture", "summary": "Initial report", "curves": [curve],
            "sections": [{"id": "evidence", "title": "Evidence", "text": "Test records.",
                          "curves": [dict(curve, title="Appendix fixture")]}],
            "main_sections": [{"title": "Recorded result", "text": "See [evidence](#evidence).",
                               "sections": ["evidence"], "curves": [dict(curve, title="Main fixture")]}],
        }, goal="refresh")
        dashboard = Dashboard(reports)
        record = dict(task="Keyboard checks", agent_id="/fixture/checks", agent_label="Checks",
                      status="complete", summary="Initial result", updated_at="2026-11-01T10:00:00Z")
        dashboard.upsert(self.window.key, "refresh", "checks", record)
        dashboard.upsert(self.window.key, "refresh", "archived", dict(record, task="Earlier checks"))
        dashboard.change(self.window.key, "refresh", "archived", "archive")
        self.window.home()
        pump(.8)
        self.javascript("""(() => {
          const root = document.querySelector('#task-content');
          const chart = root.querySelector('#current-work > .learning-curve');
          const wrapper = document.createElement('div'); chart.before(wrapper); wrapper.append(chart);
          // A 200 response can also carry a review update with the same report
          // revision. Exercise that path instead of relying only on 304s.
          const original = window.fetch.bind(window);
          window.fetch = (url, options) => String(url).endsWith('/data') ?
            original(url, {cache:'no-store'}) : original(url, options);
          window.kept = {header:root.querySelector('header'),
            host:document.querySelector('#session-dashboard'),
            row:document.querySelector('[data-dashboard-id="checks"]'),
            history:document.querySelector('#dashboard-history'),
            button:document.querySelector('#dashboard-history button'),
            disconnected:0};
          new MutationObserver(records => {
            for (const record of records) for (const node of record.removedNodes)
              if (node === kept.host || node.contains?.(kept.host)) kept.disconnected++;
          }).observe(root,{childList:true,subtree:true});
          kept.history.open = true; kept.button.focus({preventScroll:true});
          kept.host.scrollIntoView();
          return 'ready';
        })()""")
        position = float(self.javascript("String(scrollY)"))
        pump(3.3)
        self.assertEqual(self.javascript("String(kept.header === document.querySelector('#task-content header'))"), "true",
                         "A nested chart must not trigger a same-revision rebuild")
        reports.update(self.window.key, {"summary": "New recorded report"}, goal="refresh")
        dashboard.upsert(self.window.key, "refresh", "checks",
                         dict(record, status="running", summary="New recorded result",
                              updated_at="2026-11-01T10:01:00Z"))
        pump(5.5)
        stable = self.javascript("""String(
          kept.host === document.querySelector('#session-dashboard') &&
          kept.row === document.querySelector('[data-dashboard-id="checks"]') &&
          kept.history === document.querySelector('#dashboard-history') &&
          kept.history.open && kept.button === document.activeElement &&
          kept.disconnected === 0)""")
        self.assertEqual(stable, "true", "Refresh must preserve mounted controls, focus and open history")
        self.assertEqual(self.javascript("kept.row.querySelector('.sd-detail').textContent"), "New recorded result")
        self.assertEqual(self.javascript("kept.row.querySelector('.sd-status').dataset.status"), "running")
        self.assertEqual(self.javascript("String(kept.row.querySelector('.sd-remove').hidden)"), "true")
        self.assertEqual(self.javascript("document.querySelector('#task-content').dataset.revision"), "2")
        self.assertAlmostEqual(float(self.javascript("String(scrollY)")), position, delta=2)
        self.assertEqual(self.javascript("String(document.querySelectorAll('.ic-holder').length)"), "2")
        self.javascript("document.querySelector('#evidence').open=true; 'opened'")
        pump(.2)
        self.assertEqual(self.javascript("String(document.querySelectorAll('.ic-holder').length)"), "3")

    def test_closed_appendix_chart_uses_latest_data_when_opened_and_reopened(self):
        reports = self.window.report_server.reports
        curve = {"title": "Appendix measurements", "caption": "Synthetic fixture.",
                 "x_label": "Updates", "y_label": "Score", "x_maximum": 3,
                 "y_maximum": 1, "gap": 1,
                 "series": [{"label": "Fixture", "points": [{"x": 1, "y": .5}]}]}
        def publish(count):
            points = [{"x": i + 1, "y": .5} for i in range(count)]
            reports.update(self.window.key, {"summary": "Read [details](#measurements).",
                "sections": [{"id": "measurements", "title": "Measurements", "text": "Recorded data.",
                              "curves": [dict(curve, series=[{"label": "Fixture", "points": points}])]}]},
                goal="deferred", new_title="Deferred chart" if count == 1 else None)
        publish(1)
        self.window.home()
        pump(.7)
        self.assertEqual(self.javascript("String(document.querySelectorAll('.ic-holder').length)"), "0")
        publish(2)
        pump(3.4)
        self.assertEqual(self.javascript("String(document.querySelectorAll('.ic-holder').length)"), "0")
        self.javascript("location.hash='curve-measurements-1'; 'open exact chart'")
        pump(.3)
        self.assertEqual(self.javascript("String(document.querySelectorAll('.ic-series circle').length)"), "2")
        self.javascript("window.chartHolder=document.querySelector('.ic-holder'); document.querySelector('#measurements').open=false; 'closed'")
        pump(.2)
        publish(3)
        pump(3.4)
        self.assertEqual(self.javascript("String(document.querySelectorAll('.ic-series circle').length)"), "2")
        self.javascript("document.querySelector('#measurements').open=true; 'reopened'")
        pump(.3)
        self.assertEqual(self.javascript("String(document.querySelectorAll('.ic-series circle').length)"), "3")
        self.assertEqual(self.javascript("String(chartHolder===document.querySelector('.ic-holder'))"), "true")

    def test_report_refresh_adds_removes_and_reorders_rows_and_sections(self):
        reports = self.window.report_server.reports
        table = dict(title="Recorded checks", columns=["Check", "Result"],
                     rows=[["A", "Initial"], ["B", "Initial"]], caption="Synthetic records.")
        reports.update(self.window.key, {
            "title": "Structure fixture", "summary": "First snapshot", "tables": [table],
            "sections": [{"id": "old-record", "title": "Earlier record", "text": "Old detail"}],
        }, goal="structure")
        self.window.home()
        pump(.8)
        self.javascript("""window.rowB = document.querySelector('figure.comparison tbody').children[1];
          window.reportHeader = document.querySelector('#overview'); 'ready'""")
        reports.update(self.window.key, {
            "summary": "New snapshot",
            "tables": [dict(table, rows=[["B", "Updated"], ["C", "Added"], ["A", "Retained"]])],
            "sections": [{"id": "new-record", "title": "Current record", "text": "New detail"}],
        }, goal="structure")
        pump(3.4)
        self.assertEqual(self.javascript("String(reportHeader===document.querySelector('#overview'))"), "true")
        self.assertEqual(self.javascript("String(rowB===document.querySelector('figure.comparison tbody').firstElementChild)"), "true")
        self.assertEqual(self.javascript("JSON.stringify([...document.querySelectorAll('figure.comparison tbody tr')].map(row=>row.textContent))"),
                         '["BUpdated","CAdded","ARetained"]')
        self.assertEqual(self.javascript("String(!document.querySelector('#old-record') && !!document.querySelector('#new-record'))"), "true")
        self.assertEqual(self.javascript("String(document.querySelectorAll('figure.comparison tr[data-diagram-bound]').length)"), "3")
        self.javascript("document.querySelector('figure.comparison tbody').children[1].focus(); 'focused'")
        self.assertIn("Added", self.javascript("document.querySelector('figure.comparison > .ic-readout').textContent"))
        reports.update(self.window.key, {"tables": [dict(table, rows=[["B", "Final"]])]}, goal="structure")
        pump(3.3)
        self.assertEqual(self.javascript("String(document.querySelectorAll('figure.comparison tbody tr').length)"), "1")
        self.assertEqual(self.javascript("String(rowB===document.querySelector('figure.comparison tbody').firstElementChild)"), "true")
        self.assertEqual(self.javascript("rowB.textContent"), "BFinal")

    def test_session_index_switches_verified_task_and_reference_keeps_hub(self):
        reports = self.window.report_server.reports
        owner = self.window.key
        first = reports.update(owner, {"references": [
            {"title": "Source", "url": self.window.report_server.base + "/reference"}]}, new_title="First outcome")
        url = self.window.report_server.task_url(owner, first["id"])
        self.window.store.save_link(owner, url, "First outcome", "Task pages")
        self.window.open_terminal_url(url, owner)
        pump(.5)
        self.javascript('setTimeout(() => document.querySelector(".linked-pages a").click(), 50); "scheduled"')
        pump(.4)
        saved = self.window.store.task(owner)["links"]
        self.assertEqual(next(page for page in saved if page["title"] == "First outcome")["url"], url)
        self.assertEqual(len(saved), 2)
        other = task_key(self.window.connection, self.context, "t2")
        second = reports.update(other, {"summary": "Another task"}, new_title="Second outcome")
        other_url = self.window.report_server.task_url(other, second["id"])
        self.window.store.save_link(other, other_url, "Second outcome", "Task pages")
        self.window.session_overview()
        pump(.5)
        self.assertEqual(self.window.key, owner)
        self.javascript(f'setTimeout(() => document.querySelector("a[href=\\"{other_url}\\"]").click(), 50); "scheduled"')
        pump()
        request = json.loads(self.window.context_path.with_suffix(".request").read_text())
        self.assertEqual(request["tab_id"], "t2")
        self.assertEqual(self.window.key, owner)
        self.context["focused_tab_id"] = "t2"
        atomic_json(self.window.context_path, self.context)
        pump(.5)
        self.assertEqual(self.window.key, other)
        self.assertEqual(self.window.view.get_uri(), other_url)
        self.assertNotIn(other_url, [page["url"] for page in self.window.store.task(owner)["links"]])

    def click_website(self, selector):
        """Click the rendered link with a real pointer on the private display."""
        position = json.loads(self.javascript(f"""(() => {{
            const node = document.querySelector({json.dumps(selector)});
            node.scrollIntoView({{block: 'center'}});
            const rect = node.getBoundingClientRect();
            return JSON.stringify([rect.x + rect.width / 2, rect.y + rect.height / 2]);
        }})()"""))
        x, y = self.window.view.translate_coordinates(self.window, 0, 0)
        event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        event.window = self.window.get_window()
        event.x, event.y = x + position[0], y + position[1]
        event.button = 1
        click_native_terminal(event)
        pump(.5)

    def test_website_link_opens_saved_report_without_reassigning_its_owner(self):
        owner = self.window.key
        reports = self.window.report_server.reports
        previous = dict(self.context, boot_id="previous-server-boot")
        self.window.store.register_context(self.window.connection, previous)
        previous_owner = task_key(self.window.connection, previous, "t1")
        reports.update(previous_owner, {"summary": "Recorded earlier"},
                       goal="saved", new_title="Saved outcome")
        saved_url = self.window.report_server.task_url(previous_owner, "saved")
        reports.update(owner, {"references": [{"title": "Saved report", "url": saved_url}]},
                       goal="source", new_title="Current outcome")
        source_url = self.window.report_server.task_url(owner, "source")
        for target in ("_self", "_blank"):
            with self.subTest(target=target):
                self.window.open_terminal_url(source_url, owner)
                pump(.5)
                self.javascript(f'document.querySelector(".linked-pages a").target={json.dumps(target)}; "ready"')
                self.click_website(".linked-pages a")
                self.assertEqual(self.window.view.get_uri(), saved_url)
                self.assertEqual(self.javascript("document.querySelector('h1').textContent"), "Saved outcome")
                self.assertEqual(self.window.key, owner)
                self.assertFalse(self.window.context_path.with_suffix(".request").exists())
                self.assertEqual(reports.get(previous_owner, "saved")["owner"], previous_owner)
                self.assertIsNone(reports.get(owner, "saved"))
                self.assertEqual({row["url"] for row in self.window.store.task(owner)["links"]},
                                 {source_url, saved_url})

    def test_website_section_link_opens_and_scrolls_to_closed_appendix(self):
        owner = self.window.key
        self.window.report_server.reports.update(owner, {
            "summary": "Read the [saved settings](#settings).",
            "sections": [{"id": "settings", "title": "Saved settings", "text": "Recorded value: 42."}],
        }, goal="sections", new_title="Section navigation")
        url = self.window.report_server.task_url(owner, "sections")
        self.window.open_terminal_url(url, owner)
        pump(.5)
        self.javascript("document.querySelector('#settings').open=false; 'closed'")
        view = self.window.view
        self.click_website('#task-summary a[href="#settings"]')
        self.assertIs(self.window.view, view)
        self.assertEqual(self.javascript("location.hash"), "#settings")
        self.assertEqual(self.javascript("String(document.querySelector('#settings').open)"), "true")
        self.assertEqual(self.javascript("""String((() => {
            const box = document.querySelector('#settings summary').getBoundingClientRect();
            return box.top >= 0 && box.top < innerHeight;
        })())"""), "true")

    def test_website_reference_click_loads_destination_and_preserves_report(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading

        class Website(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<title>Reference website</title><h1>Reference website</h1>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Website)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        destination = f"http://127.0.0.1:{server.server_port}/reference"
        owner = self.window.key
        self.window.report_server.reports.update(owner, {
            "references": [{"title": "Open reference website", "url": destination}],
        }, goal="reference-source", new_title="Reference source")
        source_url = self.window.report_server.task_url(owner, "reference-source")
        for target in ("_self", "_blank"):
            with self.subTest(target=target):
                self.window.open_terminal_url(source_url, owner)
                pump(.5)
                self.javascript(f'document.querySelector(".linked-pages a").target={json.dumps(target)}; "ready"')
                self.click_website(".linked-pages a")
                self.assertEqual(self.window.view.get_uri(), destination)
                self.assertEqual(self.window.view.get_title(), "Reference website")
                self.assertEqual({row["url"] for row in self.window.store.task(owner)["links"]},
                                 {source_url, destination})
                self.assertEqual(len(self.window.web_holder.get_children()), 1)

    def test_terminal_printed_url_and_existing_section_are_preserved(self):
        url = "http://127.0.0.1:9/printed-report"
        self.window.store.save_link(self.window.key, url, "Report", "Review")
        event = self.terminal_link_event(f"({url}).", url)
        self.click_terminal(event)
        self.assertEqual(self.window.section, "Review")
        self.assertFalse(self.window.showing_home)
        self.assertEqual(len(self.window.store.task(self.window.key)["links"]), 1)

    def test_repeated_terminal_and_agent_links_preserve_original_live_tab(self):
        url = "http://127.0.0.1:9/repeated"
        self.window.store.save_link(self.window.key, url, "Report", "Review")
        self.window.open_terminal_url(url, self.window.key)
        pump()
        original, view = self.window.active_page, self.window.view
        self.window.open_terminal_url(url + "#details", self.window.key)
        self.assertIs(self.window.view, view)
        self.assertEqual(self.window.active_page, original)
        request = {"schema": 1, "endpoint": self.context["endpoint"],
                   "boot_id": self.context["boot_id"], "key": self.window.key,
                   "tab_id": "t1", "created_at": time.time(), "url": url,
                   "title": "", "section": "Different section",
                   "focus": False, "background": False}
        self.window.pending_route = request
        self.window.poll_routes()
        self.assertIs(self.window.view, view)
        self.assertEqual(self.window.section, "Review")
        self.assertEqual(len(self.window.store.task(self.window.key)["links"]), 1)
        self.assertNotIn("Different section", self.window.store.task(self.window.key)["sections"])

    def test_terminal_click_retries_a_page_that_previously_failed_to_load(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<title>Recovered report</title><p>Available now</p>")
        # Reserve a port without listening, so the first navigation fails.
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler, bind_and_activate=False)
        server.server_bind()
        self.addCleanup(server.server_close)
        url = f"http://127.0.0.1:{server.server_address[1]}/report"
        self.window.open_terminal_url(url, self.window.key)
        pump(.8)
        view, page = self.window.view, self.window.active_page
        self.assertIn("Page could not load", self.window.message.get_text())
        server.server_activate()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        event = self.terminal_link_event(url, url)
        click_native_terminal(event)
        pump(.8)
        self.assertEqual(self.window.view.get_title(), "Recovered report")
        self.assertIs(self.window.view, view)
        self.assertEqual(self.window.active_page, page)
        self.assertEqual(len(self.window.store.task(self.window.key)["links"]), 1)

    def test_terminal_drag_and_shift_selection_do_not_open_url(self):
        url = "http://127.0.0.1:9/selection"
        event = self.terminal_link_event(url, url)
        self.window.terminal.emit("button-press-event", event)
        released = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
        released.window, released.button = event.window, 1
        released.x, released.y = event.x + 40, event.y
        self.window.terminal.emit("button-release-event", released)
        pump()
        self.assertFalse(self.window.store.task(self.window.key)["links"])
        event.state = Gdk.ModifierType.SHIFT_MASK
        self.click_terminal(event)
        self.assertFalse(self.window.store.task(self.window.key)["links"])

    def test_terminal_physical_drag_selects_text_without_opening_website(self):
        url = "http://127.0.0.1:9/drag-selection"
        event = self.terminal_link_event(url, url)
        click_native_terminal(event, drag_dx=80)
        self.assertTrue(self.window.terminal.get_has_selection())
        self.assertFalse(self.window.store.task(self.window.key)["links"])

    def test_terminal_opt_out_uses_previous_browser_and_never_creates_view(self):
        url = "http://127.0.0.1:9/off-link"
        self.window.toggle.set_active(False)
        pump()
        event = self.terminal_link_event(url, url)
        with patch("herdr_browser.app.open_external") as external:
            self.click_terminal(event)
            external.assert_called_once_with(url)
        self.assertIsNone(self.window.engine)
        self.assertIsNone(self.window.view)

    def test_terminal_non_web_link_does_not_open_and_changed_task_is_rejected(self):
        event = self.terminal_link_event("http://127.0.0.1:9/old-task",
                                         "http://127.0.0.1:9/old-task")
        self.window.terminal.emit("button-press-event", event)
        old_key = self.window.key
        self.context["focused_tab_id"] = "t2"
        atomic_json(self.window.context_path, self.context)
        pump()
        self.window.open_terminal_url("http://127.0.0.1:9/old-task", old_key)
        self.assertFalse(self.window.store.task(self.window.key)["links"])
        self.window.terminal.reset(True, True)
        self.window.terminal.feed(b"\x1b]8;;file:///tmp/report\x1b\\Local file\x1b]8;;\x1b\\")
        pump()
        self.assertIsNone(self.window.terminal_url(event))

    def test_inline_close_inactive_page_preserves_live_page(self):
        first = self.window.store.save_link(self.window.key, "https://example.com/one", "One")
        second = self.window.store.save_link(self.window.key, "https://example.com/two", "Two")
        self.window.open_page(None, first)
        view = self.window.view
        button = next(button for button in self.window.page_buttons
                      if button.get_tooltip_text().startswith("Two\n"))
        close = next(child for child in button.get_parent().get_children() if child is not button)
        close.clicked()
        self.assertIs(self.window.view, view)
        self.assertEqual(self.window.active_page, first)
        self.assertNotIn(second, [link["id"] for link in self.window.store.task(self.window.key)["links"]])

    def test_installed_update_detection_and_failed_restart_preserve_interface(self):
        import hashlib
        source = Path(self.tmp.name) / "installed-update.py"
        source.write_text("old")
        self.window.loaded_sources = {source: hashlib.sha256(source.read_bytes()).digest()}
        self.window.update_stamps = {}
        self.window.next_update_check = 0
        self.window.check_updates()
        self.assertFalse(self.window.update_button.get_sensitive())
        previous_stat = source.stat()
        source.write_text("new")
        # The two same-size writes can share a filesystem timestamp on CI.
        # Give the simulated update a distinct recorded modification time.
        os.utime(source, ns=(previous_stat.st_atime_ns,
                            previous_stat.st_mtime_ns + 1_000_000_000))
        self.window.next_update_check = 0
        self.window.check_updates()
        self.assertTrue(self.window.update_button.get_sensitive())
        with patch("herdr_browser.app.subprocess.Popen",
                   side_effect=OSError("Private test launch failure")):
            self.window.apply_update()
        self.assertFalse(self.window.closed)
        self.assertIn("Update could not start", self.window.message.get_text())

    def test_many_saved_pages_do_not_create_more_webviews_or_wide_tab_strip(self):
        key = self.window.key
        for i in range(100):
            self.window.store.save_link(key, f"https://example.com/{i}", f"Page {i}")
        self.window.refresh_pages()
        self.assertEqual(len(self.window.links), 100)
        self.assertLessEqual(len(self.window.page_buttons), 3)
        self.assertEqual(len(self.window.web_holder.get_children()), 1)
        self.window.page_picker.set_active(100)
        self.assertEqual(self.window.active_page, self.window.links[-1]["id"])
        self.assertLessEqual(len(self.window.page_buttons), 3)
        self.assertEqual(len(self.window.web_holder.get_children()), 1)

    def test_compact_window_keeps_browser_controls_inside_its_pane(self):
        self.assertIn("Session: ui-test", self.window.heading.get_tooltip_text())
        for index in range(8):
            self.window.store.save_link(
                self.window.key, f"https://example.com/{index}", "A very long page title")
        self.window.refresh_pages()
        self.window.resize(1100, 700)
        self.window.paned.set_position(600)
        pump(1.0)
        self.assertLessEqual(self.window.get_size()[0], 1100)
        pane_width = self.window.browser_box.get_allocated_width()
        for widget in (self.window.address, self.window.page_picker,
                       self.window.section_picker, *self.window.page_buttons):
            position = widget.translate_coordinates(self.window.browser_box, 0, 0)
            description = (widget.get_name(), position, widget.get_allocated_width(), pane_width)
            self.assertGreaterEqual(position[0], 0, description)
            self.assertLessEqual(position[0] + widget.get_allocated_width(), pane_width, description)

    def test_no_browser_launch_does_not_initialize_engine(self):
        self.window.on_close()
        self.window.destroy()
        self.args.no_browser = True
        self.window = BrowserWindow(self.args)
        pump()
        self.assertIsNone(self.window.engine)
        self.assertIsNone(self.window.view)
        self.assertFalse(self.window.browser_box.get_visible())
        # Also verify a fresh interpreter never imports the browser dependency.
        code = f"""
import argparse, sys
from pathlib import Path
from herdr_browser.app import BrowserWindow
from herdr_browser.test_app import pump
args = argparse.Namespace(client=Path({str(self.args.client)!r}), session='ui-test',
                          no_browser=True, browser=False)
window = BrowserWindow(args)
pump(.2)
assert window.engine is None
assert 'gi.repository.WebKit2' not in sys.modules
window.on_close()
window.destroy()
pump(.1)
"""
        subprocess.run(["/usr/bin/python3", "-c", code], check=True, timeout=15,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def test_spawn_does_not_reinherit_the_calling_herdr_pane(self):
        self.window.on_close()
        self.window.destroy()
        captured = Path(self.tmp.name) / "child-environment.json"
        client = Path(self.tmp.name) / "capture-client"
        names = ("HERDR_ENV", "HERDR_TAB_ID", "HERDR_PANE_ID",
                 "HERDR_WORKSPACE_ID", "HERDR_CLIENT_SOCKET_PATH", "HERDR_SOCKET_PATH")
        client.write_text(
            "#!/usr/bin/python3\nimport json, os\nfrom pathlib import Path\n"
            f"Path({str(captured)!r}).write_text(json.dumps("
            f"{{name: os.environ.get(name) for name in {names!r}}}))\n"
            "os.execv('/bin/cat', ['cat'])\n")
        client.chmod(0o700)
        old = {name: os.environ.get(name) for name in names}
        try:
            os.environ.update({name: "calling-pane-value" for name in names})
            os.environ["HERDR_ENV"] = "1"
            self.args.client = client
            self.args.no_browser = True
            self.window = BrowserWindow(self.args)
            pump(0.5)
            child = json.loads(captured.read_text())
            self.assertEqual(child, {name: None for name in names})
        finally:
            for name, value in old.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
