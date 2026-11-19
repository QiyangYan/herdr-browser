#!/usr/bin/env bash
# Install the public v0 snapshot. Inspect this script before running it.
set -euo pipefail

version=v0
repository=QiyangYan/herdr-browser
prefix="${HOME}/.local"
release_dir=
options=()
usage() {
  cat <<'EOF'
Herdr Browser v0 — Ubuntu 22.04, x86_64
Usage: bash install.sh [--prefix DIRECTORY] [--codex] [--skip-deps] [--from DIRECTORY]
  --codex       Add the browser workflow to ~/.codex/AGENTS.md, preserving other text.
  --skip-deps   Check dependencies without installing missing system packages.
  --from        Install an already downloaded release and SHA256SUMS from a directory.
Run as your normal user. Installing missing system packages can request sudo.
EOF
}
while (($#)); do
  case "$1" in
    --prefix|--from)
      (($# >= 2)) || { echo "Missing argument for $1" >&2; exit 2; }
      if [[ $1 == --prefix ]]; then prefix=$2; else release_dir=$2; fi
      shift 2 ;;
    --codex|--skip-deps) options+=("$1"); shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || {
  echo "v0 supports Ubuntu 22.04 on x86_64 only." >&2; exit 1;
}
[[ -r /etc/os-release ]] || { echo "Cannot identify this OS." >&2; exit 1; }
source /etc/os-release
[[ ${ID:-} == ubuntu && ${VERSION_ID:-} == 22.04 ]] || {
  echo "v0 supports Ubuntu 22.04; this system has not been validated." >&2; exit 1;
}
for program in /usr/bin/python3 sha256sum; do
  command -v "$program" >/dev/null || { echo "Required command missing: $program" >&2; exit 1; }
done
work=$(mktemp -d -t herdr-browser-install.XXXXXXXX)
trap 'rm -rf -- "$work"' EXIT
asset="herdr-browser-${version}-linux-x86_64.tar.gz"
if [[ -n $release_dir ]]; then
  cp -- "$release_dir/$asset" "$release_dir/SHA256SUMS" "$work/"
else
  command -v curl >/dev/null || { echo "Install curl first: sudo apt-get install curl" >&2; exit 1; }
  base="https://github.com/${repository}/releases/download/${version}"
  curl --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
    --retry 3 "$base/$asset" -o "$work/$asset"
  curl --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
    --retry 3 "$base/SHA256SUMS" -o "$work/SHA256SUMS"
fi
# Verify the selected asset only; do not let a checksum file name arbitrary paths.
expected=$(/usr/bin/python3 - "$work/SHA256SUMS" "$asset" <<'PY'
import re, sys
from pathlib import Path
matches = []
for line in Path(sys.argv[1]).read_text().splitlines():
    match = re.fullmatch(r"([0-9a-f]{64})  ([^/]+)", line)
    if match and match[2] == sys.argv[2]:
        matches.append(match[1])
if len(matches) != 1:
    sys.exit("Missing or ambiguous release checksum")
print(matches[0])
PY
)
printf '%s  %s\n' "$expected" "$work/$asset" | sha256sum --check --status || {
  echo "Release checksum failed; installation stopped." >&2; exit 1;
}
/usr/bin/python3 - "$work/$asset" "$work" "herdr-browser-$version" <<'PY'
from pathlib import Path, PurePosixPath
import sys, tarfile
with tarfile.open(sys.argv[1], "r:gz") as archive:
    members = archive.getmembers()
    seen = set()
    for member in members:
        path = PurePosixPath(member.name)
        if (path.is_absolute() or ".." in path.parts or not path.parts
                or path.parts[0] != sys.argv[3] or member.name in seen
                or not (member.isfile() or member.isdir())):
            sys.exit("Unsafe release archive; installation stopped")
        seen.add(member.name)
    archive.extractall(sys.argv[2], members=members)
PY
/usr/bin/python3 "$work/herdr-browser-$version/install_bundle.py" \
  --prefix "$prefix" "${options[@]}"
