"""Task-scoped URL delivery; external apps retain their desktop browser."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time
import uuid
from urllib.parse import urlsplit

from .store import (Store, atomic_json, browser_home, checked_section, checked_url,
                    task_key, find_page, url_identity)

HANDLER = "herdr-browser.desktop"
SCHEMES = ("x-scheme-handler/http", "x-scheme-handler/https")

# Exact hostnames that should use Firefox instead of the task browser.
# Configure per user; no site-specific or organization-specific defaults ship.
FIREFOX_HOSTS = frozenset(host.strip().lower().rstrip(".") for host in
    os.environ.get("HERDR_BROWSER_FIREFOX_HOSTS", "").split(",") if host.strip())


def firefox_required(url):
    """Match an explicitly configured hostname, never a domain suffix."""
    parts = urlsplit(url)
    return (parts.scheme in ("http", "https")
            and (parts.hostname or "").lower().rstrip(".") in FIREFOX_HOSTS)


def open_firefox(url):
    """Reuse the user's Firefox profile without changing desktop defaults."""
    subprocess.run(["/usr/bin/firefox", "--new-tab", checked_url(url)],
                   check=True, timeout=15)


def process_token(pid):
    try:
        # /proc's command name may contain spaces or parentheses.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] not in ("Z", "X") else None
    except (OSError, IndexError):
        return None


def desktop_path():
    data = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    return data / "applications" / HANDLER


