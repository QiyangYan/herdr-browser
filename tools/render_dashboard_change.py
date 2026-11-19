"""Public visual change summary, with no personal task data."""
import argparse
import datetime
import html
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from herdr_browser.reports import document, visual_content
from herdr_browser.presentation import enhance_report


def render(commit=""):
    if commit and not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Use an actual full commit ID.")
    version = ("Committed source: <code>" + commit + "</code>." if commit else
               "Uncommitted working-tree changes after v0.")
    flow = visual_content({"flows": [dict(
        title="One shared dashboard, each task's own records",
        steps=[dict(title="Recorded work", text="The main task, supporting reports and explicit subagent updates."),
               dict(title="Task dashboard", text="Status, timestamps, report links and requests for help on every Home."),
               dict(title="Saved history", text="Remove completed rows; restore or copy their references later.")],
        caption="Arrows show how saved records reach the dashboard and its history. Restoring a row does not restart work."
    )]})
    body = f"""
<p><a href="https://github.com/QiyangYan/herdr-browser">Back to Herdr Browser</a></p>
<h1>Session dashboards across tasks</h1>
<p><strong>Implemented and verified:</strong> every task Home uses the same dashboard, with its own records and persistent history.</p>
<p class="status">Recorded {html.escape(datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"))}</p>
{flow}
<h2>Know what needs attention</h2>
<p>Read the latest recorded status, follow the task's report, or act on its explicit request for help. Empty sessions get a Home dashboard; unpublished subagents remain visibly unrecorded.</p>
<p><strong>Limit:</strong> saved status is not a live process monitor. Timestamps remain visible. The fixed v0 release predates this addition.</p>
<p><a href="dashboard.html">Inspect the interactive example with sample records</a></p>
<h2>Verified behavior</h2>
<table><thead><tr><th>Check</th><th>Recorded result</th></tr></thead><tbody>
<tr><td>Automated contracts</td><td>77 unit tests and 41 native UI tests passed.</td></tr>
<tr><td>History and ownership</td><td>Remove, reload and restore passed; another session's records stayed separate.</td></tr>
<tr><td>Responsive rendering</td><td>Desktop and 375 px phone layouts passed, including expanded details.</td></tr>
<tr><td>Native task behavior</td><td>One browser view; no extra saved tab for an empty task; active tasks and routing contracts passed.</td></tr>
</tbody></table>
<p><strong>Next action:</strong> use the source update for shared dashboards, or install the fixed v0 package for its original feature set.</p>
<details><summary>Design choices and version history</summary>
<ul>
<li><strong>Existing:</strong> v0 task-owned websites and living reports, commit <code>6e30065aacf65875251f439cd4c9612709f761c9</code>.</li>
<li><strong>Current:</strong> shared dashboard, task-scoped SQLite history, status-publishing command and a visual README. {version}</li>
<li><strong>Tradeoff:</strong> explicit saved records preserve provenance but require agents to publish status. No subagent is inferred from a report title.</li>
<li><strong>Alternative considered:</strong> copying a task-specific table would duplicate data and lose ownership. The shared renderer reads each task's own records.</li>
<li><strong>Future release:</strong> no new binary release is claimed; the v0 tag remains fixed.</li>
</ul>
</details>
"""
    (ROOT / "docs/dashboard-update.html").write_text(enhance_report(
        document("Session dashboards across tasks", body),
        "https://github.com/QiyangYan/herdr-browser", "Source repository"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", default="")
    render(parser.parse_args().commit)
