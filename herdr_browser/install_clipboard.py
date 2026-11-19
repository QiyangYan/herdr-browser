"""Stage/apply only the clipboard repair, preserving other installed changes."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil


METHODS = ("copy_terminal", "paste_terminal", "terminal_keys", "terminal_popup",
           "terminal_clipboard_pressed")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def clipboard_upgrade(installed, source):
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    window = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == "BrowserWindow")
    methods = {node.name: "".join(lines[node.lineno - 1:node.end_lineno]) + "\n"
               for node in window.body if isinstance(node, ast.FunctionDef)
               and node.name in METHODS}
    if set(methods) != set(METHODS):
        raise ValueError("Source does not contain the complete clipboard repair")
    if "    def terminal_keys(" in installed:
        if not all(methods[name].rstrip() in installed for name in METHODS):
            raise ValueError("Installed clipboard implementation differs; preserve it")
        return installed
    start = source.index('        self.terminal.connect("key-press-event", self.terminal_keys)')
    end = source.index('        self.terminal.connect("child-exited", self.child_exited)', start)
    anchor = '        self.terminal.connect("child-exited", self.child_exited)\n'
    method_anchor = "    def browser_keys(self, entry, event):\n"
    if installed.count(anchor) != 1 or installed.count(method_anchor) != 1:
        raise ValueError("Installed app layout changed; cannot apply a narrow repair")
    upgraded = installed.replace(anchor, source[start:end] + anchor, 1)
    upgraded = upgraded.replace(method_anchor,
                                "\n".join(methods[name] for name in METHODS)
                                + method_anchor, 1)
    ast.parse(upgraded)
    return upgraded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-root", type=Path, required=True)
    parser.add_argument("--stage-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    installed_app = args.installed_root / "herdr_browser/app.py"
    before = installed_app.read_bytes()
    if args.expected_sha256 and digest(before) != args.expected_sha256:
        raise ValueError("Installed app changed since verification; stage and test again")
    after = clipboard_upgrade(before.decode(), (source / "app.py").read_text()).encode()
    if args.apply:
        # Keep the original bytes available for a targeted rollback.
        backup = args.stage_root / "app.before.py"
        if not backup.exists() or backup.read_bytes() != before:
            raise ValueError("Verified stage is missing the installed baseline backup")
        staged = args.stage_root / "herdr_browser/app.py"
        if staged.read_bytes() != after:
            raise ValueError("Proposed installation differs from the tested stage")
        temporary = installed_app.with_suffix(".clipboard.tmp")
        temporary.write_bytes(after)
        temporary.chmod(installed_app.stat().st_mode & 0o777)
        temporary.replace(installed_app)
        for name in ("installation.json", "herdr_browser/provenance.json"):
            record_path = args.installed_root / name
            record = json.loads(record_path.read_text())
            record["clipboard_revision"] = "uncommitted verified terminal clipboard repair"
            record["clipboard_summary"] = "docs/herdr_browser_clipboard.html"
            record["launcher_history"].append({
                "version": "before clipboard repair, uncommitted",
                "app_sha256": digest(before),
            })
            record["previous_launcher_app_sha256"] = digest(before)
            record["launcher_app_sha256"] = digest(after)
            record.setdefault("interface_files_sha256", {})["app.py"] = digest(after)
            record_path.write_text(json.dumps(record, indent=2) + "\n")
    else:
        shutil.copytree(args.installed_root / "herdr_browser",
                        args.stage_root / "herdr_browser", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (args.stage_root / "app.before.py").write_bytes(before)
        (args.stage_root / "herdr_browser/app.py").write_bytes(after)
        for name in ("test_app.py", "test_clipboard.py"):
            shutil.copy2(source / name, args.stage_root / "herdr_browser" / name)
    print(json.dumps({"applied": args.apply, "before_sha256": digest(before),
                      "after_sha256": digest(after), "stage_root": str(args.stage_root)},
                     indent=2))


if __name__ == "__main__":
    main()
