import argparse
import json
import os
from pathlib import Path
import sys
import subprocess

from .store import Store, browser_home, checked_url, task_key, url_identity


def report_origin():
    """Resolve the actual originating task, including Browser-off companions."""
    from .router import process_token
    tab = os.environ.get("HERDR_TAB_ID")
    socket = os.environ.get("HERDR_SOCKET_PATH")
    if not tab or not socket:
        raise ValueError("Run task publishing inside its originating Herdr task.")
    connection = str(Path(socket).resolve())
    candidates = []
    for path in (browser_home() / "instances").glob("*.json"):
        try:
            info = json.loads(path.read_text())
            if info["connection"] != connection or info.get("process_token") != process_token(info["pid"]):
                continue
            context = json.loads(Path(info["context"]).read_text())
            if not context.get("available") or context["endpoint"] != "local":
                continue
            actual = next((task for task in context["tasks"] if task["tab_id"] == tab), None)
            if actual:
                candidates.append((info.get("focused_at", 0), info, context, actual))
        except (ValueError, KeyError, OSError):
            continue
    if not candidates:
        raise ValueError("Open a companion on this originating Herdr session first.")
    _, info, context, actual = max(candidates, key=lambda row: row[0])
    store = Store()
    store.register_context(connection, context)
    base = info.get("reports_url")
    if not base:
        from .serve_reports import start_bridge
        base = start_bridge(store.home)
    return store, task_key(connection, context, tab), actual, base


def publish_task(args):
    from .reports import Reports, session_id, task_document
    from .presentation import enhance_report
    from .router import deliver
    store, owner, actual, base = report_origin()
    patch = json.loads(args.file.read_text()) if args.file else {}
    if args.status:
        patch["status"] = args.status
    if getattr(args, "parent", None):
        patch["parent"] = args.parent
    if getattr(args, "section_title", None):
        patch["section_title"] = args.section_title
    reports = Reports(store.home)
    state = reports.update(owner, patch, args.id, args.new, actual["label"])
    main = reports.get(owner, state["parent"]) if state.get("parent") else state
    main_url = f'{base}/task/{owner}/{main["id"]}'
    url = main_url + ("#" + state["id"] if state.get("parent") else "")
    session_url = f'{base}/session/{session_id(store.task(owner))}'
    child_urls = {url_identity(f'{base}/task/{owner}/{child["id"]}')
                  for child in reports.list(owner) if child.get("parent") == main["id"]}
    # Attaching an existing subplan also repairs its saved tab. Preserve the
    # original page ID and address alias; do not leave a second task-home tab.
    for saved in store.task(owner)["links"]:
        if url_identity(saved["url"]) in child_urls:
            store.record_location(owner, saved["section"], saved["id"], main_url, main["title"])
    page = store.save_link(owner, url, main["title"], "Task pages")
    if args.export:
        args.export.parent.mkdir(parents=True, exist_ok=True)
        snapshot = task_document(main, session_url, live=False,
                                 history=reports.related_history(owner, main["id"], limit=100000))
        args.export.write_text(enhance_report(snapshot, main_url, "Main task"))
    # Registration is quiet by default. The live task page updates itself.
    deliver(url, main["title"], "Task pages", focus=args.focus, background=not args.focus)
    print(json.dumps({"id": state["id"], "revision": state["revision"],
                      "url": url, "session_url": session_url, "page_id": page,
                      "status": state["status"]}))


