"""Exercise a built release in a disposable Ubuntu user/container."""
import argparse
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ASSET = "herdr-browser-v0-linux-x86_64.tar.gz"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(release):
    with tempfile.TemporaryDirectory(prefix="herdr-installer-check-") as temp:
        work = Path(temp)
        prefix = work / "prefix with spaces"
        command = ["bash", str(ROOT / "install.sh"), "--from", str(release),
                   "--prefix", str(prefix), "--skip-deps", "--codex"]
        agents = Path.home() / ".codex/AGENTS.md"
        existing = agents.read_text() if agents.exists() else ""
        subprocess.run(command, check=True)
        first = agents.read_text()
        if existing:
            assert existing.split("<!-- herdr-browser-workflow:begin -->")[0].strip() in first
        launcher = prefix / "bin/herdr-browser"
        client = prefix / "share/herdr-browser/client/herdr"
        subprocess.run([str(launcher), "--help"], check=True, stdout=subprocess.DEVNULL)
        initial_hash = digest(client)
        subprocess.run(command, check=True)
        assert first == agents.read_text(), "Repeated installation duplicated/changed instructions"
        assert list((prefix / "share").glob("herdr-browser.backup-*"))
        assert digest(client) == initial_hash
        for kind in ("corrupt", "traversal"):
            bad = work / kind
            bad.mkdir()
            asset = bad / ASSET
            if kind == "corrupt":
                asset.write_bytes((release / ASSET).read_bytes() + b"corrupt")
                (bad / "SHA256SUMS").write_bytes((release / "SHA256SUMS").read_bytes())
            else:
                with tarfile.open(asset, "w:gz") as archive:
                    item = tarfile.TarInfo("herdr-browser-v0/../../escaped")
                    item.size = 6
                    archive.addfile(item, io.BytesIO(b"unsafe"))
                (bad / "SHA256SUMS").write_text(digest(asset) + "  " + ASSET + "\n")
            trial = subprocess.run(["bash", str(ROOT / "install.sh"), "--from", str(bad),
                                    "--prefix", str(prefix), "--skip-deps"],
                                   capture_output=True, text=True)
            assert trial.returncode != 0, f"{kind} archive was accepted"
            expected = "checksum failed" if kind == "corrupt" else "Unsafe release archive"
            assert expected in trial.stderr, trial.stderr[-500:]
            assert digest(client) == initial_hash
            assert first == agents.read_text()
        print("PASS: install, executable path with spaces, repeat install, Codex merge, "
              "backup, corrupt download and traversal rejection.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("release", type=Path)
    main(parser.parse_args().release.resolve())
