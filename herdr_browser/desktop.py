"""Visible app launcher, separate from the legacy external-link forwarder."""
from pathlib import Path

APP_ID = "dev.herdr.Browser"


def install_app_launcher(launcher, prefix):
    prefix = Path(prefix)
    icon = prefix / "share/icons/hicolor/scalable/apps" / (APP_ID + ".svg")
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_bytes((Path(__file__).parent / "assets/herdr-browser.svg").read_bytes())
    entry = prefix / "share/applications" / (APP_ID + ".desktop")
    entry.parent.mkdir(parents=True, exist_ok=True)
    executable = str(Path(launcher).resolve())
    for original, replacement in [("\\", "\\\\"), ('"', '\\"'), ("`", "\\`"),
                                  ("$", "\\$"), ("%", "%%")]:
        executable = executable.replace(original, replacement)
    entry.write_text(
        "[Desktop Entry]\nType=Application\nName=Herdr Browser\n"
        "Comment=Herdr terminal with its optional task browser\n"
        f'Exec=/usr/bin/env "{executable}" --browser\n'
        f"Icon={APP_ID}\nStartupWMClass={APP_ID}\n"
        "Terminal=false\nStartupNotify=true\n"
        "Categories=Development;\nKeywords=Herdr;Terminal;Tasks;Browser;\n")
    return entry
