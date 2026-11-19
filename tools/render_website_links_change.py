"""Portable explanation of website navigation; excludes private task data."""
import argparse
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
    version = f"Source commit <code>{commit}</code>." if commit else "Uncommitted working-tree change."
    destinations = visual_content({"tables": [{
        "title": "Each website link keeps its intended destination",
        "columns": ["Link destination", "What happens"],
        "rows": [
            ["A saved report", "Open the report with its original URL, even when its old task is no longer active."],
            ["A report in a live task", "Switch to the task identified by that report."],
            ["An ordinary website", "Open the website in the companion; retain the source report."],
            ["A section in this report", "Open the section and move to it."],
        ],
        "caption": "A saved report remains a reference to its original task. Opening it does not resume that task or assign the report to a new owner.",
        "row_headers": True,
    }]})
    body = f"""
<p><a href="https://github.com/QiyangYan/herdr-browser">Back to Herdr Browser</a></p>
<h1>Website links across sessions</h1>
<p><strong>Implemented:</strong> links inside report pages can open saved reports after their original task session ends.</p>
<p>Previously, Herdr required the report's original task to be active before following its link.
That rejected a click even when the saved report was still available.</p>
{destinations}
<p><strong>Limit:</strong> the destination still needs to be available. This change does not recover a deleted report or start an unavailable website.</p>
<p><strong>Next action:</strong> apply the installed update, then click the report link again.</p>
<details><summary>Verification and design choice</summary>
<ul>
<li><strong>Saved report:</strong> a real mouse click opens a report from an earlier server session, with both ordinary and new-window links.</li>
<li><strong>Ownership:</strong> its original report URL and owner remain unchanged; no task switch or new report is created.</li>
<li><strong>Other links:</strong> native checks cover section jumps, ordinary websites and switching to a verified live task.</li>
<li><strong>Rejected alternative:</strong> matching an old report to a task by title could attach it to unrelated work. The reference keeps its exact owner instead.</li>
</ul>
</details>
<details><summary>Version history</summary>
<ul>
<li><strong>Baseline:</strong> <code>bcdc372c84fd4928d10fa42d851d0d37359af627</code>, including the earlier terminal-link retry.</li>
<li><strong>Website navigation:</strong> {version}</li>
</ul>
</details>
"""
    script = "<script>" + (ROOT / "herdr_browser/interactive_curve.js").read_text() + \
        "\nInteractiveDiagram.mountAll(document);</script>"
    (ROOT / "docs/website-links.html").write_text(enhance_report(
        document("Website links across sessions", body, script),
        "https://github.com/QiyangYan/herdr-browser", "Source repository"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", default="")
    render(parser.parse_args().commit)
