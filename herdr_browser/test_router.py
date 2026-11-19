import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from . import router
from .store import Store, atomic_json, task_key


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = patch.dict(os.environ, HERDR_BROWSER_HOME=str(self.home),
                              XDG_DATA_HOME=str(self.home / "data"))
        self.env.start()
        self.addCleanup(self.env.stop)
        hosts = patch.object(router, "FIREFOX_HOSTS", frozenset({"external.example.test"}))
        hosts.start()
        self.addCleanup(hosts.stop)
        self.store = Store(self.home)
        self.context = {
            "schema": 1, "available": True, "endpoint": "local", "boot_id": "test",
            "focused_tab_id": "a",
            "tasks": [{"tab_id": "a", "workspace_id": "w", "label": "First"},
                      {"tab_id": "b", "workspace_id": "w", "label": "Second"}],
        }
        self.store.register_context("/test/socket", self.context)
        self.first = task_key("/test/socket", self.context, "a")
        self.second = task_key("/test/socket", self.context, "b")
        self.info = {"pid": os.getpid(), "process_token": router.process_token(os.getpid()),
                     "connection": "/test/socket", "selection": self.context,
                     "focused_at": 1}

    def test_configured_site_routes_by_exact_hostname(self):
        for url in (
            "https://external.example.test/s3/bucket/a#video",
            "https://EXTERNAL.example.test/s3/bucket",
        ):
            self.assertTrue(router.firefox_required(url), url)
        for url in (
            "http://localhost:8871/index.html", "http://127.0.0.1:36407/task",
            "https://code.example.test/review/1", "https://wiki.example.test/",
            "https://login.example.test/login", "https://portal.example.test/",
            "https://other.example.test/",
            "https://docs.example.test/", "https://wwwiki.example.test/",
            "https://example.org/?next=https://external.example.test/",
            "https://external.example.test.example.org/",
        ):
            self.assertFalse(router.firefox_required(url), url)

    def test_login_explicit_open_skips_herdr_and_default_changes(self):
        url = "https://external.example.test/s3/bucket"
        with patch.object(router, "open_firefox") as firefox, \
             patch.object(router, "deliver") as delivered, \
             patch.object(router, "sync_defaults") as defaults:
            self.assertFalse(router.open_url(url))
            firefox.assert_called_once_with(url)
            delivered.assert_not_called()
            defaults.assert_not_called()

    def test_login_background_request_does_not_steal_focus(self):
        with patch.object(router, "open_firefox") as firefox, \
             patch.object(router, "deliver") as delivered:
            self.assertFalse(router.open_url("https://external.example.test/", background=True))
            firefox.assert_not_called()
            delivered.assert_not_called()

    def test_login_cannot_be_queued_in_herdr(self):
        with patch.object(router, "instances") as targets:
            self.assertFalse(router.deliver("https://external.example.test/"))
            targets.assert_not_called()

    def test_login_external_open_uses_firefox_even_if_desktop_default_differs(self):
        with patch.object(router, "open_firefox") as firefox, \
             patch.object(router, "desktop_default") as default:
            router.open_external("https://external.example.test/")
            firefox.assert_called_once_with("https://external.example.test/")
            default.assert_not_called()

    def test_firefox_launcher_preserves_the_exact_url(self):
        url = "https://external.example.test/?literal=%24%28example%29#review"
        with patch.object(router.subprocess, "run") as launch:
            router.open_firefox(url)
        launch.assert_called_once_with(["/usr/bin/firefox", "--new-tab", url],
                                       check=True, timeout=15)

    def test_origin_precedes_known_url_and_window_focus(self):
        self.store.save_link(self.first, "https://example.com/report", "Report")
        chosen = router.choose_target("https://example.com/report", [self.info],
                                      self.store.read()["tasks"], "b", "/test/socket")
        self.assertEqual(chosen[2], self.second)

    def test_url_ownership_includes_fragments_and_recent_window_breaks_ties(self):
        self.store.save_link(self.second, "https://example.com/report", "Report")
        recent = dict(self.info, focused_at=2)
        chosen = router.choose_target("https://example.com/report#figure",
                                      [self.info, recent], self.store.read()["tasks"])
        self.assertEqual(chosen[2], self.second)
        self.assertIs(chosen[0], recent)

    def test_unknown_click_uses_most_recent_selected_task(self):
        recent = dict(self.info, selection=dict(self.context, focused_tab_id="b"), focused_at=2)
        chosen = router.choose_target("https://example.com/new", [self.info, recent], {})
        self.assertEqual(chosen[2], self.second)

    def test_stale_pid_token_and_browser_off_are_ignored(self):
        context_path = self.home / "context.json"
        atomic_json(context_path, self.context)
        root = self.home / "instances"
        root.mkdir()
        info = dict(self.info, context=str(context_path), inbox=str(self.home),
                    routing_enabled=True)
        info.pop("selection")
        path = root / "one.json"
        atomic_json(path, dict(info, process_token="reused-pid"))
        self.assertEqual(router.instances(), [])
        atomic_json(path, dict(info, routing_enabled=False))
        self.assertEqual(router.instances(), [])
        atomic_json(path, info)
        self.assertEqual(len(router.instances()), 1)

    def test_delivery_is_private_and_keeps_task_section(self):
        self.store.save_link(self.second, "https://example.com/report", "Report", "Review")
        inbox = self.home / "inbox"
        inbox.mkdir()
        info = dict(self.info, inbox=str(inbox))
        with patch.object(router, "instances", return_value=[info]):
            self.assertTrue(router.deliver("https://example.com/report", focus=True))
        path = next(inbox.glob("*.json"))
        request = json.loads(path.read_text())
        self.assertEqual(request["key"], self.second)
        self.assertEqual(request["section"], "Review")
        self.assertTrue(request["focus"])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_desktop_defaults_restore_and_preserve_manual_choice(self):
        desktop = router.install_desktop("/test/launcher")
        self.assertIn('Exec=/usr/bin/env "/test/launcher" external %u', desktop.read_text())
        defaults = {scheme: router.HANDLER for scheme in router.SCHEMES}
        atomic_json(self.home / "routing.json", {
            "active": True, "previous": {scheme: "previous.desktop" for scheme in router.SCHEMES}})
        def set_default(application, scheme):
            defaults[scheme] = application
        with patch.object(router, "desktop_default", side_effect=lambda scheme: defaults[scheme]), \
             patch.object(router, "set_default", side_effect=set_default), \
             patch.object(router, "instances", return_value=[self.info]) as enabled:
            router.sync_defaults(force=True)
            self.assertEqual(set(defaults.values()), {"previous.desktop"})
            # Enabled companions must not take over external application links.
            router.sync_defaults(force=True)
            defaults[router.SCHEMES[0]] = "manually-selected.desktop"
            enabled.return_value = []
            router.sync_defaults(force=True)
            self.assertEqual(defaults[router.SCHEMES[0]], "manually-selected.desktop")
            self.assertEqual(defaults[router.SCHEMES[1]], "previous.desktop")
            self.assertFalse(json.loads((self.home / "routing.json").read_text())["active"])

    def test_open_companion_never_claims_desktop_browser(self):
        router.install_desktop("/test/launcher")
        defaults = {scheme: "previous.desktop" for scheme in router.SCHEMES}
        with patch.object(router, "desktop_default", side_effect=lambda scheme: defaults[scheme]), \
             patch.object(router, "set_default") as selected, \
             patch.object(router, "instances", return_value=[self.info]) as enabled:
            router.sync_defaults(force=True)
            enabled.return_value = []
            router.sync_defaults(force=True)
            enabled.return_value = [self.info]
            router.sync_defaults(force=True)
            selected.assert_not_called()
        self.assertEqual(set(defaults.values()), {"previous.desktop"})

    def test_external_app_command_bypasses_herdr_delivery(self):
        from .cli import main
        with patch.object(router, "sync_defaults"), \
             patch.object(router, "open_external") as external, \
             patch.object(router, "deliver") as delivered:
            self.assertEqual(main(["external", "https://example.com"]), 0)
            external.assert_called_once_with("https://example.com")
            delivered.assert_not_called()

    def test_background_output_does_not_launch_an_external_browser(self):
        with patch.object(router, "deliver", return_value=False), \
             patch.object(router, "open_external") as external:
            self.assertFalse(router.open_url("https://example.com", background=True))
            external.assert_not_called()

    def test_fallback_launches_captured_app_without_recursive_open(self):
        applications = self.home / "data/applications"
        applications.mkdir(parents=True)
        original = applications / "previous.desktop"
        original.write_text("[Desktop Entry]\nType=Application\nExec=/test/browser %u\n")
        atomic_json(self.home / "routing.json", {
            "active": True, "previous": {scheme: "previous.desktop" for scheme in router.SCHEMES}})
        with patch.object(router, "desktop_default", return_value=router.HANDLER), \
             patch.object(router.subprocess, "run") as launched:
            router.open_external("https://example.com")
        self.assertEqual(launched.call_args.args[0],
                         ["gio", "launch", str(original), "https://example.com"])


if __name__ == "__main__":
    unittest.main()
