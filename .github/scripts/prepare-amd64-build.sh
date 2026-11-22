#!/usr/bin/env bash
set -Eeuo pipefail

# Stage the checked-out Git sources directly in the builder's isolated src
# directory. No intermediate ZIP or redundant second source copy is needed.

: "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is required}"
: "${GITHUB_ENV:?GITHUB_ENV is required}"
git config --global --add safe.directory "$GITHUB_WORKSPACE"
SOURCE_ROOT="$(git rev-parse --show-toplevel)"
BUILD_ROOT="$SOURCE_ROOT/.ci-build"
BUILDKIT_SOURCE="$SOURCE_ROOT/buildkit"
BUILDKIT_DIR="$BUILD_ROOT/buildkit"
SOURCE_DIR="$BUILDKIT_DIR/build_work/src"
CHECKSUMS="$BUILDKIT_DIR/SHA256SUMS.txt"

for cmd in git python3 sha256sum; do
    command -v "$cmd" >/dev/null || { echo "Missing command: $cmd" >&2; exit 1; }
done

git -C "$SOURCE_ROOT" diff --quiet HEAD -- || {
    echo "Refusing to build a modified Git worktree." >&2
    exit 1
}
[[ -f "$BUILDKIT_SOURCE/build.sh" && -f "$BUILDKIT_SOURCE/SHA256SUMS.txt" ]] || {
    echo "Version-controlled buildkit is missing." >&2
    exit 1
}

echo "Checking version-controlled FFmpeg, SoundSolution and builder checksums..."
(
    cd "$BUILDKIT_SOURCE"
    sha256sum --check --status SHA256SUMS.txt
)
mkdir -p "$BUILD_ROOT"
rm -rf -- "$BUILDKIT_DIR" "$SOURCE_DIR"
cp -a -- "$BUILDKIT_SOURCE" "$BUILDKIT_DIR"

python3 - "$SOURCE_ROOT" "$SOURCE_DIR" "$GITHUB_ENV" <<'PY'
from pathlib import Path
import re
import shutil
import subprocess
import sys

repo_root, source_dir, github_env = map(Path, sys.argv[1:4])
repo_root = repo_root.resolve()
source_dir.mkdir(parents=True, exist_ok=False)

# Exclude CI inputs, tracked old C objects and known retired assets.
# The actual application files are always taken from the checked-out Git HEAD.
def exclude(path: str) -> bool:
    return (
        path.startswith(".github/")
        or path.startswith("buildkit/")
        or path.startswith("html/static/icons/")
        or (path.startswith("native_engine/src/") and path.endswith(".o"))
        or path == "tests/test_v6042_deployment_modes_docs.py"
    )

raw_paths = subprocess.check_output(
    ["git", "-C", str(repo_root), "ls-files", "--cached", "-z"]
)
paths = [name for name in raw_paths.split(b"\0") if name]
copied = 0
skipped = 0
for raw_path in paths:
    path = raw_path.decode("utf-8", errors="surrogateescape")
    if exclude(path):
        skipped += 1
        continue
    src = repo_root / path
    dst = source_dir / path
    if src.is_symlink() or not src.is_file():
        raise SystemExit(f"Invalid Git source file (symlink or missing): {path}")
    if not src.resolve().is_relative_to(repo_root):
        raise SystemExit(f"Git file resolves outside checkout: {path}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    copied += 1

app_text = (source_dir / "app.py").read_text(encoding="utf-8")
header_text = (source_dir / "native_engine/include/engine.h").read_text(encoding="utf-8")
app_match = re.search(r'^APP_VERSION\s*=\s*["\'](\d+)["\']', app_text, re.MULTILINE)
if not app_match:
    raise SystemExit("APP_VERSION is missing or nonnumeric in app.py")
version = app_match.group(1)
if header_text.count('#define WB_NATIVE_DAEMON_VERSION "APP_VERSION_FROM_APP_PY"') != 1:
    raise SystemExit("Native daemon header must use the generated-version placeholder")
if copied < 1:
    raise SystemExit("No application files were staged")
commit = subprocess.check_output(
    ["git", "-C", str(repo_root), "rev-parse", "HEAD"], text=True
).strip()
with github_env.open("a", encoding="utf-8") as env:
    env.write(f"APP_VERSION={version}\n")
    env.write(f"BUILDKIT_DIR={source_dir.parents[1]}\n")
    env.write(f"SOURCE_DIR={source_dir}\n")
print(f"Staged {copied} tracked source files directly in build_work/src; excluded {skipped} CI/retired files.")
print(f"Web Broadcaster v{version} from Git commit {commit}; no source ZIP created.")
PY

(
    cd "$BUILDKIT_DIR"
    sha256sum --check --status SHA256SUMS.txt
)
echo "Git source and pinned build dependencies prepared successfully."
