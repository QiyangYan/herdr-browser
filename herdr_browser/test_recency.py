from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .recency import grouped_tasks
from .store import Store, task_key


class RecencyTests(unittest.TestCase):
    def test_boundaries_unknown_dates_and_newest_first(self):
        now = 100 * 86400
        records = {
            "newest": {"last_activity_at": now},
            "recent": {"last_activity_at": now - 6 * 86400},
            "week": {"last_activity_at": now - 7 * 86400},
            "month": {"last_activity_at": now - 30 * 86400},
            "invalid": {"last_activity_at": float("nan")},
        }
        tasks = [{"tab_id": name, "label": name} for name in
                 ("month", "recent", "unknown", "week", "newest", "invalid")]
        groups = grouped_tasks(tasks, records, lambda row: row["tab_id"], now)
        self.assertEqual([row["tab_id"] for row, _ in groups[0][1]], ["newest", "recent"])
        self.assertEqual([row["tab_id"] for row, _ in groups[1][1]], ["week"])
        self.assertEqual({row["tab_id"] for row, _ in groups[2][1]},
                         {"month", "unknown", "invalid"})

    def test_context_polling_does_not_make_old_tasks_recent(self):
        with tempfile.TemporaryDirectory() as home:
            store = Store(home)
            context = {"endpoint": "local", "boot_id": "test",
                       "tasks": [{"tab_id": "one", "workspace_id": "w",
                                  "label": "Old task", "agent_status": "idle"}]}
            store.register_context("/test/socket", context)
            key = task_key("/test/socket", context, "one")
            self.assertNotIn("last_activity_at", store.task(key))
            with patch("herdr_browser.store.time.time", return_value=100):
                store.touch_task(key)
            with patch("herdr_browser.store.time.time", return_value=200):
                store.register_context("/test/socket", context)
            self.assertEqual(store.task(key)["last_activity_at"], 100)
            context["tasks"][0]["agent_status"] = "done"
            with patch("herdr_browser.store.time.time", return_value=300):
                store.register_context("/test/socket", context)
            self.assertEqual(store.task(key)["last_activity_at"], 300)

    def test_actual_page_publication_records_activity(self):
        with tempfile.TemporaryDirectory() as home:
            store = Store(home)
            context = {"endpoint": "local", "boot_id": "test",
                       "tasks": [{"tab_id": "one", "workspace_id": "w", "label": "Task"}]}
            store.register_context("/test/socket", context)
            key = task_key("/test/socket", context, "one")
            with patch("herdr_browser.store.time.time", return_value=400):
                store.save_link(key, "https://example.com/report", "Report")
            self.assertEqual(Store(home).task(key)["last_activity_at"], 400)
