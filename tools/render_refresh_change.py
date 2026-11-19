"""Portable explanation of the dashboard refresh fix, using no session data."""
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
    checks = ("77 unit tests and 42 native UI tests passed, including repeated refreshes "
              "with nested charts, changed statuses, open history, keyboard focus and scroll. "
              "Desktop and 375 px browser checks passed." if verified else
              "Native browser regression and installed-page verification are pending.")
    visual = visual_content({"flows": [dict(
        title="New data updates the existing dashboard",
        steps=[dict(title="Saved data changes", text="The existing report and dashboard polls detect the update."),
               dict(title="Keep the dashboard", text="Its rows, history and controls stay mounted."),
               dict(title="Change the affected fields", text="New status, text and timestamps appear in place.")],
        caption="Arrows show the update sequence. Polling continues at its existing cadence."
    )]})
    body = f"""
<p><a href="https://github.com/QiyangYan/herdr-browser">Back to Herdr Browser</a></p>
<h1>Steady dashboard refresh</h1>
<p><strong>{status}:</strong> new report data no longer rebuilds the session dashboard.</p>
<p class="status">Recorded {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}</p>
{visual}
<h2>Why the dashboard could flash</h2>
<p>A report update removed and recreated the dashboard. A timestamp-only dashboard update also replaced every row.
A chart nested inside a comparison layout could trigger an unnecessary report rebuild.</p>
<h2>What stays steady</h2>
<p>Report updates keep the dashboard connected. Dashboard updates change fields within stable rows,
and chart detection recognizes nested charts. Open history and focused controls survive updates.</p>
<p><strong>Limit:</strong> a changed row can naturally grow or shrink. Saved status still describes recorded work;
it does not establish whether an agent process is alive.</p>
<p><strong>Next action:</strong> use the updated source. The fixed v0 release predates this dashboard feature.</p>
<details><summary>Verification and design choices</summary>
<p>{checks}</p>
<p>Disabling refresh would hide new results. Keeping the dashboard mounted preserves updates and interaction
without changing the server API or stored records.</p>
<p>Only dashboard content is updated in place. Other report sections retain their existing refresh behavior.</p>
</details>
<details><summary>Version history</summary>
<ul>
<li><strong>v0:</strong> original release, <code>6e30065aacf65875251f439cd4c9612709f761c9</code>.</li>
<li><strong>Shared dashboard:</strong> <code>e998eeeb10b32e4a03fd47716536425bba77df75</code>.</li>
<li><strong>Refresh fix:</strong> {version}</li>
</ul>
</details>
"""
    (ROOT / "docs/dashboard-refresh.html").write_text(enhance_report(
        document("Steady dashboard refresh", body),
        "https://github.com/QiyangYan/herdr-browser", "Source repository"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", default="")
    parser.add_argument("--verified", action="store_true")
    args = parser.parse_args()
    render(args.commit, args.verified)
