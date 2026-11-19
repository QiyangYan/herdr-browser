<!-- herdr-browser-routing:begin -->

## Automatic website routing and work throughput

- Optimize the task workflow for clear ownership, minimal manual steps and high
  work throughput. When an enabled Herdr companion is open, use it as the
  destination for websites from Herdr tasks, including task reports, visual plans,
  dashboards and reference pages.
- Keep one living home page per main task, plus a short session overview.
  Evaluation, debugging, setup, input previews and other steps toward that goal
  belong inside the main page, even when each produces a deliverable.
  Create a new named page only for a distinct main objective. Keep the main
  page as Home; a recently updated subplan must not replace it.
  Open the main task directly. Use the session overview for switching.
- Publish plans, progress and change summaries automatically with
  `herdr-browser task --id OUTCOME --file update.json --export docs/OUTCOME.html`.
  On the first publication of an independent outcome, add `--new "Title"`.
  Reuse the returned outcome ID explicitly for later iterations; it is separate
  from the actual Herdr task ID. Do not infer task ownership from page titles.
  To maintain a substantial subplan, publish with `--id SUBPLAN --parent MAIN
  --section-title "Evaluation"`; it updates a section of the main page, not a
  separate task home. Reuse both IDs. Existing subplan pages can be attached
  this way without losing their saved revisions.
  The JSON fields are title, summary, plan, results, next_action, status,
  references, sections, parent, section_title, links_title, tables, figures, curves, results_title, plan_diagram, flows, main_sections, objective, current_task and progress_overview; see the installed
  companion README. Keep the main goal, latest recorded progress and next action
  concise and directly visible. Put substantial detail in named expandable
  sections. Earlier updates are collapsed; do not add mandatory Results,
  References and History navigation tabs.
- Make the main page sufficient to choose the next action without opening any
  section or another website: show the recommendation, the useful comparison,
  primary evidence (including learning curves when relevant), important tradeoffs,
  missing evidence and the next action directly. Expandable sections and linked
  pages only explain or verify that summary; they must not hide a limitation that
  could change the decision. Keep the summary current when a subplan changes the
  evidence. Show the evidence snapshot time; do not imply a saved snapshot is live.
- Make pages visual when relationships or comparisons need explanation: use
  a concise table for options/settings and a figure for supported measurements
  or task structure. Do not replace a diagram with paragraphs. Use the report's
  declarative tables/figures/curves/plan_diagram/flows or a supporting interactive page; avoid chart
  libraries or extra live browser views for simple visuals. Keep essential
  limitations visible beside each visual. Update visual data when results change;
  never invent measurements or imply checkpoints from different rounds are
  a matched convergence comparison.
- Apply a visual clarity pass to every task website and substantial section.
  When a paragraph makes the reader reconstruct a comparison, sequence,
  dependency or measured pattern, replace it with the appropriate table,
  labeled flow or recorded plot. Keep short prose for simple facts, conclusions
  and caveats. Do not repeat the same explanation in prose and a visual or turn
  paragraphs into decorative boxes. Keep exact source wording recoverable in
  named details and preserve decision-changing limitations beside the visual.
  Diagrams and categorical figures support hover, touch and keyboard inspection;
  learning curves reuse the shared renderer and offer recorded axes/ranges.
- Make prose understandable at a glance. Use headings that state the supported
  finding, and give each paragraph one message: a short conclusion followed by
  only the explanation needed to understand it. Use `**bold**` for one key
  phrase or decisive value per paragraph; plain text explains it. Separate
  evidence, decision-changing limits and next action into short blocks when
  they answer different questions. Aim for one or two supporting sentences,
  rather than long paragraphs. Do not bold whole blocks, add competing fonts,
  clip text to meet a length target, or hide an important caveat. Remove
  repetition between the overview, visuals and expandable detail.
- Avoid large plain-text blocks throughout the current report, including
  the overview and appendices. Use short bullets for parallel evidence,
  limitations and actions, with one concise **bold key idea** per item.
  The renderer supports lines starting with - or * in text fields. Do not
  disguise a dense paragraph as one long bullet. Use a table or figure for a
  comparison or sequence; keep short prose for a simple single fact. Retain
  exact earlier wording in clearly named folded source records.
- Keep the unfolded main report connected and sufficient for a decision.
  Treat folded sections as appendices to specific findings, not an equally
  prominent list of unrelated topics. Use main_sections with concise title/text
  and exact supporting section IDs; each contextual link opens the appendix,
  and each appendix returns to the passage that introduced it. Give sections
  explicit group names and short summary previews. Use a few meaningful
  appendix groups, clear heading levels and selective bold inside each section.
  Preserve exact earlier wording in source_text when simplifying it. Keep
  decision-changing uncertainty in the main report, not only in an appendix.
