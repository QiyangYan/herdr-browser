"""Living outcome pages, with independent revisions and one shared local server."""
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import urlsplit, parse_qs
import uuid

from .store import Store, atomic_json, checked_url, task_home, status_label
from .router import process_token
from .presentation import inline_text
from .dashboard import Dashboard, snapshot_from_state, start_state

FIELDS = {"title", "summary", "plan", "results", "next_action", "status", "references",
          "sections", "parent", "section_title", "links_title", "tables", "figures", "curves", "results_title",
          "plan_diagram", "flows", "main_sections", "objective", "current_task", "progress_overview"}
STATUSES = {"planned", "working", "blocked", "done", "archived"}
APPENDIX_REFERENCE = re.compile(r"\[([^\]\n]+)\]\(#([a-zA-Z0-9_-]{1,96})\)")


def referenced_ids(text):
    return [match[1] for match in APPENDIX_REFERENCE.findall(text)]


def visual_targets(row):
    return [f'{kind}-{row["id"]}-{index + 1}'
            for field, kind in (("curves", "curve"), ("tables", "table"))
            for index, _ in enumerate(row.get(field, []))]


def checked_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value):
        raise ValueError("Use an outcome ID containing 1–64 letters, digits, _ or -.")
    return value


def checked_patch(patch):
    if not isinstance(patch, dict) or set(patch) - FIELDS:
        raise ValueError("Unknown task-page fields.")
    if len(json.dumps(patch).encode()) > 1024 * 1024:
        raise ValueError("A task-page update must be smaller than 1 MiB.")
    for name, value in patch.items():
        if name not in ("references", "sections", "tables", "figures", "curves", "plan_diagram", "flows", "main_sections", "progress_overview") and not isinstance(value, str):
            raise ValueError(f"{name} must be text.")
    if "title" in patch and not 1 <= len(patch["title"].strip()) <= 120:
        raise ValueError("Use a task-page title of 1–120 characters.")
    if "status" in patch and patch["status"] not in STATUSES:
        raise ValueError("Status must be planned, working, blocked, done or archived.")
    if patch.get("parent"):
        checked_id(patch["parent"])
    if "objective" in patch and (len(patch["objective"]) > 240 or "\n" in patch["objective"]):
        raise ValueError("Keep the main objective to one short paragraph of at most 240 characters.")
    if "current_task" in patch and (len(patch["current_task"]) > 240 or "\n" in patch["current_task"]):
        raise ValueError("Keep the current task to one short paragraph of at most 240 characters.")
    if "progress_overview" in patch:
        overview = patch["progress_overview"]
        if not isinstance(overview, dict) or (overview and (
                set(overview) != {"completed", "current", "next", "caption"} or
                any(not isinstance(text, str) or not text.strip() or len(text) > 240 or "\n" in text
                    for text in overview.values()))):
            raise ValueError("Progress overview needs concise completed, current, next and caption text; use {} to remove it.")
    if "main_sections" in patch:
        rows = patch["main_sections"]
        if not isinstance(rows, list) or len(rows) > 12:
            raise ValueError("Use at most twelve main reading sections.")
        for row in rows:
            if (not isinstance(row, dict) or not {"title", "text", "sections"} <= set(row)
                    or set(row) - {"title", "text", "sections", "phase", "curves", "figures"}
                    or row.get("phase", "current") not in ("current", "future", "previous")
                    or not all(isinstance(row[key], str) for key in ("title", "text"))
                    or not 1 <= len(row["title"].strip()) <= 120
                    or not isinstance(row["sections"], list) or len(row["sections"]) > 20
                    or not all(isinstance(identity, str) for identity in row["sections"])
                    or len(set(row["sections"])) != len(row["sections"])):
                raise ValueError("Main sections need a title, text, unique supporting section IDs and an optional current, future or previous phase.")
            for identity in row["sections"]:
                checked_id(identity)
            visuals = {name: row[name] for name in ("curves", "figures") if name in row}
            checked_patch(visuals)
            if sum(len(value) for value in visuals.values()) > 2:
                raise ValueError("Show at most two key plots per main passage; keep extra plots in linked appendices.")
    if "flows" in patch:
        flows = patch["flows"]
        if not isinstance(flows, list) or len(flows) > 8:
            raise ValueError("Use at most eight explanatory flows.")
        for flow in flows:
            if (not isinstance(flow, dict) or set(flow) != {"title", "caption", "steps"}
                    or not all(isinstance(flow[key], str) for key in ("title", "caption"))
                    or not isinstance(flow["steps"], list) or not 2 <= len(flow["steps"]) <= 4):
                raise ValueError("Flows need a title, caption and two to four ordered steps.")
            for step in flow["steps"]:
                if (not isinstance(step, dict) or set(step) != {"title", "text"}
                        or not isinstance(step["title"], str) or not 1 <= len(step["title"].strip()) <= 80
                        or not isinstance(step["text"], str) or len(step["text"]) > 180):
                    raise ValueError("Use short step titles and brief explanations.")
    if "plan_diagram" in patch:
        diagram = patch["plan_diagram"]
        if not isinstance(diagram, dict):
            raise ValueError("A plan diagram must be an object; use {} to remove it.")
        if diagram:
            if (set(diagram) != {"title", "caption", "comparisons", "fixed", "resources"}
                    or not all(isinstance(diagram[key], str) for key in ("title", "caption", "resources"))
                    or not isinstance(diagram["comparisons"], list) or not 1 <= len(diagram["comparisons"]) <= 6
                    or not isinstance(diagram["fixed"], list) or not 1 <= len(diagram["fixed"]) <= 12
                    or not all(isinstance(value, str) and 1 <= len(value.strip()) <= 120
                               for value in diagram["fixed"])):
                raise ValueError("Plan diagrams need labeled comparisons, shared settings and a caption.")
            for row in diagram["comparisons"]:
                if (not isinstance(row, dict) or set(row) != {"from", "to", "change"}
                        or not all(isinstance(value, str) and 1 <= len(value.strip()) <= 120
                                   for value in row.values())):
                    raise ValueError("Each comparison needs short from, to and change labels.")
    if "curves" in patch:
        charts = patch["curves"]
        if not isinstance(charts, list) or len(charts) > 8:
            raise ValueError("Use at most eight measured curve figures.")
        for chart in charts:
            required = {"title", "caption", "x_label", "y_label", "x_maximum", "y_maximum", "gap", "series"}
            if (not isinstance(chart, dict) or not required <= set(chart) or set(chart) - required - {"axes", "metrics", "follows_table"}
                    or not all(isinstance(chart[key], str) for key in ("title", "caption", "x_label", "y_label"))
                    or not all(type(chart[key]) in (int, float) and 0 < chart[key] < 1e15
                               for key in ("x_maximum", "y_maximum", "gap"))
                    or not isinstance(chart["series"], list) or not 1 <= len(chart["series"]) <= 16):
                raise ValueError("Curves need explicit scales, units, measurement cadence and series.")
            if "follows_table" in chart and (not isinstance(chart["follows_table"], str) or not chart["follows_table"].strip()):
                raise ValueError("A preceding table needs its exact nonempty title.")
            if "metrics" in chart:
                metrics = chart["metrics"]
                metric_fields = {"key", "label", "y_label", "y_maximum", "gap", "series"}
                if (not isinstance(metrics, list) or not 1 <= len(metrics) <= 8
                        or any(not isinstance(m, dict) or set(m) != metric_fields for m in metrics)):
                    raise ValueError("Curve metrics need unique keys, labels, scales and recorded series.")
                if (any(not isinstance(m["key"], str)
                               or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", m["key"])
                               or not isinstance(m["label"], str) or not m["label"].strip()
                               for m in metrics)
                        or len({m["key"] for m in metrics}) != len(metrics)):
                    raise ValueError("Curve metrics need unique keys and text labels.")
                for metric in metrics:
                    combined = {k: v for k, v in chart.items() if k != "metrics"}
                    combined.update({k: metric[k] for k in ("y_label", "y_maximum", "gap", "series")})
                    checked_patch({"curves": [combined]})
            if "axes" in chart:
                axes = chart["axes"]
                if (not isinstance(axes, list) or not 1 <= len(axes) <= 3
                        or len({a.get("key") for a in axes if isinstance(a, dict)}) != len(axes)):
                    raise ValueError("Use unique measured x axes")
                for axis in axes:
                    if (not isinstance(axis, dict) or not {"key", "label"} <= set(axis)
                            or set(axis) - {"key", "label", "divisor"}
                            or axis["key"] not in ("x", "round", "elapsed_seconds")
                            or not isinstance(axis["label"], str)
                            or type(axis.get("divisor", 1)) not in (int, float)
                            or not 0 < axis.get("divisor", 1) < 1e15):
                        raise ValueError("Axes need explicit recorded keys, labels and positive units")
            for series in chart["series"]:
                if (not isinstance(series, dict) or not {"label", "points"} <= set(series)
                        or set(series) - {"label", "points", "resume_of", "resume_from", "elapsed_offset_seconds", "reference"}
                        or not isinstance(series["label"], str) or not isinstance(series["points"], list)
                        or len(series["points"]) > 2000):
                    raise ValueError("Invalid measured curve series.")
                if "reference" in series and (type(series["reference"]) is not bool or (series["reference"] and len(series["points"]) != 1)):
                    raise ValueError("A constant reference needs exactly one actual measurement.")
                if set(series) & {"resume_of", "resume_from", "elapsed_offset_seconds"}:
                    if (not {"resume_of", "resume_from"} <= set(series)
                            or not isinstance(series["resume_of"], str)
                            or series["resume_of"] == series["label"]
                            or type(series["resume_from"]) not in (int, float)
                            or not 0 <= series["resume_from"] <= chart["x_maximum"]
                            or not any(s["label"] == series["resume_of"] and "resume_of" not in s
                                       for s in chart["series"])
                            or ("elapsed_offset_seconds" in series and (
                                type(series["elapsed_offset_seconds"]) not in (int, float)
                                or not 0 <= series["elapsed_offset_seconds"] < 1e15))
                            or any(p["x"] <= series["resume_from"] for p in series["points"])):
                        raise ValueError("Resume series need a recorded checkpoint, original parent and optional recorded runtime offset.")
                previous = -1
                for point in series["points"]:
                    if (not isinstance(point, dict) or not {"x", "y"} <= set(point) or set(point) - {"x", "y", "round", "elapsed_seconds", "episodes", "successes", "lo", "hi"}
                            or type(point["x"]) not in (int, float) or type(point["y"]) not in (int, float)
                            or not 0 <= point["x"] <= chart["x_maximum"] or not previous < point["x"]
                            or not 0 <= point["y"] <= chart["y_maximum"]):
                        raise ValueError("Use finite measured points, ordered by x within the explicit scales.")
                    for key in ("round", "elapsed_seconds", "episodes", "successes", "lo", "hi"):
                        if key in point and point[key] is not None:
                            if type(point[key]) not in (int, float) or not 0 <= point[key] < 1e15:
                                raise ValueError("Point metadata must be finite and nonnegative")
                    if "episodes" in point:
                        if (type(point["episodes"]) is not int or point["episodes"] < 1
                                or type(point.get("successes")) is not int
                                or not 0 <= point["successes"] <= point["episodes"]
                                or not all(type(point.get(k)) in (int, float) for k in ("lo", "hi"))
                                or not 0 <= point["lo"] <= point["y"] <= point["hi"] <= chart["y_maximum"]):
                            raise ValueError("Evaluation metadata needs valid counts and intervals")
                    previous = point["x"]
    for name in ("tables", "figures"):
        if name not in patch:
            continue
        if not isinstance(patch[name], list) or len(patch[name]) > 12:
            raise ValueError(f"{name} must contain at most 12 visuals.")
        for visual in patch[name]:
            common = {"title", "caption"}
            required = common | ({"columns", "rows"} if name == "tables" else
                                 {"maximum", "unit", "values"})
            optional = {"row_headers"} if name == "tables" else set()
            if not isinstance(visual, dict) or not required <= set(visual) or set(visual) - required - optional:
                raise ValueError(f"Invalid {name} entry.")
            if not all(isinstance(visual[key], str) for key in common):
                raise ValueError("Visual titles and captions must be text.")
            if name == "tables":
                if "row_headers" in visual and type(visual["row_headers"]) is not bool:
                    raise ValueError("row_headers must be a boolean.")
                columns, rows = visual["columns"], visual["rows"]
                if (not isinstance(columns, list) or not 1 <= len(columns) <= 8
                        or not all(isinstance(value, str) for value in columns)
                        or not isinstance(rows, list) or len(rows) > 100
                        or not all(isinstance(row, list) and len(row) == len(columns)
                                   and all(isinstance(value, str) for value in row) for row in rows)):
                    raise ValueError("Use text table cells matching 1–8 column labels.")
            else:
                maximum, values = visual["maximum"], visual["values"]
                if (type(maximum) not in (int, float) or not 0 < maximum < 1e15
                        or not isinstance(visual["unit"], str) or not isinstance(values, list)
                        or not 1 <= len(values) <= 30):
                    raise ValueError("Bar figures need a positive scale, units and 1–30 measured values.")
                for row in values:
                    if (not isinstance(row, dict) or set(row) != {"label", "value", "note"}
                            or not isinstance(row["label"], str) or not isinstance(row["note"], str)
                            or type(row["value"]) not in (int, float)
                            or not 0 <= row["value"] <= maximum):
                        raise ValueError("Bar values must be measured numbers between zero and the scale maximum.")
    for name in ("references", "sections"):
        if name not in patch:
            continue
        rows = patch[name]
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError(f"{name} must contain at most 100 entries.")
        seen = set()
        for row in rows:
            required = {"url", "title"} if name == "references" else {"id", "title", "text"}
            optional = {"description"} if name == "references" else {"references", "links_title", "tables", "figures", "curves", "plan_diagram", "flows", "group", "summary", "source_text"}
            if (not isinstance(row, dict) or not required <= set(row)
                    or set(row) - required - optional
                    or not all(isinstance(row[key], str) for key in required | (set(row) & {"description", "links_title", "group", "summary", "source_text"}))):
                raise ValueError(f"Invalid {name} entry.")
            if name == "references":
                checked_url(row["url"])
            else:
                identity = checked_id(row["id"])
                if (identity in seen or identity in ("overview", "plan", "results", "references", "history", "appendices")
                        or identity in ("task-progress", "task-summary", "next-action")
                        or re.fullmatch(r"main-\d+", identity)):
                    raise ValueError("Custom section IDs must be unique and distinct from built-in sections.")
                seen.add(identity)
                if row.get("group") and len(row["group"]) > 120:
                    raise ValueError("Use short appendix group names.")
                checked_patch({key: row[key] for key in ("references", "tables", "figures", "curves", "plan_diagram", "flows") if key in row})
    return patch


