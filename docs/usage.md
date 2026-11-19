# Optional Herdr task browser

Linux companion for Herdr **0.9.3**. It embeds the real TUI in VTE and shows one
system WebKit browser view beside it. Normal `herdr` and its running server
are untouched. No Electron, Chromium download, Python package installation or
new server protocol is required.

The visible **Herdr Browser** app launcher opens this window with the browser
enabled. Running `herdr` in a
terminal keeps the original terminal-only interface.

The **Task** dropdown groups choices by last recorded use: **Last 7 days**,
**Last 1 month** (7–30 days), and **Earlier**, newest first. Group headings cannot
be selected. A task selection, browser focus, published page, or observed
agent-status change records activity; context polling alone does not.
Herdr has not supplied historical task dates, so undated existing tasks appear
under Earlier with “date unavailable” until activity is recorded.

```sh
herdr-browser
herdr-browser --session experiments
herdr-browser --no-browser
herdr-browser --browser
```

The **Browser** toggle closes the active web process and gives the terminal the
full window width. This preference persists. `--browser` starts with it enabled;
`--no-browser` does not import or initialize WebKit. Running ordinary `herdr`
opts out of the companion entirely.

Select a task in Herdr's tabs or in the browser task picker. Pages follow this
window's selected task, including when other Herdr windows use the same server.
Launching the command inside a Herdr pane opens that pane's task in a separate
client window. The client receives an explicit environment, so VTE cannot
restore inherited pane/nesting markers. Herdr's nesting setting is unchanged.
The hierarchy is **task group → optional subplan section → website page tabs**.
Small tasks can stay in **General**; existing saved URLs migrate there without
loss. The window heading includes its session name. Use the folder-plus button
to add a section. Enter an HTTP/HTTPS URL to
create a remembered page tab; use **+** to start another page,
**All pages** for overflow, and the **× beside each visible page tab** to close
that page directly. Closing an inactive page leaves the current website running.
The toolbar close button also handles the selected overflow page.
At most three page buttons are visible and only the selected page runs.
Opening the same website again reuses its original tab and section within the
task. Reopening the active page leaves its live WebView running. Anchors and
redirected addresses reuse that tab. Local HTML report refresh versions also reuse
and refresh it; different paths or query filters stay
separate. Saved duplicates are merged into the first tab automatically, with
its section and ID preserved. Separate tasks retain their own page ownership.
The light native toolbar and start page are Safari-inspired; external websites
keep their own styles. Sections are explicit labels, not inferred agent plans.

Use **Next action** for a short reminder. Each section remembers its selected
page. Closing the window detaches its own client; the Herdr
session and its task processes continue.

## Living pages and session overview

Use one living home page per main task. Evaluation, debugging, setup and previews
are parts of that task, even when each produces a deliverable. Keep them as named
expandable sections. Start another page only for a distinct main objective.
The actual Herdr task identity always remains its owner.

Agents publish a JSON patch; omitted fields keep their existing values:

```json
{
  "summary": "The current recommendation and why it matters.",
  "plan": "The current plan, with baseline/proposed behavior and tradeoffs.",
  "results": "The latest implemented result and verification limits.",
  "next_action": "The next useful action.",
  "status": "working",
  "links_title": "Inspect the browser behavior",
  "references": [{"title": "Compare the old and new layout",
                  "url": "http://127.0.0.1:8796/report.html",
                  "description": "The interface change and its verification limits."}]
}
```

```sh
herdr-browser task --new "Browser workflow" --id browser-workflow \
  --file update.json --export docs/browser-workflow.html
herdr-browser task --id browser-workflow --file next-update.json \
  --export docs/browser-workflow.html
herdr-browser task --id browser-evaluation --parent browser-workflow \
  --section-title "Evaluation" --file evaluation.json \
  --export docs/browser-workflow.html
```

