"""Behavioral checks for ownership, history and saved report compatibility."""
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .dashboard import Dashboard, checked_record, legacy_id, start_state
from .reports import Reports, ReportServer, task_document
from .store import Store, task_key


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.reports = Reports(self.tmp.name)
        self.store = Store(self.tmp.name)
        context = dict(schema=1, available=True, endpoint="local", boot_id="test",
                       focused_tab_id="one", tasks=[
                           dict(tab_id="one", workspace_id="w", label="First"),
                           dict(tab_id="two", workspace_id="w", label="Second")])
        self.store.register_context("/test.sock", context)
        self.a = task_key("/test.sock", context, "one")
        self.b = task_key("/test.sock", context, "two")
        for owner in (self.a, self.b):
            self.reports.update(owner, dict(summary="Own work", status="working"), goal="home")
        self.dashboard = Dashboard(self.reports)
        self.record = dict(agent_id="/root/check", agent_label="Validation",
                           task="Verify the result", status="complete", summary="Check passed.",
                           updated_at="2026-11-01T10:00:00Z",
                           report_url="https://example.org/result")

    def test_main_and_subplans_are_automatic_and_owners_never_mix(self):
        self.reports.update(self.a, dict(parent="home", title="Evaluation", status="done"),
                            goal="eval")
        self.dashboard.upsert(self.b, "home", "agent", self.record)
        first = self.dashboard.snapshot(self.a, "home", "http://127.0.0.1:1/token")
        self.assertEqual({x["id"] for x in first["rows"]}, {"main:home", "report:eval"})
        self.assertNotIn("Validation", json.dumps(first))
        second = self.dashboard.snapshot(self.b, "home")
        self.assertEqual({x["id"] for x in second["rows"]}, {"main:home", "agent"})
        self.assertEqual(self.dashboard.snapshot(self.a, "eval")["goal"], "home")

    def test_history_survives_restart_refresh_and_restore_without_changing_work(self):
        self.dashboard.upsert(self.a, "home", "agent", self.record)
        before = self.reports.get(self.a, "home")
        self.dashboard.change(self.a, "home", "agent", "archive")
        restarted = Dashboard(Reports(self.tmp.name))
        hidden = restarted.snapshot(self.a, "home")
        self.assertEqual(hidden["history"][0]["agent_id"], "/root/check")
        self.reports.update(self.a, dict(summary="A newer main report"), goal="home")
        self.assertEqual(restarted.snapshot(self.a, "home")["history"][0]["summary"], "Check passed.")
        restored = restarted.change(self.a, "home", "agent", "restore")
        self.assertEqual(restored["history"], [])
        self.assertEqual(next(x for x in restored["rows"] if x["id"] == "agent")["status"], "complete")
        self.assertEqual(self.reports.get(self.a, "home")["status"], before["status"])
        self.assertEqual(self.dashboard.snapshot(self.b, "home")["history"], [])

    def test_running_work_cannot_be_hidden_and_resuming_unhides_same_identity(self):
        self.dashboard.upsert(self.a, "home", "agent", self.record)
        self.dashboard.change(self.a, "home", "agent", "archive")
        self.dashboard.upsert(self.a, "home", "agent", dict(self.record, status="running"))
        self.assertEqual(self.dashboard.snapshot(self.a, "home")["history"], [])
        with self.assertRaises(ValueError):
            self.dashboard.change(self.a, "home", "agent", "archive")
        with self.assertRaises(ValueError):
            self.dashboard.change(self.b, "home", "agent", "archive")

    def test_changed_completion_returns_to_dashboard(self):
        self.dashboard.upsert(self.a, "home", "agent", self.record)
        self.dashboard.change(self.a, "home", "agent", "archive")
        self.dashboard.upsert(self.a, "home", "agent", dict(self.record, summary="A new result."))
        snapshot = self.dashboard.snapshot(self.a, "home")
        self.assertFalse(snapshot["history"])
        self.assertTrue(any(row["summary"] == "A new result." for row in snapshot["rows"]))

    def test_legacy_table_stays_live_while_imported_history_keeps_its_reference(self):
        table = dict(title="Subagent dashboard",
                     columns=["Subagent", "Task", "Status", "Last updated", "Needs your help"],
                     rows=[["Validation", "Verify the result", "Complete — Check passed.",
                            self.record["updated_at"], "Nothing needed from you."]], caption="Recorded")
        self.reports.update(self.a, dict(tables=[table]), goal="home")
        identity = legacy_id("Validation", "Verify the result")
        self.dashboard.upsert(self.a, "home", identity, self.record, hidden=True)
        self.assertEqual(self.dashboard.snapshot(self.a, "home")["history"][0]["agent_id"], "/root/check")
        table["rows"][0][2] = "Running — Checking a new result."
        table["rows"][0][3] = "2026-11-02T10:00:00Z"
        self.reports.update(self.a, dict(tables=[table]), goal="home")
        current = self.dashboard.snapshot(self.a, "home")
        self.assertFalse(current["history"])
        row = next(x for x in current["rows"] if x["id"] == identity)
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["summary"], "Checking a new result.")
        self.assertEqual(row["report_url"], self.record["report_url"])

    def test_validation_escapes_payload_and_rejects_unsafe_links_and_ambiguous_time(self):
        for value in (dict(self.record, report_url="javascript:alert(1)"),
                      dict(self.record, updated_at="2026-11-01T10:00:00"),
                      dict(self.record, status="alive"), dict(self.record, task="")):
            with self.assertRaises(ValueError):
                checked_record(value, "agent")
        value = dict(self.record, summary="</script><script>alert(1)</script>")
        self.dashboard.upsert(self.a, "home", "agent", value)
        body = task_document(self.reports.get(self.a, "home"), "http://127.0.0.1:1/t/session/s",
                             live=False, dashboard=self.dashboard.snapshot(self.a, "home"))
        self.assertNotIn(value["summary"], body)
        self.assertIn("\\u003c/script>", body)
        self.assertIn('id="herdr-dashboard-data"', body)
        self.assertIn('data-live="false"', body)

    def test_api_enforces_origin_and_exact_task_identity(self):
        server = ReportServer(self.store)
        server.ensure()
        self.addCleanup(server.close)
        self.dashboard.upsert(self.a, "home", "agent", self.record)
        url = server.task_url(self.a, "home") + "/dashboard"
        def read(target, body=None, origin=None):
            request = Request(target, data=json.dumps(body).encode() if body else None,
                              headers={"Content-Type": "application/json",
                                       **({"Origin": origin} if origin else {})})
            with urlopen(request, timeout=3) as response:
                return json.load(response)
        self.assertEqual(len(read(url)["rows"]), 2)
        change = dict(id="agent", action="archive")
        with self.assertRaises(HTTPError) as error:
            read(url, change, "https://unrelated.example")
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(HTTPError):
            read(url.replace(self.a, self.b), change)
        self.assertEqual(len(read(url, change)["history"]), 1)
        self.assertFalse(read(url.replace(self.a, self.b))["history"])
        self.assertEqual(len(read(url, dict(id="agent", action="restore"))["rows"]), 2)
        self.assertIn("Session dashboard", urlopen(server.task_url(self.a, "home")).read().decode())

    def test_unpublished_real_task_gets_a_dashboard_without_inventing_subagents(self):
        server = ReportServer(self.store)
        server.ensure()
        self.addCleanup(server.close)
        url = server.task_url(self.a, "start")
        with urlopen(url + "/dashboard", timeout=3) as response:
            value = json.load(response)
        self.assertEqual(len(value["rows"]), 1)
        self.assertEqual(value["rows"][0]["agent_id"], "")
        self.assertEqual(value["rows"][0]["status"], "unknown")
        self.assertIsNone(self.reports.get(self.a, "start"))


if __name__ == "__main__":
    unittest.main()