def install_desktop(launcher, destination=None):
    path = Path(destination or desktop_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    executable = str(Path(launcher).resolve())
    # Desktop Exec quoting is distinct from shell quoting.
    executable = executable.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$").replace("%", "%%")
    path.write_text(
        "[Desktop Entry]\nType=Application\nName=Herdr task browser\n"
        # xdg-utils' desktop lookup cannot parse a quoted first executable.
        # An unquoted env launcher keeps paths with spaces valid for both
        # the desktop specification and the older xdg-utils parser.
        # Compatibility guard: even an older companion that selects this
        # desktop entry must send external application clicks to Firefox.
        f'Exec=/usr/bin/env "{executable}" external %u\n'
        "Terminal=false\nNoDisplay=true\n"
        "MimeType=x-scheme-handler/http;x-scheme-handler/https;\n"
        "Categories=Network;WebBrowser;\n")
    return path


def instances(home=None):
    result = []
    for path in (Path(home or browser_home()) / "instances").glob("*.json"):
        try:
            info = json.loads(path.read_text())
            os.kill(info["pid"], 0)
            if info.get("process_token") != process_token(info["pid"]):
                continue
            if not info.get("routing_enabled") or not info.get("inbox"):
                continue
            context = json.loads(Path(info["context"]).read_text())
            if not context.get("available"):
                continue
            info["selection"] = context
            result.append(info)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result


def desktop_default(scheme):
    return subprocess.check_output(
        ["xdg-mime", "query", "default", scheme], text=True, timeout=5).strip()


def set_default(application, scheme):
    subprocess.run(["xdg-mime", "default", application, scheme],
                   check=True, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


@contextlib.contextmanager
def routing_lock(home):
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(home / "routing.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def sync_defaults(home=None, force=False):
    """Release legacy desktop takeover; never claim HTTP/HTTPS associations."""
    if not force and (os.environ.get("HERDR_BROWSER_TEST_DISPLAY") == "private"
                      or os.environ.get("HERDR_BROWSER_NO_DESKTOP") == "1"):
        return
    home = Path(home or browser_home())
    with routing_lock(home):
        state_path = home / "routing.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        previous = state.get("previous", {})
        for scheme in SCHEMES:
            current = desktop_default(scheme)
            if current == HANDLER:
                application = previous.get(scheme)
                if not application or application == HANDLER:
                    raise ValueError(f"Cannot determine the previous browser for {scheme}.")
                set_default(application, scheme)
                if desktop_default(scheme) != application:
                    raise ValueError(f"Desktop did not restore {application} for {scheme}.")
            elif current:
                previous[scheme] = current
        # Keep the legacy active bit aligned with running receivers so an older
        # window does not immediately reclaim restored defaults. New code
        # treats this solely as receiver availability, never desktop ownership.
        atomic_json(state_path, {"active": bool(instances(home)),
                                "desktop_claimed": False, "scope": "herdr-only",
                                "previous": previous})


def base_url(url):
    return url_identity(url)


def choose_target(url, candidates, tasks, origin_tab=None, origin_socket=None):
    """Prefer exact origin, then known ownership, then recent browser focus."""
    if not candidates:
        return None
    owners = {key for key, row in tasks.items() if find_page(row, url)}
    choices = []
    for info in candidates:
        context = info["selection"]
        for task in context.get("tasks", []):
            key = task_key(info["connection"], context, task["tab_id"])
            origin = (origin_tab == task["tab_id"] and origin_socket == info["connection"]
                      and context["endpoint"] == "local")
            selected = task["tab_id"] == context.get("focused_tab_id")
            if not origin and key not in owners and not selected:
                continue
            score = (bool(origin), key in owners, selected, info.get("focused_at", 0))
            choices.append((score, info, task, key))
    if not choices:
        return None
    _, info, task, key = max(choices, key=lambda row: row[0])
    return info, task, key


def deliver(url, title="", section=None, focus=False, background=False):
    url = checked_url(url)
    if firefox_required(url):
        return False
    if section is not None:
        section = checked_section(section)
    store = Store()
    socket = os.environ.get("HERDR_SOCKET_PATH")
    target = choose_target(url, instances(), store.read()["tasks"],
                           os.environ.get("HERDR_TAB_ID"),
                           str(Path(socket).resolve()) if socket else None)
    if target is None:
        return False
    info, task, key = target
    row = store.task(key)
    known = find_page(row, url) or {}
    section = known.get("section") or section or row.get("active_section", "General")
    nonce = uuid.uuid4().hex
    request = {
        "schema": 1, "nonce": nonce, "created_at": time.time(),
        "endpoint": info["selection"]["endpoint"], "boot_id": info["selection"]["boot_id"],
        "tab_id": task["tab_id"], "key": key, "url": url,
        "title": title or known.get("title", ""), "section": section,
        "focus": bool(focus), "background": bool(background),
    }
    atomic_json(Path(info["inbox"]) / f"{time.time_ns()}-{nonce}.json", request)
    return True


def open_external(url):
    """Crash/off fallback uses the captured desktop app, never our own handler."""
    url = checked_url(url)
    if firefox_required(url):
        open_firefox(url)
        return
    home = browser_home()
    state_path = home / "routing.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    scheme = "x-scheme-handler/" + urlsplit(url).scheme
    current = desktop_default(scheme)
    application = state.get("previous", {}).get(scheme) if current == HANDLER else current
    if not application or application == HANDLER:
        raise ValueError("The previous desktop browser is unavailable. Choose one in desktop settings.")
    roots = [Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))]
    roots.extend(Path(path) for path in os.environ.get(
        "XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":"))
    for root in roots:
        path = root / "applications" / application
        if path.is_file():
            env = dict(os.environ)
            if "herdr-browser" in env.get("BROWSER", ""):
                env.pop("BROWSER")
            subprocess.run(["gio", "launch", str(path), url], env=env,
                           check=True, timeout=15)
            return
    raise ValueError(f"Desktop browser application not found: {application}")


def open_url(url, title="", section=None, focus=False, background=False):
    url = checked_url(url)
    if firefox_required(url):
        if background:
            print("Configured website uses Firefox; background request did not open it.")
            return False
        open_firefox(url)
        print("Configured website opened in Firefox.")
        return False
    if deliver(url, title, section, focus, background):
        print("Website delivered to its Herdr task.")
        return True
    if background:
        print("Herdr Browser is off or closed; background website was not opened.")
        return False
    sync_defaults()
    open_external(url)
    return False