The JSON response includes the stable outcome ID, revision, rendered HTTP URL
and session overview URL. Use the same explicit outcome ID for later updates;
parallel outcomes in one conversation should not rely on the implicit current
selection. Without `--id` or `--new`, publishing continues the last published
outcome for that actual Herdr task. Use `--focus` only to explicitly show it;
publication otherwise prepares the page without changing your current website.
References are links inside the report; publishing them creates no browser tabs.
Status can be planned, working, blocked, done or archived. The session overview
groups **Space → actual Herdr task → main report**, using recorded IDs rather
than titles. Supporting subplans link to sections in their main report.
Active work and conclusions awaiting attention stay unfolded; quieter tasks are
folded within their space. Completed conclusions stay visible for review.
Archived reports and closed tasks are optional. Existing tasks without a living
summary still link to their saved websites.

Task circles show attention:

* **Green — in progress:** Herdr reports the agent is working.
* **Yellow — needs attention:** a conclusion or quiet-task update is unviewed,
  a new finish was recorded, or Herdr reports that input is needed. The label
  distinguishes these; reviewing a proposal does not mark its work complete.
* **Red — quiet:** labels distinguish Viewed, Reviewed and Idle. Recorded last
  activity supports an idle-age label; missing times are never guessed.

Opening a report alone does not clear attention. Foreground interaction with its
visible conclusion marks it **Viewed**; **Mark reviewed** confirms review.
New conclusion wording returns to yellow; whitespace, bold and list formatting
do not. The ledger is shared across native windows and survives relaunch.
When no report exists, interacting with the finished task's terminal marks that
finish Viewed. Viewing is an acknowledgement, not approval of a proposed action.
An updated companion can share this state even with an older report server.

**Home** opens this task's current active outcome directly, falling back to
another active outcome if the last published one is complete. **Session overview**
opens the compact index; choosing a page from another task requests verified
selection from this window's Herdr client. Closed tasks' HTTP reports remain
readable, but native task selection requires the real task to be available.
The index updates quietly, preserving open groups, focus and scroll. **+** opens a blank page tab.
Each main task appears once in the native **Task pages** section. Subplans
publish into it and select the main task as Home. Their URLs point to the
relevant section; existing subplan URLs redirect there after attaching a parent.
The session index lists main tasks, with their subplans inside.

The main page must support a decision without opening a section or another page:
show the recommendation, comparison, key evidence, decision limits and next action.
Support pages and expandable sections explain or verify it. Update the main
summary when a subplan changes the decision evidence, and show source snapshot
times rather than calling stored measurements live. Set `results_title` to a
short heading such as "What the evidence does not establish" to keep all essential
results/limitations visible instead of truncating that block.

Apply a clarity pass to **every page and substantial section** before publishing.
Use a table when the reader compares options or exact settings, a labeled flow
for order/dependencies, and a measured plot for numerical patterns. Replace the
corresponding prose; do not repeat the same explanation or put paragraphs into
decorative boxes. Keep brief conclusions and essential caveats as text. Preserve
full source wording in named details when the visual condenses technical claims.

Use **message-first prose**: a short conclusion, followed by one or two
supporting sentences. Headings should state the finding; keep evidence,
decision-changing limits and the next action distinct. Emphasize one key
phrase or decisive value using `**bold**` in text or table cells. The renderer
escapes raw HTML and supports this inline emphasis and shallow bullet lists.
Use lines starting with `- ` or `* ` for parallel facts or actions, with a short
bold lead in each item. Keep one message per item; split a dense paragraph into
its evidence, limit and decision rather than pasting it into one long bullet.
Keep ordinary short paragraphs where a list adds no clarity. The overview always
uses list items. For older updates, the renderer splits prose longer than 45 words
at recorded sentence boundaries and emphasizes a short existing lead; it preserves
numbers and evidence links. This fallback adds no claims and does not replace
authoring concise bullets. Exact source records retain their original prose.
Tables/figures still explain comparisons or sequences better than a long inventory
of bullets. Avoid bolding a whole paragraph or disguising a dense block.
For tables comparing prose, set `"row_headers": false` so the first column
does not become a bold row label. Category-based tables keep row headers by default.

For example:

