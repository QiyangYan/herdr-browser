import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest

from .store import Store, checked_url, task_key, task_home, find_page


def write_link(home, key, index):
    Store(home).save_link(key, f"https://example.com/{index}", str(index))


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.context = {
            "endpoint": "local", "boot_id": "server-a",
            "tasks": [{"tab_id": "t1", "workspace_id": "w1", "label": "First",
                       "agent_status": "working"},
                      {"tab_id": "t2", "workspace_id": "w1", "label": "Second",
                       "agent_status": "blocked"}],
        }
        self.store.register_context("/session-a/socket", self.context)
        self.first = task_key("/session-a/socket", self.context, "t1")
        self.second = task_key("/session-a/socket", self.context, "t2")

    def test_switching_tasks_preserves_separate_pages_and_reminders(self):
        self.store.save_link(self.first, "https://example.com/report", "Report")
        self.store.set_value(self.first, "next_action", "Inspect failure")
        self.store.save_link(self.second, "https://example.com/logs", "Logs")
        reopened = Store(self.tmp.name)
        self.assertEqual(reopened.task(self.first)["links"][0]["title"], "Report")
        self.assertEqual(reopened.task(self.second)["links"][0]["title"], "Logs")
        self.assertEqual(reopened.task(self.second)["next_action"], "")
        self.assertEqual(reopened.task(self.first)["next_action"], "Inspect failure")

    def test_parallel_sessions_and_restart_cannot_share_tab_identity(self):
        identities = {
            self.first,
            task_key("/session-b/socket", self.context, "t1"),
            task_key("/session-a/socket", dict(self.context, boot_id="server-b"), "t1"),
            task_key("/session-a/socket", dict(self.context, endpoint="ssh:one"), "t1"),
        }
        self.assertEqual(len(identities), 4)

    def test_parallel_windows_do_not_drop_saved_links(self):
        workers = [multiprocessing.Process(target=write_link,
                                           args=(self.tmp.name, self.first, i))
                   for i in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual(len(self.store.task(self.first)["links"]), 8)
        self.assertEqual(os.stat(self.store.path).st_mode & 0o777, 0o600)

    def test_corrupt_state_is_preserved(self):
        self.store.path.write_text("{broken")
        with self.assertRaisesRegex(ValueError, "preserved"):
            self.store.preference(False)
        self.assertEqual(self.store.path.read_text(), "{broken")

    def test_browser_off_survives_relaunch(self):
        self.store.preference(False)
        self.assertFalse(Store(self.tmp.name).read()["browser_enabled"])

    def test_legacy_bookmarks_upgrade_without_losing_urls(self):
        with self.store.transaction() as data:
            data["tasks"][self.first] = {
                "links": [{"url": "https://example.com/old", "title": "Old page"}],
                "last_url": "https://example.com/old", "next_action": "Review"}
        first = self.store.task(self.first)
        self.assertEqual(first["active_section"], "General")
        self.assertEqual(first["sections"]["General"]["last_url"], "https://example.com/old")
        self.store.register_context("/session-a/socket", self.context)
        self.assertEqual(self.store.task(self.first)["links"], first["links"])
        self.assertEqual(self.store.task(self.first)["next_action"], "Review")

    def test_sections_keep_independent_pages_and_restore_locations(self):
        general = self.store.save_link(self.first, "https://example.com/report", "Report")
        review = self.store.save_link(self.first, "https://example.com/review", "Review", "Review")
        self.assertNotEqual(general, review)
        self.store.select_section(self.first, "Review")
        self.store.activate_page(self.first, "Review", review)
        self.store.record_location(self.first, "Review", review, "https://example.com/details")
        self.store.select_section(self.first, "General")
        row = self.store.task(self.first)
        self.assertEqual(row["last_url"], "https://example.com/report")
        self.assertEqual(row["sections"]["Review"]["last_url"], "https://example.com/details")
        self.assertEqual(self.store.task(self.second)["links"], [])

    def test_repeated_page_reuses_original_section_and_preserves_title(self):
        original = self.store.save_link(self.first, "https://example.com", "Report", "Review")
        repeated = self.store.save_link(self.first, "https://EXAMPLE.com:443/#results", "", "Other")
        self.assertEqual(repeated, original)
        row = self.store.task(self.first)
        self.assertEqual(len(row["links"]), 1)
        self.assertEqual(row["links"][0]["title"], "Report")
        self.assertEqual(row["links"][0]["section"], "Review")
        self.assertNotIn("Other", row["sections"])
        self.store.activate_page(self.first, "General", repeated)
        self.assertEqual(self.store.task(self.first)["active_section"], "Review")
        self.store.save_link(self.second, "https://example.com", "Separate task")
        self.assertEqual(len(self.store.task(self.second)["links"]), 1)

    def test_redirect_reopen_and_collision_keep_original_tab(self):
        original = self.store.save_link(self.first, "https://example.com/start", "Report")
        other = self.store.save_link(self.first, "https://example.com/final", "Redirect", "Review")
        self.store.activate_page(self.first, "Review", other)
        page = self.store.record_location(self.first, "Review", other, "https://example.com/start")
        self.assertEqual(page["id"], original)
        row = self.store.task(self.first)
        self.assertEqual(len(row["links"]), 1)
        self.assertEqual(row["active_section"], "General")
        self.assertEqual(find_page(row, "https://example.com/final")["id"], original)
        self.assertEqual(self.store.save_link(self.first, "https://example.com/final", ""), original)

    def test_local_report_refresh_versions_reuse_tab_but_filters_and_remote_versions_do_not(self):
        url = "http://127.0.0.1:8796/report.html"
        original = self.store.save_link(self.first, url, "Report")
        self.assertEqual(self.store.save_link(self.first, url + "?v=one", ""), original)
        self.assertEqual(self.store.save_link(self.first, url + "?v=two#results", "", "Review"), original)
        filtered = self.store.save_link(self.first, url + "?filter=one&v=two", "Filtered")
        self.assertNotEqual(filtered, original)
        self.assertEqual(self.store.save_link(self.first, url + "?filter=one&v=three", ""), filtered)
        first = self.store.save_link(self.first, "https://example.com/report.html?v=one", "Remote")
        second = self.store.save_link(self.first, "https://example.com/report.html?v=two", "Remote")
        self.assertNotEqual(first, second)

    def test_saved_duplicates_are_repaired_atomically_without_losing_unique_pages(self):
        original = self.store.save_link(self.first, "https://example.com/report", "Report")
        with self.store.transaction() as data:
            row = data["tasks"][self.first]
            row["links"].append({"id": "duplicate", "url": "https://example.com/report#top",
                                 "section": "Review", "title": "Duplicate"})
            row["links"].append({"id": "unique", "url": "https://example.com/report?filter=one",
                                 "section": "Review", "title": "Filtered"})
            row["sections"]["Review"] = {"active_page": "duplicate",
                                         "last_url": "https://example.com/report#top"}
            row["active_section"] = "Review"
        row = self.store.task(self.first)
        self.assertEqual([page["id"] for page in row["links"]], [original, "unique"])
        self.assertEqual(row["active_section"], "General")
        persisted = json.loads(self.store.path.read_text())["tasks"][self.first]
        self.assertEqual(persisted, row)

    def test_parallel_repeated_opens_make_one_tab(self):
        workers = [multiprocessing.Process(target=write_link,
                                           args=(self.tmp.name, self.first, "same"))
                   for _ in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual(len(self.store.task(self.first)["links"]), 1)

    def test_close_page_and_late_callback_cannot_recreate_it(self):
        page = self.store.save_link(self.first, "https://example.com/report", "Report", "Review")
        self.store.select_section(self.first, "Review")
        self.store.activate_page(self.first, "Review", page)
        self.store.close_page(self.first, page)
        self.store.record_location(self.first, "Review", page, "https://example.com/late")
        row = self.store.task(self.first)
        self.assertEqual(row["links"], [])
        self.assertEqual(row["last_url"], "")
        self.assertIsNone(row["sections"]["Review"]["active_page"])

    def test_agent_add_does_not_replace_selected_page(self):
        page = self.store.save_link(self.first, "https://example.com/active", "Active")
        self.store.activate_page(self.first, "General", page)
        self.store.save_link(self.first, "https://example.com/agent", "Added")
        row = self.store.task(self.first)
        self.assertEqual(row["last_url"], "https://example.com/active")
        self.assertEqual(row["sections"]["General"]["active_page"], page)
        for name in ("", "x" * 81, "bad\nname"):
            with self.assertRaises(ValueError):
                self.store.add_section(self.first, name)

    def test_home_escapes_task_content(self):
        page = task_home({"label": "<script>steal()</script>",
                          "next_action": '<img src=x onerror="run()">',
                          "links": [{"url": "https://example.com/?a=1&b=2",
                                     "title": "<b>Report</b>"}]})
        self.assertNotIn("<script>", page)
        self.assertNotIn("<img ", page)
        self.assertIn("&lt;b&gt;Report&lt;/b&gt;", page)

    def test_unsupported_urls_and_passwords_are_not_stored(self):
        for url in ("javascript:alert(1)", "file:///etc/passwd",
                    "https://user:secret@example.com", "http://", "https://example.com/\nfoo"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                checked_url(url)
        self.assertEqual(checked_url("http://127.0.0.1:8796/report.html"),
                         "http://127.0.0.1:8796/report.html")


if __name__ == "__main__":
    unittest.main()