def add_link(url, title, next_action=None, section="General"):
    url = checked_url(url)
    tab_id = os.environ.get("HERDR_TAB_ID")
    socket = os.environ.get("HERDR_SOCKET_PATH")
    if not tab_id or not socket:
        raise ValueError("Run this command inside the Herdr task receiving the page.")
    candidates = {}
    for instance in (browser_home() / "instances").glob("*.json"):
        try:
            info = json.loads(instance.read_text())
            os.kill(info["pid"], 0)
            if info["connection"] != str(Path(socket).resolve()):
                continue
            context = json.loads(Path(info["context"]).read_text())
            if context["endpoint"] != "local" or not context["available"]:
                continue
            if any(task["tab_id"] == tab_id for task in context["tasks"]):
                key = task_key(info["connection"], context, tab_id)
                candidates[key] = context
        except (OSError, KeyError, ValueError):
            continue
    if len(candidates) != 1:
        raise ValueError("Open a herdr-browser window on this session first. "
                         "Remote tasks can save URLs using the browser's Save page button.")
    key, context = next(iter(candidates.items()))
    store = Store()
    store.register_context(str(Path(socket).resolve()), context)
    store.save_link(key, url, title, section)
    if next_action is not None:
        store.set_value(key, "next_action", next_action)
    print(f"Saved page to this Herdr task · {section}.")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="herdr-browser",
        description="Optional Herdr terminal with a task-owned native browser.")
    sub = parser.add_subparsers(dest="command")
    link = sub.add_parser("link", help="save a page to the current Herdr task")
    link.add_argument("url")
    link.add_argument("--title", default="")
    link.add_argument("--next-action")
    link.add_argument("--section", default="General", help="optional subplan section")
    opened = sub.add_parser("open", help="open website URLs in an enabled Herdr browser")
    opened.add_argument("urls", nargs="+")
    opened.add_argument("--title", default="")
    opened.add_argument("--section")
    open_mode = opened.add_mutually_exclusive_group()
    open_mode.add_argument("--focus", action="store_true",
                        help="show the owning task and raise its window (desktop link clicks)")
    open_mode.add_argument("--background", action="store_true",
                        help="add pages without changing the current website")
    external = sub.add_parser("external", help="open external-app links in the desktop browser")
    external.add_argument("urls", nargs="+")
    task = sub.add_parser("task", help="create or update a living page for the originating task")
    task.add_argument("--new", metavar="TITLE", help="start an independent outcome")
    task.add_argument("--id", help="stable outcome ID; omit to continue the current outcome")
    task.add_argument("--parent", help="main outcome ID; publish this work as a section inside it")
    task.add_argument("--section-title", help="reader-facing name of this part of the main task")
    task.add_argument("--file", type=Path, help="JSON update with summary, plan, results and next_action")
    task.add_argument("--status", choices=("planned", "working", "blocked", "done", "archived"))
    task.add_argument("--export", type=Path, help="also write a standalone HTML artifact")
    task.add_argument("--focus", action="store_true", help="explicitly show this task page")
    parser.add_argument("--no-browser", action="store_true",
                        help="start with browser unloaded; terminal uses the full window")
    parser.add_argument("--browser", action="store_true",
                        help="override the saved Browser-off preference for this launch")
    parser.add_argument("--session", help="attach to a named Herdr session")
    parser.add_argument("--restore-tab", help=argparse.SUPPRESS)
    adjacent_client = Path(__file__).resolve().parent.parent / "client/herdr"
    default_client = adjacent_client if adjacent_client.is_file() else (
        Path.home() / ".local/share/herdr-browser/client/herdr")
    parser.add_argument("--client", type=Path,
                        default=Path(os.environ.get(
                            "HERDR_BROWSER_CLIENT", str(default_client))))
    args = parser.parse_args(argv)
    try:
        if args.command == "link":
            add_link(args.url, args.title, args.next_action, args.section)
        elif args.command == "task":
            publish_task(args)
        elif args.command == "external":
            from .router import sync_defaults, open_external
            sync_defaults()
            for url in args.urls:
                open_external(url)
        elif args.command == "open":
            from .router import open_url
            for url in args.urls:
                open_url(url, args.title, args.section, args.focus, args.background)
        else:
            if not args.client.is_file():
                raise ValueError(f"Optional client is missing: {args.client}")
            from .app import run
            return run(args)
    except (ValueError, OSError, ImportError, subprocess.SubprocessError) as error:
        print(f"herdr-browser: {error}", file=sys.stderr)
        return 1
    return 0