```json
{
  "summary": "- **Ready:** Implementation complete.\n- **Pending:** Hardware verification.",
  "results_title": "Implementation passed; hardware remains unverified",
  "results": "- **Evidence:** Local checks passed.\n- **Limit:** Hardware behavior has not been measured.",
  "next_action": "**Run the hardware check** before choosing the final setting."
}
```

This is an illustrative wording example, not a recorded result. The overview
never clips a claim halfway through; expandable prose contains the remaining
paragraphs without repeating the opening.

`flows` provides lightweight ordered diagrams, on the main page or in a section:

```json
{
  "flows": [{"title": "Verify before training",
             "steps": [{"title": "Prepare", "text": "Pin the source and inputs."},
                       {"title": "Verify", "text": "Check the actual acceptance gates."},
                       {"title": "Run", "text": "Proceed when checks pass."}],
             "caption": "Proposed sequence; arrows indicate order, not proof of completion."}]
}
```

Use two to four short steps per diagram; split longer procedures into meaningful
phases. They reflow vertically on phones. Hover, tap and focus expose step
details and the caption. The same shared interactive renderer supports learning
curves, axis/range selection and inspection in both living and exported pages.
Use recorded `round`, `elapsed_seconds`, counts and intervals when available;
do not estimate missing time from updates. Plain explanatory diagrams need no
time axis.

`presentation.py` gives standalone report builders responsive table labels,
exact main-task return links and the shared figure inspector. Its sibling
`report_presentation.json` supplies optional exact-match prose conversions into
tables or flows. Changed evidence does not match an old conversion, so the new
source wording stays visible instead of being replaced with stale values.
Existing scripts, controls and recorded data are preserved. Call
`enhance_from_config(document, output_path)` before writing HTML.

Use `plan_diagram` for controlled comparisons rather than arrows embedded in
prose. It renders labeled comparison arrows and groups the shared settings
underneath, with a vertical layout on phones. It is a schematic of the rules,
not a measurement or an audit of launched settings. Set `plan` to `""` when
the diagram replaces the written rules; set `plan_diagram` to `{}` to remove it.
The diagram also works in custom sections and attached subplans.

```json
{
  "plan": "",
  "plan_diagram": {
    "title": "Change one component per comparison",
    "comparisons": [
      {"from": "RGB", "to": "Depth", "change": "visual input"},
      {"from": "RGB", "to": "Point cloud", "change": "visual frontend"},
      {"from": "Point cloud · recurrent", "to": "Point cloud · diffusion", "change": "policy head"}
    ],
    "fixed": ["Teacher", "Cube", "Calibration", "Seed", "Loss", "Optimizer settings", "Training budget"],
    "resources": "8 GPUs per run",
    "caption": "Arrows indicate comparisons, not execution order. Planned controls; launched settings require separate verification."
  }
}
```

The optional `curves` list renders measured learning curves with plain HTML/CSS
and SVG. Each entry has `title`, `caption`, `x_label`, `y_label`, `x_maximum`,
`y_maximum`, `gap` (expected measurement cadence), and `series`. Each series has
`label` and `points`, containing ordered numeric `x` and `y` coordinates. All
points must be finite, nonnegative and within the explicit scales. A single
measurement stays a point; empty series are labeled missing; gaps longer than
`gap` break the line. No smoothing, extrapolation, chart library or extra browser
view is used. Axis labels retain their displayed size at phone widths.

Pages also support declarative comparison tables and measured bar figures, either
on the main page or inside a section. These use plain HTML/CSS with zero-based
bar scales; no chart library, remote asset or additional WebView is loaded.
Tables reflow into labeled rows on phones. Figures require an explicit maximum,
units, measured values and a caption stating relevant limits. For example:

```json
{
  "tables": [{"title": "Training variants", "columns": ["Policy", "Input"],
              "rows": [["Depth recurrent", "Depth"], ["PC recurrent", "Point cloud"]],
              "caption": "Recorded configuration; shared settings stay fixed."}],
  "figures": [{"title": "Recorded evaluation", "maximum": 128, "unit": "Successful episodes",
               "values": [{"label": "Depth recurrent", "value": 125, "note": "Round 250"}],
               "caption": "Different training rounds are not a matched convergence comparison."}]
}
```

