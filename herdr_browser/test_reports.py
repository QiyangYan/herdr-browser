import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .reports import Reports, ReportServer, task_body, session_id
from .store import Store, atomic_json, task_key
from .router import process_token
from .cli import publish_task


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.context = {"schema": 1, "available": True, "endpoint": "local", "boot_id": "boot",
                        "focused_tab_id": "one", "tasks": [
                            {"tab_id": "one", "workspace_id": "w", "label": "First"},
                            {"tab_id": "two", "workspace_id": "w", "label": "Second"}]}
        self.connection = str(Path(self.tmp.name) / "herdr.sock")
        self.store.register_context(self.connection, self.context)
        self.owner = task_key(self.connection, self.context, "one")
        self.other = task_key(self.connection, self.context, "two")
        self.reports = Reports(self.tmp.name)
        self.server = ReportServer(self.store)
        self.server.ensure()
        self.addCleanup(self.server.close)

    def get(self, url):
        with urlopen(url, timeout=3) as result:
            return result.read().decode()

    def test_same_outcome_updates_in_place_and_keeps_all_revisions(self):
        first = self.reports.update(self.owner, {"summary": "First plan"}, new_title="Browser")
        second = self.reports.update(self.owner, {"summary": "Revised plan", "results": "Passed"})
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["revision"], 2)
        self.assertEqual(len(self.reports.list(self.owner)), 1)
        self.assertEqual(self.reports.history(self.owner, first["id"])[1]["summary"], "First plan")
        unchanged = self.reports.update(self.owner, {"summary": "Revised plan", "results": "Passed"})
        self.assertEqual(unchanged["revision"], 2)

    def test_publisher_commits_while_a_reader_keeps_its_snapshot(self):
        first = self.reports.update(self.owner, {"summary": "Before"}, goal="concurrent")
        with self.reports.connect() as reader:
            reader.execute("BEGIN")
            old = reader.execute("SELECT revision FROM goals WHERE id='concurrent'").fetchone()[0]
            with ThreadPoolExecutor(max_workers=1) as executor:
                new = executor.submit(self.reports.update, self.owner,
                                      {"summary": "After"}, "concurrent").result(timeout=2)
            self.assertEqual(reader.execute(
                "SELECT revision FROM goals WHERE id='concurrent'").fetchone()[0], old)
        self.assertEqual(new["revision"], first["revision"] + 1)
        self.assertEqual(self.reports.get(self.owner, "concurrent")["summary"], "After")
        self.assertEqual(len(self.reports.history(self.owner, "concurrent")), 2)

    def test_healthy_server_check_does_not_take_a_task_transaction(self):
        self.server.next_check = 0
        with patch.object(self.store, "transaction", side_effect=AssertionError("Unexpected task lock")):
            self.assertEqual(self.server.ensure(), self.server.base)

    def test_new_outcomes_and_actual_tasks_remain_separate(self):
        first = self.reports.update(self.owner, {"summary": "Tabs"}, new_title="Tabs")
        second = self.reports.update(self.owner, {"summary": "Auth"}, new_title="Sign-in")
        third = self.reports.update(self.other, {}, goal=first["id"], new_title="Other task")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(self.reports.list(self.owner)), 2)
        self.assertEqual(self.reports.current(self.other)["title"], third["title"])
        self.reports.update(self.owner, {"results": "Only tab work"}, goal=first["id"])
        self.assertEqual(self.reports.get(self.owner, second["id"])["results"], "")

    def test_home_resumes_active_outcome_after_another_is_completed(self):
        first = self.reports.update(self.owner, {}, goal="first", new_title="Active")
        self.reports.update(self.owner, {"status": "done"}, goal="second", new_title="Finished")
        self.assertEqual(self.reports.primary(self.owner)["id"], first["id"])
        self.reports.update(self.owner, {"status": "archived"}, goal="first")
        self.assertEqual(self.reports.primary(self.owner)["status"], "archived")

    def test_history_cursor_does_not_skip_revisions_during_updates(self):
        for index in range(25):
            self.reports.update(self.owner, {"summary": str(index)}, goal="history")
        first = self.reports.history(self.owner, "history")
        self.reports.update(self.owner, {"summary": "New revision"}, goal="history")
        older = self.reports.history(self.owner, "history", before=first[-1]["revision"])
        self.assertEqual([row["revision"] for row in older], [5, 4, 3, 2, 1])
        url = self.server.task_url(self.owner, "history") + "/history?before=6"
        self.assertEqual([row["revision"] for row in json.loads(self.get(url))], [5, 4, 3, 2, 1])

    def test_http_pages_data_history_etag_and_wrong_token(self):
        state = self.reports.update(self.owner, {"summary": "<script>bad()</script>"}, new_title="Report")
        url = self.server.task_url(self.owner, state["id"])
        self.assertIn("&lt;script&gt;", self.get(url))
        self.assertNotIn("<script>bad()", self.get(url))
        self.assertEqual(json.loads(self.get(url + "/data"))["state"]["revision"], 1)
        self.assertEqual(len(json.loads(self.get(url + "/history"))), 1)
        etag = f'"{self.owner}-{state["id"]}-1"'
        with patch("herdr_browser.reports.task_body", side_effect=AssertionError("Unchanged page rendered")):
            with self.assertRaises(HTTPError) as result:
                urlopen(Request(url + "/data", headers={"If-None-Match": etag}), timeout=3)
        self.assertEqual(result.exception.code, 304)
        with self.assertRaises(HTTPError) as result:
            self.get(url.replace(self.server.config["token"], "wrong-token"))
        self.assertEqual(result.exception.code, 404)

    def test_navigation_cache_tracks_external_changes_without_reading_unchanged_measurements(self):
        from .reports import conclusion_token
        first = self.reports.update(self.owner, {"summary": "Initial"}, goal="main", new_title="Main")
        self.reports.update(self.owner, {"parent": "main", "section_title": "Evidence"}, goal="child")
        rows = self.server.reports.navigation()
        main = next(row for row in rows if row["id"] == "main")
        self.assertEqual(conclusion_token(main), conclusion_token(first))
        self.assertNotIn("sections", main)
        self.assertNotIn("curves", main)
        # The warm path reads revision metadata, without decoding saved JSON.
        with patch("herdr_browser.reports.json.loads", side_effect=AssertionError("Unchanged state decoded")):
            self.assertEqual(self.server.reports.navigation(), rows)
        external = Reports(self.tmp.name)
        external.update(self.owner, {"summary": "Changed", "status": "done"}, goal="main")
        changed = next(row for row in self.server.reports.navigation() if row["id"] == "main")
        self.assertEqual(changed["status"], "done")
        self.assertNotEqual(conclusion_token(changed), conclusion_token(main))
        with external.connect() as db:
            db.execute("DELETE FROM goals WHERE owner=? AND id=?", (self.owner, "child"))
        self.assertEqual([row["id"] for row in self.server.reports.navigation()], ["main"])
        # A caller adding derived navigation fields must not contaminate the cache.
        changed["title"] = "Not a saved title"
        self.assertEqual(self.server.reports.navigation()[0]["title"], "Main")

    def test_session_index_preserves_conclusions_and_excludes_archived_and_other_sessions(self):
        state = self.reports.update(self.owner, {"next_action": "Inspect output", "current_task": "Inspect output"}, new_title="Active")
        completed = self.reports.update(self.other, {"status": "done"}, new_title="Completed")
        outsider = "f" * 64
        self.reports.update(outsider, {}, new_title="Other session")
        url = self.server.session_url(self.store.task(self.owner))
        body = self.get(url)
        self.assertIn("Active", body)
        self.assertIn("Inspect output", body)
        self.assertIn(">Completed<", body)
        self.reports.update(self.other, {"status": "archived"}, goal=completed["id"])
        self.assertNotIn(">Completed<", self.get(url))
        self.assertNotIn("Other session", body)
        self.assertIn(">Completed<", self.get(url + "?completed=true"))
        self.assertIn(self.server.task_url(self.owner, state["id"]), body)

    def test_index_includes_legacy_tasks_and_excludes_closed_tasks(self):
        url = self.server.session_url(self.store.task(self.owner))
        body = self.get(url)
        self.assertIn(">First<", body)
        self.assertIn(">Second<", body)
        self.assertIn("no living summary yet", body)
        self.assertIn("Session navigation", body)
        self.assertIn("setInterval(refresh, 5000)", body)
        self.assertIn("First", self.get(self.server.task_url(self.owner, "start")))
        context = dict(self.context, tasks=self.context["tasks"][:1])
        self.store.register_context(self.connection, context)
        self.assertNotIn(">Second<", self.get(url))
        self.assertFalse(self.store.task(self.other)["available_in_context"])

    def test_shared_server_takeover_preserves_url_and_data(self):
        state = self.reports.update(self.owner, {}, new_title="Stable")
        peer = ReportServer(self.store)
        self.addCleanup(peer.close)
        self.assertEqual(peer.ensure(), self.server.base)
        self.assertIsNone(peer.server)
        url = self.server.task_url(self.owner, state["id"])
        self.server.close()
        peer.next_check = 0
        peer.ensure()
        self.assertEqual(peer.task_url(self.owner, state["id"]), url)
        self.assertIn("Stable", self.get(url))

    def test_small_task_has_plain_links_with_escaped_purpose(self):
        state = self.reports.update(self.owner, {"summary": "Small", "plan": "One step"})
        self.assertNotIn('role="tablist"', task_body(state))
        state = self.reports.update(self.owner, {"references": [
            {"title": "<b>Source</b>", "url": "https://example.com/?a=1&b=2",
             "description": "Why <this> matters"}]})
        body = task_body(state)
        self.assertNotIn('role="tablist"', body)
        self.assertIn("Open related pages", body)
        self.assertIn("&lt;b&gt;Source&lt;/b&gt;", body)
        self.assertIn("Why &lt;this&gt; matters", body)

    def test_subplan_updates_main_without_losing_history_or_section_order(self):
        self.reports.update(self.owner, {"summary": "Train policies"}, goal="training")
        self.reports.update(self.owner, {"summary": "First evaluation"}, goal="evaluation")
        self.reports.update(self.owner, {"parent": "training", "section_title": "Evaluation"},
                            goal="evaluation")
        main = self.reports.current(self.owner)
        self.assertEqual(main["id"], "training")
        self.assertEqual(main["sections"][0]["title"], "Evaluation")
        self.reports.update(self.owner, {"sections": [
            {"id": "evaluation", "title": "Evaluation", "text": "Keep maintained"},
            {"id": "setup", "title": "Setup", "text": "Recorded setup"}]}, goal="training")
        revision = self.reports.get(self.owner, "training")["revision"]
        self.reports.update(self.owner, {"results": "New evidence"}, goal="evaluation")
        main = self.reports.get(self.owner, "training")
        self.assertEqual(main["revision"], revision + 1)
        self.assertEqual([s["id"] for s in main["sections"]], ["evaluation", "setup"])
        self.assertIn("New evidence", main["sections"][0]["text"])
        self.assertEqual(self.reports.history(self.owner, "evaluation")[-1]["summary"],
                         "First evaluation")
        self.assertEqual(self.reports.primary(self.owner)["id"], "training")
        self.assertEqual(self.reports.get(self.owner, "training")["summary"], "Train policies")

    def test_subplan_parent_must_be_existing_root_in_same_owner(self):
        self.reports.update(self.other, {}, goal="foreign")
        original = self.reports.update(self.owner, {}, goal="evaluation")
        for parent in ("evaluation", "missing", "foreign"):
            with self.subTest(parent=parent), self.assertRaises(ValueError):
                self.reports.update(self.owner, {"parent": parent}, goal="evaluation")
        self.assertEqual(self.reports.get(self.owner, "evaluation"), original)
        self.reports.update(self.owner, {}, goal="training")
        self.reports.update(self.owner, {"parent": "training"}, goal="evaluation")
        with self.assertRaises(ValueError):
            self.reports.update(self.owner, {"parent": "evaluation"}, goal="nested")
        with self.assertRaises(ValueError):
            self.reports.update(self.owner, {"parent": ""}, goal="evaluation")

    def test_old_subplan_url_opens_parent_and_index_lists_main_only(self):
        self.reports.update(self.owner, {"title": "Train main"}, goal="training")
        self.reports.update(self.owner, {"parent": "training", "title": "Evaluate child"},
                            goal="evaluation")
        main_url = self.server.task_url(self.owner, "training")
        child_url = self.server.task_url(self.owner, "evaluation")
        with urlopen(child_url, timeout=3) as response:
            self.assertEqual(response.url, main_url + "#evaluation")
            self.assertIn("Train main", response.read().decode())
        data = json.loads(self.get(child_url + "/data"))
        self.assertEqual(data["state"]["id"], "training")
        index = self.get(self.server.session_url(self.store.task(self.owner)))
        self.assertIn(">Train main<", index)
        self.assertIn(">Evaluate child<", index)
        snapshot = json.loads(self.get(self.server.session_url(self.store.task(self.owner)) + "/data"))
        goals = [goal for space in snapshot["spaces"] for task in space["tasks"] for goal in task["reports"]]
        self.assertNotIn("evaluation", [goal["id"] for goal in goals])
        main = next(goal for goal in goals if goal["id"] == "training")
        self.assertEqual([step["id"] for step in main["steps"]], ["evaluation"])
        updates = json.loads(self.get(main_url + "/updates"))["rows"]
        self.assertEqual({row["id"] for row in updates}, {"training", "evaluation"})
        self.assertTrue(any(row["part"] == "Evaluate child" for row in updates))

    def test_combined_history_cursor_remains_stable_during_new_updates(self):
        self.reports.update(self.owner, {}, goal="training")
        for index in range(14):
            self.reports.update(self.owner, {"parent": "training", "summary": str(index)},
                                goal="evaluation")
        first = self.reports.related_history(self.owner, "training", limit=10)
        last = first[-1]
        cursor = [last["updated"], last["id"], last["revision"]]
        self.reports.update(self.owner, {"summary": "New"}, goal="evaluation")
        older = self.reports.related_history(self.owner, "training", before=cursor, limit=100)
        self.assertEqual(len(first) + len(older), 29)
        self.assertFalse({(r["id"], r["revision"]) for r in first}
                         & {(r["id"], r["revision"]) for r in older})

    def test_visuals_are_safe_and_subplan_chart_updates_main_atomically(self):
        table = {"title": "Inputs", "columns": ["Policy", "Input"],
                 "rows": [["Depth", "<script>unsafe</script>"]], "caption": "Recorded setup"}
        self.reports.update(self.owner, {"tables": [table]}, goal="training")
        chart = {"title": "Recorded success", "unit": "Episodes", "maximum": 128,
                 "values": [{"label": "Depth", "value": 125, "note": "Round 250"}],
                 "caption": "Different rounds; not matched convergence."}
        self.reports.update(self.owner, {"parent": "training", "figures": [chart]},
                            goal="evaluation")
        main = self.reports.get(self.owner, "training")
        body = task_body(main)
        self.assertIn('<table>', body)
        self.assertIn("&lt;script&gt;unsafe&lt;/script&gt;", body)
        self.assertNotIn("<script>unsafe</script>", body)
        self.assertIn("125 / 128", body)
        self.assertIn('width:97.6562%', body)
        self.assertIn("not matched convergence", body)
        self.assertIn('class="recorded-details"', body)
        self.assertEqual(main["sections"][0]["figures"], [chart])
        for invalid in (
            dict(table, rows=[["Missing column"]]),
            dict(chart, maximum=0),
            dict(chart, values=[{"label": "Fake", "value": 129, "note": ""}]),
            dict(chart, values=[{"label": "Bad", "value": float("nan"), "note": ""}]),
        ):
            field = "tables" if "columns" in invalid else "figures"
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.reports.update(self.owner, {field: [invalid]}, goal="evaluation")
        self.assertEqual(self.reports.get(self.owner, "training"), main)

    def test_decision_evidence_stays_visible_and_curves_do_not_fill_missing_measurements(self):
        curve = {"title": "Measured learning", "caption": "One seed; no extrapolation.",
                 "x_label": "Optimizer updates", "y_label": "Success (%)",
                 "x_maximum": 500, "y_maximum": 100, "gap": 100,
                 "series": [{"label": "PC", "points": [{"x": 100, "y": 20},
                                                     {"x": 200, "y": 30}, {"x": 400, "y": 25}]},
                            {"label": "Depth", "points": []}]}
        limits = "One seed. " + "Essential limitation. " * 30 + "\n\nDo not select a final policy yet."
        main = self.reports.update(self.owner, {"curves": [curve], "results_title": "Decision limits",
                                               "results": limits, "next_action": "Compare the next budget"},
                                   goal="training")
        body = task_body(main)
        self.assertIn("Do not select a final policy yet.", body)
        self.assertNotIn('id="full-results"', body)
        self.assertEqual(body.count("<polyline"), 2)
        self.assertEqual(body.count('class="curve-point"'), 3)
        self.assertIn("Depth · no measurement", body)
        self.assertLess(body.index('class="learning-curve"'), body.index("Decision limits"))
        self.assertLess(body.index("Decision limits"), body.index("Next action"))
        self.reports.update(self.owner, {"parent": "training", "summary": "Supporting detail"},
                            goal="evaluation")
        self.assertEqual(self.reports.get(self.owner, "training")["curves"], [curve])
        for points in ([{"x": -1, "y": 0}], [{"x": 20, "y": 10}, {"x": 20, "y": 15}],
                       [{"x": 10, "y": float("nan")}], [{"x": 501, "y": 20}]):
            invalid = dict(curve, series=[{"label": "Invalid", "points": points}])
            with self.subTest(points=points), self.assertRaises(ValueError):
                self.reports.update(self.owner, {"curves": [invalid]}, goal="training")

    def test_parallel_updates_preserve_revision_sequence(self):
        self.reports.update(self.owner, {}, goal="parallel")
        def update(index):
            return self.reports.update(self.owner, {"summary": str(index)}, goal="parallel")
        with ThreadPoolExecutor(max_workers=6) as pool:
            rows = list(pool.map(update, range(6)))
        self.assertEqual(sorted(row["revision"] for row in rows), list(range(2, 8)))
        self.assertEqual(len(self.reports.history(self.owner, "parallel")), 7)

    def test_invalid_update_preserves_existing_state(self):
        original = self.reports.update(self.owner, {"summary": "Keep"}, goal="test")
        for bad in ({"unknown": "field"}, {"status": "fake"}, {"summary": None},
                    {"references": [{"url": "file:///etc/passwd", "title": "Unsafe"}]},
                    {"sections": [{"id": "overview", "title": "Duplicate", "text": ""}]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.reports.update(self.owner, bad, goal="test")
        self.assertEqual(self.reports.get(self.owner, "test"), original)

    def test_cli_publishes_to_actual_origin_without_stealing_focus(self):
        runtime = Path(self.tmp.name) / "runtime"
        runtime.mkdir()
        context = runtime / "context.json"
        atomic_json(context, self.context)
        inbox = runtime / "urls"
        inbox.mkdir()
        instances = Path(self.tmp.name) / "instances"
        instances.mkdir()
        atomic_json(instances / "one.json", {
            "pid": os.getpid(), "process_token": process_token(os.getpid()),
            "connection": self.connection, "context": str(context),
            "inbox": str(inbox), "routing_enabled": True, "focused_at": 1,
            "reports_url": self.server.base})
        source = runtime / "update.json"
        source.write_text(json.dumps({"summary": "Published", "next_action": "Verify"}))
        args = argparse.Namespace(file=source, status=None, id="workflow", new="Workflow",
                                  export=None, focus=False)
        with patch.dict(os.environ, HERDR_BROWSER_HOME=self.tmp.name,
                        HERDR_TAB_ID="two", HERDR_SOCKET_PATH=self.connection):
            publish_task(args)
        state = self.reports.get(self.other, "workflow")
        self.assertEqual(state["summary"], "Published")
        request = json.loads(next(inbox.glob("*.json")).read_text())
        self.assertEqual(request["key"], self.other)
        self.assertTrue(request["background"])
        self.assertFalse(request["focus"])
        self.assertEqual(len(self.store.task(self.other)["links"]), 1)
        self.assertEqual(self.store.task(self.owner)["links"], [])
        args.new = None
        args.export = runtime / "snapshot.html"
        source.write_text(json.dumps({"results": "Verified"}))
        with patch.dict(os.environ, HERDR_BROWSER_HOME=self.tmp.name,
                        HERDR_TAB_ID="two", HERDR_SOCKET_PATH=self.connection):
            publish_task(args)
        self.assertEqual(len(self.store.task(self.other)["links"]), 1)
        self.assertEqual(self.reports.get(self.other, "workflow")["revision"], 2)
        exported = args.export.read_text()
        self.assertIn("Revision 1", exported)
        self.assertIn('data-live="false"', exported)
        # Portable exports intentionally link back to their exact originating task.
        self.assertIn('href="' + self.server.task_url(self.other, "workflow") + '"', exported)
        args.id = "evaluation"
        args.new = "Evaluate workflow"
        args.parent = "workflow"
        args.section_title = "Evaluation"
        old_child_url = self.server.task_url(self.other, "evaluation")
        self.store.save_link(self.other, old_child_url, "Old evaluation home", "Task pages")
        original_main_id = self.store.task(self.other)["links"][0]["id"]
        source.write_text(json.dumps({"summary": "Check the same main task"}))
        with patch.dict(os.environ, HERDR_BROWSER_HOME=self.tmp.name,
                        HERDR_TAB_ID="two", HERDR_SOCKET_PATH=self.connection):
            publish_task(args)
        self.assertEqual(len(self.store.task(self.other)["links"]), 1)
        page = self.store.task(self.other)["links"][0]
        self.assertEqual(page["id"], original_main_id)
        self.assertIn(old_child_url, page["aliases"])
        self.assertEqual(self.reports.primary(self.other)["id"], "workflow")
        self.assertIn("Evaluation", args.export.read_text())
        self.assertEqual(self.reports.get(self.other, "evaluation")["parent"], "workflow")

    def test_old_companion_bridge_hands_stable_server_back_to_new_companion(self):
        from .serve_reports import start_bridge, available_base
        instances = Path(self.tmp.name) / "instances"
        instances.mkdir()
        info = {"pid": os.getpid(), "process_token": process_token(os.getpid())}
        path = instances / "old.json"
        atomic_json(path, info)
        self.server.close()
        base = start_bridge(self.tmp.name)
        peer = ReportServer(self.store)
        self.addCleanup(peer.close)
        self.assertEqual(peer.ensure(), base)
        self.assertIsNone(peer.server)
        atomic_json(path, dict(info, reports_url=base))
        deadline = time.monotonic() + 4
        while available_base(self.tmp.name) and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertIsNone(available_base(self.tmp.name))
        peer.next_check = 0
        self.assertEqual(peer.ensure(), base)
        self.assertIsNotNone(peer.server)


if __name__ == "__main__":
    unittest.main()
