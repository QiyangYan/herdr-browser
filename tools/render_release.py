"""Render a public release overview using only the public companion renderer."""
import argparse
import datetime
import html
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from herdr_browser.reports import document, visual_content
from herdr_browser.presentation import enhance_report


def render(source_commit=""):
    if source_commit and not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("Use the actual full source commit")
    records = json.loads((ROOT / "docs/checks.json").read_text())
    esc = html.escape
    flow = visual_content({"flows": [{
        "title": "One release, separate personal workspaces",
        "steps": [
            {"title": "Current browser", "text": "The recorded Herdr 0.9.3 client and reusable installed companion features."},
            {"title": "Public v0", "text": "A versioned installer, source, workflow template and checksums; private task data excluded."},
            {"title": "Your workspace", "text": "Task-linked websites and reports, with your own tasks, agents and sign-ins."},
        ],
        "caption": "Arrows show how the current app becomes an installable release, not measured performance."
    }]})
    rows = "".join("<tr><th>" + esc(row["check"]) + "</th><td data-label=\"Status\">" +
                   esc(row["status"]) + "</td><td data-label=\"Evidence\">" +
                   esc(row["evidence"]) + "</td></tr>" for row in records["checks"])
    versions = ("<li><strong>Upstream:</strong> Herdr v0.9.3, commit "
                "<code>7b116c05bfda646af39d2524c54e70c751f57ee8</code>.</li>")
    versions += ("<li><strong>Public source:</strong> commit <code>" + source_commit + "</code>.</li>"
                 if source_commit else "<li><strong>Public source:</strong> uncommitted packaging work.</li>")
    versions += "<li><strong>v0:</strong> " + esc(records["release_status"]) + "</li>"
    if records.get("release_commit"):
        versions += "<li><strong>v0 tag:</strong> <code>" + esc(records["release_commit"]) + "</code>.</li>"
    body = f"""
<style>code{{overflow-wrap:anywhere}}</style>
<p><a href="https://github.com/QiyangYan/herdr-browser">Open the source repository</a></p>
<h1>Herdr Browser v0</h1>
<p><strong>Status:</strong> {esc(records['headline'])}</p>
<p class="status">Recorded {esc(records['snapshot_utc'])} · Ubuntu 22.04 x86_64</p>
{flow}
<h2>Install the same app with one command</h2>
<pre style="white-space:pre-wrap;overflow-wrap:anywhere"><code>curl -fsSL https://raw.githubusercontent.com/QiyangYan/herdr-browser/v0/install.sh | bash</code></pre>
<p>Add <code>-s -- --codex</code> after <code>bash</code> to install the Codex browser workflow too.</p>
<p><strong>Scope:</strong> v0 supports Ubuntu 22.04 desktops on x86_64. Other operating systems and physical recipient desktops have not been verified. System package installation may request sudo.</p>
<h2>Verification recorded for v0</h2>
<table><thead><tr><th>Check</th><th>Status</th><th>Evidence</th></tr></thead><tbody>{rows}</tbody></table>
<p><strong>Next action:</strong> {esc(records['next_action'])}</p>
<details id="choices"><summary>What is preserved, and what is excluded</summary>
<p><strong>Preserved:</strong> task-owned websites, native terminal/browser, living reports, interactive figures, review markers and desktop browser defaults.</p>
<p><strong>Excluded:</strong> personal tasks, report data, cookies, credentials and adapters tied to private tasks. Firefox site exceptions are configurable.</p>
<p><strong>Tradeoff:</strong> a prebuilt client avoids a compiler setup, but this first release supports one Linux platform. The upstream patch and source remain available for inspection.</p>
</details>
<details id="versions"><summary>Version history and provenance</summary><ul>{versions}</ul>
<p>See <a href="https://github.com/QiyangYan/herdr-browser/blob/v0/herdr_browser/provenance.json">source and client hashes</a>
and the <a href="https://github.com/QiyangYan/herdr-browser/releases/tag/v0">versioned release and checksums</a>.</p></details>
"""
    result = enhance_report(document("Herdr Browser v0", body),
                            "https://github.com/QiyangYan/herdr-browser", "Source repository")
    (ROOT / "docs/v0.html").write_text(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-commit", default="")
    args = parser.parse_args()
    render(args.source_commit)
