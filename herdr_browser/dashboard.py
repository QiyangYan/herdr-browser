"""Task-owned dashboard records and persistent, reversible completed-work history.

Reading a report never claims that its agent is currently alive. Every row
keeps its recorded timestamp; agent records are explicit, not guessed from a
page title or the window that happens to have focus.
"""
import datetime
import hashlib
import json
import re
import time

from .store import checked_url

STATES = {"preparing", "running", "waiting", "complete", "error", "stopped", "unknown"}
TERMINAL = {"complete", "stopped"}
LABELS = dict(preparing="Preparing", running="Running", waiting="Waiting",
              complete="Complete", error="Error", stopped="Stopped", unknown="Unknown")
REPORT_STATES = dict(planned="preparing", working="running", blocked="waiting",
                     done="complete", archived="stopped", idle="stopped")
RECORD_FIELDS = {"agent_id", "agent_label", "task", "status", "summary",
                 "updated_at", "needs_help", "report_url", "task_id"}


def record_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,160}", value):
        raise ValueError("Use a stable dashboard ID with letters, digits, _, ., : or -.")
    return value


def timestamp(value):
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).isoformat()
    if not isinstance(value, str):
        raise ValueError("Use a recorded ISO 8601 timestamp with a timezone.")
    parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Dashboard update timestamps need a timezone.")
    return parsed.astimezone(datetime.timezone.utc).isoformat()


def checked_record(value, identity):
    record_id(identity)
    if (not isinstance(value, dict) or set(value) - RECORD_FIELDS
            or not {"task", "status"} <= set(value)):
        raise ValueError("Dashboard records require task and status.")
    for key, text in value.items():
        if not isinstance(text, str) or len(text) > (2000 if key in ("summary", "needs_help") else 500):
            raise ValueError(f"Use concise text for dashboard {key}.")
    if not value["task"].strip() or value["status"] not in STATES:
        raise ValueError("Use a task name and a supported dashboard status.")
    row = dict(id=identity, agent_id="", agent_label="Subagent", task_id="",
               summary="", needs_help="", report_url="", **{
                   "updated_at": timestamp(time.time())})
    row.update(value)
    row["updated_at"] = timestamp(row["updated_at"])
    if row["report_url"]:
        row["report_url"] = checked_url(row["report_url"])
    return row


def plain(text):
    """Keep the wording while removing supported presentation markup."""
    return re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text).replace("**", "")


def legacy_id(label, task):
    return "table:" + hashlib.sha256(json.dumps([label, task]).encode()).hexdigest()[:24]


def legacy_rows(state):
    """Compatibility for saved five-column dashboards, scoped to this report."""
    rows = []
    for table in state.get("tables", []):
        if table.get("title", "").lower() != "subagent dashboard":
            continue
        if table.get("columns") != ["Subagent", "Task", "Status", "Last updated", "Needs your help"]:
            continue
        for cells in table["rows"]:
            label, task, status, updated, help_text = cells
            if help_text.strip().lower() in ("nothing needed from you.", "no request recorded.", "none"):
                help_text = ""
            link = re.fullmatch(r"\[([^\]]+)\]\((https?://[^)]+)\)", task)
            url = link[2] if link else ""
            task = link[1] if link else task
            heading, _, detail = status.partition(" — ")
            key = {"preparing launch": "preparing", "preparing": "preparing",
                   "running": "running", "complete": "complete", "error": "error",
                   "waiting": "waiting", "blocked": "waiting", "stopped": "stopped"}.get(
                       heading.lower(), "unknown")
            try:
                updated = timestamp(updated)
            except (TypeError, ValueError, OverflowError):
                updated = ""
            rows.append(dict(id=legacy_id(label, task), agent_id="", agent_label=label,
                             task_id="", task=task, status=key, summary=detail or status,
                             updated_at=updated, needs_help=help_text, report_url=url,
                             source="saved table", archived=False))
    return rows


def report_row(state, base="", main=True):
    return dict(id=("main:" if main else "report:") + state["id"],
                agent_id="", agent_label="Main session" if main else "Supporting task",
                task_id=state["id"], task=state["title"],
                status=REPORT_STATES.get(state["status"], "unknown"),
                summary=plain(state.get("current_task") or state.get("summary", "")),
                updated_at=timestamp(state["updated"]) if state.get("updated") else "",
                needs_help="", report_url=(f'{base}/task/{state["owner"]}/{state["id"]}'
                                           if base else ""),
                source="saved main report" if main else "saved supporting report",
                archived=False)