class Reports:
    def __init__(self, home):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.home / "reports.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS goals (
                    owner TEXT NOT NULL, id TEXT NOT NULL, state TEXT NOT NULL,
                    revision INTEGER NOT NULL, updated REAL NOT NULL,
                    PRIMARY KEY (owner,id));
                CREATE TABLE IF NOT EXISTS revisions (
                    owner TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
                    state TEXT NOT NULL, updated REAL NOT NULL,
                    PRIMARY KEY (owner,id,revision));
                CREATE TABLE IF NOT EXISTS selections (
                    owner TEXT PRIMARY KEY, goal TEXT NOT NULL);
            """)
        self.path.chmod(0o600)
        self._navigation_cache = {}
        self._navigation_lock = threading.Lock()

    def connect(self):
        return sqlite3.connect(str(self.path), timeout=10)

    @staticmethod
    def unpack(owner, goal, row):
        if row is None:
            return None
        state, revision, updated = row
        return dict(json.loads(state), owner=owner, id=goal, revision=revision, updated=updated)

    def get(self, owner, goal):
        with self.connect() as db:
            row = db.execute("SELECT state,revision,updated FROM goals WHERE owner=? AND id=?",
                             (owner, goal)).fetchone()
        return self.unpack(owner, goal, row)

    def current(self, owner):
        with self.connect() as db:
            row = db.execute("SELECT goal FROM selections WHERE owner=?", (owner,)).fetchone()
        state = self.get(owner, row[0]) if row else None
        return self.get(owner, state["parent"]) if state and state.get("parent") else state

    def primary(self, owner):
        current = self.current(owner)
        if current and current["status"] not in ("done", "archived"):
            return current
        return next((goal for goal in self.list(owner)
                     if not goal.get("parent") and goal["status"] not in ("done", "archived")), current)

    def list(self, owner=None):
        with self.connect() as db:
            rows = db.execute("SELECT owner,id,state,revision,updated FROM goals "
                              + ("WHERE owner=? " if owner else "") + "ORDER BY updated DESC,id",
                              (owner,) if owner else ()).fetchall()
        return [self.unpack(row[0], row[1], row[2:]) for row in rows]

    def navigation(self):
        """Read small navigation records; decode a report only when it changes."""
        fields = ("title", "summary", "results", "current_task", "status", "parent", "section_title")
        with self._navigation_lock, self.connect() as db:
            # Metadata and changed states must come from the same snapshot.
            db.execute("BEGIN")
            rows = db.execute("SELECT owner,id,revision,updated FROM goals "
                              "ORDER BY updated DESC,id").fetchall()
            current = {}
            for owner, goal, revision, updated in rows:
                key = (owner, goal, revision, updated)
                record = self._navigation_cache.get(key)
                if record is None:
                    raw = db.execute("SELECT state FROM goals WHERE owner=? AND id=?",
                                     (owner, goal)).fetchone()[0]
                    state = json.loads(raw)
                    record = {field: state[field] for field in fields if field in state}
                    record.update(owner=owner, id=goal, revision=revision, updated=updated)
                current[key] = record
            self._navigation_cache = current
            return [dict(record) for record in current.values()]

    @staticmethod
    def write_state(db, owner, goal, state):
        previous = db.execute("SELECT state,revision,updated FROM goals WHERE owner=? AND id=?",
                              (owner, goal)).fetchone()
        checked_patch(state)
        targets = [row["id"] for row in state["sections"]] + [
            identity for row in state["sections"] for identity in visual_targets(row)]
        if len(targets) != len(set(targets)):
            raise ValueError("A supporting section ID collides with a generated visual destination.")
        section_ids = set(targets)
        references = [identity for row in state.get("main_sections", [])
                      for identity in row["sections"] + referenced_ids(row["text"])]
        references.extend(identity for name in ("summary", "results", "plan", "next_action")
                          for identity in referenced_ids(state[name]))
        if any(identity not in section_ids for identity in references):
            raise ValueError("Main text must link to existing supporting section IDs.")
        serialized = json.dumps(state, ensure_ascii=False, sort_keys=True)
        revision = previous[1] if previous else 0
        if not previous or serialized != previous[0]:
            revision += 1
            updated = time.time()
            db.execute("INSERT OR REPLACE INTO goals VALUES (?,?,?,?,?)",
                       (owner, goal, serialized, revision, updated))
            db.execute("INSERT INTO revisions VALUES (?,?,?,?,?)",
                       (owner, goal, revision, serialized, updated))
        else:
            updated = previous[2]
        return dict(state, owner=owner, id=goal, revision=revision, updated=updated)

    @staticmethod
    def subplan_section(goal, state):
        blocks = [state["summary"]]
        for name, label in (("status", "Recorded status"), ("results", "Recorded progress"),
                            ("next_action", "Next action"), ("plan", "Approach")):
            if state[name]:
                separator = "\n" if re.match(r"^[-*] ", state[name]) else " "
                blocks.append("**" + label + ":**" + separator + state[name])
        blocks.extend("**" + row["title"] + "**\n" + row["text"] for row in state["sections"])
        return {"id": goal, "title": state.get("section_title") or state["title"],
                "text": "\n\n".join(block for block in blocks if block),
                "references": state["references"],
                "links_title": state.get("links_title") or "Open pages for this part of the task",
                **{key: state[key] for key in ("tables", "figures", "curves", "plan_diagram", "flows") if key in state}}

    def update(self, owner, patch, goal=None, new_title=None, default_title="Main task"):
        checked_id(owner)
        patch = checked_patch(patch)
        if goal:
            checked_id(goal)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if goal is None and not new_title:
                selected = db.execute("SELECT goal FROM selections WHERE owner=?", (owner,)).fetchone()
                goal = selected[0] if selected else None
            goal = goal or uuid.uuid4().hex[:16]
            previous = db.execute("SELECT state,revision,updated FROM goals WHERE owner=? AND id=?",
                                  (owner, goal)).fetchone()
            if new_title and previous:
                raise ValueError("That outcome already exists. Update it without --new.")
            if previous:
                state = json.loads(previous[0])
            else:
                state = {"title": new_title or default_title, "summary": "", "plan": "",
                         "results": "", "next_action": "", "status": "working",
                         "references": [], "sections": []}
            if "sections" in patch:
                previous_sections = {row["id"]: row for row in state["sections"]}
                patch = dict(patch, sections=[
                    {**{key: previous_sections.get(row["id"], {})[key]
                        for key in ("group", "summary", "source_text")
                        if key in previous_sections.get(row["id"], {})}, **row}
                    for row in patch["sections"]])
            state.update(patch)
            parent = state.get("parent")
            previous_parent = json.loads(previous[0]).get("parent") if previous else None
            if previous_parent and parent != previous_parent:
                raise ValueError("Move a subplan only through an explicit hierarchy repair.")
            if parent:
                if parent == goal:
                    raise ValueError("A task cannot be its own parent.")
                parent_row = db.execute("SELECT state FROM goals WHERE owner=? AND id=?",
                                        (owner, parent)).fetchone()
                if not parent_row or json.loads(parent_row[0]).get("parent"):
                    raise ValueError("Choose an existing main task from this same Herdr task.")
            else:
                # Main-task edits must retain sections maintained by subplans.
                for child_id, raw in db.execute("SELECT id,state FROM goals WHERE owner=?", (owner,)):
                    child = json.loads(raw)
                    if child.get("parent") == goal:
                        section = self.subplan_section(child_id, child)
                        local = next((row for row in state["sections"] if row["id"] == child_id), {})
                        section.update({key: local[key] for key in ("group", "summary", "source_text") if key in local})
                        index = next((i for i, row in enumerate(state["sections"])
                                      if row["id"] == child_id), len(state["sections"]))
                        state["sections"] = [row for row in state["sections"] if row["id"] != child_id]
                        state["sections"].insert(index, section)
            result = self.write_state(db, owner, goal, state)
            if parent:
                parent_state = json.loads(parent_row[0])
                section = self.subplan_section(goal, state)
                sections = parent_state["sections"]
                local = next((row for row in sections if row["id"] == goal), {})
                section.update({key: local[key] for key in ("group", "summary", "source_text") if key in local})
                index = next((i for i, row in enumerate(sections) if row["id"] == goal), len(sections))
                sections = [row for row in sections if row["id"] != goal]
                sections.insert(min(index, len(sections)), section)
                parent_state["sections"] = sections
                self.write_state(db, owner, parent, parent_state)
            db.execute("INSERT OR REPLACE INTO selections VALUES (?,?)", (owner, parent or goal))
        return result

    def related_history(self, owner, goal, before=None, limit=20):
        goals = [goal] + [row["id"] for row in self.list(owner) if row.get("parent") == goal]
        titles = {row["id"]: row.get("section_title") or row["title"] for row in self.list(owner)}
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,state,revision,updated FROM revisions WHERE owner=? AND id IN ("
                + ",".join("?" for _ in goals) + ") "
                + ("AND (updated,id,revision)<(?,?,?) " if before else "")
                + "ORDER BY updated DESC,id DESC,revision DESC LIMIT ?",
                (owner, *goals, *(before or []), limit)).fetchall()
        return [dict(self.unpack(owner, row[0], row[1:]),
                     part="Main task" if row[0] == goal else titles[row[0]]) for row in rows]

    def history(self, owner, goal, offset=0, limit=20, before=None):
        with self.connect() as db:
            rows = db.execute(
                "SELECT state,revision,updated FROM revisions WHERE owner=? AND id=? "
                + ("AND revision<? " if before else "")
                + "ORDER BY revision DESC LIMIT ? OFFSET ?",
                (owner, goal, before, limit, offset) if before else
                (owner, goal, limit, offset)).fetchall()
        return [self.unpack(owner, goal, row) for row in rows]


def session_id(row):
    return hashlib.sha256(json.dumps([row["connection"], row["endpoint"],
                                      row["boot_id"]]).encode()).hexdigest()[:24]


def conclusion_token(goal):
    # Attention tracks the visible conclusion, not revision numbers or archived
    # source wording. Presentation-only whitespace/bold/list changes stay quiet.
    def normalize(text):
        text = re.sub(r"(?m)^[-*] +", "", text.replace("**", ""))
        return re.sub(r"\s+", " ", text).strip()
    value = json.dumps([normalize(goal.get(name, "")) for name in
                        ("summary", "results", "current_task")] +
                       ["done" if goal.get("status") == "done" else "update"],
                       ensure_ascii=False, separators=(",", ":"))
    checksum = 2166136261
    encoded = value.encode("utf-16-le")
    for offset in range(0, len(encoded), 2):
        checksum = ((checksum ^ int.from_bytes(encoded[offset:offset+2], "little")) * 16777619) & 0xffffffff
    return f"{checksum:08x}"


def session_snapshot(rows, include_completed=False):
    """Small navigation read model, grouped by actual space and task IDs."""
    spaces = {}
    for owner, task, goal in rows:
        if goal.get("parent") or (goal["status"] == "archived" or
                not task.get("available_in_context", True)) and not include_completed:
            continue
        space_key = task.get("workspace_id") or "unassigned"
        space = spaces.setdefault(space_key, {
            "id": space_key, "label": task.get("workspace_label") or "Space unavailable",
            "dom_id": "space-" + hashlib.sha256(space_key.encode()).hexdigest()[:16],
            "tasks": {}})
        entry = space["tasks"].setdefault(owner, {
            "owner": owner, "label": task.get("label") or "Unnamed",
            "agent_status": task.get("agent_status") or "unknown",
            "available": task.get("available_in_context", True),
            "last_activity_at": task.get("last_activity_at"),
            "finished_at": task.get("finished_at"), "finished_viewed_at": task.get("finished_viewed_at"), "reports": []})
        token = conclusion_token(goal)
        mark = task.get("conclusion_marks", {}).get(goal["id"], {})
        readable = goal["status"] != "archived" and not goal.get("legacy") and bool(
            goal.get("summary", "").strip() or goal.get("results", "").strip())
        entry["reports"].append({
            "id": goal["id"], "title": goal["title"], "status": goal["status"],
            "legacy": bool(goal.get("legacy")), "current_task": goal.get("current_task", ""),
            "token": token, "updated": goal.get("updated"),
            "has_conclusion": readable and goal["status"] == "done", "needs_reading": readable,
            "mark": mark if mark.get("token") == token else {},
            "steps": goal.get("subplans", [])})
    for space in spaces.values():
        if sum(other["label"] == space["label"] for other in spaces.values()) > 1:
            space["display_label"] = space["label"] + " · " + space["id"]
        space["tasks"] = list(space["tasks"].values())
        for task in space["tasks"]:
            task["reports"].sort(key=lambda goal: (
                goal["status"] in ("done", "archived"), -(goal.get("updated") or 0), goal["id"]))
            task["color"], task["attention"] = session_attention(task)
        space["tasks"].sort(key=lambda task: (
            {"yellow": 0, "green": 1, "red": 2}[task["color"]], task["label"].casefold(), task["owner"]))
    return sorted(spaces.values(), key=lambda space: (
        all(task["color"] == "red" for task in space["tasks"]), space["label"].casefold(), space["id"]))


def session_attention(task):
    if task["agent_status"] == "working" and task.get("available", True):
        return "green", "In progress"
    if task["agent_status"] == "blocked" and task.get("available", True):
        return "yellow", "Needs input"
    conclusions = [goal for goal in task["reports"] if goal["needs_reading"]]
    if any(not goal["mark"] for goal in conclusions):
        return "yellow", "Needs review"
    if (task.get("finished_at") or 0) > (task.get("finished_viewed_at") or 0):
        return "yellow", "Finished · check outcome"
    if conclusions:
        return "red", "Reviewed" if all(goal["mark"].get("mode") == "reviewed" for goal in conclusions) else "Viewed"
    return "red", ("Viewed" if task.get("finished_at") else "Idle") if task.get("available", True) else "Task closed"


def paragraphs(text, lookup=None, compact=False, scan=True):
    def inline(block):
        value = inline_text(block)
        if lookup:
            def reference(match):
                context = lookup.get(match[2])
                if not context:
                    return match[0]
                # The label has already passed through escaped, restricted
                # inline markup; destinations come only from existing IDs.
                return (f'<a class="appendix-citation" href="#{html.escape(match[2])}">'
                        f'{match[1]} <span class="citation-number">(Appendix {context["number"]})</span></a>')
            value = APPENDIX_REFERENCE.sub(reference, value)
        return value.replace(chr(10), '<br>')
    output, prose, items = [], [], []
    def flush_prose():
        if prose:
            block = "\n".join(prose)
            words = len(block.split()) + 2 * len(APPENDIX_REFERENCE.findall(block))
            if scan and (compact or words > 45):
                # Split only recorded sentence boundaries. Keep decimals, units,
                # links and the underlying source text intact.
                parts = re.split(r"(?:(?<=[.!?;])|(?<=[.!?;]\*\*))\s+(?=(?:\*\*)?[A-Z])", block)
                output.append('<ul class="report-bullets">' + "".join(
                    "<li>" + inline(scan_lead(part)) + "</li>" for part in parts) + "</ul>")
            else:
                output.append("<p>" + inline(block) + "</p>")
            prose.clear()
    def flush_items():
        if items:
            output.append('<ul class="report-bullets">' + "".join(
                "<li>" + inline(item) + "</li>" for item in items) + "</ul>")
            items.clear()
    for line in text.strip().splitlines():
        match = re.match(r"^[-*] +(.*)", line)
        if match:
            flush_prose()
            items.append(match[1])
        elif not line.strip():
            flush_prose()
        elif items and line.startswith(("  ", "\t")):
            items[-1] += "\n" + line.strip()
        else:
            flush_items()
            prose.append(line)
    flush_prose()
    flush_items()
    return "".join(output)


def scan_lead(text):
    """Emphasize an existing short lead without splitting links or markup."""
    if "**" in text:
        return text
    prefix = text.split("[", 1)[0]
    words = list(re.finditer(r"\S+", prefix))
    if not words:
        return text
    colon = prefix.find(":")
    end = colon + 1 if 0 < colon < 60 and len(prefix[:colon].split()) <= 7 else words[min(3, len(words)-1)].end()
    return "**" + text[:end] + "**" + text[end:]


def snippet(text):
    text = text.strip().split("\n\n", 1)[0]
    return text


def linked_pages(rows, title="Open related pages", identity="supporting-links"):
    if not rows:
        return ""
    esc = html.escape
    items = "".join(
        f'<li><a href="{esc(row["url"], quote=True)}">{esc(row["title"])}</a>'
        + (f'<p class="link-purpose">{esc(row["description"])}</p>' if row.get("description") else "")
        + "</li>" for row in rows)
    if len(rows) > 3:
        return f'<details id="{esc(identity)}"><summary>{esc(title)}</summary><ul>{items}</ul></details>'
    return f'<div class="linked-pages"><h3>{esc(title)}</h3><ul>{items}</ul></div>'


def visual_content(state, section_id=None):
    """Small declarative visuals; no scripts, external assets or chart library."""
    esc = html.escape
    result = ""
    colors = ("#59636f", "#2263bd", "#08775e", "#9a4eb3", "#9a4b56", "#537684", "#68602a", "#555")
    for chart_index, chart in enumerate(state.get("curves", [])):
        legend, marks = "", ""
        for index, series in enumerate(chart["series"]):
            color = colors[index % len(colors)]
            legend += (f'<span><i style="background:{color}"></i>{esc(series["label"])}'
                       + (" · no measurement" if not series["points"] else "")
                       + (" · one measurement" if len(series["points"]) == 1 else "") + "</span>")
            segments, current = [], []
            for point in series["points"]:
                if current and point["x"] - current[-1]["x"] > chart["gap"]:
                    segments.append(current)
                    current = []
                current.append(point)
            if current:
                segments.append(current)
            for segment in segments:
                coordinates = " ".join(f'{p["x"]/chart["x_maximum"]*100:g},{100-p["y"]/chart["y_maximum"]*100:g}'
                                       for p in segment)
                marks += (f'<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">'
                          f'<polyline points="{coordinates}" fill="none" stroke="{color}" '
                          'stroke-width="2" vector-effect="non-scaling-stroke"/></svg>')
            for point in series["points"]:
                label = f'{series["label"]}: {point["y"]:g} at {point["x"]:g}'
                marks += (f'<span class="curve-point" aria-label="{esc(label)}" title="{esc(label)}" '
                          f'style="left:{point["x"]/chart["x_maximum"]*100:g}%;'
                          f'top:{100-point["y"]/chart["y_maximum"]*100:g}%;background:{color}"></span>')
        y_ticks = "".join(f'<span style="top:{100-i*25}%">{chart["y_maximum"]*i/4:g}</span>' for i in range(5))
        x_ticks = "".join(f'<span style="left:{i*25}%">'
                          + (f'{chart["x_maximum"]*i/4/1000:g}k' if chart["x_maximum"] >= 1000 else
                             f'{chart["x_maximum"]*i/4:g}') + "</span>" for i in range(5))
        anchor = f' id="curve-{esc(section_id)}-{chart_index + 1}" tabindex="-1"' if section_id else ""
        result += (f'<figure class="learning-curve"{anchor} data-chart="{esc(json.dumps(chart), quote=True)}"><h2>{esc(chart["title"])}</h2>'
                   f'<div class="curve-legend">{legend}</div><p class="axis-note">{esc(chart["y_label"])}</p>'
                   f'<div class="curve-layout"><div class="curve-y-ticks">{y_ticks}</div>'
                   f'<div class="curve-plot">{marks}</div><div class="curve-x-ticks">{x_ticks}</div></div>'
                   f'<p class="curve-x-label">{esc(chart["x_label"])}</p>'
                   f'<figcaption>{esc(chart["caption"])}</figcaption></figure>')
    for table_index, table in enumerate(state.get("tables", [])):
        columns = table["columns"]
        head = "".join(f'<th scope="col">{esc(label)}</th>' for label in columns)
        rows = "".join("<tr>" + "".join(
            f'<{"th" if i == 0 and table.get("row_headers", True) else "td"} '
            + ('scope="row" ' if i == 0 and table.get("row_headers", True) else "")
            + f'data-label="{esc(columns[i])}">{inline_text(value)}</{"th" if i == 0 and table.get("row_headers", True) else "td"}>'
            for i, value in enumerate(row)) + "</tr>" for row in table["rows"])
        anchor = f' id="table-{esc(section_id)}-{table_index + 1}" tabindex="-1"' if section_id else ""
        result += (f'<figure class="comparison"{anchor}><h2>{esc(table["title"])}</h2>'
                   + ('<table class="prose-comparison">' if not table.get("row_headers", True) else '<table>')
                   + f'<thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>'
                   f'<figcaption>{inline_text(table["caption"])}</figcaption></figure>')
    for figure in state.get("figures", []):
        maximum = figure["maximum"]
        bars = "".join(
            '<div class="bar-row"><div class="bar-label">'
            + esc(row["label"]) + '<small>' + esc(row["note"]) + '</small></div>'
            + '<div class="bar-track" aria-hidden="true"><div class="bar-fill" style="width:'
            + f'{row["value"] / maximum * 100:.4f}%"></div></div>'
            + '<div class="bar-value">' + f'{row["value"]:g} / {maximum:g}'
            + "</div></div>" for row in figure["values"])
        result += (f'<figure class="bar-chart"><h2>{esc(figure["title"])}</h2>'
                   f'<p class="axis-note">{esc(figure["unit"])} · scale 0–{maximum:g}</p>{bars}'
                   f'<figcaption>{esc(figure["caption"])}</figcaption></figure>')
    for flow in state.get("flows", []):
        steps = "".join(f'<li><strong>{esc(step["title"])}</strong>'
                        f'<span>{esc(step["text"])}</span></li>' for step in flow["steps"])
        result += (f'<figure class="flow-diagram"><h2>{esc(flow["title"])}</h2>'
                   f'<ol class="flow-steps">{steps}</ol><figcaption>{esc(flow["caption"])}</figcaption></figure>')
    return result


def plan_diagram(state):
    diagram = state.get("plan_diagram")
    if not diagram:
        return ""
    esc = html.escape
    rows = "".join(
        '<li class="comparison-pair">'
        f'<span class="comparison-node">{esc(row["from"])}</span>'
        f'<span class="comparison-edge"><span>Change: {esc(row["change"])}</span>'
        '<span class="comparison-arrow" aria-hidden="true">→</span></span>'
        f'<span class="comparison-node">{esc(row["to"])}</span></li>'
        for row in diagram["comparisons"])
    fixed = "".join(f'<li>{esc(label)}</li>' for label in diagram["fixed"])
    return (f'<figure class="plan-diagram"><h2>{esc(diagram["title"])}</h2>'
            f'<ul class="comparison-pairs">{rows}</ul>'
            '<div class="comparison-fixed"><h3>Keep the original setup</h3>'
            f'<ul>{fixed}</ul><p class="comparison-resources">{esc(diagram["resources"])}</p></div>'
            f'<figcaption>{esc(diagram["caption"])}</figcaption></figure>')


def appendix_layout(state):
    """Explicit section IDs connect authored main text to grouped appendices."""
    groups, lookup = {}, {}
    for row in state["sections"]:
        group = row.get("group") or "Supporting detail"
        groups.setdefault(group, []).append(row)
    for group_index, rows in enumerate(groups.values(), 1):
        for index, row in enumerate(rows, 1):
            lookup[row["id"]] = {"row": row, "number": f"{group_index}.{index}", "mains": []}
            for identity in visual_targets(row):
                lookup[identity] = lookup[row["id"]]
    for index, main in enumerate(state.get("main_sections", []), 1):
        for identity in dict.fromkeys(main["sections"] + referenced_ids(main["text"])):
            if identity in lookup:
                context = (f"main-{index}", main["title"])
                if context not in lookup[identity]["mains"]:
                    lookup[identity]["mains"].append(context)
    for name, anchor, title in (
            ("summary", "task-summary", "Current work"),
            ("results", "results", state.get("results_title") or "Latest recorded progress"),
            ("next_action", "next-action", "Next action"),
            ("plan", "full-plan" if any(state.get(key) for key in
                                      ("tables", "figures", "curves", "flows", "plan_diagram")) else "plan",
             "Plan")):
        for identity in dict.fromkeys(referenced_ids(state[name])):
            if identity in lookup:
                context = (anchor, title)
                if context not in lookup[identity]["mains"]:
                    lookup[identity]["mains"].append(context)
    return groups, lookup


def main_reading(state, lookup, phase="current"):
    esc = html.escape
    body = ""
    for index, row in enumerate(state.get("main_sections", []), 1):
        if row.get("phase", "current") != phase:
            continue
        links = []
        cited = {lookup[identity]["row"]["id"] for identity in referenced_ids(row["text"]) if identity in lookup}
        for identity in row["sections"]:
            if identity in cited:
                continue
            if identity not in lookup:
                continue
            section = lookup[identity]
            links.append(f'<a href="#{esc(identity)}">Appendix {section["number"]}: '
                         f'{esc(section["row"]["title"])}</a>')
        body += (f'<section id="main-{index}" class="main-reading" tabindex="-1">'
                 f'<h3>{esc(row["title"])}</h3>{paragraphs(row["text"], lookup, compact=True)}'
                 + visual_content(row, f"main-{index}").replace("<h2>", "<h4>").replace("</h2>", "</h4>")
                 + ('<p class="appendix-links">Supporting detail: ' + "; ".join(links) + "</p>" if links else "")
                 + "</section>")
    return body


def appendix_body(state, groups, lookup):
    if not groups:
        return ""
    esc = html.escape
    intro = ("The main report links to the relevant detail below. Each appendix returns to the finding it supports."
             if state.get("main_sections") else "Supporting records and implementation details are grouped below.")
    body = ('<section class="appendices" id="appendices"><h2>Appendix · supporting detail</h2>'
            f'<p class="appendix-intro">{intro}</p>')
    for group_index, (title, rows) in enumerate(groups.items(), 1):
        body += f'<section class="appendix-group"><h3>{group_index} · {esc(title)}</h3>'
        for row in rows:
            context = lookup[row["id"]]
            body += (f'<details id="{esc(row["id"])}" class="task-part">'
                     f'<summary id="appendix-summary-{esc(row["id"])}">'
                     f'<span class="appendix-number">{context["number"]}</span> '
                     f'<span class="appendix-title">{esc(row["title"])}</span>'
                     + (f'<span class="appendix-preview">{inline_text(row["summary"])}</span>'
                        if row.get("summary") else "") + '</summary>')
            if context["mains"]:
                body += '<p class="appendix-return">' + " · ".join(
                    f'<a href="#{esc(anchor)}">↑ Return to: {esc(title)}</a>'
                    for anchor, title in context["mains"]) + "</p>"
            if row.get("summary"):
                body += paragraphs(row["text"])
            body += visual_content(row, row["id"]) + plan_diagram(row)
            has_visuals = any(row.get(key) for key in ("tables", "figures", "curves", "plan_diagram", "flows"))
            if has_visuals and not row.get("summary"):
                body += ('<details class="recorded-details"><summary>Recorded details</summary>'
                         + paragraphs(row["text"]) + "</details>")
            elif not row.get("summary"):
                body += paragraphs(row["text"])
            if row.get("source_text"):
                body += (f'<details id="source-{esc(row["id"])}" class="recorded-details">'
                         '<summary>Exact source wording and records</summary>'
                         + paragraphs(row["source_text"], scan=False) + "</details>")
            body += linked_pages(row.get("references", []), row.get("links_title") or "Open related pages",
                                 "links-" + row["id"]) + "</details>"
        body += "</section>"
    return body + "</section>"


def progress_overview(state):
    """Author-recorded work stages, never a computed completion percentage."""
    overview = state.get("progress_overview")
    if not overview:
        return ""
    steps = "".join(
        f'<li class="progress-{key}" id="progress-{key}"><strong>{label}</strong>'
        f'<span>{inline_text(overview[key])}</span></li>'
        for key, label in (("completed", "Done so far"), ("current", "Current stage"), ("next", "Next step")))
    return ('<figure class="progress-overview" aria-label="Progress toward the main task">'
            f'<ol class="flow-steps">{steps}</ol>'
            f'<figcaption>{inline_text(overview["caption"])}</figcaption></figure>')


def task_body(state):
    esc = html.escape
    groups, lookup = appendix_layout(state)
    body = (f'<header class="task-overview" id="overview" data-header-layout="task-v2">'
            f'<h1>{esc(state["title"])}</h1>')
    if state.get("current_task"):
        body += (f'<p class="current-task"><strong>Current task:</strong> '
                 f'{inline_text(state["current_task"])}'
                 f' <span class="status">· {esc(state["status"].title())}</span></p>')
    else:
        body += f'<p class="current-task"><strong>Current status:</strong> {esc(state["status"].title())}</p>'
    if state.get("objective"):
        body += f'<p id="task-objective">{inline_text(state["objective"])}</p>'
    body += '</header><!-- report-lists-v2 --><!-- report-groups-v3 -->'
    if state.get("progress_overview"):
        body += ('<section id="task-progress" tabindex="-1"><h2>Progress toward the main task</h2>'
                 + progress_overview(state) + '</section>')
    body += ('<section id="current-work" class="current-work" aria-labelledby="current-work-title" tabindex="-1">'
             '<h2 id="current-work-title">Current work</h2><div id="task-summary" tabindex="-1">'
             + (paragraphs(state["summary"], lookup, compact=True)
                or "<p>Add the main goal and current recommendation.</p>") + '</div>')
    body += visual_content(state).replace("<h2>", "<h3>").replace("</h2>", "</h3>")
    for name, label in (("results", state.get("results_title") or "Latest recorded progress"),
                        ("plan", "What we're doing")):
        if name == "plan":
            body += main_reading(state, lookup)
        if name == "plan" and state.get("plan_diagram"):
            body += ('<details id="full-plan"><summary>Plan and comparison rules</summary>'
                     + plan_diagram(state) + paragraphs(state[name], lookup) + "</details>")
            continue
        if not state[name].strip():
            continue
        if name == "plan" and (state.get("tables") or state.get("figures") or state.get("curves") or state.get("flows")):
            body += ('<details id="full-plan"><summary>Plan and comparison rules</summary>'
                     + paragraphs(state[name], lookup) + "</details>")
            continue
        essential = name == "results" and bool(state.get("results_title"))
        body += f'<section id="{name}" tabindex="-1"><h3>{esc(label)}</h3>{paragraphs(state[name] if essential else snippet(state[name]), lookup)}'
        remaining = state[name].strip().partition("\n\n")[2]
        if not essential and remaining:
            body += (f'<details id="full-{name}"><summary>Read the full '
                     + ("progress update" if name == "results" else "plan")
                     + f'</summary>{paragraphs(remaining, lookup)}</details>')
        body += "</section>"
    body += "</section>"
    if state["next_action"].strip():
        body += ('<section class="next-action" id="next-action" tabindex="-1"><h2>Next action</h2>'
                 + paragraphs(state["next_action"], lookup) + "</section>")
    for phase, identity, label in (("future", "future-work", "Future work"),
                                   ("previous", "previous-work", "Completed work and prior records")):
        reading = main_reading(state, lookup, phase)
        if reading:
            body += (f'<section id="{identity}" class="phase-group" aria-labelledby="{identity}-title">'
                     f'<h2 id="{identity}-title">{label}</h2>{reading}</section>')
    body += appendix_body(state, groups, lookup)
    body += linked_pages(state["references"], state.get("links_title") or "Open related pages")
    if state["revision"] > 1:
        body += ('<details id="history"><summary>Earlier updates</summary>'
                 '<p class="history-note">Saved earlier plans and progress, including updates to parts of this task.</p>'
                 '<button id="refresh-history" type="button" hidden>Show latest updates</button>'
                 '<div id="history-items"></div>'
                 '<button id="more-history" type="button">Load earlier updates</button></details>')
    return body


STYLE = """
*{box-sizing:border-box}[hidden]{display:none!important}
body{margin:0;background:#f7f7f9;color:#272d36;font:19px/1.6 system-ui}
strong{font-weight:700}
.report-bullets{max-width:68ch;padding-left:25px;margin:14px 0 24px}
.report-bullets>li{margin:10px 0;line-height:1.55;overflow-wrap:anywhere}
.report-bullets>li::marker{color:#507bb1}
main{max-width:900px;margin:auto;padding:30px 24px 60px}


h1{font-size:clamp(30px,5vw,42px);line-height:1.2;margin:14px 0 20px}
h2{font-size:24px;line-height:1.3}h3{font-size:19px;margin-bottom:8px}
p{max-width:70ch;white-space:normal;overflow-wrap:anywhere}
nav{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 26px}
button{font:inherit;font-size:16px;cursor:pointer;border:1px solid #d4d8df;
 border-radius:7px;background:white;color:#303944;padding:6px 12px}
button[aria-selected=true]{background:#e8eff8;color:#254f83;border-color:#abc0da}
a{color:#285e9c;overflow-wrap:anywhere}.top,.status{font-size:16px;color:#626771}
.top{display:flex;flex-wrap:wrap;align-items:center;gap:14px}
.update{font-size:15px;color:#285e9c;margin-left:auto}
li{margin:12px 0}details{border-top:1px solid #d4d8df;padding:14px 0}
summary{cursor:pointer;font-weight:600}.task{padding:17px 0;border-top:1px solid #d4d8df}
.task h2{font-size:21px;margin:0 0 4px}.task p{font-size:17px;margin:4px 0}
.next-action{border-left:3px solid #507bb1;padding-left:16px;margin:24px 0}
.linked-pages ul{padding-left:22px}.link-purpose,.history-note{font-size:16px;color:#626771;margin:4px 0}
.task-part{margin-top:8px}
.task-overview{padding-bottom:18px;margin-bottom:30px;border-bottom:1px solid #d4dce6}
.task-overview h1{margin:0 0 12px}
.task-overview p{margin:0}
.task-overview .current-task{margin-bottom:12px;color:#465368}
.current-task .status{white-space:nowrap}
.session-guide{margin-bottom:18px}
.attention-legend{list-style:none;display:flex;flex-wrap:wrap;gap:12px 24px;padding:0;font-size:16px}
.attention-legend li,.task-attention{display:flex;align-items:center;gap:9px}
.attention-dot{display:inline-block;flex:none;width:12px;height:12px;border-radius:50%;border:1px solid #0002}
.attention-dot.green{background:#18804a}.attention-dot.yellow{background:#d8a012}.attention-dot.red{background:#bd3342}
.session-options,.space-count,.task-attention,.report-attention{font-size:16px;color:#596575}
.session-space{margin:38px 0 46px;padding-top:22px;border-top:2px solid #c6d0dc}
.session-space>h2{font-size:27px;margin:0 0 6px}
.space-count{margin:0 0 20px}
.session-task{margin:16px 0 24px;padding:0 0 0 18px;border-left:3px solid #d5dde7}
.session-task-heading{display:flex;gap:12px;flex-wrap:wrap;align-items:baseline}
.session-task h3{font-size:21px;margin:0}.task-attention{line-height:1.5}
.session-reports{list-style:none;padding:0;margin:12px 0 0}
.session-report{margin:14px 0 24px}.session-report h4{font-size:19px;margin:0 0 6px}
.session-report p{margin:5px 0}.session-phase{font-size:17px;line-height:1.5}
.quiet-tasks{margin-top:18px;padding:12px 0}.quiet-tasks>summary{font-size:17px;font-weight:600}
.quiet-tasks[open]>.session-task{margin-top:22px}
.session-steps{font-size:16px;margin:10px 0}.session-steps>summary{font-size:16px}
.review-strip{display:flex;gap:12px;align-items:center;flex-wrap:wrap;font-size:16px;margin-top:18px}
.review-strip button{font-size:16px;padding:7px 11px}.review-note{font-size:15px;color:#596575}
.citation-number{font-size:.85em;white-space:nowrap}
figure[id]{scroll-margin-top:24px}
#task-progress,#results,#next-action{scroll-margin-top:24px}
.main-reading{margin:32px 0;scroll-margin-top:24px}
.current-work{margin:28px 0;padding-left:18px;border-left:3px solid #d5dde7}
#task-content>section#current-work.current-work{border-top:0;padding-top:0;margin-top:28px}
.current-work>h2{margin-top:0}
.current-work h3,.phase-group h3{font-size:22px;line-height:1.35}
.main-reading h4{font-size:20px;line-height:1.4;margin:16px 0}
#task-content>#next-action,#task-content>#future-work,#task-content>#previous-work,#task-content>#history{
 border-top:1px solid #bdc8d4;margin-top:32px;padding-top:24px}
.appendix-links,.appendix-return{font-size:16px;line-height:1.65}
.appendix-links a{display:inline-block;margin:3px 12px 3px 0}
.appendices{margin-top:52px;border-top:3px solid #9cabbc;padding-top:18px}
.appendices>h2{font-size:28px;margin-bottom:8px}
.appendix-intro{font-size:17px;color:#626771;margin-top:0}
.appendix-group{margin:32px 0;padding:4px 0 0 18px;border-left:2px solid #d4dce6}
.appendix-group>h3{font-size:22px;line-height:1.35;margin:0 0 16px}
.appendix-group .task-part{margin:0;scroll-margin-top:24px}
.task-part>summary{font-size:18px;line-height:1.45}
.appendix-number{font-size:16px;color:#626771;font-weight:400;margin-right:7px}
.appendix-preview{display:block;font-size:16px;font-weight:400;line-height:1.5;color:#505b68;margin:7px 0 0 26px}
.task-part[open]>summary .appendix-preview{display:none}
.task-part h2{font-size:21px}
.appendix-return{margin:10px 0 18px}
.recorded-details{font-size:17px;margin-top:20px}
figure{margin:26px 0}figcaption,.axis-note{font-size:16px;color:#626771;margin-top:10px}
.comparison-pairs{list-style:none;padding:0;margin:20px 0}
.comparison-pair{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.2fr) minmax(0,1fr);gap:16px;align-items:center;margin:20px 0}
.comparison-node{border:1px solid #bdc8d4;border-radius:8px;background:#fff;padding:15px 12px;text-align:center;font-size:18px;font-weight:600;overflow-wrap:anywhere}
.comparison-edge{display:flex;flex-direction:column;align-items:center;text-align:center;color:#285e9c;font-size:17px;line-height:1.45}
.comparison-arrow{font-size:34px;line-height:1.1}
.comparison-fixed{border-top:2px solid #bdc8d4;padding:8px 16px;background:#edf1f5}
.comparison-fixed ul{list-style:none;display:flex;flex-wrap:wrap;gap:6px 20px;padding:0;margin:8px 0;font-size:17px}
.comparison-fixed li{margin:0}.comparison-resources{margin:14px 0 6px;font-size:18px;font-weight:600}
.flow-steps{list-style:none;padding:0;display:flex;gap:30px;counter-reset:flow}
.flow-steps li{position:relative;flex:1;min-width:0;border-top:3px solid #507bb1;background:#edf1f5;padding:16px;margin:0;counter-increment:flow}
.flow-steps strong,.flow-steps span{display:block;font-size:17px;overflow-wrap:anywhere}
.flow-steps strong:before{content:counter(flow) ". ";color:#285e9c}
.flow-steps span{margin-top:8px;line-height:1.5}
.flow-steps li:not(:last-child):after{content:"→";position:absolute;right:-25px;top:20px;color:#285e9c;font-size:25px}
.progress-overview{margin:20px 0 28px;padding-bottom:24px;border-bottom:1px solid #bdc8d4}
.progress-overview .flow-steps strong:before{content:none}
.progress-overview .progress-completed{border-color:#48705c;background:#f0f4f1}
.progress-overview .progress-current{border-color:#285e9c;background:#e9f0f9}
.progress-overview .progress-next{border-color:#8793a2;background:#f0f1f3}
.progress-overview .ic-readout{display:none}
.progress-overview[data-inspecting=true] .ic-readout{display:block}
table{width:100%;border-collapse:collapse;font-size:17px;line-height:1.45}
.prose-comparison{table-layout:fixed}
th,td{padding:12px 10px;text-align:left;border-bottom:1px solid #d4d8df;overflow-wrap:anywhere}
thead{background:#edf1f5}tbody th{font-weight:600}tbody tr:nth-child(even){background:#f0f2f5}
.bar-row{display:grid;grid-template-columns:minmax(140px,1fr) 2fr 94px;gap:16px;align-items:center;margin:18px 0}
.bar-label{font-size:17px;font-weight:600}.bar-label small{display:block;font-size:15px;font-weight:400;color:#626771}
.bar-track{height:22px;background:#e0e6ed;border-radius:4px;overflow:hidden}
.bar-fill{height:100%;background:#507bb1}.bar-value{font-size:17px;font-variant-numeric:tabular-nums}
.curve-legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:16px}
.curve-legend i{display:inline-block;width:12px;height:12px;margin-right:7px;border-radius:50%}
.curve-layout{display:grid;grid-template-columns:38px minmax(0,1fr);column-gap:8px}
.curve-y-ticks{position:relative;grid-column:1;height:240px;font-size:16px}
.curve-y-ticks span{position:absolute;right:0;transform:translateY(-50%)}
.curve-plot{grid-column:2;position:relative;height:240px;border-left:1px solid #9faebd;border-bottom:1px solid #9faebd;
 background:repeating-linear-gradient(to top,#dfe5ec 0 1px,transparent 1px 25%);}
.curve-plot svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
.curve-point{position:absolute;width:8px;height:8px;border-radius:50%;transform:translate(-50%,-50%)}
.curve-x-ticks{grid-column:2;position:relative;height:36px;font-size:16px}
.curve-x-ticks span{position:absolute;top:9px;transform:translateX(-50%)}
.curve-x-ticks span:first-child{transform:none}.curve-x-ticks span:last-child{transform:translateX(-100%)}
.curve-x-label{font-size:16px;text-align:center;margin:5px 0}
:focus-visible{outline:3px solid #8d5300;outline-offset:3px}
@media(max-width:600px){main{padding:24px 18px}.update{margin-left:0}
.comparison-pair{grid-template-columns:1fr;gap:8px;padding-bottom:20px;border-bottom:1px solid #d4d8df}
.comparison-edge{flex-direction:row;justify-content:center;gap:12px}
.comparison-arrow{transform:rotate(90deg)}
.flow-steps{flex-direction:column;gap:26px}
.flow-steps li:not(:last-child):after{content:"↓";right:50%;top:auto;bottom:-28px;transform:translateX(50%)}
table,tbody,tr,th,td{display:block}thead{display:none}tbody tr{border-bottom:1px solid #bdc8d4;padding:8px 0}
tbody th,tbody td{border:0;padding:6px 10px}tbody td:before{content:attr(data-label) ": ";font-weight:600}
.bar-row{grid-template-columns:1fr 94px;gap:8px}.bar-label{grid-column:1 / -1}.bar-track{grid-column:1}}
"""


def document(title, body, script=""):
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>{STYLE}</style></head>"
            f"<body><main>{body}</main>{script}</body></html>")


def task_document(state, session_url, live=True, history=None, dashboard=None):
    navigation = (f'<a href="{html.escape(session_url, quote=True)}">Session overview</a>'
                  if live else '<span>Standalone task snapshot</span>')
    top = (f'<div class="top">{navigation}'
           '<span class="update" id="update-indicator">Current task</span></div>')
    content = task_body(state)
    if not live and history:
        rows = "".join(
            f'<details><summary>{html.escape(row.get("part", "Main task"))} · Revision {row["revision"]}</summary>'
            + "".join(f"<h3>{html.escape(name.replace('_', ' ').title())}</h3>{paragraphs(row[name])}"
                      for name in ("summary", "plan", "results", "next_action") if row[name])
            + plan_diagram(row)
            + visual_content({"flows": row.get("flows", [])})
            + "</details>" for row in history)
        content = content.replace('<div id="history-items"></div>', f'<div id="history-items">{rows}</div>')
        content = content.replace('<button id="more-history" type="button">Load earlier updates</button>', "")
        content = content.replace(
            '<button id="refresh-history" type="button" hidden>Show latest updates</button>', "")
    body = top + (f'<article id="task-content" data-owner="{state["owner"]}" data-goal="{state["id"]}" data-live="{str(live).lower()}" '
                  f'data-revision="{state["revision"]}">{content}</article>')
    dashboard = dashboard or snapshot_from_state(state, session_url.split("/session/", 1)[0])
    payload = json.dumps(dashboard, ensure_ascii=False).replace("<", "\\u003c")
    body += '<script type="application/json" id="herdr-dashboard-data">' + payload + "</script>"
    script = "".join("<script>" + (Path(__file__).parent / name).read_text() + "</script>"
                     for name in ("report_view.js", "dashboard.js"))
    return document(state["title"], body, script)


def session_items(spaces, base):
    esc = html.escape
    def task_html(task):
        owner = task["owner"]
        reports = []
        for goal in task["reports"]:
            url = f'{base}/task/{owner}/{goal["id"]}'
            identity = f'goal-{owner}-{goal["id"]}'
            reports.append(f'<li class="session-report" data-goal="{esc(goal["id"])}">'
                           f'<h4><a id="{identity}" href="{esc(url, quote=True)}">{esc(goal["title"])}</a></h4>')
            if goal["needs_reading"]:
                mode = goal["mark"].get("mode")
                reports.append('<p class="report-attention">' +
                               ({"viewed": "Viewed", "reviewed": "Reviewed"}.get(mode,
                                   "New conclusion · needs review" if goal["has_conclusion"]
                                   else "Latest update · needs review")) + '</p>')
            elif goal.get("legacy"):
                reports.append('<p class="report-attention">Saved websites · no living summary yet</p>')
            if goal.get("current_task"):
                reports.append('<p class="session-phase"><strong>Current:</strong> ' + inline_text(goal["current_task"]) + '</p>')
            if goal.get("steps"):
                reports.append(f'<details id="steps-{identity}" class="session-steps"><summary>Supporting steps · {len(goal["steps"])}</summary><ul>')
                for step in goal["steps"]:
                    reports.append(f'<li><a href="{esc(url, quote=True)}#{esc(step["id"])}">{esc(step["title"])}</a></li>')
                reports.append('</ul></details>')
            reports.append('</li>')
        return (f'<article class="session-task" data-owner="{owner}" id="task-{owner}">'
                f'<div class="session-task-heading"><h3>Task: {esc(task["label"])}</h3>'
                f'<span class="task-attention" title="{esc(task["attention"])}"><span class="attention-dot {task["color"]}" aria-hidden="true"></span>'
                f'<span class="attention-label">{esc(task["attention"])}</span></span></div>'
                + '<ul class="session-reports">' + ''.join(reports) + '</ul></article>')
    output = []
    for space in spaces:
        identity = 'space-' + hashlib.sha256(space["id"].encode()).hexdigest()[:16]
        active = [task for task in space["tasks"] if task["color"] != "red"]
        quiet = [task for task in space["tasks"] if task["color"] == "red"]
        output.append(f'<section class="session-space" id="{identity}" data-space-id="{esc(space["id"])}">'
                      f'<h2>Space: {esc(space.get("display_label") or space["label"])}</h2><p class="space-count">{len(active)} active or awaiting attention · {len(quiet)} quiet</p>')
        output.extend(task_html(task) for task in active)
        if quiet:
            output.append(f'<details class="quiet-tasks" id="quiet-{identity}"><summary>Other tasks · {len(quiet)}</summary>')
            output.extend(task_html(task) for task in quiet)
            output.append('</details>')
        output.append('</section>')
    payload = json.dumps(spaces, ensure_ascii=False).replace('<', '\\u003c')
    return (''.join(output) or '<p>No task pages yet.</p>') + '<script type="application/json" id="session-data">' + payload + '</script>'


def session_document(rows, session, base, include_completed=False):
    spaces = session_snapshot(rows, include_completed)
    toggle = "false" if include_completed else "true"
    body = ('<div class="top"><span>Session navigation</span>'
            '<span class="update" id="update-indicator">Current tasks</span></div><h1>Session overview</h1>'
            '<p class="session-guide"><strong>Space → task → main report.</strong> Active work and new conclusions come first.</p>'
            '<ul class="attention-legend"><li><span class="attention-dot green" aria-hidden="true"></span>In progress</li>'
            '<li><span class="attention-dot yellow" aria-hidden="true"></span>Needs review or input</li>'
            '<li><span class="attention-dot red" aria-hidden="true"></span>Quiet · viewed, reviewed or idle</li></ul>'
            f'<p class="session-options"><a href="{base}/session/{session}?completed={toggle}">'
            + ("Hide archived / closed tasks" if include_completed else "Show archived / closed tasks") + '</a></p>'
            + '<div id="session-items" data-layout="spaces-v1">' + session_items(spaces, base) + '</div>')
    script = "<script>" + (Path(__file__).parent / "session_view.js").read_text() + "</script>"
    return document("Herdr session overview", body, script)


class ReportServer:
    """Companions share a stable loopback port; a peer takes over when its owner closes."""
    def __init__(self, store):
        self.store = store
        self.reports = Reports(store.home)
        self.dashboard = Dashboard(self.reports)
        self.config_path = store.home / "reports-server.json"
        self.server = None
        self.thread = None
        self.config = None
        self.next_check = 0

    @property
    def base(self):
        return f'http://127.0.0.1:{self.config["port"]}/{self.config["token"]}' if self.config else None

    def navigation_rows(self, session):
        members = {owner: row for owner, row in self.store.read()["tasks"].items()
                   if row.get("connection") and session_id(row) == session}
        if not members:
            return None
        all_goals = self.reports.navigation()
        goals = [dict(goal) for goal in all_goals if not goal.get("parent")]
        for goal in goals:
            goal["subplans"] = [{"id": child["id"], "title": child.get("section_title") or child["title"]}
                               for child in all_goals if child.get("parent") == goal["id"]
                               and child["owner"] == goal["owner"]]
        existing = {goal["owner"] for goal in goals}
        for owner, row in members.items():
            if owner not in existing and row.get("available_in_context", True):
                goals.append({"owner": owner, "id": "start", "legacy": True,
                              "title": row.get("label", "Unnamed"),
                              "next_action": row.get("next_action", ""),
                              "status": {"working": "working", "blocked": "blocked",
                                         "done": "done"}.get(row.get("agent_status"), "planned")})
        return [(goal["owner"], members[goal["owner"]], goal)
                for goal in goals if goal["owner"] in members]

    def task_url(self, owner, goal):
        return f"{self.base}/task/{owner}/{goal}"

    def session_url(self, row):
        return f"{self.base}/session/{session_id(row)}"

    def parse_url(self, url):
        if not self.base:
            return None
        parsed, root = urlsplit(url), urlsplit(self.base)
        if (parsed.scheme, parsed.netloc) != (root.scheme, root.netloc):
            return None
        prefix = root.path + "/"
        if not parsed.path.startswith(prefix):
            return None
        parts = parsed.path[len(prefix):].split("/")
        if len(parts) == 3 and parts[0] == "task":
            return {"kind": "task", "owner": parts[1], "goal": parts[2]}
        if len(parts) == 2 and parts[0] == "session":
            return {"kind": "session", "session": parts[1]}
        return None

    def ensure(self):
        if time.monotonic() < self.next_check:
            return self.base
        self.next_check = time.monotonic() + 2
        with self.store.transaction():
            config = json.loads(self.config_path.read_text()) if self.config_path.exists() else None
            if config and config.get("pid") and process_token(config["pid"]) == config.get("process_token"):
                self.config = config
                return self.base
            server = ThreadingHTTPServer(("127.0.0.1", config["port"] if config else 0), self.handler())
            server.daemon_threads = True
            config = dict(config or {}, port=server.server_port,
                          token=(config or {}).get("token") or uuid.uuid4().hex,
                          pid=os.getpid(), process_token=process_token(os.getpid()))
            self.config = config
            self.server = server
            atomic_json(self.config_path, config)
            self.thread = threading.Thread(target=server.serve_forever, daemon=True)
            self.thread.start()
        return self.base

    def close(self):
        if self.server:
            with self.store.transaction():
                self.server.shutdown()
                self.server.server_close()
                current = json.loads(self.config_path.read_text())
                if current.get("pid") == os.getpid() and current.get("token") == self.config["token"]:
                    atomic_json(self.config_path, dict(current, pid=None, process_token=None))
            self.server = None
            self.next_check = 0

    def handler(self):
        coordinator = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def not_modified(self, etag):
                if etag and self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.end_headers()
                    return True
                return False

            def send(self, body, content_type="text/html; charset=utf-8", etag=None):
                if self.not_modified(etag):
                    return
                body = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                if etag:
                    self.send_header("ETag", etag)
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                try:
                    parts = urlsplit(self.path).path.removeprefix("/" + coordinator.config["token"] + "/").split("/")
                    if (not self.path.startswith("/" + coordinator.config["token"] + "/")
                            or len(parts) != 4 or parts[0] != "task" or parts[3] not in ("review", "dashboard")):
                        self.send_error(404)
                        return
                    origin = self.headers.get("Origin")
                    base = urlsplit(coordinator.base)
                    if origin and origin != f"{base.scheme}://{base.netloc}":
                        self.send_error(403)
                        return
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4096:
                        self.send_error(400)
                        return
                    patch = json.loads(self.rfile.read(length))
                    state = coordinator.reports.get(parts[1], parts[2])
                    row = coordinator.store.task(parts[1])
                    if not row or "connection" not in row:
                        self.send_error(404)
                        return
                    if not state and parts[2] == "start":
                        state = start_state(parts[1], row)
                    if not state:
                        self.send_error(404)
                        return
                    if parts[3] == "dashboard":
                        if not isinstance(patch, dict) or set(patch) != {"id", "action"}:
                            self.send_error(400)
                            return
                        result = coordinator.dashboard.change(parts[1], parts[2], patch["id"],
                                                              patch["action"], coordinator.base,
                                                              fallback=state)
                        self.send(json.dumps(result), "application/json")
                        return
                    if (set(patch) != {"token", "mode"} or patch["mode"] not in ("viewed", "reviewed")
                            or state["status"] == "archived" or not (state["summary"] or state["results"])):
                        self.send_error(400)
                        return
                    if patch["token"] != conclusion_token(state):
                        self.send_error(409)
                        return
                    coordinator.store.mark_conclusion(parts[1], parts[2], patch["token"], patch["mode"])
                    self.send(json.dumps({"token": patch["token"],
                        "mark": coordinator.store.task(parts[1])["conclusion_marks"][parts[2]],
                        "persistent": True}), "application/json")
                except (ValueError, KeyError, TypeError, sqlite3.Error):
                    self.send_error(400)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_GET(self):
                try:
                    path = urlsplit(self.path)
                    prefix = "/" + coordinator.config["token"] + "/"
                    if not path.path.startswith(prefix):
                        self.send_error(404)
                        return
                    parts = path.path[len(prefix):].split("/")
                    if len(parts) in (3, 4) and parts[0] == "task":
                        owner, goal = parts[1:3]
                        state = coordinator.reports.get(owner, goal)
                        row = coordinator.store.task(owner)
                        if not row or "connection" not in row:
                            self.send_error(404)
                            return
                        if not state and goal == "start":
                            state = start_state(owner, row)
                        if not state:
                            self.send_error(404)
                            return
                        main = coordinator.reports.get(owner, state["parent"]) if state.get("parent") else state
                        if len(parts) == 3:
                            if state.get("parent"):
                                self.send_response(302)
                                self.send_header("Location", coordinator.task_url(owner, main["id"]) + "#" + goal)
                                self.send_header("Cache-Control", "no-cache")
                                self.end_headers()
                            else:
                                self.send(task_document(state, coordinator.session_url(row),
                                    dashboard=coordinator.dashboard.snapshot(
                                        owner, goal, coordinator.base, fallback=state)))
                        elif parts[3] == "dashboard":
                            result = coordinator.dashboard.snapshot(owner, main["id"], coordinator.base,
                                                                    fallback=main)
                            self.send(json.dumps(result), "application/json")
                        elif parts[3] == "data":
                            token = conclusion_token(main)
                            mark = row.get("conclusion_marks", {}).get(main["id"], {})
                            mark = mark if mark.get("token") == token else {}
                            etag = (f'"{owner}-{main["id"]}-{main["revision"]}'
                                    + (f'-{mark["mode"]}' if mark else "") + '"')
                            if self.not_modified(etag):
                                return
                            self.send(json.dumps({"state": main, "html": task_body(main),
                                      "review": {"token": token, "mark": mark, "persistent": True}}),
                                      "application/json", etag)
                        elif parts[3] == "updates":
                            query = parse_qs(path.query)
                            before = json.loads(query["before"][0]) if "before" in query else None
                            if before is not None:
                                if (not isinstance(before, list) or len(before) != 3
                                        or not isinstance(before[0], (int, float))
                                        or not isinstance(before[2], int)):
                                    raise ValueError("Invalid update cursor.")
                                checked_id(before[1])
                            rows = coordinator.reports.related_history(owner, main["id"], before)
                            cursor = [rows[-1]["updated"], rows[-1]["id"], rows[-1]["revision"]] if len(rows) == 20 else None
                            self.send(json.dumps({"rows": rows, "cursor": cursor}), "application/json")
                        elif parts[3] == "history":
                            query = parse_qs(path.query)
                            offset = max(0, int(query.get("offset", ["0"])[0]))
                            before = int(query["before"][0]) if "before" in query else None
                            history = coordinator.reports.history(owner, goal, offset, before=before)
                            self.send(json.dumps(history), "application/json")
                        else:
                            self.send_error(404)
                    elif len(parts) in (2, 3) and parts[0] == "session":
                        rows = coordinator.navigation_rows(parts[1])
                        if rows is None or len(parts) == 3 and parts[2] != "data":
                            self.send_error(404)
                            return
                        archived = parse_qs(path.query).get("completed") == ["true"]
                        if len(parts) == 3:
                            body = json.dumps({"spaces": session_snapshot(rows, archived),
                                              "base": coordinator.base, "persistent": True})
                            self.send(body, "application/json", '"' + hashlib.sha256(body.encode()).hexdigest() + '"')
                        else:
                            body = session_document(rows, parts[1], coordinator.base, archived)
                            self.send(body, etag='"' + hashlib.sha256(body.encode()).hexdigest() + '"')
                    else:
                        self.send_error(404)
                except (ValueError, KeyError, sqlite3.Error):
                    self.send_error(400)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        return Handler
