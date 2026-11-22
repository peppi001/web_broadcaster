#!/usr/bin/env bash
set -Eeuo pipefail

# Web Broadcaster current self-contained Linux buildkit.
# Script revision: build_current_linux-r1-scheduler-url-custom-metadata (amd64 + native Raspberry Pi 5).
#
# The buildkit uses a clean source directory prepared from the current Git commit,
# plus both architecture-specific FFmpeg SDK/runtime and SoundSolution source/runtime
# packages. This script automatically selects exactly one native target.
#
# Supported release targets:
#   Debian 12 and Debian 13 x86-64 / amd64, always built inside the bundled Debian 12 Podman baseline
#   Raspberry Pi 5 running a 64-bit aarch64 operating system, built natively
#
# Final application/runtime layout is the unchanged v6023 flat-bin contract:
#   bin/web_broadcaster
#   bin/_internal/
#   bin/web_broadcaster_engine
#   bin/libavformat.so.61
#   bin/libavcodec.so.61
#   bin/libswresample.so.5
#   bin/libavutil.so.59
#   bin/libfdk-aac.so.2
#   bin/libmp3lame.so.0
#   bin/libsoundsolution.so.2
#   bin/ss18.dat
#
# The Raspberry Pi 5 SoundSolution runtime is never rebuilt or modified. The
# library and ss18.dat are copied byte-for-byte and verified before and after.
#
# Usage:
#   chmod +x build.sh
#   ./build.sh /path/to/clean/git-source

SCRIPT_REVISION="web-broadcaster-linux-build-git-r1"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_TREE="${1:-${SCRIPT_DIR}/../source}"
WORK_DIR="${SCRIPT_DIR}/build_work"
VENV_DIR="${WORK_DIR}/venv"
CHECKSUM_MANIFEST="${SCRIPT_DIR}/SHA256SUMS.txt"
CONTAINERFILE_DEBIAN12="${SCRIPT_DIR}/Containerfile.debian12"
PODMAN_IMAGE="localhost/web-broadcaster-builder:debian12"
PODMAN_LABEL_KEY="com.webbroadcaster.builder.containerfile-sha256"

EXPECTED_SOURCE_NATIVE_RPATH='$ORIGIN/../../lib:$ORIGIN/../../bin/soundsolution'
EXPECTED_SOURCE_MAKEFILE_RPATH='$$ORIGIN/../../lib:$$ORIGIN/../../bin/soundsolution'
EXPECTED_FINAL_NATIVE_RPATH='$ORIGIN'
EXPECTED_FINAL_MAKEFILE_RPATH='$$ORIGIN'
EXPECTED_FFMPEG_VERSION="7.1.5"
EXPECTED_FFMPEG_RUNTIME_ID="7.1.5-for-web-broadcaster-r13"
RUN_SOURCE_TESTS="${RUN_SOURCE_TESTS:-1}"

FFMPEG_AMD64_SDK="${SCRIPT_DIR}/ffmpeg_build/wb-ffmpeg-7.1.5-amd64-sdk.tar.gz"
FFMPEG_AMD64_RUNTIME="${SCRIPT_DIR}/ffmpeg_build/wb-ffmpeg-7.1.5-amd64-runtime.tar.gz"
FFMPEG_RPI5_SDK="${SCRIPT_DIR}/ffmpeg_build/wb-ffmpeg-7.1.5-arm64-rpi5-sdk.tar.gz"
FFMPEG_RPI5_RUNTIME="${SCRIPT_DIR}/ffmpeg_build/wb-ffmpeg-7.1.5-arm64-rpi5-runtime.tar.gz"
SS_AMD64_RUNTIME="${SCRIPT_DIR}/soundsolution_build/wb-soundsolution-6.1.0-amd64-runtime.tar.gz"
SS_RPI5_RUNTIME="${SCRIPT_DIR}/soundsolution_build/wb-soundsolution-6.0.0-arm64-rpi5-runtime.tar.gz"

REQUIRED_FFMPEG_LIBS=(
    "libavformat.so.61"
    "libavcodec.so.61"
    "libswresample.so.5"
    "libavutil.so.59"
    "libfdk-aac.so.2"
    "libmp3lame.so.0"
)

SOURCE_SOUNDSOLUTION_ENTRIES=(
    "libsoundsolution.so.2"
    "ss18.dat"
)

error() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

on_error() {
    local exit_code=$?
    printf 'ERROR: Build failed at line %s (exit %s).\n' "${BASH_LINENO[0]}" "${exit_code}" >&2
    exit "${exit_code}"
}
trap on_error ERR

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || error "Required command not found: $1"
}