When a section has a visual, its narrative evidence stays in **Recorded details**.
Update figure/table data when results change. Text-only updates preserve existing
visuals. Do not supply fabricated values for a schematic or an unfinished test.

The page shows the goal, next action, latest recorded progress and current plan
directly. Longer text and technical detail are expandable, with no mandatory
Results, References or History navigation tabs. Empty sections are omitted.
Optional custom sections use entries with `id`, `title` and `text`, and may have
their own `references` and `links_title`.
Plan/results text is escaped and displayed as paragraphs; raw HTML is not run.

Keep the **main report unfolded and sufficient for a decision**. Treat folded
sections as appendices to specific findings. Use `main_sections` for short
connected passages, each with `title`, `text`, and a `sections` list of exact
supporting section IDs. Contextual links open those appendices in place; each
appendix links back to its originating passage. Every supporting ID must exist.
Set each passage's optional `phase` to `current`, `future`, or `previous`.
The default is `current`. Future phases appear in a separate **Future work**
group; completed work and provenance appear in **Completed work and prior
records**. Headings alone do not classify a passage. Keep phase metadata in the
publisher so refreshes preserve these boundaries. Current findings stay together
without internal dividers, and appendix return links keep their original IDs.
Include the **one or two most important plots** beside a completed phase's
conclusion, using its `main_sections` entry's optional `curves` and `figures`.
The renderer accepts at most two plots per passage and uses the shared
interactive renderer. For visual-policy distillation, the primary recorded
learning curve belongs here. Keep other plots, full settings and raw records
in contextually linked appendices. State snapshot times, evaluation protocols
and important limits next to the visible plots; do not present running work as
a completed result.
An appendix may have a `group` (explicit topic), `summary` (short takeaway shown
while folded), and `source_text` (exact earlier wording or technical records).
Use a few meaningful groups rather than a flat catalogue. Claims and
decision-changing limits remain visible in main text.

```json
{
  "main_sections": [
    {"title": "Task effectiveness remains unmeasured",
     "text": "**A matched test is still needed.** Training loss alone does not establish task success.",
     "sections": ["evaluation-scope"]}
  ],
  "sections": [
    {"id": "evaluation-scope", "title": "Evaluation contract",
     "group": "Evaluation evidence",
     "summary": "**Benchmark pending.** Inspect the planned scope.",
     "text": "**Use the same placements.** Compare the checkpoint with the parent.",
     "source_text": "Exact technical records can be preserved here."}
  ]
}
```

Appendix numbers reflect the current explicit group/section order; links use
stable IDs. Parent-local groups and previews survive child report refreshes.
Keep previews structural, or update them whenever their underlying claims change.
With a preview, short section text stays visible above its visuals; exact source
wording stays in a named disclosure. Legacy sections remain supported. This
uses the existing page and renderer, with no additional WebView or chart library.

Use `title` for the **main task**, not the latest subtask. It appears first and
also names the browser page. Use `current_task` for the current phase or action
(one paragraph, up to 240 characters); it appears immediately below the title,
labeled **Current task:**, followed by status. If omitted, the renderer shows
**Current status:** rather than guessing a phase from the last updated child.
Use `objective` for a stable, concise statement of the main task (one paragraph,
up to 240 characters). `summary` describes current progress in the main report.
Progress updates should update `current_task`, `summary` or `status`, preserving
the main title and objective. Child refreshes must not replace those main fields.

Use `progress_overview` when a short stage diagram clarifies progress toward
the main goal. It appears in the unfolded main report, before detailed curves:

```json
{
  "progress_overview": {
    "completed": "Converter checks and pilot evaluations complete.",
    "current": "Full-scale student fine-tuning underway.",
    "next": "Matched checkpoint benchmark, proposed.",
    "caption": "Recorded stages, not a completion percentage. Held-out performance remains unmeasured."
  }
}
```

