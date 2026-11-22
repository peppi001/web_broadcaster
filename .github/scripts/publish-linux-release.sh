#!/usr/bin/env bash
set -Eeuo pipefail

# Publish only verified amd64 and Raspberry Pi 5 ARM64 builds of the same
# exact main-branch commit. This script does not build or execute the assets.

: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${BUILD_SHA:?BUILD_SHA is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"

for command_name in gh jq git python3 sha256sum tar; do
    command -v "$command_name" >/dev/null || {
        echo "Required publishing command missing: $command_name" >&2
        exit 1
    }
done

checkout_sha="$(git rev-parse HEAD)"
[[ "$checkout_sha" == "$BUILD_SHA" ]] || {
    echo "Checked-out commit differs from the triggering build: $checkout_sha != $BUILD_SHA" >&2
    exit 1
}
main_sha="$(gh api "repos/${GITHUB_REPOSITORY}/git/ref/heads/main" --jq '.object.sha')"
if [[ "$BUILD_SHA" != "$main_sha" ]]; then
    echo "Skipping release: commit $BUILD_SHA is no longer the main branch HEAD ($main_sha)."
    exit 0
fi

version="$(python3 - <<'PY'
from pathlib import Path
import re

app = Path('app.py').read_text(encoding='utf-8')
match = re.search(r'^APP_VERSION\s*=\s*["\'](\d+)["\']\s*$', app, re.MULTILINE)
if not match:
    raise SystemExit('Invalid or missing APP_VERSION in app.py')
version = match.group(1)
history = Path('version.txt').read_text(encoding='utf-8')
entries = re.findall(r'^v(\d+)\b', history, re.MULTILINE)
if not entries or entries[-1] != version:
    raise SystemExit('Latest version.txt entry does not match APP_VERSION')
print(version)
PY
)"
[[ "$version" =~ ^[0-9]+$ ]] || { echo "Invalid application version" >&2; exit 1; }
tag="v${version}"

temporary="$(mktemp -d)"
trap 'rm -rf -- "$temporary"' EXIT

# Compare workflow runs by head SHA and branch; do not mix packages produced
# from different commits or publish an incomplete release.
gh api "repos/${GITHUB_REPOSITORY}/actions/runs?head_sha=${BUILD_SHA}&branch=main&per_page=100" \
    > "$temporary/runs.json"

run_id_for() {
    local workflow_name="$1"
    jq -r --arg name "$workflow_name" --arg sha "$BUILD_SHA" '
        [ .workflow_runs[]
          | select(.name == $name and .head_sha == $sha and .head_branch == "main"
                   and .event == "push" and .status == "completed"
                   and .conclusion == "success")
        ] | .[0].id // empty
    ' "$temporary/runs.json"
}

amd_run="$(run_id_for 'Build Web Broadcaster (Linux amd64)')"
arm_run="$(run_id_for 'Build Web Broadcaster (Raspberry Pi 5 ARM64)')"
if [[ -z "$amd_run" || -z "$arm_run" ]]; then
    echo "Release $tag is not ready: both architecture workflows must pass for $BUILD_SHA."
    echo "amd64 run: ${amd_run:-missing}; Raspberry Pi 5 run: ${arm_run:-missing}"
    exit 0
fi

echo "Both architecture workflows passed for commit $BUILD_SHA (amd64 $amd_run; ARM64 $arm_run)."

assets=()
for target in amd64 arm64_rpi5; do
    artifact="Web_Broadcaster_Linux_v${version}_${target}"
    case "$target" in
        amd64) run="$amd_run" ;;
        arm64_rpi5) run="$arm_run" ;;
    esac
    directory="$temporary/$target"
    mkdir -p -- "$directory"
    gh run download "$run" --repo "$GITHUB_REPOSITORY" \
        --name "$artifact" --dir "$directory"

    package="$directory/$artifact.tar.gz"
    checksum="$package.sha256"
    [[ -s "$package" && -s "$checksum" ]] || {
        echo "Missing release package or SHA256 checksum for $target" >&2
        exit 1
    }
    (cd "$directory" && sha256sum --check --status "$(basename "$checksum")") || {
        echo "Invalid release checksum for $target" >&2
        exit 1
    }
    tar -tzf "$package" >/dev/null || {
        echo "Invalid release tarball for $target" >&2
        exit 1
    }
    assets+=("$package" "$checksum")
    echo "Verified release assets for $target."
done

# Do not silently repoint existing tags. v6085 is the one historical exception:
# its tag predates the Git-based buildkit integration but its application
# runtime code is otherwise the same as the current validated v6085 sources.
tag_object_sha="$(gh api "repos/${GITHUB_REPOSITORY}/git/ref/tags/${tag}" --jq '.object.sha' 2>/dev/null || true)"
if [[ -n "$tag_object_sha" && "$tag_object_sha" != "$BUILD_SHA" ]]; then
    if [[ "$tag" != 'v6085' ]]; then
        echo "Release tag $tag points to $tag_object_sha, not build commit $BUILD_SHA; refusing to mix versions." >&2
        exit 1
    fi
    echo "Historical v6085 tag predates Git-based builds. Keeping that tag and preserving existing release assets."
    echo "New binary assets were built from commit $BUILD_SHA."
fi

if gh release view "$tag" --repo "$GITHUB_REPOSITORY" >/dev/null 2>&1; then
    mapfile -t existing_assets < <(
        gh release view "$tag" --repo "$GITHUB_REPOSITORY" \
            --json assets --jq '.assets[].name'
    )
    additions=()
    for file in "${assets[@]}"; do
        filename="$(basename "$file")"
        if printf '%s\n' "${existing_assets[@]}" | grep -Fxq -- "$filename"; then
            echo "Preserving already published asset: $filename"
        else
            additions+=("$file")
        fi
    done
    if ((${#additions[@]})); then
        gh release upload "$tag" "${additions[@]}" --repo "$GITHUB_REPOSITORY"
        echo "Uploaded ${#additions[@]} missing assets to existing release $tag."
    else
        echo "All four release assets already exist; no changes needed for $tag."
    fi
else
    echo "Creating complete $tag release from validated main commit $BUILD_SHA."
    gh release create "$tag" "${assets[@]}" \
        --repo "$GITHUB_REPOSITORY" \
        --target "$BUILD_SHA" \
        --title "Web Broadcaster $tag" \
        --generate-notes
fi

echo "Verified Linux Release assets: https://github.com/${GITHUB_REPOSITORY}/releases/tag/${tag}"
