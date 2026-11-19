"""Portable stability change summary, containing aggregate measurements only."""
import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from herdr_browser.reports import document, visual_content
from herdr_browser.presentation import enhance_report


def render(commit="", verified=False):
    if commit and not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Use an actual full source commit ID.")
    version = f"Source commit <code>{commit}</code>." if commit else "Uncommitted working-tree changes."
    status = "Implemented and regression tested" if verified else "Implemented; native verification in progress"
    checks = ("82 unit tests and 51 native checks passed, including a blocked navigation "
              "reader, task switches, chart focus and ordinary website links."
              if verified else "82 unit tests passed. Native UI checks are still in progress.")
    figure = visual_content({"figures": [{
        "title": "Repeated task reads do less work",
        "unit": "ms", "maximum": 12,
        "values": [{"label": "Before", "value": 8.271738508483395, "note": "One task read"},
                   {"label": "After", "value": 0.040184982935898006, "note": "One task read"}],
        "caption": "Median of 100 repeated task reads on the same host, using a copied state file and cProfile. Recorded 19 November 2026. Lower is better; this is not a whole-app speed measurement.",
    }]})
    body = f"""
<p><a href="https://github.com/QiyangYan/herdr-browser">Back to Herdr Browser</a></p>
<h1>Keep the browser responsive</h1>
<p><strong>{status}:</strong> reuse unchanged task state and keep routine database waits off the window's UI thread.</p>
<p>The investigation found repeated state processing and a recorded Wayland display disconnect.
Available memory in that sample did not support blaming the number of saved tabs.</p>
{figure}
<ul>
<li><strong>Responsive navigation:</strong> one background worker reads report summaries; task switches reject outdated results.</li>
<li><strong>Steadier updates:</strong> unchanged section controls and an unchanged selected metric keep their existing widgets and plot.</li>
<li><strong>Display compatibility:</strong> GTK prefers X11 when it is available, with Wayland fallback and explicit backend settings preserved.</li>
</ul>
<p><strong>Limit:</strong> these checks address specific causes. They do not establish long-term crash freedom or reproduce every reported flash.</p>
<p><strong>Next action:</strong> use Apply update for an installed update, then continue normal work and check for recurrence.</p>
<details><summary>Storage, verification and tradeoffs</summary>
<ul>
<li><strong>Verification:</strong> {checks}</li>
<li><strong>Atomic snapshots:</strong> external updates invalidate the cache. Returned objects remain independent, and concurrent writes retain their existing lock.</li>
<li><strong>Report history:</strong> SQLite WAL lets a publisher commit while readers retain a consistent snapshot. No saved revisions are deleted. Live database backups must include its WAL or use SQLite's backup API.</li>
<li><strong>Refresh behavior:</strong> navigation keeps its last snapshot while storage is busy and retries later. This favors an interactive window over blocking for the newest status.</li>
<li><strong>X11 tradeoff:</strong> native Wayland scaling behavior may differ. An explicit <code>GDK_BACKEND=wayland</code> keeps native Wayland. This preference does not change desktop browser associations or child applications' environment.</li>
</ul>
</details>
<details><summary>Measurements and version history</summary>
<ul>
<li><strong>Before:</strong> median 8.271738508483395 ms; 95th ordered sample 9.481880028033629 ms.</li>
<li><strong>After:</strong> median 0.040184982935898006 ms; 95th ordered sample 0.043768988689407706 ms.</li>
<li><strong>Protocol:</strong> 100 calls to <code>Store.task()</code>, after a warm-up read, using a copied saved task file. Both measurements used the same profiler. These are short local measurements with no confidence interval.</li>
<li><strong>Baseline:</strong> <code>c8656156f52850c87322ba7a55396fd3e9f45b43</code>.</li>
<li><strong>Stability changes:</strong> {version}</li>
<li><strong>Future:</strong> longer observation of native display stability remains unverified.</li>
</ul>
</details>
"""
    script = "<script>" + (ROOT / "herdr_browser/interactive_curve.js").read_text() + """
const recorded = [8.271738508483395, 0.040184982935898006];
document.querySelectorAll('.bar-row').forEach((row, index) => {
  row.classList.add('node');
  row.dataset.detail = (index ? 'After: ' : 'Before: ') + recorded[index] + ' ms median per task read';
  row.querySelector('.bar-value').textContent = recorded[index].toFixed(index ? 3 : 2) + ' ms';
});
InteractiveDiagram.mountAll(document);
document.querySelector('figure > .ic-readout').textContent = 'Hover, tap or focus a bar to inspect the exact recorded value.';</script>"""
    (ROOT / "docs/browser-stability.html").write_text(enhance_report(
        document("Keep the browser responsive", body, script),
        "https://github.com/QiyangYan/herdr-browser", "Source repository"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", default="")
    parser.add_argument("--verified", action="store_true")
    args = parser.parse_args()
    render(args.commit, args.verified)
