"""A portable, public-safe README example. All task records are illustrative."""
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from herdr_browser.reports import Reports, task_document
from herdr_browser.dashboard import Dashboard


def build():
    with tempfile.TemporaryDirectory() as home:
        reports = Reports(home)
        state = reports.update("example", dict(
            title="Build a small website",
            objective="Help a teammate review a working site with clear evidence and one next action.",
            current_task="Review the site and its checks",
            summary="**Illustrative example:** the site is ready for review; the phone layout still needs a check.",
            status="working",
            next_action="Review the preview and decide whether the phone layout is ready.",
            progress_overview=dict(completed="Site implemented; navigation verified.",
                                   current="Phone layout review.",
                                   next="Approve the final preview.",
                                   caption="Sample workflow, not a report of measured work."),
            sections=[dict(id="dashboard-how", title="How this dashboard works",
                           text="- **Each task owns its records:** switching tasks changes the report and dashboard.\n"
                                "- **Status is recorded:** timestamps show when an agent reported, not proof of a live process.\n"
                                "- **History is reversible:** completed rows can be removed, restored or copied without restarting work.\n"
                                "- **Version:** shared dashboards are a source update after v0. The v0 release remains fixed.",
                           group="About this example")],
            main_sections=[dict(title="One place to choose the next action", phase="current",
                                text="Read the current status, open the supporting report, or follow the request for help. "
                                     "[How the dashboard works](#dashboard-how).",
                                sections=["dashboard-how"])]
        ), goal="website")
        # Stable example dates are explicitly illustrative, never live measurements.
        with reports.connect() as db:
            db.execute("UPDATE goals SET updated=? WHERE owner=?", (1795046400, "example"))
        state = reports.get("example", "website")
        dashboard = Dashboard(reports)
        for identity, label, task, status, summary, needs in [
            ("layout", "Layout", "Check the phone layout", "waiting", "Desktop fits; phone review is pending.", "Review the phone preview."),
            ("navigation", "Navigation", "Verify links and keyboard access", "complete", "Links and focus order checked.", ""),
        ]:
            dashboard.upsert("example", "website", identity, dict(
                agent_id="/example/" + identity, agent_label=label, task=task,
                status=status, summary=summary, needs_help=needs,
                updated_at="2026-11-19T02:00:00Z"))
        dashboard.upsert("example", "website", "setup", dict(
            agent_id="/example/setup", agent_label="Setup", task="Prepare the site",
            status="complete", summary="Starter files are ready.",
            updated_at="2026-11-19T01:30:00Z"))
        dashboard.change("example", "website", "setup", "archive")
        snapshot = dashboard.snapshot("example", "website")
        body = task_document(state, "", live=False, dashboard=snapshot)
        body = body.replace('<span>Standalone task snapshot</span>',
            '<a href="https://github.com/QiyangYan/herdr-browser">Back to Herdr Browser</a>')
        (ROOT / "docs/dashboard.html").write_text(body)


if __name__ == "__main__":
    build()
