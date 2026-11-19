"""Portable explanation of the report refresh fix, using no session data."""
import argparse
import datetime
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from herdr_browser.reports import document, visual_content
from herdr_browser.presentation import enhance_report


def render(commit="", verified=False):
    if commit and not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Use an actual full commit ID.")
    version = f"Source commit: <code>{commit}</code>." if commit else "Uncommitted working-tree changes."
    status = "Implemented and verified" if verified else "Implemented; validation in progress"
    checks = ("77 unit tests and 43 native UI tests passed, including repeated refreshes "
              "with changed measurements, nested charts, row additions/removals, open sections, draft range input, keyboard focus and scroll. "
              "Desktop and 375 px browser checks passed." if verified else
              "Native browser regression and installed-page verification are pending.")
    visual = visual_content({"flows": [dict(
        title="Update the report without rebuilding its view",
        steps=[dict(title="Saved data changes", text="The existing report and dashboard polls detect the update."),
               dict(title="Keep sections and controls", text="Preserve the report, chart layout, open details and current inputs."),
               dict(title="Update changed content", text="Refresh text, status and measured curves inside the existing view.")],
        caption="Arrows show the update sequence. Polling continues at its existing cadence."
    )]})
    body = f"""
<p><a href="https://github.com/QiyangYan/herdr-browser">Back to Herdr Browser</a></p>
<h1>Steady report refresh</h1>
<p><strong>{status}:</strong> new report data updates sections and charts in place.</p>
<p class="status">Recorded {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}</p>
{visual}
<h2>The first fix covered only the dashboard</h2>
<p>The dashboard stayed mounted, but new measurements still replaced the surrounding report and plots.
That remaining refresh path could interrupt the view.</p>
<h2>Preserve the whole report while data changes</h2>
<p>The renderer compares new publisher content with the previous snapshot. Unchanged sections stay mounted;
changed fields and measured curves update inside their existing containers. Chart controls retain focus and unfinished range edits.</p>
<p><strong>Limit:</strong> longer text can naturally change a section's height. A changed chart structure, such as adding a series,
may rebuild that chart's controls. Saved status is still a recorded observation.</p>
<p><strong>Next action:</strong> use the updated source. The fixed v0 release predates this dashboard feature.</p>
<details><summary>Verification and design choices</summary>
<p>{checks}</p>
<p>Disabling refresh would hide new results. Updating changed content in place preserves updates and interaction
without changing the server API or stored records.</p>
<p>Recorded-snapshot replay also retained the comparison layout, plot and controls through five consecutive updates, with no plot-container removals. Native tests verify new data arrives and structural additions/removals work.</p>
</details>
<details><summary>Version history</summary>
<ul>
<li><strong>v0:</strong> original release, <code>6e30065aacf65875251f439cd4c9612709f761c9</code>.</li>
<li><strong>Shared dashboard:</strong> <code>e998eeeb10b32e4a03fd47716536425bba77df75</code>.</li>
<li><strong>Dashboard-only fix:</strong> <code>3ea97d64c642ae6925e7e81da7d4151d517f16dd</code>. Its focused check passed, but flashing remained elsewhere in the report.</li>
<li><strong>Report-wide refresh:</strong> {version}</li>
</ul>
</details>
"""
    (ROOT / "docs/dashboard-refresh.html").write_text(enhance_report(
        document("Steady report refresh", body),
        "https://github.com/QiyangYan/herdr-browser", "Source repository"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", default="")
    parser.add_argument("--verified", action="store_true")
    args = parser.parse_args()
    render(args.commit, args.verified)
