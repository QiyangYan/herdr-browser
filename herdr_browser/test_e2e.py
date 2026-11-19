"""Real Herdr test; uses only a disposable session and a private display."""
import argparse
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from urllib.parse import urlsplit

if os.environ.get("HERDR_BROWSER_TEST_DISPLAY") != "private":
    raise RuntimeError("This smoke test requires a private Xvfb display.")

from .app import BrowserWindow
from .test_app import pump
from . import test_app as native_ui
from .install import install
from .desktop import APP_ID
from .router import sync_defaults, desktop_default, set_default, SCHEMES, HANDLER
from .store import atomic_json
from gi.repository import Gdk, Gio


def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for the private Herdr client")
        pump(0.05)


def own_pss(excluded=()):
    """PSS for this GUI and descendants; excludes detached/existing Herdr servers."""
    pending = [os.getpid()]
    total = 0
    while pending:
        pid = pending.pop()
        if pid in excluded:
            continue
        try:
            children = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
            pending.extend(map(int, children))
            for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
                if line.startswith("Pss:"):
                    total += int(line.split()[1])
        except (OSError, ValueError):
            continue
    return round(total / 1024, 1)


class Page(BaseHTTPRequestHandler):
    requests = 0

    def do_GET(self):
        Page.requests += 1
        title = "Training logs" if urlsplit(self.path).path == "/logs" else "Private evaluation report"
        body = f"""<!doctype html><title>{html.escape(title)}</title>
<body style="font:20px system-ui;padding:30px"><h1>Evaluation report</h1>
<p>This page is served by the isolated feature test.</p>
<button onclick="document.querySelector('#result').textContent='Interaction works'">
Inspect result</button><p id="result">Ready</p></body>""".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class RealHerdrTests(unittest.TestCase):
    def test_two_clients_pages_and_off_switch(self):
        client = Path(os.environ["HERDR_BROWSER_CLIENT"])
        original = Path.home() / ".local/bin/herdr"
        tmp = tempfile.TemporaryDirectory(prefix="herdr-browser-e2e-")
        root = Path(tmp.name)
        session = "optional-browser-test"
        previous = dict(os.environ)
        variables = {
            # Match Ubuntu desktop routing; generic xdg-open does not parse quoted Exec arguments.
            "XDG_CURRENT_DESKTOP": "GNOME",
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_STATE_HOME": str(root / "state"),
            "XDG_DATA_HOME": str(root / "installation/share"),
            "HERDR_BROWSER_HOME": str(root / "browser"),
        }
        for name in ("HERDR_SOCKET_PATH", "HERDR_CLIENT_SOCKET_PATH",
                     "HERDR_TAB_ID", "HERDR_PANE_ID", "HERDR_WORKSPACE_ID",
                     "HERDR_CONFIG_PATH", "HERDR_ENV", "HERDR_SESSION"):
            os.environ.pop(name, None)
        os.environ.update(variables)
        config = root / "config/herdr"
        config.mkdir(parents=True)
        (config / "config.toml").write_text(
            'onboarding = false\n[terminal]\ndefault_shell = "/bin/sh"\n')
        windows = []
        server = None
        packaged = None
        packaged_context = None
        updated_process = None
        updated_context = None
        web_server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
        threading.Thread(target=web_server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{web_server.server_port}/report"
        try:
            # Attach patched clients to an ORIGINAL 0.9.3 server, as on the desktop.
            server_log = (root / "server.log").open("w")
            server = subprocess.Popen(
                [str(original), "--session", session, "server"], cwd=root,
                stdout=server_log, stderr=subprocess.STDOUT)
            server_log.close()
            socket = config / "sessions" / session / "herdr.sock"
            wait_for(lambda: socket.exists())
            # Reproduce launching from a real Herdr pane. VTE must not merge
            # these markers back into either client's sanitized environment.
            os.environ.update(
                HERDR_ENV="1", HERDR_TAB_ID="calling-tab", HERDR_PANE_ID="calling-pane",
                HERDR_WORKSPACE_ID="calling-workspace",
                HERDR_CLIENT_SOCKET_PATH=str(root / "wrong-client.sock"),
                HERDR_SOCKET_PATH=str(root / "wrong-api.sock"),
            )
            args = argparse.Namespace(client=client, session=session,
                                      browser=True, no_browser=True)
            started = time.monotonic()
            first = BrowserWindow(args)
            windows.append(first)
            wait_for(lambda: first.key and first.client_pid)
            ready_seconds = round(time.monotonic() - started, 2)
            pump(1)
            cold_off_pss = own_pss((server.pid,))
            self.assertIsNone(first.engine)
            first.toggle.set_active(True)
            pump(1.0)
            with_browser_pss = own_pss((server.pid,))
            first.toggle.set_active(False)
            pump(1)
            without_browser_pss = own_pss((server.pid,))
            self.assertIsNone(first.view)
            self.assertIsNone(first.engine)
            first.toggle.set_active(True)
            args.no_browser = False
            second = BrowserWindow(args)
            windows.append(second)
            wait_for(lambda: second.key and second.client_pid)
            second_original = second.key
            response = subprocess.check_output(
                [str(original), "--session", session, "tab", "create",
                 "--label", "Evaluation", "--cwd", str(root), "--no-focus"],
                text=True, timeout=10)
            created = json.loads(response)["result"]["tab"]["tab_id"]
            wait_for(lambda: created in first.task_ids and created in second.task_ids)
            first.task_picker.set_active(first.task_ids.index(created))
            wait_for(lambda: first.context.get("focused_tab_id") == created)
            pump(0.5)
            self.assertEqual(second.key, second_original,
                             "Selecting this client must not retarget another window")
            first.address.set_text(url)
            first.navigate(first.address)
            wait_for(lambda: first.view.get_title() == "Private evaluation report")
            first.save_page()
            self.assertEqual(first.store.task(first.key)["links"][0]["url"], url)
            results = []
            def javascript_done(view, result, data=None):
                results.append(view.run_javascript_finish(result).get_js_value().to_string())
            first.view.run_javascript(
                "document.querySelector('button').click();"
                "document.querySelector('#result').textContent",
                None, javascript_done, None)
            wait_for(lambda: results)
            self.assertEqual(results[0], "Interaction works")
            script = Path(__file__).resolve().parent
            env = dict(os.environ, HERDR_TAB_ID=created,
                       HERDR_SOCKET_PATH=first.connection)
            subprocess.run(
                ["/usr/bin/python3", "-m", "herdr_browser", "link", url,
                 "--title", "Evaluation report", "--next-action", "Inspect the result"],
                cwd=script.parent, env=env, check=True, timeout=10,
                stdout=subprocess.DEVNULL)
            self.assertEqual(first.store.task(first.key)["next_action"], "Inspect the result")
            subprocess.run(
                ["/usr/bin/python3", "-m", "herdr_browser", "link",
                 url.replace("/report", "/logs"), "--title", "Agent logs",
                 "--section", "Monitoring"],
                cwd=script.parent, env=env, check=True, timeout=10,
                stdout=subprocess.DEVNULL)
            self.assertEqual(first.store.task(first.key)["active_section"], "General")
            self.assertTrue(any(link["section"] == "Monitoring"
                                for link in first.store.task(first.key)["links"]))
            first_task = first.key
            previous_tab = next(task for task in first.task_ids if task is not None and task != created)
            first.task_picker.set_active(first.task_ids.index(previous_tab))
            wait_for(lambda: first.key != first_task)
            first.task_picker.set_active(first.task_ids.index(created))
            wait_for(lambda: first.key == first_task and
                     first.view.get_title() == "Private evaluation report")
            self.assertEqual(first.view.get_uri(), url)
            # Optional subplans contain real page tabs, all using one active view.
            review_page = first.store.save_link(first.key, url + "?review=1", "Report", "Review results")
            logs_url = url.replace("/report", "/logs") + "?review=1"
            logs_page = first.store.save_link(
                first.key, logs_url, "Logs", "Review results")
            first.store.save_link(first.key, url + "?compare=1", "Comparison", "Review results")
            first.refresh_pages()
            first.section_picker.set_active(first.section_names.index("Review results"))
            wait_for(lambda: first.view.get_title() == "Private evaluation report")
            first.open_page(None, logs_page)
            wait_for(lambda: first.view.get_title() == "Training logs")
            self.assertEqual(len(first.web_holder.get_children()), 1)
            first.section_picker.set_active(first.section_names.index("General"))
            wait_for(lambda: first.view.get_uri() == url)
            first.section_picker.set_active(first.section_names.index("Review results"))
            wait_for(lambda: first.view.get_title() == "Training logs")
            first.open_page(None, review_page)
            wait_for(lambda: first.view.get_title() == "Private evaluation report")
            pump(1)
            before_many_pss = own_pss((server.pid,))
            requests_before = Page.requests
            active_view = first.view
            # Inactive pages remain metadata: no request or extra WebView.
            for index in range(100):
                first.store.save_link(
                    first.key, url + f"?inactive={index}", f"Reference {index}", "Reference")
            first.refresh_pages()
            pump(1)
            after_many_pss = own_pss((server.pid,))
            self.assertIs(first.view, active_view)
            self.assertEqual(len(first.web_holder.get_children()), 1)
            self.assertEqual(Page.requests, requests_before)
            # Test the actual installed entry point with NO --session argument,
            # inherited socket routing, and the launching task to select.
            prefix = root / "installation"
            install(client, prefix)
            app_entry = prefix / "share/applications" / (APP_ID + ".desktop")
            subprocess.run(["desktop-file-validate", str(app_entry)], check=True)
            app_info = Gio.DesktopAppInfo.new_from_filename(str(app_entry))
            self.assertTrue(app_info.should_show())
            self.assertEqual(app_info.get_startup_wm_class(), APP_ID)
            self.assertTrue(app_info.get_commandline().endswith("--browser"))
            self.assertFalse(app_info.get_supported_types())
            packaged_env = dict(os.environ, HERDR_SOCKET_PATH=first.connection,
                                HERDR_TAB_ID=previous_tab)
            packaged_env.pop("HERDR_BROWSER_CLIENT", None)
            packaged_log = (root / "packaged-launch.log").open("w")
            packaged = subprocess.Popen(
                [str(prefix / "bin/herdr-browser"), "--browser"],
                cwd=root, env=packaged_env, stdout=packaged_log, stderr=subprocess.STDOUT)
            packaged_log.close()
            def packaged_ready():
                nonlocal packaged_context
                for instance in (root / "browser/instances").glob("*.json"):
                    info = json.loads(instance.read_text())
                    if info["pid"] == packaged.pid:
                        self.assertEqual(info["application_id"], APP_ID)
                        packaged_context = Path(info["context"])
                        try:
                            state = json.loads(packaged_context.read_text())
                        except (OSError, ValueError):
                            return False
                        return state["available"] and state["focused_tab_id"] == previous_tab
                return False
            wait_for(packaged_ready)
            self.assertIsNone(packaged.poll())
            self.assertEqual(first.context["focused_tab_id"], created)
            packaged.terminate()
            packaged.wait(timeout=10)
            pump(0.2)
            output = Path(os.environ.get("HERDR_BROWSER_ARTIFACTS", "/tmp"))
            output.mkdir(parents=True, exist_ok=True)
            second.hide()
            first.present()
            first.move(0, 0)
            pump(1.0)
            self.assertEqual(first.message.get_text(), "")
            results.clear()
            first.view.run_javascript("document.readyState", None, javascript_done, None)
            wait_for(lambda: results)
            self.assertEqual(results[0], "complete")
            # Click an actual OSC 8 label through VTE's native input window.
            # This bypasses the desktop router and preserves source-task identity.
            terminal_url = url + "?terminal-click=1"
            text = (f"\x1b]8;;{terminal_url}\x1b\\"
                    "View the change summary\x1b]8;;\x1b\\")
            helper = SimpleNamespace(window=first, fail=self.fail)
            event = native_ui.NativeTests.terminal_link_event(helper, text, terminal_url)
            source_key = first.key
            native_ui.click_native_terminal(event)
            wait_for(lambda: first.view.get_uri() == terminal_url)
            wait_for(lambda: first.view.get_title() == "Private evaluation report")
            self.assertEqual(first.key, source_key)
            self.assertEqual(second.key, second_original)
            results.clear()
            first.view.run_javascript("document.querySelector('#result').textContent",
                                     None, javascript_done, None)
            wait_for(lambda: results)
            self.assertEqual(results[0], "Ready")
            original_page, original_view = first.active_page, first.view
            native_ui.click_native_terminal(event)
            pump(0.4)
            self.assertIs(first.view, original_view)
            self.assertEqual(first.active_page, original_page)
            repeated = first.store.save_link(
                first.key, terminal_url, "", "Another section")
            self.assertEqual(repeated, original_page)
            self.assertEqual(sum(link["url"] == terminal_url
                                 for link in first.store.task(first.key)["links"]), 1)
            # Version suffixes on generated HTML reports refresh the same tab.
            html_report = url.replace("/report", "/report.html")
            first.open_terminal_url(html_report + "?v=one", first.key)
            wait_for(lambda: first.view.get_uri() == html_report + "?v=one"
                     and first.view.get_title() == "Private evaluation report")
            report_page, report_view = first.active_page, first.view
            first.open_terminal_url(html_report + "?v=two", first.key)
            wait_for(lambda: first.view.get_uri() == html_report + "?v=two")
            self.assertIs(first.view, report_view)
            self.assertEqual(first.active_page, report_page)
            self.assertEqual(sum(urlsplit(link["url"]).path == "/report.html"
                                 for link in first.store.task(first.key)["links"]), 1)
            first.open_terminal_url(terminal_url, first.key)
            wait_for(lambda: first.view.get_uri() == terminal_url
                     and first.view.get_title() == "Private evaluation report")
            # Exercise the actual desktop URL association in a private profile.
            captured_external = root / "external-url.txt"
            external_script = root / "previous-browser.py"
            external_script.write_text(
                "import sys\nfrom pathlib import Path\n"
                f"Path({str(captured_external)!r}).write_text(sys.argv[1])\n")
            application = prefix / "share/applications/previous-test-browser.desktop"
            application.write_text(
                "[Desktop Entry]\nType=Application\nName=Private test browser\n"
                f'Exec=/usr/bin/python3 {external_script} %u\n'
                "MimeType=x-scheme-handler/http;x-scheme-handler/https;\n")
            for scheme in SCHEMES:
                set_default(application.name, scheme)
                self.assertEqual(desktop_default(scheme), application.name)
            sync_defaults(force=True)
            for scheme in SCHEMES:
                self.assertEqual(desktop_default(scheme), application.name)
            clicked = url + "?conversation-click=1"
            first.store.save_link(first.key, clicked, "Conversation report", "Review results")
            outside_env = dict(os.environ)
            for name in ("HERDR_BROWSER_TEST_DISPLAY", "HERDR_TAB_ID", "HERDR_SOCKET_PATH",
                         "HERDR_ENV", "HERDR_PANE_ID", "HERDR_WORKSPACE_ID", "BROWSER"):
                outside_env.pop(name, None)
            subprocess.run(["xdg-open", clicked], env=outside_env, check=True, timeout=15,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            wait_for(captured_external.exists)
            self.assertEqual(captured_external.read_text(), clicked)
            self.assertEqual(first.view.get_uri(), terminal_url,
                             "External clicks must not replace the Herdr website, even if it owns the URL")
            subprocess.run(
                [str(prefix / "bin/herdr-browser"), "open", clicked, "--focus"],
                env=dict(os.environ, HERDR_TAB_ID=created, HERDR_SOCKET_PATH=first.connection),
                check=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            wait_for(lambda: first.view.get_uri() == clicked)
            wait_for(lambda: first.view.get_title() == "Private evaluation report")
            results.clear()
            first.view.run_javascript("document.querySelector('#result').textContent",
                                     None, javascript_done, None)
            wait_for(lambda: results)
            self.assertEqual(results[0], "Ready")
            self.assertEqual(second.key, second_original)
            self.assertEqual(captured_external.read_text(), clicked)
            background_url = url + "?agent-background=1"
            requests_before_background = Page.requests
            subprocess.run(
                [str(prefix / "bin/herdr-browser"), "open", background_url,
                 "--section", "Review results", "--background"],
                env=dict(os.environ, HERDR_TAB_ID=created, HERDR_SOCKET_PATH=first.connection),
                check=True, timeout=15, stdout=subprocess.DEVNULL)
            wait_for(lambda: any(link["url"] == background_url
                                 for link in first.store.task(first.key)["links"]))
            self.assertEqual(first.view.get_uri(), clicked)
            self.assertEqual(Page.requests, requests_before_background)
            update_file = root / "task-update.json"
            update_file.write_text(json.dumps({
                "summary": "Living report in the real private Herdr task.",
                "plan": "Keep current plan, result and next action together.",
                "next_action": "Verify the next iteration."}))
            report_env = dict(os.environ, HERDR_TAB_ID=created, HERDR_SOCKET_PATH=first.connection)
            published = json.loads(subprocess.check_output(
                [str(prefix / "bin/herdr-browser"), "task", "--new", "Living workflow",
                 "--id", "workflow", "--file", str(update_file), "--focus"],
                env=report_env, text=True, timeout=15))
            wait_for(lambda: first.view.get_uri() == published["url"]
                     and first.view.get_title() == "Living workflow")
            active_report_view, active_report_page = first.view, first.active_page
            update_file.write_text(json.dumps({"results": "Next iteration verified."}))
            repeated = json.loads(subprocess.check_output(
                [str(prefix / "bin/herdr-browser"), "task", "--id", "workflow",
                 "--file", str(update_file), "--export", str(root / "task-snapshot.html")],
                env=report_env, text=True, timeout=15))
            self.assertEqual(published["url"], repeated["url"])
            self.assertEqual(published["page_id"], repeated["page_id"])
            pump(3.3)
            results.clear()
            first.view.run_javascript("document.querySelector('#task-content').dataset.revision",
                                     None, javascript_done, None)
            wait_for(lambda: results)
            self.assertEqual(results[0], "2")
            self.assertIs(first.view, active_report_view)
            self.assertEqual(first.active_page, active_report_page)
            self.assertEqual(second.key, second_original)
            self.assertEqual(sum(link["url"] == published["url"]
                                 for link in first.store.task(first.key)["links"]), 1)
            width = first.get_window().get_width()
            height = first.get_window().get_height()
            image = Gdk.pixbuf_get_from_window(first.get_window(), 0, 0, width, height)
            image.savev(str(output / "real-herdr-browser.png"), "png", [], [])
            first.toggle.set_active(False)
            pump()
            self.assertIsNone(first.view)
            self.assertFalse(first.browser_box.get_visible())
            sync_defaults(force=True)
            # The other enabled window must also leave the desktop browser alone.
            self.assertEqual(desktop_default(SCHEMES[0]), application.name)
            second.toggle.set_active(False)
            sync_defaults(force=True)
            for scheme in SCHEMES:
                self.assertEqual(desktop_default(scheme), application.name)
            # Simulate an interrupted shutdown, then click through real xdg-open.
            atomic_json(root / "browser/routing.json", {
                "active": True, "previous": {scheme: application.name for scheme in SCHEMES}})
            for scheme in SCHEMES:
                set_default(HANDLER, scheme)
            fallback_url = url + "?crash-fallback=1"
            fallback_env = dict(os.environ)
            fallback_env.pop("HERDR_BROWSER_TEST_DISPLAY", None)
            fallback_env.pop("HERDR_BROWSER_NO_DESKTOP", None)
            fallback = subprocess.run(["xdg-open", fallback_url], env=fallback_env,
                                      timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            self.assertEqual(fallback.returncode, 0, fallback.stderr)
            wait_for(captured_external.exists)
            self.assertEqual(captured_external.read_text(), fallback_url)
            for scheme in SCHEMES:
                self.assertEqual(desktop_default(scheme), application.name)
            # Applying an installed update reattaches the same task without
            # stopping the original server and preserves Browser-off.
            second.update_available = True
            update_tab = second.context["focused_tab_id"]
            updated_process = second.apply_update()
            self.assertIsNotNone(updated_process)
            def updated_ready():
                nonlocal updated_context
                for instance in (root / "browser/instances").glob("*.json"):
                    info = json.loads(instance.read_text())
                    if info["pid"] == updated_process.pid:
                        updated_context = Path(info["context"])
                        try:
                            state = json.loads(updated_context.read_text())
                        except (OSError, ValueError):
                            return False
                        return (state["available"] and state["focused_tab_id"] == update_tab
                                and not info["routing_enabled"])
                return False
            wait_for(updated_ready)
            self.assertIsNone(server.poll())
            self.assertIsNone(updated_process.poll())
            measured = {
                "private_session": session, "real_client_switching": "passed",
                "two_clients_same_server_isolated": "passed",
                "original_cli_compatibility": "passed",
                "original_server_compatibility": "passed",
                "real_http_page_and_javascript": "passed",
                "agent_link_registration": "passed",
                "actual_external_xdg_open_keeps_previous_browser": "passed",
                "explicit_herdr_open_to_native_view": "passed",
                "actual_terminal_named_link_click_and_native_page_load": "passed",
                "in_app_update_preserves_task_server_and_browser_opt_out": "passed",
                "installed_task_publisher_uses_real_herdr_origin": "passed",
                "same_task_page_url_and_id_across_iterations": "passed",
                "native_task_page_updates_without_view_reload": "passed",
                "standalone_task_snapshot_export": "passed",
                "agent_background_open_preserves_active_page": "passed",
                "desktop_defaults_restore_after_last_browser_off": "passed",
                "desktop_crash_fallback_and_restore": "passed",
                "private_desktop_profile": str(root / "config"),
                "real_section_and_page_tab_restore": "passed",
                "one_live_view_with_100_inactive_pages": "passed",
                "inactive_pages_make_no_http_requests": "passed",
                "two_window_before_100_inactive_pages_pss_mib": before_many_pss,
                "two_window_after_100_inactive_pages_pss_mib": after_many_pss,
                "last_url_restore": "passed", "browser_off": "passed",
                "launch_with_inherited_herdr_env": "passed",
                "installed_command_default_session_from_pane": "passed",
                "visible_app_entry_icon_and_native_application_identity": "passed",
                "launching_task_selected": "passed",
                "client_ready_seconds": ready_seconds,
                "cold_no_browser_gui_tree_pss_mib": cold_off_pss,
                "home_gui_tree_pss_mib": with_browser_pss,
                "after_off_gui_tree_pss_mib": without_browser_pss,
                "measurement_limit": "One private software-rendered Xvfb run. "
                    "PSS includes this Python GUI and descendants, excluding the detached "
                    "Herdr server. No real SSO or GPU-heavy viewer qualification.",
            }
            (output / "verification.json").write_text(json.dumps(measured, indent=2) + "\n")
        finally:
            if updated_process:
                if updated_process.poll() is None:
                    updated_process.terminate()
                    updated_process.wait(timeout=10)
                if updated_context:
                    import shutil
                    shutil.rmtree(updated_context.parent, ignore_errors=True)
            if packaged:
                if packaged.poll() is None:
                    packaged.terminate()
                    packaged.wait(timeout=10)
                if packaged_context:
                    import shutil
                    shutil.rmtree(packaged_context.parent, ignore_errors=True)
            for window in windows:
                window.on_close()
                window.destroy()
            pump(0.2)
            # Stop only the session this test created in its private config root.
            subprocess.run([str(client), "--session", session, "session", "stop", session],
                           timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if server:
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.terminate()
                    server.wait(timeout=5)
            web_server.shutdown()
            web_server.server_close()
            os.environ.clear()
            os.environ.update(previous)
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