Each field is one nonempty paragraph of at most 240 characters. Use `{}` to
remove the diagram. Update it with the summary and current task as evidence
changes; mark planned, stopped, failed and unmeasured work explicitly.
The three labels are **Done so far**, **Current stage** and **Next step**.
For a finished task with no follow-up, explicitly say “No further work planned.”
Keep the brief overall finding and decision-changing limits in `summary`;
retain precise next-action instructions below. Do not duplicate the diagram
as a second flow, infer stages from paragraph wording, or invent percentages.
It uses the shared diagram inspector for hover, touch and keyboard and stacks
vertically on phones; it adds no chart library or browser view.

Reference an appendix **inside the sentence that uses its evidence**, with
`[descriptive label](#exact-section-id)`. For example:

```json
{
  "title": "BAE student fine-tuning",
  "current_task": "Full-scale training; matched checkpoint evaluation is pending.",
  "objective": "Fine-tune the BAE student and verify its task performance.",
  "main_sections": [
    {"title": "Train with the original recurrent recipe",
     "text": "See the [full training settings](#training-settings) and [additional learning curves](#learning-evidence).",
     "sections": ["training-settings", "learning-evidence"]}
  ]
}
```

The renderer adds the current appendix number to the named reference. Inline
citations automatically establish return links and suppress duplicate generic
link lists. They also work in summary, results, plan and next-action paragraphs.
Only exact IDs of existing supporting sections become links; arbitrary HTML
and executable URLs stay escaped. Publishing rejects unknown appendix IDs.
Every supporting appendix should be introduced from a relevant sentence,
including detailed settings, extra learning curves and archived evidence.
To land directly on a recorded curve or settings table, use generated
destinations `#curve-SECTION_ID-1` or `#table-SECTION_ID-1` (one-based order
within that appendix). The link opens the containing appendix and focuses the
exact visual. For example, `[pilot learning curve](#curve-pilot-1)`.
Update these references if you deliberately reorder the visuals.

A selected living page checks for updates every three seconds using conditional
HTTP requests. It updates its content without replacing the WebView, preserving
the selected content section, focus and scroll position. Text selection pauses
refresh to avoid interrupting copying. General websites and unsaved forms are
not polled or refreshed. Old revisions live in SQLite and load on demand in
Earlier updates; pagination uses stable time/ID/revision cursors so concurrent updates cannot skip
older revisions. A small button reveals newer history entries without replacing
open history details automatically. Exporting writes a self-contained HTML
snapshot with revision history. **Next action** also updates a selected living
task page.

Subplans keep their original revisions. A subplan update atomically refreshes its
section in the main task and preserves other sections and the main goal.
Parent assignment requires an existing main page owned by the same actual Herdr
task. Main-task edits preserve sections maintained by subplans. Earlier updates
include both main-page and subplan revisions, clearly labeled by task part.
An older running companion can render the simplified layout on the next page
load; **Apply update** also activates the main-only session index and combined
history endpoint.

There is one loopback HTTP server shared by the open companions, with no extra
browser process per outcome. Peers preserve its port/URLs when its owner closes.
Pages remain available while a companion is open. Older companions get a small
temporary server bridge until Apply update transfers serving into the GUI.
The server has no write API, directory listing or arbitrary file reader.
Private state lives in `reports.sqlite3` and `reports-server.json` beside task
state. The loopback URL includes an unguessable path and sends no referrer.

## Herdr-only website routing

To route specific sites directly to Firefox, set exact hostnames before launching:

```sh
export HERDR_BROWSER_FIREFOX_HOSTS="external.example.com,another.example.com"
herdr-browser
```

No site exceptions are enabled by default. Background requests do not launch
Firefox or steal focus. Desktop HTTP/HTTPS associations remain unchanged.

Website links clicked inside the companion terminal open beside that task.
Explicit `herdr-browser open` requests deliver agent reports into the companion.
Links clicked in other apps or outside Herdr continue using the desktop browser
 even if Herdr has saved the same URL. The companion
never claims the desktop's HTTP/HTTPS associations.

