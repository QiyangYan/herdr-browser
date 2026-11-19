"""Portable performance explanation; contains no task names or report data."""
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
    version = f"Source commit <code>{commit}</code>." if commit else "Uncommitted working-tree changes."
    visual = visual_content({"figures": [{
        "title": "Repeated navigation work takes less time",
        "unit": "Milliseconds per navigation refresh", "maximum": 50,
        "values": [
            {"label": "Before", "value": 39.92, "note": "Median of five warm calls"},
            {"label": "After", "value": 9.00, "note": "Median of five warm calls"}],
        "caption": "Same saved reports, same navigation output. The first cache fill took 35.39 ms. "
                   "This measures navigation work, not overall website responsiveness."
    }]})
    for label, value in (("Before", "39.92"), ("After", "9.00")):
        visual = visual.replace(
            '<div class="bar-row"><div class="bar-label">' + label,
            '<div class="bar-row node" data-detail="' + label + ': ' + value +
            ' milliseconds per navigation refresh; median of five warm calls.">'
            '<div class="bar-label">' + label, 1)
    body = f"""
<p><a href="https://github.com/QiyangYan/herdr-browser">Back to Herdr Browser</a></p>
<h1>Lighter live reports</h1>
<p><strong>Implemented:</strong> reports do less repeated work, and clicking a failed page's chat link retries it.</p>
<p class="status">Measurements recorded 19 November 2026. Local benchmark; not a continuous monitor.</p>
{visual}
<h2>Keep unnecessary work out of the update path</h2>
<ul>
<li><strong>Navigation:</strong> read revision metadata and decode only changed reports.</li>
<li><strong>Closed charts:</strong> retain recorded data; build or update controls when the appendix opens.</li>
<li><strong>Unchanged responses:</strong> return the cache response before generating unused HTML.</li>
<li><strong>Changed reports:</strong> run visual enhancement once, preserving existing controls.</li>
</ul>
<h2>A repeated chat-link click now retries a failed page</h2>
<p>Previously, a page that failed to load could remain stuck: clicking its link again reused the same failed view.
An explicit click now retries loading that page while retaining its existing tab.</p>
<p>Refreshes also respect an appendix you closed after following a link to it.</p>
<p><strong>Limit:</strong> large visible reports can still take time to lay out. The isolated browser was mostly idle;
these changes do not establish the cause of every native-window stall or failures in a separate chat application.</p>
<p><strong>Next action:</strong> use the updated source and apply installed changes through Herdr's Apply update button.</p>
<details><summary>Measurements and tradeoffs</summary>
<p>A frozen report containing 15 charts mounted 12 immediately and deferred three closed appendix charts.
All recorded charts remained available. Opening an appendix mounted its current data.</p>
<p>Two alternating browser loads used 0.485 / 0.372 seconds of main-thread work before
and 0.354 / 0.343 seconds after. This small sample is not an end-to-end speedup claim.</p>
<p>Reducing the refresh frequency would delay new results without removing unnecessary work.
This update keeps the existing refresh cadence and all recorded measurements.</p>
</details>
<details><summary>Version history and validation</summary>
<ul>
<li><strong>Previous stable refresh:</strong> <code>069aca37998b07fe9548734c5b1948d183db6f31</code>.</li>
<li><strong>Responsiveness update:</strong> {version}</li>
<li><strong>Regression coverage:</strong> changed and deleted navigation records; unchanged HTTP responses;
closed appendix charts receiving new data; direct chart links; reopening charts without replacing controls.</li>
<li><strong>Link regression:</strong> a real terminal mouse click after a connection failure must load the recovered
website in the existing tab. Healthy repeated links still preserve their live view.</li>
</ul>
</details>
"""
    script = "<script>" + (ROOT / "herdr_browser/interactive_curve.js").read_text() + \
        "\nInteractiveDiagram.mountAll(document);</script>"
    (ROOT / "docs/report-performance.html").write_text(enhance_report(
        document("Lighter live reports", body, script),
        "https://github.com/QiyangYan/herdr-browser", "Source repository"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", default="")
    render(parser.parse_args().commit)
