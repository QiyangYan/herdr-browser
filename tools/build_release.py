"""Create a deterministic v0 bundle from public source and the verified client."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "v0"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(client, output):
    provenance = json.loads((ROOT / "herdr_browser/provenance.json").read_text())
    if digest(client) != provenance["client_sha256"]:
        raise ValueError("v0 requires the recorded current client; its SHA-256 differs")
    output.mkdir(parents=True, exist_ok=True)
    name = f"herdr-browser-{VERSION}"
    with tempfile.TemporaryDirectory() as temp:
        bundle = Path(temp) / name
        shutil.copytree(ROOT / "herdr_browser", bundle / "herdr_browser",
                        ignore=shutil.ignore_patterns("test_*", "__pycache__", "*.pyc"))
        shutil.copytree(ROOT / "docs", bundle / "docs")
        shutil.copytree(ROOT / "patches", bundle / "patches")
        shutil.copy2(ROOT / "docs/usage.md", bundle / "herdr_browser/README.md")
        (bundle / "client").mkdir()
        shutil.copy2(client, bundle / "client/herdr")
        (bundle / "client/herdr").chmod(0o755)
        for filename in ("LICENSE", "README.md", "NOTICE", "DEVELOPMENT.md"):
            shutil.copy2(ROOT / filename, bundle / filename)
        shutil.copy2(ROOT / "tools/install_bundle.py", bundle / "install_bundle.py")
        hashes = {str(p.relative_to(bundle)): digest(p)
                  for p in sorted(bundle.rglob("*")) if p.is_file()}
        (bundle / "payload-sha256.json").write_text(json.dumps(hashes, indent=2) + "\n")
        asset = output / f"{name}-linux-x86_64.tar.gz"
        with asset.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as archive:
                for path in sorted(bundle.rglob("*")):
                    if not path.is_file():
                        continue
                    info = archive.gettarinfo(str(path), str(path.relative_to(bundle.parent)))
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ""
                    info.mode = 0o755 if path == bundle / "client/herdr" else 0o644
                    with path.open("rb") as stream:
                        archive.addfile(info, stream)
    shutil.copy2(ROOT / "install.sh", output / "install.sh")
    assets = [asset, output / "install.sh"]
    (output / "SHA256SUMS").write_text(
        "".join(f"{digest(p)}  {p.name}\n" for p in assets))
    print(asset)
    print((output / "SHA256SUMS").read_text(), end="")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    build(args.client.resolve(), args.output.resolve())