Inside the companion terminal, click a named HTTP/HTTPS hyperlink or a printed
website address to open it beside the task selected in that window. The source
terminal's task wins even if another task saved the same URL. Existing page
titles and sections are preserved. Dragging text, Shift-selection and non-web
links retain their terminal behavior. With Browser off, website clicks use the
previous desktop browser.
Website presses and releases are consumed by the companion, so the child TUI
cannot also launch Firefox. A drag returns to VTE's normal selection handling.

After upgrading, close the older companion and relaunch once:

```sh
herdr-browser --browser
```

This is the last manual relaunch needed to gain **Apply update**. Later installed
updates enable that header button automatically. It restarts only the companion
interface, restoring the selected local task, window size, split and Browser
preference. Herdr's server and task processes keep running. Saved page tabs
return; ephemeral browser logins and unsaved website state may reset.
The button applies files already installed locally; it does not download updates.
Detection uses cached file timestamps and checksums, with no separate daemon.

Agents should automatically deliver generated pages:

```sh
herdr-browser open http://127.0.0.1:8796/report.html \
  --title "Evaluation report" --section "Review results"
```

Output for another task prepares its page without switching your foreground
task. Use `--background` to add reference pages without replacing any active
page, and `--focus` to explicitly select and raise the owning task.
The `link` command below remains registration-only.

The upgrade restores associations claimed by the older global router. A
user-selected desktop browser is preserved. There is no separate daemon.
Existing browser tabs are not imported.

The legacy desktop entry at `~/.local/share/applications/herdr-browser.desktop`
is a compatibility guard: external app clicks always go to the desktop browser,
even if an older running companion accidentally selects that entry.
Previous associations are recorded privately in
`~/.local/state/herdr-browser/routing.json`.
The visible launcher is a separate `dev.herdr.Browser.desktop`, with a matching
native application identity and SVG icon. It declares no URL handlers and does
not replace the legacy external-link forwarder.
`HERDR_BROWSER_NO_DESKTOP=1` disables association changes for a launch.
Global Codex instructions include the preference from
`global_browser_preference.md`; this does not replace other global instructions.

From an agent or shell inside a **local** task, register a report:

```sh
herdr-browser link http://127.0.0.1:8796/report.html \
  --title "Evaluation report" --section "Review results" \
  --next-action "Inspect the failed cases"
```

At least one companion must be attached to that session. Remote tasks can save
reachable URLs through the UI; automatic registration from a remote machine is
not implemented. Use existing forwarding for remote localhost pages.
Omit `--section` to save into General. Registration does not switch the active
page. Ctrl+T / Ctrl+W work while the browser address field has focus; terminal
shortcuts remain owned by Herdr.

The terminal supports **Ctrl+Shift+C** to copy selected text and
**Ctrl+Shift+V** to paste text, plus a right-click Copy/Paste menu.
**Ctrl+V** also pastes when the clipboard offers text; an image-only clipboard
still reaches Herdr's image-paste handler. **Ctrl+C** remains the terminal
interrupt. Hold **Shift** while dragging to select text when Herdr captures
mouse input. These shortcuts affect the terminal pane; website fields and the
address bar use their native clipboard behavior.

Both panes have **− / percentage / +** controls. Click the percentage to reset
to 100%. Click inside the terminal or browser, then use **Ctrl+plus** (or
**Ctrl+equal**), **Ctrl+minus** and **Ctrl+0** to zoom in, zoom out and reset.
Terminal and browser levels are saved separately across task switches and
interface updates. Terminal text supports 50–300%; webpages support 25–300%.
Each button or shortcut changes the scale by ten percentage points.

