"""Install an independent, opt-in launcher without touching ordinary Herdr."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess


def install(client, prefix):
    version = subprocess.check_output([str(client), "--version"], text=True).strip()
    if version != "herdr 0.9.3":
        raise ValueError(f"Expected the patched 0.9.3 client, received {version}")
    if b"HERDR_BROWSER_CONTEXT_PATH" not in client.read_bytes():
        raise ValueError("The client does not contain the optional browser signal patch.")
    root = prefix / "share/herdr-browser"
    root.mkdir(parents=True, exist_ok=True)
    (root / "client").mkdir(exist_ok=True)
    binary = root / "client/herdr"
    shutil.copy2(client, binary)
    binary.chmod(0o755)
    source = Path(__file__).resolve().parent
    destination = root / "herdr_browser"
    shutil.copytree(source, destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    entry = root / "launch.py"
    entry.write_text("from herdr_browser.cli import main\nraise SystemExit(main())\n")
    bin_dir = prefix / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    launcher = bin_dir / "herdr-browser"
    launcher.write_text("#!/bin/sh\nexec /usr/bin/python3 " +
                        shlex.quote(str(entry)) + ' "$@"\n')
    launcher.chmod(0o755)
    # Import lazily so this module remains runnable as a standalone installer.
    if __package__:
        from .router import install_desktop
        from .desktop import install_app_launcher
    else:
        import sys
        sys.path.insert(0, str(source.parent))
        from herdr_browser.router import install_desktop
        from herdr_browser.desktop import install_app_launcher
    install_desktop(launcher, prefix / "share/applications/herdr-browser.desktop")
    install_app_launcher(launcher, prefix)
    provenance = json.loads((source / "provenance.json").read_text())
    provenance["installed_client_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    (root / "installation.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(f"Installed optional launcher: {launcher}")
    print("Ordinary herdr has not been changed. Toggle Browser off or run herdr to opt out.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
    arguments = parser.parse_args()
    install(arguments.client.resolve(), arguments.prefix.resolve())
