"""Install a verified bundle with rollback and no personal-state migration."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import uuid

PACKAGES = ["python3", "python3-gi", "gir1.2-gtk-3.0", "gir1.2-vte-2.91",
            "gir1.2-webkit2-4.0", "xdg-utils", "libglib2.0-bin",
            "ca-certificates", "fonts-dejavu-core"]
BEGIN = "<!-- herdr-browser-workflow:begin -->"
END = "<!-- herdr-browser-workflow:end -->"


def verify(bundle):
    manifest = json.loads((bundle / "payload-sha256.json").read_text())
    for name, expected in manifest.items():
        path = bundle / name
        if (not path.resolve().is_relative_to(bundle.resolve()) or path.is_symlink()
                or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError(f"Bundle integrity check failed: {name}")
    client = bundle / "client/herdr"
    if b"HERDR_BROWSER_CONTEXT_PATH" not in client.read_bytes():
        raise ValueError("The client is missing the browser context patch")
    if subprocess.check_output([str(client), "--version"], text=True).strip() != "herdr 0.9.3":
        raise ValueError("Unexpected Herdr client version")


def dependencies(skip):
    missing = []
    for package in PACKAGES:
        check = subprocess.run(["dpkg-query", "-W", "-f=${Status}", package],
                               capture_output=True, text=True)
        if check.returncode or check.stdout != "install ok installed":
            missing.append(package)
    if missing:
        if skip:
            raise ValueError("Missing system packages: " + " ".join(missing))
        sudo = [] if os.geteuid() == 0 else ["sudo"]
        print("Installing required system packages: " + " ".join(missing), flush=True)
        subprocess.run(sudo + ["apt-get", "update"], check=True)
        subprocess.run(sudo + ["apt-get", "install", "-y"] + missing, check=True)
    subprocess.run(["/usr/bin/python3", "-c",
                    "import gi; gi.require_version('Gtk','3.0'); "
                    "gi.require_version('Vte','2.91'); gi.require_version('WebKit2','4.0'); "
                    "from gi.repository import Gtk,Vte,WebKit2"], check=True)


def write_atomic(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def workflow_text(existing, template):
    block = BEGIN + "\n" + template.strip() + "\n" + END
    if BEGIN in existing or END in existing:
        if existing.count(BEGIN) != 1 or existing.count(END) != 1:
            raise ValueError("Ambiguous Herdr workflow markers in AGENTS.md")
        start, end = existing.index(BEGIN), existing.index(END)
        if end < start:
            raise ValueError("Invalid Herdr workflow markers in AGENTS.md")
        return existing[:start] + block + existing[end + len(END):]
    return existing.rstrip() + ("\n\n" if existing.strip() else "") + block + "\n"


def install(bundle, prefix, codex=False):
    share = prefix / "share"
    share.mkdir(parents=True, exist_ok=True)
    root = share / "herdr-browser"
    if root.is_symlink():
        raise ValueError("Refusing to replace a symlink installation")
    token = uuid.uuid4().hex[:12]
    backup = share / ("herdr-browser.backup-" + token)
    launcher = prefix / "bin/herdr-browser"
    desktop = share / "applications/dev.herdr.Browser.desktop"
    handler = share / "applications/herdr-browser.desktop"
    icon = share / "icons/hicolor/scalable/apps/dev.herdr.Browser.svg"
    targets = [launcher, desktop, handler, icon]
    agents = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "AGENTS.md"
    agent_content = None
    if codex:
        existing = agents.read_text() if agents.exists() else ""
        agent_content = workflow_text(
            existing, (bundle / "herdr_browser/global_browser_preference.md").read_text())
        targets.append(agents)
    # Refuse links rather than accidentally writing through user-managed files.
    if any(path.is_symlink() for path in targets):
        raise ValueError("An installation target is a symlink; choose a different prefix")
    previous = {p: (p.read_bytes(), p.stat().st_mode & 0o777) if p.exists() else None
                for p in targets}
    staged = Path(tempfile.mkdtemp(prefix=".herdr-browser-stage-", dir=share))
    moved_old = activated = False
    try:
        shutil.copytree(bundle / "herdr_browser", staged / "herdr_browser")
        shutil.copytree(bundle / "client", staged / "client")
        for name in ("LICENSE", "NOTICE", "README.md", "DEVELOPMENT.md"):
            shutil.copy2(bundle / name, staged / name)
        for name in ("docs", "patches"):
            shutil.copytree(bundle / name, staged / name)
        (staged / "launch.py").write_text(
            "from herdr_browser.cli import main\nraise SystemExit(main())\n")
        provenance = json.loads((staged / "herdr_browser/provenance.json").read_text())
        provenance["installed_client_sha256"] = hashlib.sha256(
            (staged / "client/herdr").read_bytes()).hexdigest()
        (staged / "installation.json").write_text(json.dumps(provenance, indent=2) + "\n")
        if root.exists():
            os.replace(root, backup)
            moved_old = True
        os.replace(staged, root)
        activated = True
        write_atomic(launcher, ("#!/bin/sh\nexec /usr/bin/python3 " +
                     shlex.quote(str(root / "launch.py")) + ' "$@"\n').encode(), 0o755)
        # The desktop helpers create entries only; they never claim browser defaults.
        import sys
        sys.path.insert(0, str(root))
        from herdr_browser.desktop import install_app_launcher
        from herdr_browser.router import install_desktop
        install_desktop(launcher, handler)
        install_app_launcher(launcher, prefix)
        subprocess.run([str(launcher), "--help"], check=True, stdout=subprocess.DEVNULL)
        if agent_content is not None:
            if agents.exists():
                shutil.copy2(agents, agents.with_name("AGENTS.md.backup-" + token))
            write_atomic(agents, agent_content.encode())
    except BaseException:
        if activated:
            shutil.rmtree(root)
        if moved_old:
            os.replace(backup, root)
        for path, saved in previous.items():
            if saved is None:
                path.unlink(missing_ok=True)
            else:
                write_atomic(path, *saved)
        raise
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    print(f"Installed Herdr Browser v0: {launcher}")
    if moved_old:
        print(f"Previous application preserved: {backup}")
    if codex:
        print(f"Browser workflow added to {agents}; other instructions preserved.")
    else:
        print(f"Optional agent workflow: {root}/herdr_browser/global_browser_preference.md")
    if str(prefix / "bin") not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"Add to your shell profile: export PATH={shlex.quote(str(prefix / 'bin'))}:\"$PATH\"")
    print(f"Launch with: {launcher}")
    print("Each person uses their own agent configuration and sign-ins.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
    parser.add_argument("--skip-deps", action="store_true")
    parser.add_argument("--codex", action="store_true")
    args = parser.parse_args()
    bundle = Path(__file__).resolve().parent
    verify(bundle)
    dependencies(args.skip_deps)
    prefix = args.prefix.expanduser().resolve()
    prefix.mkdir(parents=True, exist_ok=True)
    with (prefix / ".herdr-browser-install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        install(bundle, prefix, args.codex)


if __name__ == "__main__":
    main()