Clipboard repair and activation notes:
[visual summary](http://127.0.0.1:8796/herdr_browser_clipboard.html).

## Practical limits

- Requires a graphical desktop and `/usr/bin/python3` with GTK 3, VTE 2.91 and
  WebKit2 4.0. This is a desktop window containing a terminal, not a text-only
  SSH browser.
- Cookies/logins are shared across tasks **within one window** and last until
  the browser is turned off or the window closes. Existing Chrome logins are not imported.
- One active page **per window** renders at a time. Switching tasks, sections
  or page tabs reloads the stored URL;
  unsaved forms and page-local state such as a viewer camera may reset.
- Extra saved tasks/pages store only metadata, with no background HTTP requests
  or extra live views. Multiple companion windows each add their own browser.
- Closing a page removes its saved tab; there is no undo/history manager.
  Section renaming/removal is deferred.
- Task identity includes the connection, endpoint, server boot ID and tab ID.
  Restarted servers get separate task records because tab IDs can be reused.
- Browser memory depends on the site. This is a minimal interface, not a claim
  that arbitrary websites use little memory. Real GPU-heavy viewers and SSO
  sites still need normal desktop use to qualify.
- The first task picker lists the currently selected Herdr machine. Cross-machine
  selection stays in Herdr's machine sidebar.
- Terminal image protocols unsupported by VTE may differ from the original host
  terminal. Use the browser for HTML and graphical viewers.

## Installation and verification

See the [installation instructions](../README.md) and
[recorded v0 validation](validation.md). The release carries the exact prebuilt
client recorded in `herdr_browser/provenance.json`; the upstream patch is in
`patches/`.

## Interactive measured figures

All curve figures support exact hover/touch values, keyboard inspection, editable
ranges, drag-to-zoom and reset. Controls persist across updates and reopening.
Legacy `{x, y}` points remain supported. To offer selectable x axes, add:

```json
"axes": [
  {"key": "x", "label": "Optimizer updates"},
  {"key": "round", "label": "Training rounds"},
  {"key": "elapsed_seconds", "label": "Relative wall time (hours)", "divisor": 3600}
]
```

Each point can carry actual `round` and `elapsed_seconds`, or null for a missing
coordinate. Evaluation points may include `episodes`, `successes`, `lo` and `hi`
(95% Wilson values in the plotted y units). Timestamps must come from recorded
measurements; do not infer wall time from an average speed. Missing coordinates
are omitted on that axis and counted visibly. Bars expose values/notes on hover
or focus. Diagram steps expose their recorded labels and caption on hover,
touch and keyboard focus; numerical x axes apply to measured curves.

The dependency-free InteractiveCurve/InteractiveDiagram component is embedded
in `report_view.js`; preserve it when refreshing the installed companion. The
canonical measured-curve source used by the Cube42 reports is
`FAR-ExtremeDex/.worktrees/hybrid-camera/scripts/report_assets/interactive_curve.js`.
# Session dashboard (source update after v0)

Every task home includes the same dashboard: its main report, supporting task
reports, explicitly registered subagents, recorded update times and requests
for help. The saved report is the source of status; this is not a process monitor.

Completed or stopped rows have **Remove**. They move into **Dashboard history**,
which retains their report and canonical subagent reference. **Restore to
dashboard** restores the row without restarting work. History is stored per
Herdr task and outcome in the local report database and survives browser restarts.
New results or resumed work return to the visible dashboard.

An agent can publish its status from its originating Herdr task:

```bash
herdr-browser agent --outcome website --id layout --file agent-status.json
```

```json
{
  "agent_id": "/root/layout",
  "agent_label": "Layout",
  "task": "Review the phone layout",
  "status": "waiting",
  "summary": "Desktop fits; phone review is pending.",
  "updated_at": "2026-11-19T02:00:00Z",
  "needs_help": "Review the phone preview.",
  "report_url": "https://example.org/preview"
}
```

Use the real subagent ID and recorded time. Supported statuses are `preparing`,
`running`, `waiting`, `complete`, `error`, `stopped` and `unknown`. Omit
`needs_help` when no action is requested from the reader. Omitting `updated_at`
records the publication time. Reuse `--id` for later reports; registration itself
does not create, resume or stop an agent. The `--outcome` is the main report ID,
not the actual Herdr task identity, which is resolved from the originating task.

[Open the interactive example](https://qiyangyan.github.io/herdr-browser/dashboard.html).
Its sample records are illustrative; the page is a portable snapshot.
