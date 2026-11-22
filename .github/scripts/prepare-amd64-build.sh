#!/usr/bin/env bash
set -Eeuo pipefail

# Prepare the version-independent build environment and always package the
# checked-out Git commit rather than the source ZIP shipped in the buildkit.
# The buildkit is a pinned dependency/toolchain source, never a build output.

# Actions container workspaces may be owned by the runner's host UID rather
# than the container user. Trust only this checkout's exact workspace path.
: "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is required}"
git config --global --add safe.directory "$GITHUB_WORKSPACE"
SOURCE_ROOT="$(git rev-parse --show-toplevel)"
BUILD_ROOT="$SOURCE_ROOT/.ci-build"
# Build input is checked into this branch. Only generated source/output goes
# to the temporary work directory. No release download is required.
BUILDKIT_SOURCE="$SOURCE_ROOT/buildkit"
BUILDKIT_DIR="$BUILD_ROOT/buildkit"
BUILDER="$BUILDKIT_DIR/build.sh"
SOURCE_ARCHIVE="$BUILDKIT_DIR/Web_Broadcaster_source.zip"
CHECKSUMS="$BUILDKIT_DIR/SHA256SUMS.txt"

command -v git >/dev/null
command -v python3 >/dev/null
command -v sha256sum >/dev/null
test -n "$GITHUB_ENV"

test -f "$BUILDKIT_SOURCE/build.sh" || {
    echo "Required versioned buildkit files are absent from Git." >&2
    exit 1
}
test -f "$BUILDKIT_SOURCE/SHA256SUMS.txt"

echo "Checking all versioned build inputs against their original SHA256 manifest..."
(
    cd "$BUILDKIT_SOURCE"
    # The source ZIP is generated from Git and is not a tracked dependency.
    sha256sum --check --status SHA256SUMS.txt
)

echo "Staging version-controlled FFmpeg, SoundSolution and builder files..."
mkdir -p "$BUILD_ROOT"
cp -a "$BUILDKIT_SOURCE" "$BUILDKIT_DIR"
test -f "$BUILDER"
test -f "$CHECKSUMS"

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
# validated release source ZIP and must not be included in release builds.
# Everything else comes from the exact checked-out Git commit.
def is_retired_build_input(name: str) -> bool:
    return (
        name == ".github/"
        or name.startswith(".github/")
        or name == "buildkit/"
        or name.startswith("buildkit/")
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

# The committed builder and dependency archives already passed Git-pinned
# checksum validation. Add only the generated Git source ZIP to the staging
# manifest so the original builder verifies it before compilation.
manifest = checksums_path.read_text(encoding="utf-8")
if f"  {source_archive.name}" in manifest:
    raise SystemExit("Generated source already exists in the static checksum manifest.")
digest = hashlib.sha256(source_archive.read_bytes()).hexdigest()
checksums_path.write_text(
    manifest.rstrip("\n") + f"\n{digest}  {source_archive.name}\n",
    encoding="utf-8",
)

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

# The release builder stages its own pinned FFmpeg 7.1.5 SDK/runtime and
# verifies the linked libraries and packaged output. Do not replace them with
# system libraries. Debian's ffmpeg/ffprobe are used only by source tests to
# synthesize temporary audio fixtures (the pinned runtime omits lavfi).
echo "Release compilation uses only the pinned buildkit FFmpeg libraries."