run_amd64_in_debian12_container() {
    local containerfile_sha current_image_sha source_host

    require_cmd podman
    require_cmd sha256sum
    require_cmd readlink

    [[ -f "${CONTAINERFILE_DEBIAN12}" ]] \
        || error "Debian 12 Containerfile not found: ${CONTAINERFILE_DEBIAN12}"
    [[ -f "${CHECKSUM_MANIFEST}" ]] \
        || error "Buildkit checksum manifest not found: ${CHECKSUM_MANIFEST}"

    printf '%s\n' ">>> Verifying buildkit integrity before Debian 12 container launch..."
    (
        cd "${SCRIPT_DIR}"
        sha256sum -c "$(basename -- "${CHECKSUM_MANIFEST}")"
    ) || error "Buildkit SHA-256 verification failed."

    if [[ "${SOURCE_TREE}" = /* ]]; then
        source_host="$(readlink -f -- "${SOURCE_TREE}")"
    else
        source_host="$(readlink -f -- "${SCRIPT_DIR}/${SOURCE_TREE}")"
    fi
    [[ -d "${source_host}" ]] || error "Source directory not found: ${source_host}"

    containerfile_sha="$(sha256sum "${CONTAINERFILE_DEBIAN12}" | awk '{print $1}')"
    current_image_sha="$(podman image inspect \
        --format '{{ index .Labels "com.webbroadcaster.builder.containerfile-sha256" }}' \
        "${PODMAN_IMAGE}" 2>/dev/null || true)"

    if [[ "${current_image_sha}" != "${containerfile_sha}" ]]; then
        printf '%s\n' ">>> Preparing Debian 12 amd64 release builder image..."
        podman build \
            --label "${PODMAN_LABEL_KEY}=${containerfile_sha}" \
            --tag "${PODMAN_IMAGE}" \
            --file "${CONTAINERFILE_DEBIAN12}" \
            "${SCRIPT_DIR}"
    else
        printf '%s\n' ">>> Reusing validated Debian 12 amd64 release builder image."
    fi

    printf '%s\n' ">>> Entering Debian 12 amd64 release build environment..."
    exec podman run --rm \
        --userns=keep-id \
        -e HOME=/tmp \
        -e WB_DEBIAN12_CONTAINER=1 \
        -e RUN_SOURCE_TESTS="${RUN_SOURCE_TESTS}" \
        -v "${SCRIPT_DIR}:/work:rw" \
        -v "${source_host}:/input/source:ro" \
        -w /work \
        "${PODMAN_IMAGE}" \
        ./build.sh /input/source
}

extract_single_top_directory() {
    local archive="$1"
    local destination="$2"
    local only_entry
    rm -rf -- "${destination}"
    mkdir -p -- "${destination}"
    tar -xzf "${archive}" -C "${destination}"
    mapfile -t entries < <(find "${destination}" -mindepth 1 -maxdepth 1 -print)
    [[ "${#entries[@]}" -eq 1 && -d "${entries[0]}" ]] \
        || error "Archive must contain exactly one top-level directory: ${archive}"
    only_entry="${entries[0]}"
    printf '%s\n' "${only_entry}"
}

copy_resolved_regular_file() {
    local source="$1"
    local destination="$2"
    local resolved
    resolved="$(readlink -f -- "${source}")"
    [[ -f "${resolved}" ]] || error "Required source file not found: ${source}"
    install -m 0644 "${resolved}" "${destination}"
    [[ -f "${destination}" && ! -L "${destination}" ]] \
        || error "Failed to materialize regular file: ${destination}"
}

verify_elf_machine() {
    local path="$1"
    local description
    description="$(file -b -- "${path}")"
    case "${TARGET_TAG}" in
        amd64) [[ "${description}" == *"x86-64"* ]] || error "Not an x86-64 ELF file: ${path}: ${description}" ;;
        arm64_rpi5) [[ "${description}" == *"ARM aarch64"* || "${description}" == *"aarch64"* ]] \
            || error "Not an AArch64 ELF file: ${path}: ${description}" ;;
    esac
}

[[ $# -le 1 ]] || error "Usage: $0 [clean-source-directory]"
[[ "${RUN_SOURCE_TESTS}" == "0" || "${RUN_SOURCE_TESTS}" == "1" ]] \
    || error "RUN_SOURCE_TESTS must be 0 or 1."
[[ "$(uname -s)" == "Linux" ]] || error "This build script must run on Linux."
HOST_MACHINE="$(uname -m)"

if [[ "${HOST_MACHINE}" == "x86_64" || "${HOST_MACHINE}" == "amd64" ]]; then
    if [[ "${WB_DEBIAN12_CONTAINER:-0}" != "1" ]]; then
        run_amd64_in_debian12_container
    fi

    [[ -r /etc/os-release ]] || error "Cannot verify Debian 12 build baseline: /etc/os-release is missing."
    # shellcheck disable=SC1091
    . /etc/os-release
    [[ "${ID:-}" == "debian" && "${VERSION_ID:-}" == 12* ]] \
        || error "amd64 release builds must run inside the Debian 12 Podman baseline; detected ${PRETTY_NAME:-unknown OS}."
    printf '>>> Verified amd64 release baseline: %s\n' "${PRETTY_NAME:-Debian 12}"
fi

printf '%s\n' ">>> ${SCRIPT_REVISION}"
printf '%s\n' ">>> Checking build requirements..."
for command_name in python3 tar make cc readelf ldd sha256sum find sort install file; do
    require_cmd "${command_name}"
done

PYTHON_MAJOR_MINOR="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
python3 - "${PYTHON_MAJOR_MINOR}" <<'PY_SHARED_PYTHON'
import ctypes.util
import sys

version = sys.argv[1]
library = ctypes.util.find_library(f"python{version}")
if not library:
    raise SystemExit(
        f"Python shared library for {version} was not found. "
        f"On Debian install it with: sudo apt install libpython{version}"
    )
print(f"OK: Python shared library {library}")
PY_SHARED_PYTHON

if [[ "${HOST_MACHINE}" == "x86_64" || "${HOST_MACHINE}" == "amd64" ]]; then
    [[ "${PYTHON_MAJOR_MINOR}" == "3.11" ]] \
        || error "Debian 12 amd64 release baseline must use Python 3.11; detected ${PYTHON_MAJOR_MINOR}."
fi

if [[ "${RUN_SOURCE_TESTS}" == "1" ]]; then
    require_cmd ffmpeg
    require_cmd ffprobe
    printf '%s\n' ">>> Build-environment FFmpeg/FFprobe found; full native audio regressions will run."
elif [[ "${RUN_SOURCE_TESTS}" != "0" ]]; then
    error "RUN_SOURCE_TESTS must be 0 or 1."
fi

MACHINE="${HOST_MACHINE}"
case "${MACHINE}" in
    x86_64|amd64)
        TARGET_TAG="amd64"
        TARGET_DESCRIPTION="Debian 12 userspace baseline / generic x86-64 / SSE2"
        BUILD_BASELINE_SUMMARY="Debian 12 (bookworm), Python 3.11, glibc ABI <= 2.36"
        TARGET_CFLAGS="-O2 -std=c11 -Wall -Wextra -Wpedantic -pthread"
        FFMPEG_SDK_ARCHIVE="${FFMPEG_AMD64_SDK}"
        FFMPEG_RUNTIME_ARCHIVE="${FFMPEG_AMD64_RUNTIME}"
        SOUNDSOLUTION_RUNTIME_ARCHIVE="${SS_AMD64_RUNTIME}"
        EXPECTED_SOUNDSOLUTION_VERSION="6.1.0"
        EXPECTED_SOUNDSOLUTION_ARITHMETIC="native-binary64-fast"
        EXPECTED_SOUNDSOLUTION_SHA256="1ef8a4bf2da84ef0c06ec4741993a894e713d15dbedda2709a6608f83e582cec"
        EXPECTED_FFMPEG_SHA256="454a072ca09a96f13ad6e1aa445e84b09e38c49358d5a12b430988624a880a3f"
        EXPECTED_ELF_MACHINE="Advanced Micro Devices X86-64"
        ;;
    aarch64|arm64)
        MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
        [[ "${MODEL}" == *"Raspberry Pi 5"* ]] \
            || error "ARM64 builds are restricted to a real Raspberry Pi 5; detected model: ${MODEL:-unknown}"
        TARGET_TAG="arm64_rpi5"
        TARGET_DESCRIPTION="Raspberry Pi 5 / Cortex-A76 / AArch64"
        BUILD_BASELINE_SUMMARY="Native Raspberry Pi 5 AArch64 build"
        TARGET_CFLAGS="-O3 -std=c11 -Wall -Wextra -Wpedantic -pthread -mcpu=cortex-a76 -mtune=cortex-a76"
        FFMPEG_SDK_ARCHIVE="${FFMPEG_RPI5_SDK}"
        FFMPEG_RUNTIME_ARCHIVE="${FFMPEG_RPI5_RUNTIME}"
        SOUNDSOLUTION_RUNTIME_ARCHIVE="${SS_RPI5_RUNTIME}"
        EXPECTED_SOUNDSOLUTION_VERSION="6.0.0"
        EXPECTED_SOUNDSOLUTION_ARITHMETIC="native-double"
        EXPECTED_SOUNDSOLUTION_SHA256="1c3bd4751dac868279ddf2c79ee2c9edf686e40df37094103538d60d11d0c3ab"
        EXPECTED_FFMPEG_SHA256="da09a63dc573c9750ee1ddb47d4c4b76fe40806b70c26d5ee33ab228b6a487d3"
        EXPECTED_ELF_MACHINE="AArch64"
        ;;
    *)
        error "Unsupported architecture: ${MACHINE}"
        ;;
esac

printf '>>> Native target: %s (%s)\n' "${TARGET_TAG}" "${TARGET_DESCRIPTION}"
[[ -f "${CHECKSUM_MANIFEST}" ]] || error "Buildkit checksum manifest not found: ${CHECKSUM_MANIFEST}"
(
    cd "${SCRIPT_DIR}"
    sha256sum -c "$(basename -- "${CHECKSUM_MANIFEST}")"
) || error "Buildkit SHA-256 verification failed."

if [[ "${SOURCE_TREE}" = /* ]]; then
    SOURCE_TREE_PATH="$(readlink -f -- "${SOURCE_TREE}")"
else
    SOURCE_TREE_PATH="$(readlink -f -- "${SCRIPT_DIR}/${SOURCE_TREE}")"
fi
[[ -d "${SOURCE_TREE_PATH}" ]] || error "Clean source directory not found: ${SOURCE_TREE_PATH}"
[[ -f "${SOURCE_TREE_PATH}/app.py" ]] || error "app.py not found in source directory."
case "${SOURCE_TREE_PATH}" in
    "${WORK_DIR}"|"${WORK_DIR}"/*)
        error "Source directory must not be inside the generated build_work directory."
        ;;
esac

if [[ "${SOURCE_TREE_PATH}" == "${WORK_DIR}/src" ]]; then
    # GitHub Actions has already staged its tracked files here. Preserve src
    # while removing any leftover build outputs from previous invocations.
    printf '%s\n' ">>> Using pre-staged Git source directly (no second copy)..."
    find "${WORK_DIR}" -mindepth 1 -maxdepth 1 ! -name src -exec rm -rf -- {} +
    SOURCE_ROOT="${SOURCE_TREE_PATH}"
else
    # For standalone/local builds, never modify the caller's original sources.
    printf '%s\n' ">>> Cleaning previous build..."
    rm -rf -- "${WORK_DIR}"
    mkdir -p -- "${WORK_DIR}/src"
    printf '%s\n' ">>> Copying provided source to isolated build directory..."
    cp -a -- "${SOURCE_TREE_PATH}/." "${WORK_DIR}/src/"
    SOURCE_ROOT="${WORK_DIR}/src"
fi

printf '%s\n' ">>> Verifying the architecture-neutral current source layout..."
[[ -f "${SOURCE_ROOT}/app.py" ]] || error "app.py not found in copied source."
[[ -f "${SOURCE_ROOT}/cheroot_cleanup.py" ]] || error "cheroot_cleanup.py not found in copied source."
[[ -f "${SOURCE_ROOT}/version.txt" ]] || error "version.txt not found."
[[ -f "${SOURCE_ROOT}/audio_engine/factory.py" ]] || error "audio_engine/factory.py not found."
[[ -f "${SOURCE_ROOT}/audio_engine/native_engine.py" ]] || error "audio_engine/native_engine.py not found."
[[ -f "${SOURCE_ROOT}/native_engine/Makefile" ]] || error "native_engine/Makefile not found."
[[ -f "${SOURCE_ROOT}/native_engine/include/engine.h" ]] || error "native_engine/include/engine.h not found."
[[ -d "${SOURCE_ROOT}/native_engine/src" ]] || error "native_engine/src not found."
[[ -d "${SOURCE_ROOT}/html" ]] || error "Consolidated html directory not found."
[[ -d "${SOURCE_ROOT}/html/static" ]] || error "html/static directory not found."
[[ -f "${SOURCE_ROOT}/html/base.html" ]] || error "html/base.html not found."
[[ -f "${SOURCE_ROOT}/html/broadcaster.html" ]] || error "html/broadcaster.html not found."
[[ -f "${SOURCE_ROOT}/html/static/broadcaster.js" ]] || error "html/static/broadcaster.js not found."
[[ -f "${SOURCE_ROOT}/docs/DEPLOYMENT_GUIDE.md" ]] || error "docs/DEPLOYMENT_GUIDE.md not found."
[[ -f "${SOURCE_ROOT}/docs/NGINX_PUBLIC_HTTPS.md" ]] || error "docs/NGINX_PUBLIC_HTTPS.md not found."
[[ -f "${SOURCE_ROOT}/docs/nginx_web_broadcaster.conf.example" ]] || error "docs/nginx_web_broadcaster.conf.example not found."
[[ ! -e "${SOURCE_ROOT}/templates" ]] || error "Legacy top-level templates directory must not exist in current."
[[ ! -e "${SOURCE_ROOT}/static" ]] || error "Legacy top-level static directory must not exist in current."
[[ ! -e "${SOURCE_ROOT}/html/static/icons" ]] || error "Retired html/static/icons directory must not exist in current."
python3 - "${SOURCE_ROOT}/html" <<'PY_WEB_ASSET_AUDIT'
from pathlib import Path
import sys

html_root = Path(sys.argv[1])
svg_files = sorted(str(path.relative_to(html_root.parent)) for path in html_root.rglob("*") if path.is_file() and path.suffix.lower() == ".svg")
if svg_files:
    raise SystemExit("Obsolete SVG web assets remain in current: " + ", ".join(svg_files))

offenders = []
for path in html_root.rglob("*"):
    if not path.is_file() or path.suffix.lower() not in {".html", ".css", ".js"}:
        continue
    text = path.read_text(encoding="utf-8")
    if ".svg" in text.lower() or "icons/" in text.lower():
        offenders.append(str(path.relative_to(html_root.parent)))
if offenders:
    raise SystemExit("Retired SVG/icons references remain in current: " + ", ".join(sorted(offenders)))
print("OK: current web asset cleanup verified (no SVG files, icons directory, or SVG/icons references).")
PY_WEB_ASSET_AUDIT

printf '%s\n' ">>> Verifying current Internet-security source contract..."
python3 - "${SOURCE_ROOT}" <<'PY_SECURITY_AUDIT'
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
app = (root / "app.py").read_text(encoding="utf-8")
base = (root / "html" / "base.html").read_text(encoding="utf-8")
login = (root / "html" / "login.html").read_text(encoding="utf-8")
setup = (root / "html" / "setup.html").read_text(encoding="utf-8")
requirements = (root / "requirements.txt").read_text(encoding="utf-8")
cheroot_cleanup = (root / "cheroot_cleanup.py").read_text(encoding="utf-8")

def require(text: str, haystack: str, description: str) -> None:
    if text not in haystack:
        raise SystemExit(f"Missing current security contract: {description}")

def reject(text: str, haystack: str, description: str) -> None:
    if text in haystack:
        raise SystemExit(f"Forbidden current security contract content: {description}")

reject("change_this_secret_key", app, "hardcoded Flask secret fallback")
reject("app.run(", app, "Flask development server")
require('Path(DB_DIR) / ".session_secret"', app, "persistent private session secret")
require('Path(DB_DIR) / ".setup_token"', app, "one-time initial setup token")
require("secrets.token_urlsafe(48)", app, "cryptographic session-secret generation")
require("secrets.token_urlsafe(32)", app, "cryptographic CSRF/setup token generation")
require("def _internet_security_gate", app, "deny-by-default request security gate")
require('frozenset({"login", "setup", "static"})', app, "explicit public endpoint allow-list")
require('request.headers.get("X-CSRF-Token")', app, "CSRF header validation")
require('SESSION_COOKIE_SECURE=secure_cookie', app, "secure session-cookie policy")
require('SESSION_COOKIE_HTTPONLY=True', app, "HttpOnly session-cookie policy")
require('SESSION_COOKIE_SAMESITE="Lax"', app, "SameSite session-cookie policy")
require('TRUSTED_HOSTS=', app, "Host-header allow-list")
require('MAX_CONTENT_LENGTH=4 * 1024 * 1024', app, "request-size limit")
require('Strict-Transport-Security', app, "HSTS response header")
require('Content-Security-Policy', app, "CSP response header")
require('from cheroot.wsgi import Server as CherootServer', app, "Cheroot production WSGI server")
require('from cheroot_cleanup import configure_cheroot_connection_cleanup', app, "Cheroot cleanup integration import")
require('configure_cheroot_connection_cleanup(server)', app, "Cheroot cleanup installation")
require('server.ConnectionClass = WebBroadcasterCherootHTTPConnection', cheroot_cleanup, "custom Cheroot ConnectionClass")
require('_close_writer_before_socket(getattr(self, "wfile", None))', cheroot_cleanup, "writer-before-socket close order")
require('errno.EBADF', cheroot_cleanup, "observed bad-descriptor disconnect handling")
require('from cheroot.ssl.builtin import BuiltinSSLAdapter', app, "direct TLS adapter")
require('ProxyFix', app, "explicit reverse-proxy support")
require('--trusted-hosts', app, "trusted-host CLI option")
require('--proxy-count', app, "explicit proxy-count CLI option")
require('--https', app, "direct HTTPS CLI option")
require('--public-internet', app, "fail-closed public Internet CLI option")
require('--public-internet refuses plain HTTP', app, "application-level public plain-HTTP refusal")
require('tls_adapter.context.minimum_version = ssl.TLSVersion.TLSv1_2', app, "TLS 1.2 minimum for direct TLS")
require('default_limits=["600 per minute"]', app, "global request-rate ceiling")
require('meta name="csrf-token"', base, "CSRF token meta tag")
require('headers.set("X-CSRF-Token", csrfToken)', base, "same-origin fetch CSRF injection")
require('name="_csrf_token"', login, "login form CSRF token")
require('name="_csrf_token"', setup, "setup form CSRF token")
require('name="setup_token"', setup, "initial setup-token field")
for pin in (
    "flask==3.1.3",
    "werkzeug==3.1.8",
    "Flask-Limiter==4.1.1",
    "cheroot==11.1.2",
):
    require(pin, requirements, f"dependency pin {pin}")

print("OK: current Internet-security source contract verified.")
PY_SECURITY_AUDIT

printf '%s\n' ">>> Verifying current deployment documentation contract..."
python3 - "${SOURCE_ROOT}" <<'PY_NGINX_DOC_AUDIT'
from pathlib import Path
import sys

root = Path(sys.argv[1])
deployment = (root / "docs" / "DEPLOYMENT_GUIDE.md").read_text(encoding="utf-8")
guide = (root / "docs" / "NGINX_PUBLIC_HTTPS.md").read_text(encoding="utf-8")
conf = (root / "docs" / "nginx_web_broadcaster.conf.example").read_text(encoding="utf-8")

for marker in (
    'Mode A — trusted LAN only',
    'PUBLIC_INTERNET_MODE="OFF"',
    'BIND_HOST="0.0.0.0"',
    'TRUSTED_HOSTS=""',
    'HTTPS_MODE="OFF"',
    'PROXY_COUNT="0"',
    'http://192.168.1.50:15000',
    'Do not create an Internet/NAT port-forward for TCP 15000.',
    'Mode B — public Internet through nginx',
    'PUBLIC_INTERNET_MODE="ON"',
    'BIND_HOST="127.0.0.1"',
    'PROXY_COUNT="1"',
    'docs/NGINX_PUBLIC_HTTPS.md',
):
    if marker not in deployment:
        raise SystemExit(f"Missing current deployment-mode guide marker: {marker}")

for marker in (
    'PUBLIC_INTERNET_MODE="ON"',
    'BIND_HOST="127.0.0.1"',
    'TRUSTED_HOSTS="radio.example.com"',
    'HTTPS_MODE="OFF"',
    'PROXY_COUNT="1"',
    'certbot certonly --nginx -d radio.example.com',
    'certbot renew --dry-run',
    'Do not forward TCP 15000.',
):
    if marker not in guide:
        raise SystemExit(f"Missing current nginx deployment guide marker: {marker}")

for marker in (
    'proxy_pass http://127.0.0.1:15000;',
    'proxy_set_header Host $host;',
    'proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;',
    'proxy_set_header X-Forwarded-Host $host;',
    'proxy_set_header X-Forwarded-Proto $scheme;',
    'client_max_body_size 4m;',
    'location = /api/console/stream',
    'location = /api/ui/events',
):
    if marker not in conf:
        raise SystemExit(f"Missing current nginx example marker: {marker}")
if conf.count('proxy_buffering off;') != 2:
    raise SystemExit("Both current SSE locations must explicitly disable nginx response buffering.")
print("OK: current LAN/public deployment documentation contract verified.")
PY_NGINX_DOC_AUDIT

for archive in "${FFMPEG_SDK_ARCHIVE}" "${FFMPEG_RUNTIME_ARCHIVE}" "${SOUNDSOLUTION_RUNTIME_ARCHIVE}"; do
    [[ -f "${archive}" ]] || error "Required target package not found: ${archive}"
done

printf '%s\n' ">>> Staging architecture-matched FFmpeg and SoundSolution inputs..."
FFMPEG_SDK_ROOT="$(extract_single_top_directory "${FFMPEG_SDK_ARCHIVE}" "${WORK_DIR}/ffmpeg-sdk")"
FFMPEG_RUNTIME_ROOT="$(extract_single_top_directory "${FFMPEG_RUNTIME_ARCHIVE}" "${WORK_DIR}/ffmpeg-runtime")"
SOUNDSOLUTION_RUNTIME_ROOT="$(extract_single_top_directory "${SOUNDSOLUTION_RUNTIME_ARCHIVE}" "${WORK_DIR}/soundsolution-runtime")"

rm -rf -- \
    "${SOURCE_ROOT}/native_engine/ffmpeg_sdk" \
    "${SOURCE_ROOT}/native_engine/include/ssnative" \
    "${SOURCE_ROOT}/native_engine/bin" \
    "${SOURCE_ROOT}/bin"
mkdir -p -- \
    "${SOURCE_ROOT}/native_engine/ffmpeg_sdk" \
    "${SOURCE_ROOT}/native_engine/include/ssnative" \
    "${SOURCE_ROOT}/lib" \
    "${SOURCE_ROOT}/bin/soundsolution"

[[ -d "${FFMPEG_SDK_ROOT}/include" ]] || error "FFmpeg SDK include directory is missing."
cp -a "${FFMPEG_SDK_ROOT}/include" "${SOURCE_ROOT}/native_engine/ffmpeg_sdk/include"

copy_resolved_regular_file "${FFMPEG_RUNTIME_ROOT}/bin/ffmpeg" "${SOURCE_ROOT}/bin/ffmpeg"
chmod 0755 "${SOURCE_ROOT}/bin/ffmpeg"
SOURCE_FFMPEG_LIBS=(
    libavformat.so.61
    libavcodec.so.61
    libavfilter.so.10
    libswresample.so.5
    libavutil.so.59
    libfdk-aac.so.2
    libmp3lame.so.0
)
for library in "${SOURCE_FFMPEG_LIBS[@]}"; do
    copy_resolved_regular_file \
        "${FFMPEG_RUNTIME_ROOT}/lib/${library}" \
        "${SOURCE_ROOT}/lib/${library}"
done

copy_resolved_regular_file \
    "${SOUNDSOLUTION_RUNTIME_ROOT}/include/ssnative/ssnative.h" \
    "${SOURCE_ROOT}/native_engine/include/ssnative/ssnative.h"
copy_resolved_regular_file \
    "${SOUNDSOLUTION_RUNTIME_ROOT}/lib/libsoundsolution.so.2" \
    "${SOURCE_ROOT}/bin/soundsolution/libsoundsolution.so.2"
copy_resolved_regular_file \
    "${SOUNDSOLUTION_RUNTIME_ROOT}/share/soundsolution-native/ss18.dat" \
    "${SOURCE_ROOT}/bin/soundsolution/ss18.dat"

SOUNDSOLUTION_DIR="${SOURCE_ROOT}/bin/soundsolution"
ACTUAL_SS_SHA256="$(sha256sum "${SOUNDSOLUTION_DIR}/libsoundsolution.so.2" | awk '{print $1}')"
[[ "${ACTUAL_SS_SHA256}" == "${EXPECTED_SOUNDSOLUTION_SHA256}" ]] \
    || error "Staged SoundSolution binary SHA-256 mismatch: ${ACTUAL_SS_SHA256}"
ACTUAL_SS_DAT_SHA256="$(sha256sum "${SOUNDSOLUTION_DIR}/ss18.dat" | awk '{print $1}')"
[[ "${ACTUAL_SS_DAT_SHA256}" == "a645ef67b888c6a87956420567fe39f09c51e8e540d9f320fe99995559f71b71" ]] \
    || error "Staged SoundSolution ss18.dat SHA-256 mismatch: ${ACTUAL_SS_DAT_SHA256}"
ACTUAL_FFMPEG_SHA256="$(sha256sum "${SOURCE_ROOT}/bin/ffmpeg" | awk '{print $1}')"
[[ "${ACTUAL_FFMPEG_SHA256}" == "${EXPECTED_FFMPEG_SHA256}" ]] \
    || error "Staged ffmpeg SHA-256 mismatch: ${ACTUAL_FFMPEG_SHA256}"

verify_elf_machine "${SOURCE_ROOT}/bin/ffmpeg"
verify_elf_machine "${SOUNDSOLUTION_DIR}/libsoundsolution.so.2"
for library in "${SOURCE_FFMPEG_LIBS[@]}"; do
    verify_elf_machine "${SOURCE_ROOT}/lib/${library}"
done

if find "${SOURCE_ROOT}" -type l -print -quit | grep -q .; then
    error "The staged current source tree must be completely symlink-free."
fi

mapfile -t SOURCE_SS_ENTRIES < <(
    find "${SOUNDSOLUTION_DIR}" -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort
)
mapfile -t EXPECTED_SS_ENTRIES < <(printf '%s\n' "${SOURCE_SOUNDSOLUTION_ENTRIES[@]}" | LC_ALL=C sort)
[[ "${SOURCE_SS_ENTRIES[*]}" == "${EXPECTED_SS_ENTRIES[*]}" ]] \
    || error "bin/soundsolution must contain only: ${EXPECTED_SS_ENTRIES[*]}"

APP_VERSION="$(python3 - "${SOURCE_ROOT}/app.py" <<'PY'
from pathlib import Path
import re
import sys

text = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
if not match:
    raise SystemExit("APP_VERSION was not found in app.py")
print(match.group(1).strip())
PY
)"
[[ "${APP_VERSION}" =~ ^[0-9]+$ ]] || error "APP_VERSION must be numeric, got: ${APP_VERSION}"

# Generate the native daemon version in the isolated source tree only.
# app.py is the single authoritative application version source.
python3 - "${SOURCE_ROOT}/native_engine/include/engine.h" "${APP_VERSION}" <<'PY_NATIVE_VERSION'
from pathlib import Path
import sys

header = Path(sys.argv[1])
version = sys.argv[2]
source = header.read_text(encoding="utf-8")
placeholder = '#define WB_NATIVE_DAEMON_VERSION "APP_VERSION_FROM_APP_PY"'
if source.count(placeholder) != 1:
    raise SystemExit("Native daemon header does not contain the expected build-time version placeholder")
header.write_text(
    source.replace(placeholder, f'#define WB_NATIVE_DAEMON_VERSION "{version}"', 1),
    encoding="utf-8",
)
print(f"Native daemon version generated from app.py: {version}")
PY_NATIVE_VERSION

DAEMON_VERSION="$(python3 - "${SOURCE_ROOT}/native_engine/include/engine.h" <<'PY'
from pathlib import Path
import re
import sys

text = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r'^#define\s+WB_NATIVE_DAEMON_VERSION\s+"([^"]+)"', text, re.MULTILINE)
if not match:
    raise SystemExit("WB_NATIVE_DAEMON_VERSION was not found in engine.h")
print(match.group(1).strip())
PY
)"
[[ "${DAEMON_VERSION}" == "${APP_VERSION}" ]] \
    || error "Version mismatch: app.py=${APP_VERSION}, native daemon=${DAEMON_VERSION}"
grep -Fq "#define WB_BUNDLED_FFMPEG_RUNTIME_ID \"${EXPECTED_FFMPEG_RUNTIME_ID}\"" \
    "${SOURCE_ROOT}/native_engine/include/engine.h" \
    || error "Unexpected bundled FFmpeg runtime identity in engine.h."

MAKEFILE_PATH="${SOURCE_ROOT}/native_engine/Makefile"
grep -Fq -- '-l:libsoundsolution.so.2' "${MAKEFILE_PATH}" \
    || error "Native Makefile does not link libsoundsolution.so.2."
grep -Fq -- "${EXPECTED_SOURCE_MAKEFILE_RPATH}" "${MAKEFILE_PATH}" \
    || error "Native Makefile does not contain the current source-tree private-library RPATH."

OUTPUT_BASE="Web_Broadcaster_Linux_v${APP_VERSION}"
OUTPUT_DIR="${WORK_DIR}/${OUTPUT_BASE}"
OUTPUT_TAR="${SCRIPT_DIR}/${OUTPUT_BASE}_${TARGET_TAG}.tar.gz"
OUTPUT_SHA="${OUTPUT_TAR}.sha256"
rm -f -- "${OUTPUT_TAR}" "${OUTPUT_SHA}"

printf '%s\n' ">>> Preparing v${APP_VERSION} source for PyInstaller onedir layout..."
python3 - "${SOURCE_ROOT}" <<'PYCODE'
from pathlib import Path
import sys

root = Path(sys.argv[1])

# PyInstaller places imported Python modules under _internal. Writable runtime
# data and private native libraries remain beside the top-level executable.
app_file = root / "app.py"
text = app_file.read_text(encoding="utf-8")
old = '    app_root=os.path.dirname(os.path.abspath(__file__)),\n'
new = (
    '    app_root=(os.path.dirname(os.path.dirname(os.path.abspath(sys.executable))) '
    'if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))),\n'
)
if old in text:
    text = text.replace(old, new, 1)
elif new not in text:
    raise SystemExit("Unexpected configure_audio_engine_runtime app_root in app.py")
old_base_dir = '        return os.path.dirname(os.path.abspath(sys.executable))\n'
new_base_dir = '        return os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))\n'
if old_base_dir in text:
    text = text.replace(old_base_dir, new_base_dir, 1)
elif new_base_dir not in text:
    raise SystemExit("Unexpected frozen get_base_dir logic in app.py")
app_file.write_text(text, encoding="utf-8")

factory = root / "audio_engine" / "factory.py"
text = factory.read_text(encoding="utf-8")
if "import sys\n" not in text:
    text = text.replace("import os\n", "import os\nimport sys\n", 1)
old_root = "    app_root = Path(__file__).resolve().parents[1]\n"
new_root = (
    "    app_root = (\n"
    "        Path(sys.executable).resolve().parent.parent\n"
    "        if getattr(sys, \"frozen\", False)\n"
    "        else Path(__file__).resolve().parents[1]\n"
    "    )\n"
)
if old_root in text:
    text = text.replace(old_root, new_root, 1)
elif new_root not in text:
    raise SystemExit("Unexpected app_root logic in audio_engine/factory.py")
old_binary = 'str(app_root / "native_engine" / "bin" / "web_broadcaster_engine")'
new_binary = 'str(app_root / "bin" / "web_broadcaster_engine")'
if old_binary in text:
    text = text.replace(old_binary, new_binary, 1)
elif new_binary not in text:
    raise SystemExit("Unexpected native daemon path in audio_engine/factory.py")
factory.write_text(text, encoding="utf-8")
PYCODE

printf '%s\n' ">>> Rebuilding native C engine with bundled FFmpeg and SoundSolution..."
make -C "${SOURCE_ROOT}/native_engine" clean all CFLAGS="${TARGET_CFLAGS}"
NATIVE_BINARY="${SOURCE_ROOT}/native_engine/bin/web_broadcaster_engine"
[[ -x "${NATIVE_BINARY}" ]] || error "Native engine build did not create an executable."

NATIVE_DYNAMIC="${WORK_DIR}/native-engine-readelf.txt"
readelf -d "${NATIVE_BINARY}" | tee "${NATIVE_DYNAMIC}"
grep -Fq 'Shared library: [libsoundsolution.so.2]' "${NATIVE_DYNAMIC}" \
    || error "Native engine is not linked to libsoundsolution.so.2."
grep -Fq "Library rpath: [${EXPECTED_SOURCE_NATIVE_RPATH}]" "${NATIVE_DYNAMIC}" \
    || error "Source-tree native engine RPATH is not ${EXPECTED_SOURCE_NATIVE_RPATH}."

printf '%s\n' ">>> Running direct SoundSolution library smoke test..."
python3 - \
    "${SOUNDSOLUTION_DIR}/libsoundsolution.so.2" \
    "${SOUNDSOLUTION_DIR}/ss18.dat" \
    "${EXPECTED_SOUNDSOLUTION_VERSION}" \
    "${EXPECTED_SOUNDSOLUTION_ARITHMETIC}" \
    "${TARGET_TAG}" <<'PY'
import ctypes
import math
import struct
import sys

library_path, config_path, expected_version, expected_arithmetic, target_tag = sys.argv[1:6]
library = ctypes.CDLL(library_path)
library.ssnative_version.restype = ctypes.c_char_p
library.ssnative_arithmetic_mode.restype = ctypes.c_char_p
version = library.ssnative_version().decode("utf-8")
arithmetic = library.ssnative_arithmetic_mode().decode("utf-8")
if version != expected_version:
    raise SystemExit(f"unexpected SoundSolution version: {version}, expected {expected_version}")
if arithmetic != expected_arithmetic:
    raise SystemExit(f"unexpected SoundSolution arithmetic mode: {arithmetic}, expected {expected_arithmetic}")
if target_tag == "arm64_rpi5":
    library.ssnative_state_layout.restype = ctypes.c_char_p
    library.ssnative_control_layout.restype = ctypes.c_char_p
    state_layout = library.ssnative_state_layout().decode("utf-8")
    control_layout = library.ssnative_control_layout().decode("utf-8")
    if state_layout != "named-state-v14/structured-process-flow":
        raise SystemExit(f"unexpected Raspberry Pi 5 state layout: {state_layout}")
    if control_layout != "structured-process-v1/typed-state-loop":
        raise SystemExit(f"unexpected Raspberry Pi 5 control layout: {control_layout}")
library.ssnative_create.restype = ctypes.c_void_p
library.ssnative_destroy.argtypes = [ctypes.c_void_p]
library.ssnative_load_dat.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
library.ssnative_load_dat.restype = ctypes.c_int
library.ssnative_process_s16_interleaved.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_int16),
    ctypes.c_size_t,
    ctypes.c_uint32,
    ctypes.c_uint32,
]
library.ssnative_process_s16_interleaved.restype = ctypes.c_int

context = library.ssnative_create()
if not context:
    raise SystemExit("ssnative_create failed")
try:
    if library.ssnative_load_dat(context, config_path.encode()) != 0:
        raise SystemExit("ssnative_load_dat failed")
    frames = 4096
    pcm = bytearray()
    for index in range(frames):
        left = int(12000 * math.sin(2.0 * math.pi * 440.0 * index / 44100.0))
        right = int(9000 * math.sin(2.0 * math.pi * 880.0 * index / 44100.0))
        pcm.extend(struct.pack("<hh", left, right))
    original = bytes(pcm)
    samples = (ctypes.c_int16 * (frames * 2)).from_buffer(pcm)
    result = library.ssnative_process_s16_interleaved(context, samples, frames, 2, 44100)
    if result != 0:
        raise SystemExit(f"ssnative_process_s16_interleaved failed: {result}")
    if bytes(pcm) == original:
        raise SystemExit("SoundSolution smoke test did not alter PCM")
finally:
    library.ssnative_destroy(context)
print(f"OK: SoundSolution {version} {arithmetic} PCM processing")
PY

printf '%s\n' ">>> Creating isolated Python build environment..."
python3 -m venv "${VENV_DIR}" \
    || error "Could not create Python venv. Install python3-venv on the build host."
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
python -m pip install --disable-pip-version-check \
    "pyinstaller==6.21.0" \
    "flask==3.1.3" \
    "werkzeug==3.1.8" \
    "python-dotenv==1.0.1" \
    "Flask-Limiter==4.1.1" \
    "cheroot==11.1.2" \
    "mutagen==1.47.0" \
    "pytest==8.3.5"

printf '%s\n' ">>> Verifying Python dependencies..."
python - <<'PY'
from importlib.metadata import version

for distribution in (
    "pyinstaller",
    "flask",
    "werkzeug",
    "python-dotenv",
    "Flask-Limiter",
    "cheroot",
    "mutagen",
    "pytest",
):
    print(f"OK: {distribution} {version(distribution)}")
PY

if [[ "${RUN_SOURCE_TESTS}" == "1" ]]; then
    if command -v node >/dev/null 2>&1; then
        printf '%s\n' ">>> Node.js found; JavaScript execution regression test will run."
    else
        printf '%s\n' ">>> Node.js not found; optional JavaScript execution test will be skipped."
    fi
    printf '%s\n' ">>> Running complete current source regression suite..."
    REGRESSION_LOG="${WORK_DIR}/source-regression.log"
    printf '%s\n' ">>> Regression progress is shown live and also saved to: ${REGRESSION_LOG}"
    if (
        cd "${SOURCE_ROOT}"
        PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONUNBUFFERED=1 \
            python -u -m pytest -q --tb=short 2>&1 | tee "${REGRESSION_LOG}"
    ); then
        printf '%s\n' ">>> Complete current source regression suite passed."
    else
        regression_rc=$?
        printf '%s\n' "ERROR: Complete current source regression suite failed." >&2
        python3 - "${REGRESSION_LOG}" <<'PY_REGRESSION_SUMMARY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
summary = []
for line in text.splitlines():
    if line.startswith("FAILED ") or line.startswith("ERROR "):
        summary.append(line.split(" - ", 1)[0][:500])
if summary:
    print("Failing regression tests:", file=sys.stderr)
    for line in summary:
        print(f"  {line}", file=sys.stderr)
else:
    print("No pytest failure summary was produced. Last log lines:", file=sys.stderr)
    for line in text.splitlines()[-20:]:
        print(line[:500], file=sys.stderr)
print(f"Full regression log: {path}", file=sys.stderr)
PY_REGRESSION_SUMMARY
        exit "${regression_rc}"
    fi
elif [[ "${RUN_SOURCE_TESTS}" != "0" ]]; then
    error "RUN_SOURCE_TESTS must be 0 or 1."
fi

printf '%s\n' ">>> Preparing flat final bin/ native runtime..."
python3 - "${SOURCE_ROOT}" <<'PY_FLAT_BIN'
from pathlib import Path
import sys

root = Path(sys.argv[1])

# The frozen application reads both the SoundSolution library and the default
# .dat file directly from APP_ROOT/bin/.
app_file = root / "app.py"
text = app_file.read_text(encoding="utf-8")
old_ss_root = 'return (Path(BASE_DIR) / "bin" / "soundsolution" / Path(*parts)).resolve()'
new_ss_root = 'return (Path(BASE_DIR) / "bin" / Path(*parts)).resolve()'
if old_ss_root in text:
    text = text.replace(old_ss_root, new_ss_root, 1)
elif new_ss_root not in text:
    raise SystemExit("Unexpected bundled SoundSolution path in app.py")
app_file.write_text(text, encoding="utf-8")

# The packaged native daemon is directly under APP_ROOT/bin/.
factory = root / "audio_engine" / "factory.py"
text = factory.read_text(encoding="utf-8")
old_binary = 'str(app_root / "bin" / "native_engine" / "web_broadcaster_engine")'
new_binary = 'str(app_root / "bin" / "web_broadcaster_engine")'
if old_binary in text:
    text = text.replace(old_binary, new_binary, 1)
elif new_binary not in text:
    raise SystemExit("Unexpected packaged native daemon path in audio_engine/factory.py")
factory.write_text(text, encoding="utf-8")

# All private ELF files are adjacent to the daemon, therefore one $ORIGIN RPATH
# is sufficient in the final package. Link-time -L paths still point to the
# embedded source-tree dependency directories.
makefile = root / "native_engine" / "Makefile"
text = makefile.read_text(encoding="utf-8")
old_rpath = "-Wl,--disable-new-dtags,-rpath,'$$ORIGIN/../../lib:$$ORIGIN/../../bin/soundsolution'"
new_rpath = "-Wl,--disable-new-dtags,-rpath,'$$ORIGIN'"
if old_rpath in text:
    text = text.replace(old_rpath, new_rpath, 1)
elif new_rpath not in text:
    raise SystemExit("Unexpected native-engine RPATH in Makefile")
makefile.write_text(text, encoding="utf-8")

# Runtime diagnostics must report the actual flat private libavformat path.
bridge = root / "native_engine" / "src" / "libav_bridge.c"
text = bridge.read_text(encoding="utf-8")
old_strip = '''if (!strip_last_path_component(executable)
        || !strip_last_path_component(executable)
        || !strip_last_path_component(executable))'''
new_strip = 'if (!strip_last_path_component(executable))'
if old_strip in text:
    text = text.replace(old_strip, new_strip, 1)
elif new_strip not in text:
    raise SystemExit("Unexpected executable path stripping in libav_bridge.c")
old_report = '"%s/lib/libavformat.so.61"'
new_report = '"%s/libavformat.so.61"'
if old_report in text:
    text = text.replace(old_report, new_report, 1)
elif new_report not in text:
    raise SystemExit("Unexpected linked libavformat report path in libav_bridge.c")
bridge.write_text(text, encoding="utf-8")
PY_FLAT_BIN

grep -Fq -- "${EXPECTED_FINAL_MAKEFILE_RPATH}" "${MAKEFILE_PATH}" \
    || error "Native Makefile was not updated to the flat-bin RPATH."

printf '%s\n' ">>> Rebuilding native C engine for the flat final bin/ layout..."
make -C "${SOURCE_ROOT}/native_engine" clean all CFLAGS="${TARGET_CFLAGS}"
NATIVE_BINARY="${SOURCE_ROOT}/native_engine/bin/web_broadcaster_engine"
[[ -x "${NATIVE_BINARY}" ]] || error "Flat-bin native engine rebuild did not create an executable."

FINAL_NATIVE_DYNAMIC="${WORK_DIR}/native-engine-flat-readelf.txt"
readelf -d "${NATIVE_BINARY}" | tee "${FINAL_NATIVE_DYNAMIC}"
grep -Fq 'Shared library: [libsoundsolution.so.2]' "${FINAL_NATIVE_DYNAMIC}" \
    || error "Flat-bin native engine is not linked to libsoundsolution.so.2."
grep -Fq "Library rpath: [${EXPECTED_FINAL_NATIVE_RPATH}]" "${FINAL_NATIVE_DYNAMIC}" \
    || error "Flat-bin native engine RPATH is not ${EXPECTED_FINAL_NATIVE_RPATH}."

printf '%s\n' ">>> Building Python application with PyInstaller..."
cd "${SOURCE_ROOT}"
PYINSTALLER_ARGS=(
    --noconfirm
    --clean
    --onedir
    --contents-directory _internal
    --name web_broadcaster
    --paths .
    --hidden-import flask_limiter
    --hidden-import limits
    --hidden-import cheroot.wsgi
    --hidden-import cheroot.ssl.builtin
    --collect-submodules flask_limiter
    --collect-submodules limits
    --collect-submodules cheroot
    --copy-metadata flask
    --copy-metadata werkzeug
    --copy-metadata python-dotenv
    --copy-metadata Flask-Limiter
    --copy-metadata limits
    --copy-metadata cheroot
    --copy-metadata mutagen
)
python -m PyInstaller "${PYINSTALLER_ARGS[@]}" app.py

printf '%s\n' ">>> Assembling final package with the application under bin/..."
mkdir -p -- "${OUTPUT_DIR}/bin"
PYINSTALLER_DIST="${SOURCE_ROOT}/dist/web_broadcaster"
[[ -x "${PYINSTALLER_DIST}/web_broadcaster" ]] \
    || error "PyInstaller application executable is missing."
[[ -d "${PYINSTALLER_DIST}/_internal" ]] \
    || error "PyInstaller _internal directory is missing."
install -m 0755 "${PYINSTALLER_DIST}/web_broadcaster" "${OUTPUT_DIR}/bin/web_broadcaster"
cp -a "${PYINSTALLER_DIST}/_internal" "${OUTPUT_DIR}/bin/_internal"

# Keep user-facing assets under the package root. Templates are direct children
# of html/, while CSS/JavaScript remain under html/static/. The executable and
# its bundled Python runtime live together under bin/.
for directory in html script docs; do
    [[ -d "${SOURCE_ROOT}/${directory}" ]] \
        || error "Required application directory missing: ${directory}"
    cp -a "${SOURCE_ROOT}/${directory}" "${OUTPUT_DIR}/${directory}"
done

install -m 0755 "${NATIVE_BINARY}" "${OUTPUT_DIR}/bin/web_broadcaster_engine"
# Shared objects are data loaded by the ELF loader: read permission is enough;
# they intentionally receive no executable mode bits.
for library in "${REQUIRED_FFMPEG_LIBS[@]}"; do
    install -m 0644 "${SOURCE_ROOT}/lib/${library}" "${OUTPUT_DIR}/bin/${library}"
done
install -m 0644 \
    "${SOURCE_ROOT}/bin/soundsolution/libsoundsolution.so.2" \
    "${OUTPUT_DIR}/bin/libsoundsolution.so.2"
install -m 0644 \
    "${SOURCE_ROOT}/bin/soundsolution/ss18.dat" \
    "${OUTPUT_DIR}/bin/ss18.dat"

cat > "${OUTPUT_DIR}/start.sh" <<'START_EOF'
#!/usr/bin/env bash

# ============================================================================
# USER SETTINGS
# ============================================================================
PORT="15000"
DEBUG_MODE="OFF"

# OFF = normal trusted-LAN mode. ON = Internet-facing hardened mode.
PUBLIC_INTERNET_MODE="OFF"

# Listener address. Direct Internet/TLS normally uses 0.0.0.0.
# Reverse-proxy mode MUST use 127.0.0.1.
BIND_HOST="0.0.0.0"

# Comma-separated Host values accepted by Flask, for example:
# TRUSTED_HOSTS="radio.example.com"
TRUSTED_HOSTS=""

# Direct TLS served by Web Broadcaster/Cheroot.
HTTPS_MODE="OFF"
TLS_CERT_FILE=""
TLS_KEY_FILE=""

# Number of explicitly trusted reverse proxies. Keep 0 for direct exposure.
# Use 1 for a single local nginx proxy and BIND_HOST="127.0.0.1".
PROXY_COUNT="0"
# ============================================================================
# DO NOT MODIFY ANYTHING BELOW THIS LINE!
# ============================================================================

set -Eeuo pipefail

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${APP_DIR}"

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

if [[ ! "${PORT}" =~ ^[0-9]+$ ]] || (( 10#${PORT} < 1 || 10#${PORT} > 65535 )); then
    fail "Invalid PORT value: ${PORT}. Use a number from 1 to 65535."
fi

case "${DEBUG_MODE^^}" in
    ON|OFF) ;;
    *) fail "Invalid DEBUG_MODE value: ${DEBUG_MODE}. Use ON or OFF." ;;
esac
case "${PUBLIC_INTERNET_MODE^^}" in
    ON|OFF) ;;
    *) fail "Invalid PUBLIC_INTERNET_MODE value: ${PUBLIC_INTERNET_MODE}. Use ON or OFF." ;;
esac
case "${HTTPS_MODE^^}" in
    ON|OFF) ;;
    *) fail "Invalid HTTPS_MODE value: ${HTTPS_MODE}. Use ON or OFF." ;;
esac
if [[ ! "${PROXY_COUNT}" =~ ^[0-9]+$ ]] || (( 10#${PROXY_COUNT} < 0 || 10#${PROXY_COUNT} > 4 )); then
    fail "Invalid PROXY_COUNT value: ${PROXY_COUNT}. Use 0 to 4."
fi

HTTPS_ON=0
[[ "${HTTPS_MODE^^}" == "ON" ]] && HTTPS_ON=1
PUBLIC_ON=0
[[ "${PUBLIC_INTERNET_MODE^^}" == "ON" ]] && PUBLIC_ON=1
PROXY_NUM=$((10#${PROXY_COUNT}))

if (( HTTPS_ON )); then
    [[ -n "${TRUSTED_HOSTS// }" ]] || fail "HTTPS_MODE=ON requires TRUSTED_HOSTS."
    [[ -n "${TLS_CERT_FILE}" && -r "${TLS_CERT_FILE}" ]] || fail "TLS certificate is missing or unreadable: ${TLS_CERT_FILE}"
    [[ -n "${TLS_KEY_FILE}" && -r "${TLS_KEY_FILE}" ]] || fail "TLS private key is missing or unreadable: ${TLS_KEY_FILE}"
fi

if (( PROXY_NUM > 0 )); then
    [[ "${BIND_HOST}" == "127.0.0.1" || "${BIND_HOST}" == "::1" || "${BIND_HOST}" == "localhost" ]] \
        || fail "PROXY_COUNT>0 requires BIND_HOST=127.0.0.1, ::1 or localhost."
    [[ -n "${TRUSTED_HOSTS// }" ]] || fail "PROXY_COUNT>0 requires TRUSTED_HOSTS."
fi

if (( PUBLIC_ON )); then
    [[ -n "${TRUSTED_HOSTS// }" ]] || fail "PUBLIC_INTERNET_MODE=ON requires TRUSTED_HOSTS."
    if (( ! HTTPS_ON && PROXY_NUM == 0 )); then
        fail "PUBLIC_INTERNET_MODE=ON refuses plain HTTP. Enable HTTPS_MODE or use a trusted local reverse proxy."
    fi
fi

unset LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH LD_LIBRARY_PATH_ORIG

export WEB_BROADCASTER_NATIVE_BINARY="${APP_DIR}/bin/web_broadcaster_engine"
export WEB_BROADCASTER_NATIVE_PROTOCOL_LOG="${APP_DIR}/logs/audio_engine_protocol.jsonl"
export WEB_BROADCASTER_NATIVE_DAEMON_LOG="${APP_DIR}/logs/native_engine.log"
export WEB_BROADCASTER_TRUSTED_HOSTS="${TRUSTED_HOSTS}"
export WEB_BROADCASTER_PROXY_COUNT="${PROXY_NUM}"
if (( HTTPS_ON || PROXY_NUM > 0 )); then
    export WEB_BROADCASTER_SECURE_COOKIES=1
else
    export WEB_BROADCASTER_SECURE_COOKIES=0
fi

chmod +x "${APP_DIR}/bin/web_broadcaster" "${APP_DIR}/bin/web_broadcaster_engine"

case "${DEBUG_MODE^^}" in
    ON)
        export DEBUG=1
        printf '%s\n' "Web Broadcaster debug logging: ON"
        ;;
    OFF)
        unset DEBUG
        ;;
esac

ARGS=(-p "${PORT}" --host "${BIND_HOST}")
if (( PUBLIC_ON )); then
    ARGS+=(--public-internet)
fi
if [[ -n "${TRUSTED_HOSTS// }" ]]; then
    ARGS+=(--trusted-hosts "${TRUSTED_HOSTS}")
fi
if (( PROXY_NUM > 0 )); then
    ARGS+=(--proxy-count "${PROXY_NUM}")
fi
if (( HTTPS_ON )); then
    ARGS+=(--https --cert-file "${TLS_CERT_FILE}" --key-file "${TLS_KEY_FILE}")
fi

exec "${APP_DIR}/bin/web_broadcaster" "${ARGS[@]}"
START_EOF
chmod +x "${OUTPUT_DIR}/start.sh"

cat > "${OUTPUT_DIR}/README.txt" <<README_EOF
Web Broadcaster Linux Package v${APP_VERSION}

SECURITY MODES
==============

The web interface is hardened for Internet exposure, but public deployment must
use HTTPS. Plain HTTP is intended only for a trusted LAN.

For a continuously exposed Internet service, the HTTPS reverse-proxy mode below
is preferred for additional TLS-edge resilience. Direct Cheroot TLS is also
supported when a self-contained single-process deployment is required.

1. Trusted LAN (default)
------------------------

  ./start.sh

Default settings:
  PUBLIC_INTERNET_MODE="OFF"
  HTTPS_MODE="OFF"
  BIND_HOST="0.0.0.0"

2. Direct public Internet with Web Broadcaster TLS
---------------------------------------------------

Obtain a valid TLS certificate and private key for your public hostname, then
edit the USER SETTINGS section at the beginning of start.sh:

  PUBLIC_INTERNET_MODE="ON"
  BIND_HOST="0.0.0.0"
  TRUSTED_HOSTS="radio.example.com"
  HTTPS_MODE="ON"
  TLS_CERT_FILE="/absolute/path/fullchain.pem"
  TLS_KEY_FILE="/absolute/path/privkey.pem"
  PROXY_COUNT="0"

Then run:

  ./start.sh

A typical router mapping is public TCP 443 -> this host TCP 15000. Do not expose
a plain-HTTP listener to the public Internet. The certificate must match the
hostname used by the browser and should chain to a trusted public CA.

3. HTTPS reverse proxy with nginx (preferred)
---------------------------------

If nginx terminates HTTPS locally, keep Web Broadcaster on loopback:

  PUBLIC_INTERNET_MODE="ON"
  BIND_HOST="127.0.0.1"
  TRUSTED_HOSTS="radio.example.com"
  HTTPS_MODE="OFF"
  PROXY_COUNT="1"

Configure the proxy to forward only to 127.0.0.1:15000 (or the configured PORT).
PROXY_COUNT must equal the exact number of trusted proxies in front of the
application. Never enable proxy trust while binding the Web Broadcaster backend
directly to the Internet.

DEPLOYMENT GUIDE
================

The main guide for choosing between trusted-LAN and public-nginx operation is:

  docs/DEPLOYMENT_GUIDE.md

It documents the exact LAN-only Raspberry Pi/Debian settings with no nginx,
domain, Certbot or TLS, as well as the safe transition to/from public nginx mode.

NGINX PUBLIC HTTPS GUIDE
========================

The recommended always-on public deployment is documented in:

  docs/NGINX_PUBLIC_HTTPS.md

A ready-to-copy nginx virtual-host example is included as:

  docs/nginx_web_broadcaster.conf.example

The guide covers DNS, TCP 80/443, Certbot/Let's Encrypt certificate issuance,
nginx proxy headers, SSE buffering, start.sh settings, first-run setup, renewal,
verification, upgrades and troubleshooting.

FIRST-RUN SECURITY
==================

On an empty database, Web Broadcaster prints a one-time setup token on the local
server console and writes the same token to:

  db/.setup_token

Enter that token on the Setup page when creating the first administrator. The
file is removed after successful first-user creation.

The Flask session signing secret is generated automatically with a cryptographic
random generator and stored privately in:

  db/.session_secret

Do not publish, copy into web content or casually delete this file. Deleting it
invalidates existing browser sessions; the next launch generates a new secret.
The file is created with owner-only permissions where supported.

WEB SECURITY RETAINED FROM v6028
================================

- Deny-by-default authentication with only login/setup/static intentionally public.
- CSRF validation for every state-changing request.
- Secure/HttpOnly/SameSite session-cookie policy in HTTPS/proxy mode.
- Eight-hour permanent sessions with rolling refresh.
- Trusted Host validation in public HTTPS/proxy mode.
- HSTS on HTTPS plus CSP, clickjacking, MIME-sniffing and referrer protections.
- Request body/form limits and login/setup rate limiting.
- One-time first-run setup token.
- Random persistent Flask session secret; no hardcoded fallback secret.
- Cheroot production WSGI server instead of Flask's development server.
- Optional direct TLS or explicit loopback-only reverse-proxy trust.

PASSWORD POLICY
===============

New and changed passwords must contain 12 to 256 characters. Existing accounts
remain login-compatible; changing a password applies the current policy.

OTHER USER SETTINGS
===================

  PORT="15000"       # 1..65535
  DEBUG_MODE="OFF"   # OFF or ON

DEBUG_MODE="OFF" creates no continuous protocol/native-daemon log files.
DEBUG_MODE="ON" enables only logs/audio_engine_protocol.jsonl and
logs/native_engine.log. It never enables Flask debug mode or the reloader.

Package layout:
  start.sh
  README.txt
  bin/
    web_broadcaster
    _internal/                 (Python runtime and Python packages)
    web_broadcaster_engine
    libavformat.so.61
    libavcodec.so.61
    libswresample.so.5
    libavutil.so.59
    libfdk-aac.so.2
    libmp3lame.so.0
    libsoundsolution.so.2
    ss18.dat
  html/
    base.html
    broadcaster.html
    dashboard.html
    login.html
    no_stations.html
    setup.html
    Layout1_default.pos
    Layout2_default.pos
    static/
  script/
  docs/
    DEPLOYMENT_GUIDE.md
    NGINX_PUBLIC_HTTPS.md
    nginx_web_broadcaster.conf.example

Target build: ${TARGET_DESCRIPTION}
Release baseline: ${BUILD_BASELINE_SUMMARY}
FFmpeg: 7.1.5 (${EXPECTED_FFMPEG_RUNTIME_ID})
SoundSolution: ${EXPECTED_SOUNDSOLUTION_VERSION} (${EXPECTED_SOUNDSOLUTION_ARITHMETIC})

The target system must provide the normal Debian 12 runtime libraries used by
glibc and GnuTLS. The Python runtime and pinned Web dependencies are bundled by
PyInstaller.
README_EOF

printf '%s\n' ">>> Removing build-only remnants from final package..."
find "${OUTPUT_DIR}" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "${OUTPUT_DIR}" -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '*.o' \) -delete
rm -f -- "${OUTPUT_DIR}/bin/ffmpeg" "${OUTPUT_DIR}/bin/ffprobe"
rm -rf -- "${OUTPUT_DIR}/native_engine"


printf '%s\n' ">>> Materializing every symlink in the final package..."
python3 - "${OUTPUT_DIR}" <<'PY_LINKS'
from pathlib import Path
import shutil
import sys

root = Path(sys.argv[1]).resolve()
links = sorted(
    (path for path in root.rglob("*") if path.is_symlink()),
    key=lambda path: len(path.parts),
    reverse=True,
)
for link in links:
    target = link.resolve(strict=True)
    temporary = link.with_name(link.name + ".materialized-tmp")
    if temporary.exists() or temporary.is_symlink():
        if temporary.is_dir() and not temporary.is_symlink():
            shutil.rmtree(temporary)
        else:
            temporary.unlink()
    if target.is_dir():
        shutil.copytree(target, temporary, symlinks=False)
    else:
        shutil.copy2(target, temporary)
    link.unlink()
    temporary.rename(link)

remaining = [str(path) for path in root.rglob("*") if path.is_symlink()]
if remaining:
    raise SystemExit("Symlinks remain after materialization: " + ", ".join(remaining))
print(f"OK: materialized {len(links)} symlink(s)")
PY_LINKS

if find "${OUTPUT_DIR}" -type l -print -quit | grep -q .; then
    error "A symbolic link remains in the final package."
fi

printf '%s\n' ">>> Verifying clean package root..."
[[ -d "${OUTPUT_DIR}/bin/_internal" ]] || error "PyInstaller bin/_internal directory is missing."
[[ -f "${OUTPUT_DIR}/bin/_internal/base_library.zip" ]] \
    || error "PyInstaller base_library.zip is not under bin/_internal."

while IFS= read -r entry; do
    case "${entry}" in
        bin|html|script|docs|README.txt|start.sh)
            ;;
        *)
            error "Unexpected entry in package root: ${entry}"
            ;;
    esac
done < <(find "${OUTPUT_DIR}" -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort)

printf '%s\n' ">>> Verifying packaged deployment documentation..."
[[ -d "${OUTPUT_DIR}/docs" ]] || error "Packaged docs directory is missing."
[[ -f "${OUTPUT_DIR}/docs/DEPLOYMENT_GUIDE.md" ]] \
    || error "Packaged docs/DEPLOYMENT_GUIDE.md is missing."
[[ -f "${OUTPUT_DIR}/docs/NGINX_PUBLIC_HTTPS.md" ]] \
    || error "Packaged docs/NGINX_PUBLIC_HTTPS.md is missing."
[[ -f "${OUTPUT_DIR}/docs/nginx_web_broadcaster.conf.example" ]] \
    || error "Packaged docs/nginx_web_broadcaster.conf.example is missing."

if find "${OUTPUT_DIR}" -mindepth 1 -maxdepth 1 \
    \( -type d -name '*.dist-info' -o -type f -name '*.so*' -o -type f -name 'base_library.zip' \) \
    -print -quit | grep -q .; then
    error "Python runtime content leaked into the package root."
fi

printf '%s\n' ">>> Verifying consolidated html/ package layout..."
[[ -d "${OUTPUT_DIR}/html" ]] || error "Packaged html directory is missing."
[[ -d "${OUTPUT_DIR}/html/static" ]] || error "Packaged html/static directory is missing."
[[ -f "${OUTPUT_DIR}/html/base.html" ]] || error "Packaged html/base.html is missing."
[[ -f "${OUTPUT_DIR}/html/broadcaster.html" ]] || error "Packaged html/broadcaster.html is missing."
[[ -f "${OUTPUT_DIR}/html/static/broadcaster.js" ]] || error "Packaged html/static/broadcaster.js is missing."
[[ ! -e "${OUTPUT_DIR}/templates" ]] || error "Legacy packaged templates directory must not exist."
[[ ! -e "${OUTPUT_DIR}/static" ]] || error "Legacy packaged static directory must not exist."

printf '%s\n' ">>> Verifying flat final native runtime..."
PACKAGED_BIN="${OUTPUT_DIR}/bin"
PACKAGED_APP="${PACKAGED_BIN}/web_broadcaster"
PACKAGED_INTERNAL="${PACKAGED_BIN}/_internal"
PACKAGED_NATIVE="${PACKAGED_BIN}/web_broadcaster_engine"
PACKAGED_SS="${PACKAGED_BIN}/libsoundsolution.so.2"
PACKAGED_SS_DAT="${PACKAGED_BIN}/ss18.dat"
[[ -x "${PACKAGED_APP}" ]] || error "Packaged Web Broadcaster application is missing or not executable."
[[ -d "${PACKAGED_INTERNAL}" ]] || error "Packaged PyInstaller _internal directory is missing."
[[ -x "${PACKAGED_NATIVE}" ]] || error "Packaged native engine is missing or not executable."

for library in "${REQUIRED_FFMPEG_LIBS[@]}"; do
    [[ -f "${PACKAGED_BIN}/${library}" ]] \
        || error "Packaged FFmpeg library missing: ${library}"
    [[ ! -x "${PACKAGED_BIN}/${library}" ]] \
        || error "Packaged FFmpeg shared library must not have executable mode bits: ${library}"
done

[[ -f "${PACKAGED_SS}" ]] \
    || error "Packaged libsoundsolution.so.2 regular file is missing."
[[ ! -L "${PACKAGED_SS}" ]] \
    || error "Packaged libsoundsolution.so.2 must not be a symlink."
[[ ! -x "${PACKAGED_SS}" ]] \
    || error "Packaged libsoundsolution.so.2 must not have executable mode bits."
[[ -f "${PACKAGED_SS_DAT}" ]] \
    || error "Packaged SoundSolution configuration is missing."
[[ ! -x "${PACKAGED_SS_DAT}" ]] \
    || error "Packaged ss18.dat must not have executable mode bits."
PACKAGED_SS_SHA256="$(sha256sum "${PACKAGED_SS}" | awk '{print $1}')"
[[ "${PACKAGED_SS_SHA256}" == "${EXPECTED_SOUNDSOLUTION_SHA256}" ]] \
    || error "Packaged SoundSolution binary changed: ${PACKAGED_SS_SHA256}"
PACKAGED_SS_DAT_SHA256="$(sha256sum "${PACKAGED_SS_DAT}" | awk '{print $1}')"
[[ "${PACKAGED_SS_DAT_SHA256}" == "${ACTUAL_SS_DAT_SHA256}" ]] \
    || error "Packaged ss18.dat changed: ${PACKAGED_SS_DAT_SHA256}"
verify_elf_machine "${PACKAGED_SS}"
readelf -d "${PACKAGED_SS}" \
    | grep -Fq 'Library soname: [libsoundsolution.so.2]' \
    || error "Packaged SoundSolution file has an unexpected SONAME."

mapfile -t PACKAGED_BIN_DIRS < <(
    find "${PACKAGED_BIN}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | LC_ALL=C sort
)
[[ "${PACKAGED_BIN_DIRS[*]}" == "_internal" ]] \
    || error "Only the PyInstaller _internal directory may exist under bin/: ${PACKAGED_BIN_DIRS[*]}"

EXPECTED_BIN_ENTRIES=(
    _internal
    web_broadcaster
    web_broadcaster_engine
    "${REQUIRED_FFMPEG_LIBS[@]}"
    libsoundsolution.so.2
    ss18.dat
)
mapfile -t PACKAGED_BIN_ENTRIES < <(
    find "${PACKAGED_BIN}" -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort
)
mapfile -t EXPECTED_FLAT_BIN_ENTRIES < <(
    printf '%s\n' "${EXPECTED_BIN_ENTRIES[@]}" | LC_ALL=C sort
)
[[ "${PACKAGED_BIN_ENTRIES[*]}" == "${EXPECTED_FLAT_BIN_ENTRIES[*]}" ]] \
    || error "Packaged flat bin/ contains unexpected files: ${PACKAGED_BIN_ENTRIES[*]}"

if find "${OUTPUT_DIR}" -type f \
    \( -name ffmpeg -o -name ffprobe -o -name 'liquidsoap*' -o -name '*.AppImage' -o -name 'ssnative-process-raw' \) \
    -print -quit | grep -q .; then
    error "Forbidden ffmpeg/ffprobe/Liquidsoap/AppImage/DSP executable found in final package."
fi

PACKAGED_DYNAMIC="${WORK_DIR}/packaged-native-readelf.txt"
readelf -d "${PACKAGED_NATIVE}" | tee "${PACKAGED_DYNAMIC}"
grep -Fq 'Shared library: [libsoundsolution.so.2]' "${PACKAGED_DYNAMIC}" \
    || error "Packaged native engine is not linked to libsoundsolution.so.2."
grep -Fq "Library rpath: [${EXPECTED_FINAL_NATIVE_RPATH}]" "${PACKAGED_DYNAMIC}" \
    || error "Packaged native engine RPATH is not ${EXPECTED_FINAL_NATIVE_RPATH}."

LD_LIBRARY_PATH= ldd "${PACKAGED_NATIVE}" | tee "${WORK_DIR}/native-engine-ldd.txt"
if grep -q 'not found' "${WORK_DIR}/native-engine-ldd.txt"; then
    error "Packaged native engine has unresolved shared libraries."
fi
python3 - "${WORK_DIR}/native-engine-ldd.txt" "${OUTPUT_DIR}" <<'PY_LDD'
from pathlib import Path
import sys

ldd_path = Path(sys.argv[1])
root = Path(sys.argv[2]).resolve()
resolved = {}
for raw_line in ldd_path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if "=>" not in line:
        continue
    name, remainder = line.split("=>", 1)
    candidate = remainder.strip().split(" ", 1)[0]
    if candidate and candidate != "not":
        resolved[name.strip()] = Path(candidate).resolve()

expected = {
    "libsoundsolution.so.2": (root / "bin" / "libsoundsolution.so.2").resolve(),
    "libavformat.so.61": (root / "bin" / "libavformat.so.61").resolve(),
    "libavcodec.so.61": (root / "bin" / "libavcodec.so.61").resolve(),
    "libswresample.so.5": (root / "bin" / "libswresample.so.5").resolve(),
    "libavutil.so.59": (root / "bin" / "libavutil.so.59").resolve(),
}
for name, expected_path in expected.items():
    actual = resolved.get(name)
    if actual != expected_path:
        raise SystemExit(
            f"{name} resolved outside the private runtime: actual={actual}, expected={expected_path}"
        )
print("OK: native daemon resolves FFmpeg and SoundSolution directly from the flat packaged bin directory")
PY_LDD

printf '%s\n' ">>> Running packaged native daemon smoke test..."
python3 - "${PACKAGED_NATIVE}" "${APP_VERSION}" <<'PY'
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

binary = Path(sys.argv[1]).resolve()
expected_version = sys.argv[2]
socket_path = tempfile.mktemp(prefix="wb-native-smoke-", suffix=".sock")
environment = os.environ.copy()
for key in ("LD_PRELOAD", "LD_AUDIT", "LD_LIBRARY_PATH", "LD_LIBRARY_PATH_ORIG"):
    environment.pop(key, None)
environment["WEB_BROADCASTER_ENGINE_SOCKET"] = socket_path
process = subprocess.Popen(
    [str(binary)],
    env=environment,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)
try:
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if Path(socket_path).exists():
            break
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr else ""
            raise SystemExit(f"native daemon exited early: {stderr}")
        time.sleep(0.05)
    else:
        raise SystemExit("native daemon socket was not created")

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(3.0)
    client.connect(socket_path)
    data = b""
    while b"\n" not in data:
        chunk = client.recv(65536)
        if not chunk:
            raise SystemExit("native daemon closed before engine_ready")
        data += chunk
    payload = json.loads(data.split(b"\n", 1)[0].decode("utf-8"))
    client.close()

    if payload.get("event") != "engine_ready":
        raise SystemExit(f"unexpected native ready event: {payload}")
    if str(payload.get("native_daemon_version") or "") != expected_version:
        raise SystemExit(f"unexpected native daemon version: {payload}")
    details = payload.get("payload") or {}
    if details.get("ffmpeg_source") != "linked_libav":
        raise SystemExit(f"unexpected FFmpeg source: {details}")
    if details.get("ffmpeg_subprocesses") is not False:
        raise SystemExit(f"FFmpeg subprocess mode unexpectedly enabled: {details}")
    print(
        "OK: native daemon",
        payload.get("native_daemon_version"),
        details.get("ffmpeg_version"),
        details.get("ffmpeg_source"),
    )
finally:
    process.terminate()
    try:
        process.wait(timeout=4.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=4.0)
    try:
        Path(socket_path).unlink()
    except FileNotFoundError:
        pass
PY

printf '%s\n' ">>> Verifying packaged start script security settings and launch contract..."
grep -Fq 'PORT="15000"' "${OUTPUT_DIR}/start.sh" \
    || error "Packaged start.sh is missing the default PORT=15000 setting."
grep -Fq 'DEBUG_MODE="OFF"' "${OUTPUT_DIR}/start.sh" \
    || error "Packaged start.sh is missing the default DEBUG_MODE=OFF setting."
grep -Fq 'PUBLIC_INTERNET_MODE="OFF"' "${OUTPUT_DIR}/start.sh" \
    || error "Packaged start.sh is missing the safe default PUBLIC_INTERNET_MODE=OFF setting."
grep -Fq 'BIND_HOST="0.0.0.0"' "${OUTPUT_DIR}/start.sh" \
    || error "Packaged start.sh is missing the default BIND_HOST setting."
grep -Fq 'TRUSTED_HOSTS=""' "${OUTPUT_DIR}/start.sh" \
    || error "Packaged start.sh is missing TRUSTED_HOSTS."
grep -Fq 'HTTPS_MODE="OFF"' "${OUTPUT_DIR}/start.sh" \
    || error "Packaged start.sh is missing the safe default HTTPS_MODE=OFF setting."
grep -Fq 'TLS_CERT_FILE=""' "${OUTPUT_DIR}/start.sh" \
    || error "Packaged start.sh is missing TLS_CERT_FILE."
grep -Fq 'TLS_KEY_FILE=""' "${OUTPUT_DIR}/start.sh" \
    || error "Packaged start.sh is missing TLS_KEY_FILE."
grep -Fq 'PROXY_COUNT="0"' "${OUTPUT_DIR}/start.sh" \
    || error "Packaged start.sh is missing the safe default PROXY_COUNT=0 setting."
grep -Fq 'PUBLIC_INTERNET_MODE=ON refuses plain HTTP' "${OUTPUT_DIR}/start.sh" \
    || error "Packaged start.sh does not fail closed on public plain HTTP."
grep -Fq 'PROXY_COUNT>0 requires BIND_HOST=127.0.0.1' "${OUTPUT_DIR}/start.sh" \
    || error "Packaged start.sh does not enforce loopback reverse-proxy binding."
grep -Fq 'export WEB_BROADCASTER_SECURE_COOKIES=1' "${OUTPUT_DIR}/start.sh" \
    || error "Packaged start.sh does not enable secure cookies for HTTPS/proxy mode."
grep -Fq 'ARGS=(-p "${PORT}" --host "${BIND_HOST}")' "${OUTPUT_DIR}/start.sh" \
    || error "Packaged start.sh is missing explicit host/port launch arguments."
grep -Fq 'ARGS+=(--public-internet)' "${OUTPUT_DIR}/start.sh" \
    || error "Packaged start.sh is missing application-level public Internet mode."
grep -Fq 'ARGS+=(--trusted-hosts "${TRUSTED_HOSTS}")' "${OUTPUT_DIR}/start.sh" \
    || error "Packaged start.sh is missing trusted-host launch support."
grep -Fq 'ARGS+=(--proxy-count "${PROXY_NUM}")' "${OUTPUT_DIR}/start.sh" \
    || error "Packaged start.sh is missing reverse-proxy launch support."
grep -Fq 'ARGS+=(--https --cert-file "${TLS_CERT_FILE}" --key-file "${TLS_KEY_FILE}")' "${OUTPUT_DIR}/start.sh" \
    || error "Packaged start.sh is missing direct HTTPS launch support."
grep -Fq 'exec "${APP_DIR}/bin/web_broadcaster" "${ARGS[@]}"' "${OUTPUT_DIR}/start.sh" \
    || error "Packaged start.sh does not launch bin/web_broadcaster with hardened arguments."
if grep -Fq 'Web Broadcaster debug logging: OFF' "${OUTPUT_DIR}/start.sh"; then
    error "Packaged start.sh must remain silent in OFF mode."
fi

if [[ "${TARGET_TAG}" == "amd64" ]]; then
    printf '%s\n' ">>> Auditing packaged ELF files against the Debian 12 glibc 2.36 ABI ceiling..."
    python3 - "${OUTPUT_DIR}" <<'PY_GLIBC_AUDIT'
from pathlib import Path
import re
import subprocess
import sys

root = Path(sys.argv[1])
limit = (2, 36)
offenders = []
elf_with_glibc = 0
highest = (0, 0)

for path in sorted(root.rglob("*")):
    if not path.is_file():
        continue
    result = subprocess.run(
        ["readelf", "--version-info", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        continue
    versions = {
        tuple(map(int, match))
        for match in re.findall(r"GLIBC_(\d+)\.(\d+)", result.stdout)
    }
    if not versions:
        continue
    elf_with_glibc += 1
    required = max(versions)
    highest = max(highest, required)
    if required > limit:
        offenders.append((path.relative_to(root), required))

if offenders:
    details = ", ".join(
        f"{path}=GLIBC_{version[0]}.{version[1]}" for path, version in offenders
    )
    raise SystemExit(
        "Debian 12 compatibility audit failed; packaged ELF requires newer than "
        f"GLIBC_{limit[0]}.{limit[1]}: {details}"
    )

print(
    f"OK: Debian 12 glibc ABI audit passed for {elf_with_glibc} ELF files; "
    f"highest requirement is GLIBC_{highest[0]}.{highest[1]}."
)
PY_GLIBC_AUDIT
fi

printf '%s\n' ">>> Creating symlink-free final tar.gz package..."
if find "${OUTPUT_DIR}" -type l -print -quit | grep -q .; then
    error "Refusing to create package because a symbolic link remains."
fi
cd "${WORK_DIR}"
tar -czf "${OUTPUT_TAR}" "${OUTPUT_BASE}"
cd "${SCRIPT_DIR}"
if tar -tvzf "${OUTPUT_TAR}" | awk '$1 ~ /^l/ { found=1 } END { exit !found }'; then
    error "The generated archive still contains a symbolic link."
fi
printf '%s\n' ">>> Verifying deployment documentation inside final tar.gz..."
ARCHIVE_MEMBER_LIST="${WORK_DIR}/final-archive-members.txt"
tar -tzf "${OUTPUT_TAR}" > "${ARCHIVE_MEMBER_LIST}"
for archived_doc in \
    "${OUTPUT_BASE}/docs/DEPLOYMENT_GUIDE.md" \
    "${OUTPUT_BASE}/docs/NGINX_PUBLIC_HTTPS.md" \
    "${OUTPUT_BASE}/docs/nginx_web_broadcaster.conf.example"; do
    grep -Fxq -- "${archived_doc}" "${ARCHIVE_MEMBER_LIST}" \
        || error "Final archive is missing required documentation: ${archived_doc}"
done
rm -f -- "${ARCHIVE_MEMBER_LIST}"
sha256sum "$(basename -- "${OUTPUT_TAR}")" > "${OUTPUT_SHA}"

printf '\nDONE\n'
printf 'Source:   %s (copied clean Git source tree)\n' "${SOURCE_TREE_PATH}"
printf 'Version:  %s\n' "${APP_VERSION}"
printf 'Target:   %s\n' "${TARGET_TAG}"
printf 'Baseline: %s\n' "${BUILD_BASELINE_SUMMARY}"
printf 'Package:  %s\n' "${OUTPUT_TAR}"
printf 'Checksum: %s\n' "${OUTPUT_SHA}"
printf '\nTarget usage:\n'
printf '  tar -xzf %s\n' "$(basename -- "${OUTPUT_TAR}")"
printf '  cd %s\n' "${OUTPUT_BASE}"
printf '  ./start.sh\n'
printf '  Configure the USER SETTINGS section in start.sh for LAN, direct HTTPS or reverse-proxy mode.\n'
printf '  Read README.txt before enabling PUBLIC_INTERNET_MODE.\n'
