#!/usr/bin/env bash
set -Eeuo pipefail

# Prepare the validated v6085 build environment, but always package the
# checked-out Git commit rather than the source ZIP shipped in the buildkit.
# The buildkit is a pinned dependency/toolchain source, never a build output.

# Actions container workspaces may be owned by the runner's host UID rather
# than the container user. Trust only this checkout's exact workspace path.
: "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is required}"
git config --global --add safe.directory "$GITHUB_WORKSPACE"
SOURCE_ROOT="$(git rev-parse --show-toplevel)"
BUILD_ROOT="$SOURCE_ROOT/.ci-build"
BUILDKIT_ARCHIVE="$BUILD_ROOT/Web_Broadcaster_v6085_buildkit.zip"
BUILDKIT_DIR="$BUILD_ROOT/Web_Broadcaster_v6085_buildkit"
BUILDER="$BUILDKIT_DIR/build_v6085_linux.sh"
SOURCE_ARCHIVE="$BUILDKIT_DIR/Web_Broadcaster_v6085.zip"
CHECKSUMS="$BUILDKIT_DIR/SHA256SUMS.txt"

RELEASE_URL="https://github.com/peppi001/web_broadcaster/releases/download/v6085/Web_Broadcaster_v6085_buildkit.zip"
RELEASE_SHA256="f056e2828bc327f68e39d64a311a26ed51de636b7eb0cc959f83eed9fecbe404"

command -v curl >/dev/null
command -v git >/dev/null
command -v unzip >/dev/null
command -v python3 >/dev/null
command -v sha256sum >/dev/null
test -n "$GITHUB_ENV"

echo "Preparing pinned toolchain from Web Broadcaster v6085 release..."
mkdir -p "$BUILD_ROOT"
curl --fail --location --show-error --silent --retry 4 \
    --output "$BUILDKIT_ARCHIVE" "$RELEASE_URL"
printf '%s  %s\n' "$RELEASE_SHA256" "$BUILDKIT_ARCHIVE" | sha256sum --check --status
unzip -q "$BUILDKIT_ARCHIVE" -d "$BUILD_ROOT"

test -f "$BUILDER"
test -f "$CHECKSUMS"
echo "Pinned buildkit checksum verified."

echo "Archiving exact checked-out Git HEAD (not the buildkit source)..."
git -C "$SOURCE_ROOT" diff --quiet HEAD --
git -C "$SOURCE_ROOT" archive --format=zip --output="$SOURCE_ARCHIVE" HEAD

python3 - "$SOURCE_ARCHIVE" "$BUILDER" "$CHECKSUMS" "$GITHUB_ENV" <<'PY'
from pathlib import Path
import hashlib
import re
import sys
import zipfile

source_archive, builder_path, checksums_path, github_env = map(Path, sys.argv[1:5])

# The repository still tracks retired SVGs, old build objects and a
# v6042-only regression test. They were deliberately omitted from the
# validated v6085 source ZIP and must not be included in release builds.
# Everything else comes from the exact checked-out Git commit.
def is_retired_build_input(name: str) -> bool:
    return (
        name == ".github/"
        or name.startswith(".github/")
        or name == "html/static/icons/"
        or name.startswith("html/static/icons/")
        or (name.startswith("native_engine/src/") and name.endswith(".o"))
        or name == "tests/test_v6042_deployment_modes_docs.py"
    )

filtered_archive = source_archive.with_name("source-filtered.zip")
skipped = []
with zipfile.ZipFile(source_archive, "r") as source:
    with zipfile.ZipFile(filtered_archive, "w") as target:
        for member in source.infolist():
            if is_retired_build_input(member.filename):
                skipped.append(member.filename)
                continue
            target.writestr(member, source.read(member.filename))
filtered_archive.replace(source_archive)
print(f"Excluded {len(skipped)} retired/CI-only paths from release input.")
if skipped:
    print("  " + "\n  ".join(skipped))

with zipfile.ZipFile(source_archive) as source:
    app_text = source.read("app.py").decode("utf-8")
    engine_text = source.read("native_engine/include/engine.h").decode("utf-8")

match = re.search(r'^APP_VERSION\s*=\s*["\'](\d+)["\']', app_text, re.MULTILINE)
if match is None:
    raise SystemExit("APP_VERSION must be a numeric literal in app.py.")
version = match.group(1)

daemon_match = re.search(
    r'^#define\s+WB_NATIVE_DAEMON_VERSION\s+"([^"]+)"',
    engine_text,
    re.MULTILINE,
)
if daemon_match is None or daemon_match.group(1) != version:
    raise SystemExit("Native daemon version does not match app.py APP_VERSION.")

builder = builder_path.read_text(encoding="utf-8")
old_pin = "EXPECTED_APP_VERSION=6085"
if builder.count(old_pin) != 1:
    raise SystemExit("Unexpected source-version pin in the validated builder.")
builder_path.write_text(
    builder.replace(old_pin, f"EXPECTED_APP_VERSION={version}", 1),
    encoding="utf-8",
)

modified = {
    source_archive.name: source_archive,
    builder_path.name: builder_path,
}
lines = checksums_path.read_text(encoding="utf-8").splitlines()
found = set()
updated_lines = []
for line in lines:
    checksum, separator, filename = line.partition("  ")
    if not separator:
        raise SystemExit(f"Invalid buildkit checksum line: {line}")
    if filename in modified:
        filename_path = modified[filename]
        actual = hashlib.sha256(filename_path.read_bytes()).hexdigest()
        updated_lines.append(f"{actual}  {filename}")
        found.add(filename)
    else:
        updated_lines.append(line)
if found != set(modified):
    raise SystemExit(f"Missing expected checksum entries: {set(modified) - found}")
checksums_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")

with github_env.open("a", encoding="utf-8") as env:
    env.write(f"APP_VERSION={version}\n")
    env.write(f"BUILDKIT_DIR={builder_path.parent}\n")
print(f"Git HEAD source v{version} prepared for the validated amd64 builder.")
PY

(
    cd "$BUILDKIT_DIR"
    sha256sum --check --status SHA256SUMS.txt
)
echo "All buildkit/toolchain and current-source checksums verified."
