"""Private, atomic task state shared by optional browser windows."""
import contextlib
import copy
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid
from urllib.parse import urlsplit, unquote_plus

ZOOM_LIMITS = {"terminal": (50, 300), "browser": (25, 300)}


def browser_home():
    return Path(os.environ.get(
        "HERDR_BROWSER_HOME",
        str(Path.home() / ".local/state/herdr-browser"),
    ))


def checked_url(url):
    url = url.strip()
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Enter a full http:// or https:// URL.")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing passwords cannot be saved.")
    if any(ord(char) < 32 for char in url):
        raise ValueError("The URL contains a control character.")
    return url


def task_key(connection, context, tab_id):
    identity = [connection, context["endpoint"], context["boot_id"], tab_id]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def url_identity(url):
    """Compare pages without anchors, preserving meaningful paths and queries."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    if port and (parsed.scheme.lower(), port) not in (("http", 80), ("https", 443)):
        host += f":{port}"
    query = parsed.query
    # Generated localhost HTML reports use v only to refresh cached content.
    # Preserve all other query parameters, including filters and remote versions.
    if parsed.hostname in ("localhost", "127.0.0.1", "::1") and parsed.path.endswith((".html", ".htm")):
        query = "&".join(part for part in query.split("&")
                         if unquote_plus(part.split("=", 1)[0]) != "v")
    return parsed._replace(scheme=parsed.scheme.lower(), netloc=host,
                           path=parsed.path or "/", query=query, fragment="").geturl()


def page_urls(page):
    return [page["url"], *page.get("aliases", [])]


def find_page(row, url):
    identity = url_identity(url)
    return next((page for page in row.get("links", [])
                 if any(url_identity(value) == identity for value in page_urls(page))), None)


def merge_pages(row):
    """Keep the earliest tab and repair selections when saved pages overlap."""
    originals, replacements, owners = [], {}, {}
    for page in row["links"]:
        identities = {url_identity(value) for value in page_urls(page)}
        match_ids = {owners[value]["id"] for value in identities if value in owners}
        matches = [other for other in originals if other["id"] in match_ids] if match_ids else []
        if not matches:
            originals.append(page)
            for value in identities:
                owners[value] = page
            continue
        kept = matches[0]
        # A redirect may join two earlier groups; the first saved ID still wins.
        for duplicate in [*matches[1:], page]:
            replacements[duplicate["id"]] = kept["id"]
            kept["aliases"] = list(dict.fromkeys([
                *kept.get("aliases", []), *page_urls(duplicate)]))
            if not kept.get("title") or kept["title"] == kept["url"]:
                kept["title"] = duplicate.get("title") or kept["url"]
            if duplicate in originals:
                originals.remove(duplicate)
            for value in page_urls(duplicate):
                owners[url_identity(value)] = kept
        for value in identities:
            owners[value] = kept
    row["links"] = originals
    selected = row["sections"][row["active_section"]].get("active_page")
    for section, state in row["sections"].items():
        page_id = state.get("active_page")
        while page_id in replacements:
            page_id = replacements[page_id]
        page = next((page for page in originals if page["id"] == page_id), None)
        if page and page["section"] != section:
            state.update(active_page=None, last_url="")
            if selected in replacements and state is row["sections"][row["active_section"]]:
                row["active_section"] = page["section"]
                row["sections"][page["section"]].update(
                    active_page=page["id"], last_url=page["url"])
        elif page_id != state.get("active_page"):
            state.update(active_page=page_id, last_url=page["url"] if page else "")
    row["last_url"] = row["sections"][row["active_section"]]["last_url"]
    return replacements


def organized_task(row):
    """Upgrade legacy flat bookmarks in place without losing their URL or order."""
    links = row.setdefault("links", [])
    sections = row.setdefault("sections", {})
    sections.setdefault("General", {"last_url": row.get("last_url", ""), "active_page": None})
    for link in links:
        section = link.setdefault("section", "General")
        # A deterministic legacy ID keeps read-only snapshots stable.
        link.setdefault("id", hashlib.sha256(
            json.dumps([section, link["url"]]).encode()).hexdigest()[:24])
        sections.setdefault(section, {"last_url": "", "active_page": None})
    row.setdefault("active_section", "General")
    if row["active_section"] not in sections:
        row["active_section"] = "General"
    merge_pages(row)
    return row


def checked_section(name):
    name = name.strip()
    if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
        raise ValueError("Use a section name of 1–80 characters.")
    return name


class Store:
    def __init__(self, home=None):
        self.home = Path(home or browser_home())
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.home / "tasks.json"
        self._cached_snapshot = None
        self._snapshot_lock = threading.RLock()

    @contextlib.contextmanager
    def transaction(self, blocking=True):
        fd = os.open(self.home / "tasks.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text())
                    if data.get("schema") != 1 or not isinstance(data.get("tasks"), dict):
                        raise ValueError("Unsupported task state")
                except (ValueError, TypeError, AttributeError) as error:
                    raise ValueError(
                        f"Cannot read {self.path}; it has been preserved: {error}"
                    ) from error
            else:
                data = {"schema": 1, "tasks": {}, "browser_enabled": True}
            before = json.dumps(data, sort_keys=True)
            for row in data["tasks"].values():
                organized_task(row)
            yield data
            if json.dumps(data, sort_keys=True) != before:
                atomic_json(self.path, data)
                self._cached_snapshot = None

    @staticmethod
    def _stamp(stat):
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def _snapshot(self):
        """Read an atomic file snapshot without waiting for a writer's lock."""
        with self._snapshot_lock:
            try:
                stamp = self._stamp(self.path.stat())
            except FileNotFoundError:
                return {"schema": 1, "tasks": {}, "browser_enabled": True}
            if self._cached_snapshot and self._cached_snapshot[0] == stamp:
                return self._cached_snapshot[1]
            with self.path.open() as stream:
                stamp = self._stamp(os.fstat(stream.fileno()))
                try:
                    data = json.load(stream)
                    if data.get("schema") != 1 or not isinstance(data.get("tasks"), dict):
                        raise ValueError("Unsupported task state")
                except (ValueError, TypeError, AttributeError) as error:
                    raise ValueError(
                        f"Cannot read {self.path}; it has been preserved: {error}"
                    ) from error
            before = copy.deepcopy(data)
            for row in data["tasks"].values():
                organized_task(row)
            if data != before:
                # Persist legacy repairs only after re-reading under the writer
                # lock. A busy publisher must not block readers or lose its work.
                try:
                    with self.transaction(blocking=False):
                        pass
                except BlockingIOError:
                    pass
                return data
            self._cached_snapshot = (stamp, data)
            return data

    def read(self):
        return copy.deepcopy(self._snapshot())

    def preference(self, enabled):
        with self.transaction() as data:
            data["browser_enabled"] = bool(enabled)

    def zoom_percent(self, pane):
        minimum, maximum = ZOOM_LIMITS[pane]
        saved = self.read().get("zoom_percent", {})
        value = saved.get(pane, 100) if isinstance(saved, dict) else 100
        return max(minimum, min(maximum, value)) if type(value) is int else 100

    def set_zoom_percent(self, pane, value):
        minimum, maximum = ZOOM_LIMITS[pane]
        if type(value) is not int:
            raise ValueError("Zoom percentage must be an integer.")
        value = max(minimum, min(maximum, value))
        with self.transaction() as data:
            if not isinstance(data.get("zoom_percent"), dict):
                data["zoom_percent"] = {}
            data["zoom_percent"][pane] = value
        return value

    def task(self, key):
        return copy.deepcopy(self._snapshot()["tasks"].get(key, {}))

    def register_context(self, connection, context):
        workspace_labels = {row["workspace_id"]: row["label"]
                            for row in context.get("workspaces", [])}
        with self.transaction() as data:
            for row in data["tasks"].values():
                if (row.get("connection"), row.get("endpoint"), row.get("boot_id")) == (
                        connection, context["endpoint"], context["boot_id"]):
                    row["available_in_context"] = False
            for task in context.get("tasks", []):
                key = task_key(connection, context, task["tab_id"])
                row = data["tasks"].setdefault(key, {"links": [], "last_url": "", "next_action": ""})
                organized_task(row)
                old_status, new_status = row.get("agent_status"), task.get("agent_status")
                if (old_status == "working" and new_status in ("idle", "done")
                        or new_status == "done" and old_status != "done"):
                    row["finished_at"] = time.time()
                if (row.get("agent_status") is not None and
                        row.get("agent_status") != task.get("agent_status")):
                    row["last_activity_at"] = time.time()
                row.update(task)
                row.update(connection=connection, endpoint=context["endpoint"],
                           boot_id=context["boot_id"],
                           available_in_context=True,
                           workspace_label=workspace_labels.get(task["workspace_id"], ""))

    def touch_task(self, key):
        with self.transaction() as data:
            if key in data["tasks"]:
                data["tasks"][key]["last_activity_at"] = time.time()

    def mark_conclusion(self, key, goal, token, mode="viewed"):
        """Record an explicit foreground observation, separate from agent activity."""
        if mode not in ("viewed", "reviewed"):
            raise ValueError("Use viewed or reviewed.")
        with self.transaction() as data:
            if key not in data["tasks"]:
                raise ValueError("Task is unavailable.")
            marks = data["tasks"][key].setdefault("conclusion_marks", {})
            previous = marks.get(goal, {})
            if previous.get("token") == token and previous.get("mode") == "reviewed":
                return
            marks[goal] = {"token": token, "mode": mode, "at": time.time()}
            if data["tasks"][key].get("finished_at"):
                data["tasks"][key]["finished_viewed_at"] = time.time()

    def mark_task_viewed(self, key):
        with self.transaction() as data:
            row = data["tasks"].get(key, {})
            if row.get("finished_at", 0) > row.get("finished_viewed_at", 0):
                row["finished_viewed_at"] = time.time()

    def save_link(self, key, url, title, section="General"):
        url = checked_url(url)
        section = checked_section(section)
        with self.transaction() as data:
            row = organized_task(data["tasks"][key])
            row["last_activity_at"] = time.time()
            links = row["links"]
            existing = find_page(row, url)
            if existing:
                section = existing["section"]
                if title:
                    existing["title"] = title
            else:
                existing = {"id": uuid.uuid4().hex, "section": section,
                            "url": url, "title": title or url}
                links.append(existing)
            state = row["sections"].setdefault(section, {"last_url": "", "active_page": None})
            # Agent registration does not replace another actively selected tab.
            if not state["last_url"]:
                state.update(last_url=existing["url"], active_page=existing["id"])
            if row["active_section"] == section:
                row["last_url"] = state["last_url"]
            return existing["id"]

    def add_section(self, key, name):
        name = checked_section(name)
        with self.transaction() as data:
            row = organized_task(data["tasks"][key])
            row["sections"].setdefault(name, {"last_url": "", "active_page": None})
        return name

    def select_section(self, key, name):
        with self.transaction() as data:
            row = organized_task(data["tasks"][key])
            if name not in row["sections"]:
                raise ValueError("Section no longer exists.")
            row["active_section"] = name
            row["last_url"] = row["sections"][name]["last_url"]
            row["last_activity_at"] = time.time()

    def activate_page(self, key, section, page_id=None):
        with self.transaction() as data:
            row = organized_task(data["tasks"][key])
            page = next((link for link in row["links"] if link["id"] == page_id), None)
            if page_id is not None and page is None:
                raise ValueError("Page no longer exists.")
            if page:
                section = page["section"]
            state = row["sections"][section]
            state.update(active_page=page_id, last_url=page["url"] if page else "")
            row["active_section"] = section
            row["last_url"] = state["last_url"]
            row["last_activity_at"] = time.time()

    def record_location(self, key, section, page_id, url, title=None):
        """Update only the section/page owned by the active native view."""
        url = checked_url(url)
        with self.transaction() as data:
            row = organized_task(data["tasks"][key])
            page = next((link for link in row["links"]
                         if link["id"] == page_id and link["section"] == section), None)
            if page_id and page is None:
                return  # A different window may have closed this page.
            if page:
                # Preserve the originally opened address across redirects.
                if url_identity(page["url"]) != url_identity(url) and not page.get("aliases"):
                    page["aliases"] = [page["url"]]
                page["url"] = url
                if title:
                    page["title"] = title
            row["sections"][section].update(last_url=url, active_page=page_id)
            if row["active_section"] == section:
                row["last_url"] = url
            replacements = merge_pages(row)
            while page_id in replacements:
                page_id = replacements[page_id]
            return next((link for link in row["links"] if link["id"] == page_id), None)

    def close_page(self, key, page_id):
        with self.transaction() as data:
            row = organized_task(data["tasks"][key])
            row["links"] = [link for link in row["links"] if link["id"] != page_id]
            for state in row["sections"].values():
                if state["active_page"] == page_id:
                    state.update(active_page=None, last_url="")
            row["last_url"] = row["sections"][row["active_section"]]["last_url"]

    def set_value(self, key, field, value):
        if field not in ("last_url", "next_action"):
            raise ValueError("Unsupported task field")
        if field == "last_url" and value:
            value = checked_url(value)
        with self.transaction() as data:
            row = organized_task(data["tasks"][key])
            row[field] = value
            if field == "last_url":
                row["sections"][row["active_section"]]["last_url"] = value