def start_state(owner, row):
    """A home for a real Herdr task before its first living report is published."""
    return dict(owner=owner, id="start", revision=0, updated=row.get("status_changed_at", 0),
                title=row.get("label", "Main task"), summary="", results="", plan="",
                current_task="No living task report has been published yet.",
                next_action=row.get("next_action", ""), status=row.get("agent_status") or "unknown",
                references=[dict(title=link["title"], url=link["url"])
                            for link in row.get("links", [])],
                sections=[])


def snapshot_from_state(state, base=""):
    rows = [report_row(state, base), *legacy_rows(state)]
    return dict(rows=rows, history=[], owner=state["owner"], goal=state["id"],
                updated_at=max((row["updated_at"] for row in rows), default=""),
                persistent=False)


class Dashboard:
    def __init__(self, reports):
        self.reports = reports
        with reports.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS dashboard_records (
                owner TEXT NOT NULL, goal TEXT NOT NULL, id TEXT NOT NULL,
                record TEXT NOT NULL, hidden INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(owner,goal,id))""")

    def upsert(self, owner, goal, identity, value, *, hidden=None):
        state = self.reports.get(owner, goal)
        if not state:
            raise ValueError("Publish the main task report before registering a subagent.")
        if state.get("parent"):
            goal = state["parent"]
        row = checked_record(value, identity)
        with self.reports.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT hidden,record FROM dashboard_records WHERE owner=? AND goal=? AND id=?",
                             (owner, goal, identity)).fetchone()
            hide = bool(old[0]) if old else False
            if old and any(json.loads(old[1]).get(key) != row.get(key)
                           for key in ("status", "summary", "updated_at", "needs_help")):
                hide = False
            if hidden is not None:
                hide = bool(hidden)
            if row["status"] not in TERMINAL:
                hide = False
            db.execute("INSERT OR REPLACE INTO dashboard_records VALUES (?,?,?,?,?)",
                       (owner, goal, identity, json.dumps(row), int(hide)))
        return row

    def snapshot(self, owner, goal, base="", *, fallback=None):
        state = self.reports.get(owner, goal) or fallback
        if not state:
            raise ValueError("Unknown task report.")
        if state.get("parent"):
            goal = state["parent"]
            state = self.reports.get(owner, goal)
        auto = [report_row(state, base), *legacy_rows(state)]
        auto.extend(report_row(child, base, main=False) for child in self.reports.list(owner)
                    if child.get("parent") == goal)
        current = {row["id"]: row for row in auto}
        with self.reports.connect() as db:
            saved = db.execute("SELECT id,record,hidden FROM dashboard_records "
                               "WHERE owner=? AND goal=? ORDER BY id", (owner, goal)).fetchall()
        history = []
        for identity, raw, hidden in saved:
            record = json.loads(raw)
            original = dict(record)
            # Report/table timestamps and statuses remain authoritative. Imported
            # IDs and links add provenance without freezing an old observation.
            if identity in current:
                record.update(current[identity])
                for key in ("agent_id", "task_id", "report_url"):
                    if original.get(key):
                        record[key] = original[key]
            record.setdefault("source", "saved subagent report")
            unchanged = all(original.get(key) == record.get(key)
                            for key in ("status", "summary", "updated_at", "needs_help"))
            record["archived"] = bool(hidden and unchanged and record["status"] in TERMINAL)
            if record["archived"]:
                history.append(record)
                current.pop(identity, None)
            else:
                current[identity] = record
        rows = list(current.values())
        rows.sort(key=lambda row: (row["id"].startswith("main:") is False,
                                  row["status"] in TERMINAL, row["agent_label"], row["id"]))
        return dict(owner=owner, goal=goal, rows=rows, history=history,
                    updated_at=max((row["updated_at"] for row in rows + history), default=""),
                    persistent=True)

    def change(self, owner, goal, identity, action, base="", *, fallback=None):
        record_id(identity)
        if action not in ("archive", "restore"):
            raise ValueError("Choose archive or restore.")
        # Lock the observation and change together; another agent may be updating.
        with self.reports.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            snapshot = self.snapshot(owner, goal, base, fallback=fallback)
            row = next((row for row in snapshot["rows"] + snapshot["history"]
                        if row["id"] == identity), None)
            if not row:
                raise ValueError("This row does not belong to this task.")
            if action == "archive" and row["status"] not in TERMINAL:
                raise ValueError("Only completed or stopped tasks can move to history.")
            db.execute("INSERT OR REPLACE INTO dashboard_records VALUES (?,?,?,?,?)",
                       (owner, snapshot["goal"], identity, json.dumps(row), int(action == "archive")))
        return self.snapshot(owner, snapshot["goal"], base, fallback=fallback)