- Keep the page title and first heading named for the main task. Write the
  current subtask or phase immediately below it as **Current task: …**, using
  current_task, with status beside it. If the phase is unknown, show current
  status without guessing. Add one concise objective sentence; put detailed
  progress in the main report. Preserve title and objective across routine
  progress updates; do not rename the page after the latest subtask.
  Keep objective stable across
  progress updates so the reader never loses the purpose of the task.
  Separate the overall progress overview from the current-task details with
  a horizontal divider and clear spacing. Use dividers only between distinct
  groups such as current work, future steps and previous updates; keep related
  sections within current work continuous, without a line between every section.
  Keep all main-text content related to the current task in one visible block:
  summary, evidence, results and their explanations share a single group.
  Set main_sections[].phase explicitly to current, future or previous.
  Deferred future phases and completed/prior records belong in their separate
  groups, outside Current work; a future-phase heading alone does not classify
  it. Preserve the phase in the publisher across automatic refreshes.
  Completed tasks and phases must retain the 1–2 most important figures beside
  their visible conclusion; a visual-policy distillation learning curve is
  primary evidence and must not be available only in an appendix.
  Use main_sections[].curves and .figures for at most two key plots per passage;
  keep additional plots and full records in contextual appendices. Show
  recorded snapshot times, protocols and important limits beside each plot.
  Keep current experiments distinct from completed historical measurements.
  Start the unfolded main text with a brief overview of progress toward the
  main goal: what is done, the current task's stage, and what comes next (if
  anything). Prefer a compact stage diagram when it makes the relationship
  easier to scan, using progress_overview with completed, current, next and
  caption. Mark proposed, failed, stopped and unverified work explicitly; do
  not invent a completion percentage. Keep the overall finding and important
  uncertainty visible beside it. Update the overview with the underlying
  evidence; avoid repeating it as another flow or a long paragraph.
  Cite each appendix inside the sentence that discusses its evidence, using
  [full training settings](#training-settings) or a similarly descriptive
  exact section-ID reference. Explicitly reference additional learning curves,
  configuration details and other supporting evidence; do not rely on generic
  link lists or make the reader search the appendix. Check that every appendix
  has a relevant main-text citation and a return link.
  For a settings table or learning curve, link directly to
  #table-SECTION_ID-1 or #curve-SECTION_ID-1 (its one-based order) when useful;
  the browser opens the containing appendix and focuses that exact visual.
  Update the reference if its visual order changes. Keep source and dashboard
  links with the appendix whose evidence they verify.
- Verify the returned HTTP/HTTPS task-page URL and provide it as the primary
  browser link. Publishing delivers it quietly and refreshes a live report in
  place. Keep the exported HTML artifact with the change for review/provenance.
  Complete delivery automatically; do not ask the user to register it manually.
- Supporting reports, sources and dashboards belong with the relevant section.
  Give each link an action title saying what it opens, plus a brief description
  when its purpose or limits are unclear. Use links_title for a meaningful group
  label. A supporting page is not automatically a new task.
  Do not create a new browser tab for every iteration. For a website that should
  be opened separately, serve/verify it and use
  `herdr-browser open URL --title "Page title" --section "Subplan"` from its
  originating task. Keep normal report generation and desktop behavior when the
  companion is unavailable; do not block authorized work on this feature.
- Give every supporting report a visible link back to its exact main task page.
  Preserve that link in the report generator so automated refreshes do not remove
  it. Use the known task-page URL, not browser history or a guessed task title.
- Keep task groups, optional subplan sections and page tabs distinct. Preserve
  task identity from the actual Herdr environment; do not invent tab IDs or
  assign output to an unrelated task. Existing URL ownership takes precedence
  over the last focused window when there is no originating task context.
- Keep the session overview grouped as Space → actual Herdr task → main report.
  Nest supporting steps inside their main report. Put active work and unviewed
  conclusions first; fold quieter tasks within each space.
  Use green circles for running work, yellow for conclusions needing attention
  or input needed, and red for quiet work. Short labels distinguish Viewed,
  Reviewed and Idle; show recorded idle age when available.
  Foreground interaction with a displayed conclusion marks it Viewed; explicit
  Mark reviewed confirms review. A changed conclusion returns to yellow.
  Opening or preparing a background page must not acknowledge review.
  Preserve review markers across native windows and relaunches. Viewing or
  reviewing a report does not authorize its proposed next actions.
- Background work must not take attention from the user's current task or
  replace the page they are using. Use `--background` for background/reference
  pages; ordinary agent `open` requests for another task prepare that task's
  page without switching the foreground task. Use `--focus` for an explicit
  request to show a page. Only one selected website runs per companion window.
- Scope automatic routing to Herdr: terminal website clicks and explicit
  `herdr-browser open` requests go to the task browser. Links clicked in other
  apps or outside Herdr must continue opening in Firefox, as before, even if
  Herdr is open or already owns the same URL. Never claim the desktop HTTP/HTTPS
  associations for Herdr. Preserve browser opt-out and normal desktop defaults.
- Preserve the opt-out and all running Herdr tasks. An older companion needs
  one relaunch after the routing upgrade; do not restart its Herdr server.
  Apply later installed updates through the in-app Apply update button.
  Verify native Herdr link loading and that an actual external desktop open
  still goes to the previous browser before claiming routing works.

- Every task Home includes a session dashboard with its own recorded work.
  Main and supporting task reports appear automatically. When using subagents,
  publish their real IDs, task, status, recorded update time, report link and any
  explicit request for the user's help with
  `herdr-browser agent --outcome MAIN --id STABLE_ROW --file agent-status.json`.
  Reuse the row ID after launch, when status changes, before waiting and at
  completion. Supported statuses are preparing, running, waiting, complete,
  error, stopped and unknown; see the usage schema. Do not invent subagent
  identities or imply that a saved report proves a process is still running.
  Dashboard registration does not authorize spawning or resuming work.
  Completed rows can move to persistent dashboard history; restoring a row
  restores its reference without restarting its agent.

<!-- herdr-browser-routing:end -->