def status_label(value):
    return {"working": "Working", "blocked": "Needs input", "done": "Done",
            "idle": "Idle", "unknown": "Unavailable"}.get(value, "Unavailable")


def task_caption(task, workspace="", attention=None):
    color, label = attention or ({"working": ("green", "In progress"),
                                 "blocked": ("yellow", "Needs input"),
                                 "idle": ("red", "Idle"), "done": ("red", "Quiet")}.get(
                                     task.get("agent_status"), ("red", "Unavailable")))
    circle = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[color]
    return (f'{circle} Task: {task.get("label", "Unnamed")}   |   '
            f'Workspace: {workspace or "Unavailable"}   |   '
            f'{label}')


def task_home(row, section=None):
    esc = html.escape
    section = section or row.get("active_section", "General")
    links = "".join(
        f'<a href="{esc(link["url"], quote=True)}">{esc(link["title"])}</a>'
        for link in row.get("links", []) if link.get("section", "General") == section
    ) or "<p>Open a URL above to add your first page. Use + for another tab.</p>"
    status = status_label(row.get("agent_status"))
    return f"""<!doctype html><html><head><meta name="viewport" content="width=device-width">
<style>
body{{font:17px system-ui;color:#272a30;background:#f7f7f9;margin:0;padding:40px 28px;line-height:1.6}}
main{{max-width:640px;margin:0 auto}}h1{{font-size:30px;line-height:1.25;font-weight:600}}
p{{color:#626771}}a{{display:block;background:#fff;border:1px solid #e2e3e7;
color:#285e9c;padding:12px 16px;margin:10px 0;border-radius:10px;text-decoration:none}}
a:hover{{background:#eef3f9}}a:focus-visible{{outline:3px solid #507bb1}}
small{{color:#737985}}label{{font-weight:600}}
</style></head><body><main><small>HERDR</small>
<p><label>Workspace:</label> {esc(row.get("workspace_label") or "Unavailable")}</p>
<h1>Task: {esc(row.get("label", "Unnamed"))}</h1>
<p><label>Status:</label> {status}<br><label>Section:</label> {esc(section)}</p>
<label>Next action</label><p>{esc(row.get("next_action") or "Use Next action above to add a reminder.")}</p>
<label>Pages in this section</label>{links}
<p><small>One page runs at a time. Other tabs keep their URLs and reload when selected.</small></p>
</main></body></html>"""
