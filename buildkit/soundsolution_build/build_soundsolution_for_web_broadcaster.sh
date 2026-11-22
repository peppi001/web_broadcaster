#!/usr/bin/env bash
#
# SoundSolution package builder for Web Broadcaster
# =================================================
#
# This script provides one controlled entry point for the two validated
# SoundSolution release branches used by Web Broadcaster.
#
# Supported native hosts
# ----------------------
#
# 1. 64-bit Debian Linux on x86-64 / AMD64
#
#    The script verifies and builds the original, unmodified final source:
#
#      soundsolution-native-v6.1.0-x86_64-sse2-final-source.tar.gz
#
#    It invokes the source package's own ./verify.sh and ./build.sh scripts.
#    No SoundSolution source file, compiler option, PGO option, CMake option,
#    arithmetic mode, ISA target, SONAME or validation limit is changed.
#
# 2. 64-bit Raspberry Pi 5 / AArch64
#
#    The Raspberry Pi 5 DSP is already validated and heavily optimized.
#    This script NEVER rebuilds or modifies it. It verifies the exact source
#    archive and the exact validated runtime archive, then copies both archives
#    byte-for-byte to normalized Web Broadcaster output filenames.
#
#    Required Raspberry Pi 5 inputs:
#
#      soundsolution_v6.0.0_rPi5-structured-process-flow.tar.gz
#      soundsolution_v6.0.0_rPi5.tar.gz
#
#    The validated runtime SHA-256 is fixed in this script. Any byte change,
#    including repacking the tar archive, causes the script to stop.
#
# No cross-compilation
# --------------------
#
# Run this same script separately on each native target:
#
#   - run it on the Debian x86-64 build machine for the AMD64 release;
#   - run it on the actual Raspberry Pi 5 for the ARM64/RPi5 release.
#
# The script intentionally has no build-all mode and no target override.
#
# Recommended directory layout
# ----------------------------
#
#   soundsolution-build/
#   |-- build_soundsolution_for_web_broadcaster.sh
#   |-- sources/
#   |   |-- soundsolution-native-v6.1.0-x86_64-sse2-final-source.tar.gz
#   |   |-- soundsolution_v6.0.0_rPi5-structured-process-flow.tar.gz
#   |   `-- soundsolution_v6.0.0_rPi5.tar.gz
#   `-- dist/
#
# Only the files required by the detected native host must be present.
# Input archives may also be placed directly beside this script.
#
# Normal use
# ----------
#
#   chmod +x build_soundsolution_for_web_broadcaster.sh
#   ./build_soundsolution_for_web_broadcaster.sh
#
# Show this help text:
#
#   ./build_soundsolution_for_web_broadcaster.sh --help
#
# Optional environment variables
# ------------------------------
#
#   DIST_DIR=/path/to/output
#       Select another output directory. Default: ./dist
#
#   BUILD_JOBS=4
#       Select the parallel build job count for the AMD64 release.
#
#   INSTALL_DEPS=1
#       On Debian AMD64, install the required build packages with apt.
#       The default is 0, so the script never changes the host automatically.
#
#   KEEP_WORK=1
#       Keep the temporary extraction/build directory after success.
#
#   AMD64_SOURCE_ARCHIVE=/path/to/archive.tar.gz
#   RPI5_SOURCE_ARCHIVE=/path/to/archive.tar.gz
#   RPI5_RUNTIME_ARCHIVE=/path/to/archive.tar.gz
#       Override an input archive path explicitly.
#
# Output files
# ------------
#
# AMD64:
#
#   wb-soundsolution-6.1.0-amd64-source.tar.gz
#   wb-soundsolution-6.1.0-amd64-runtime.tar.gz
#   wb-soundsolution-6.1.0-amd64-BUILD-INFO.txt
#   wb-soundsolution-6.1.0-amd64-SHA256SUMS.txt
#
# Raspberry Pi 5:
#
#   wb-soundsolution-6.0.0-arm64-rpi5-source.tar.gz
#   wb-soundsolution-6.0.0-arm64-rpi5-runtime.tar.gz
#   wb-soundsolution-6.0.0-arm64-rpi5-BUILD-INFO.txt
#   wb-soundsolution-6.0.0-arm64-rpi5-SHA256SUMS.txt
#
# The normalized output filename does not alter the archive contents. The RPi5
# runtime output is an exact byte-for-byte copy of the validated input runtime.
#
set -Eeuo pipefail
shopt -s nullglob

SCRIPT_REVISION="soundsolution-for-web-broadcaster-r2"

AMD64_VERSION="6.1.0"
AMD64_SOURCE_NAME="soundsolution-native-v6.1.0-x86_64-sse2-final-source.tar.gz"
AMD64_SOURCE_SHA256="a4d6d640e92cffe90de4ee1817a72ca293b600751148f44b73fed2d47693f8cc"
AMD64_ORIGINAL_RUNTIME_NAME="soundsolution_v6.1.0_x86_64_debian12-sse2.tar.gz"
AMD64_REFERENCE_RUNTIME_SHA256="8b3442c6b0331863abb4318768eeaa5d479b7bbee6e1cd6afba3f92dd8f62c36"

RPI5_VERSION="6.0.0"
RPI5_SOURCE_NAME="soundsolution_v6.0.0_rPi5-structured-process-flow.tar.gz"
RPI5_SOURCE_SHA256="472ec25be3467a672e4f4ce69beae1eefe59a910cbf1e973d96ef5ceffd0e56c"
RPI5_RUNTIME_NAME="soundsolution_v6.0.0_rPi5.tar.gz"
RPI5_RUNTIME_SHA256="84455ef8a15121b88835f28205a4bb325f19d80bd649ebf4ebffff4fe973f4d4"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCES_DIR="${SCRIPT_DIR}/sources"
DIST_DIR="${DIST_DIR:-${SCRIPT_DIR}/dist}"
BUILD_JOBS="${BUILD_JOBS:-$(nproc 2>/dev/null || printf '4')}"
INSTALL_DEPS="${INSTALL_DEPS:-0}"
KEEP_WORK="${KEEP_WORK:-0}"

WORK_DIR=""

log() {
    printf '\n\033[1;34m==> %s\033[0m\n' "$*"
}

warn() {
    printf '\n\033[1;33mWARNING: %s\033[0m\n' "$*" >&2
}

fail() {
    printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2
    exit 1
}

show_help() {
    sed -n '2,/^set -Eeuo pipefail$/p' "${BASH_SOURCE[0]}" | sed '$d; s/^# \{0,1\}//'
}

cleanup() {
    local exit_code=$?
    if [[ -n "${WORK_DIR}" && -d "${WORK_DIR}" ]]; then
        if [[ "${KEEP_WORK}" == "1" ]]; then
            printf '\nWork directory preserved: %s\n' "${WORK_DIR}" >&2
        else
            rm -rf -- "${WORK_DIR}"
        fi
    fi
    exit "${exit_code}"
}
trap cleanup EXIT

case "${1:-}" in
    "") ;;
    -h|--help)
        show_help
        exit 0
        ;;
    *)
        fail "Unknown argument: ${1}. Use --help for usage information."
        ;;
esac
[[ $# -le 1 ]] || fail "This script accepts no positional arguments."

case "${INSTALL_DEPS}" in 0|1) ;; *) fail "INSTALL_DEPS must be 0 or 1." ;; esac
case "${KEEP_WORK}" in 0|1) ;; *) fail "KEEP_WORK must be 0 or 1." ;; esac
[[ "${BUILD_JOBS}" =~ ^[1-9][0-9]*$ ]] || fail "BUILD_JOBS must be a positive integer."

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

require_file() {
    [[ -f "$1" ]] || fail "Required file not found: $1"
}

sha256_of() {
    sha256sum "$1" | awk '{print $1}'
}

verify_sha256() {
    local file=$1
    local expected=$2
    local actual
    actual="$(sha256_of "${file}")"
    [[ "${actual}" == "${expected}" ]] \
        || fail "SHA-256 mismatch for ${file}. Expected ${expected}, got ${actual}."
    printf 'Verified SHA-256: %s  %s\n' "${actual}" "${file}"
}

resolve_input() {
    local explicit_path=$1
    local expected_name=$2
    local candidate

    if [[ -n "${explicit_path}" ]]; then
        require_file "${explicit_path}"
        printf '%s\n' "$(cd -- "$(dirname -- "${explicit_path}")" && pwd)/$(basename -- "${explicit_path}")"
        return
    fi

    for candidate in \
        "${SOURCES_DIR}/${expected_name}" \
        "${SCRIPT_DIR}/${expected_name}"; do
        if [[ -f "${candidate}" ]]; then
            printf '%s\n' "${candidate}"
            return
        fi
    done

    fail "Input archive not found: ${expected_name}. Place it under ${SOURCES_DIR} or beside this script."
}

safe_extract_tar_gz() {
    local archive=$1
    local destination=$2
    mkdir -p -- "${destination}"
    python3 - "${archive}" "${destination}" <<'PY'
from pathlib import Path
import sys
import tarfile

archive = Path(sys.argv[1]).resolve()
destination = Path(sys.argv[2]).resolve()

with tarfile.open(archive, "r:gz") as tf:
    members = tf.getmembers()
    if not members:
        raise SystemExit(f"empty archive: {archive}")
    for member in members:
        name = member.name
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe archive member: {name}")
        target = (destination / path).resolve()
        if destination != target and destination not in target.parents:
            raise SystemExit(f"archive member escapes destination: {name}")
        if member.issym() or member.islnk():
            link = Path(member.linkname)
            if link.is_absolute() or ".." in link.parts:
                raise SystemExit(f"unsafe archive link target: {name} -> {member.linkname}")
    tf.extractall(destination, members=members)
PY
}

single_top_directory() {
    local directory=$1
    local entries=()
    mapfile -d '' entries < <(find "${directory}" -mindepth 1 -maxdepth 1 -print0)
    [[ ${#entries[@]} -eq 1 ]] || fail "Expected one top-level archive entry under ${directory}."
    [[ -d "${entries[0]}" ]] || fail "The archive top-level entry is not a directory: ${entries[0]}"
    printf '%s\n' "${entries[0]}"
}

copy_archive_exact() {
    local source=$1
    local destination=$2
    rm -f -- "${destination}"
    cp --reflink=auto -- "${source}" "${destination}"
    cmp -s -- "${source}" "${destination}" \
        || fail "Byte-for-byte archive copy verification failed: ${destination}"
}

verify_no_missing_dependencies() {
    local binary=$1
    local report=$2
    ldd "${binary}" > "${report}" 2>&1 || {
        cat "${report}" >&2
        fail "ldd failed for ${binary}."
    }
    if grep -Fq 'not found' "${report}"; then
        cat "${report}" >&2
        fail "Missing runtime dependency detected for ${binary}."
    fi
}

find_runtime_library() {
    local root=$1
    local soname_path="${root}/lib/libsoundsolution.so.2"
    local versioned=()

    if [[ -e "${soname_path}" ]]; then
        readlink -f -- "${soname_path}"
        return
    fi

    mapfile -t versioned < <(find "${root}/lib" -maxdepth 1 -type f -name 'libsoundsolution.so.*' | LC_ALL=C sort)
    [[ ${#versioned[@]} -eq 1 ]] || fail "Unable to identify the SoundSolution shared library under ${root}/lib."
    printf '%s\n' "${versioned[0]}"
}

verify_runtime_common() {
    local runtime_root=$1
    local architecture=$2
    local report_dir=$3
    local library

    require_file "${runtime_root}/share/soundsolution-native/ss18.dat"
    library="$(find_runtime_library "${runtime_root}")"
    require_file "${library}"

    readelf -h "${library}" > "${report_dir}/runtime-elf-header.txt"
    case "${architecture}" in
        amd64)
            grep -Eq 'Machine:[[:space:]]+(Advanced Micro Devices X86-64|X86-64)' \
                "${report_dir}/runtime-elf-header.txt" \
                || fail "The packaged SoundSolution library is not x86-64."
            ;;
        rpi5)
            grep -Eq 'Machine:[[:space:]]+AArch64' "${report_dir}/runtime-elf-header.txt" \
                || fail "The packaged SoundSolution library is not AArch64."
            ;;
        *) fail "Internal architecture verification error: ${architecture}" ;;
    esac

    readelf -d "${library}" > "${report_dir}/runtime-dynamic.txt"
    grep -Fq 'Library soname: [libsoundsolution.so.2]' "${report_dir}/runtime-dynamic.txt" \
        || fail "Unexpected SoundSolution SONAME."

    verify_no_missing_dependencies "${library}" "${report_dir}/runtime-ldd.txt"
    printf '%s\n' "${library}"
}

verify_amd64_runtime() {
    local runtime_root=$1
    local report_dir=$2
    local library
    local config="${runtime_root}/share/soundsolution-native/ss18.dat"

    library="$(verify_runtime_common "${runtime_root}" amd64 "${report_dir}")"

    objdump -d "${library}" > "${report_dir}/runtime-disassembly.txt"
    if grep -Eiq '\b(ymm|zmm)[0-9]+\b' "${report_dir}/runtime-disassembly.txt"; then
        fail "YMM/ZMM registers were detected in the AMD64 runtime."
    fi
    if grep -Ei $'\t(v[a-z0-9]+)[[:space:]]' "${report_dir}/runtime-disassembly.txt" \
        | grep -Eiv $'\t(verr|verw)[[:space:]]' >/dev/null; then
        fail "An AVX-family VEX instruction was detected in the AMD64 runtime."
    fi

    python3 - "${library}" "${config}" <<'PY'
import ctypes
import math
import struct
import sys

library_path, config_path = sys.argv[1:]
lib = ctypes.CDLL(library_path)
lib.ssnative_version.restype = ctypes.c_char_p
lib.ssnative_arithmetic_mode.restype = ctypes.c_char_p
version = lib.ssnative_version().decode("ascii")
mode = lib.ssnative_arithmetic_mode().decode("ascii")
if version != "6.1.0":
    raise SystemExit(f"unexpected SoundSolution version: {version}")
if mode != "native-binary64-fast":
    raise SystemExit(f"unexpected arithmetic mode: {mode}")

lib.ssnative_create.restype = ctypes.c_void_p
lib.ssnative_destroy.argtypes = [ctypes.c_void_p]
lib.ssnative_load_dat.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
lib.ssnative_load_dat.restype = ctypes.c_int
lib.ssnative_process_s16_interleaved.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_int16),
    ctypes.c_size_t,
    ctypes.c_uint32,
    ctypes.c_uint32,
]
lib.ssnative_process_s16_interleaved.restype = ctypes.c_int

frames = 4096
samples = []
for index in range(frames):
    left = round(12000 * math.sin(2.0 * math.pi * 440.0 * index / 44100.0))
    right = round(9000 * math.sin(2.0 * math.pi * 880.0 * index / 44100.0))
    samples.extend((left, right))
buffer_type = ctypes.c_int16 * len(samples)
buffer = buffer_type(*samples)
context = lib.ssnative_create()
if not context:
    raise SystemExit("ssnative_create failed")
try:
    if lib.ssnative_load_dat(context, config_path.encode()) != 0:
        raise SystemExit("ssnative_load_dat failed")
    if lib.ssnative_process_s16_interleaved(context, buffer, frames, 2, 44100) != 0:
        raise SystemExit("ssnative_process_s16_interleaved failed")
finally:
    lib.ssnative_destroy(context)
print(f"Verified SoundSolution {version}, mode={mode}, frames={frames}")
PY
}

verify_rpi5_runtime() {
    local runtime_root=$1
    local report_dir=$2
    local library
    local config="${runtime_root}/share/soundsolution-native/ss18.dat"

    library="$(verify_runtime_common "${runtime_root}" rpi5 "${report_dir}")"

    python3 - "${library}" "${config}" <<'PY'
import ctypes
import math
import sys

library_path, config_path = sys.argv[1:]
lib = ctypes.CDLL(library_path)
lib.ssnative_version.restype = ctypes.c_char_p
lib.ssnative_arithmetic_mode.restype = ctypes.c_char_p
lib.ssnative_state_layout.restype = ctypes.c_char_p
lib.ssnative_control_layout.restype = ctypes.c_char_p

version = lib.ssnative_version().decode("ascii")
mode = lib.ssnative_arithmetic_mode().decode("ascii")
state_layout = lib.ssnative_state_layout().decode("ascii")
control_layout = lib.ssnative_control_layout().decode("ascii")

if version != "6.0.0":
    raise SystemExit(f"unexpected SoundSolution version: {version}")
if mode != "native-double":
    raise SystemExit(f"unexpected arithmetic mode: {mode}")
if state_layout != "named-state-v14/structured-process-flow":
    raise SystemExit(f"unexpected state layout: {state_layout}")
if control_layout != "structured-process-v1/typed-state-loop":
    raise SystemExit(f"unexpected control layout: {control_layout}")

expected_numeric = {
    "ssnative_process_program_instruction_count": 0,
    "ssnative_process_frame_bytes": 512,
    "ssnative_structured_process_stage_count": 87,
    "ssnative_persistent_process_workspace_bytes": 0,
    "ssnative_control_domain_count": 0,
    "ssnative_control_continuation_state_bytes": 0,
}
for name, expected in expected_numeric.items():
    function = getattr(lib, name)
    function.restype = ctypes.c_size_t
    actual = int(function())
    if actual != expected:
        raise SystemExit(f"unexpected {name}: {actual}, expected {expected}")

lib.ssnative_create.restype = ctypes.c_void_p
lib.ssnative_destroy.argtypes = [ctypes.c_void_p]
lib.ssnative_load_dat.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
lib.ssnative_load_dat.restype = ctypes.c_int
lib.ssnative_process_s16_interleaved.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_int16),
    ctypes.c_size_t,
    ctypes.c_uint32,
    ctypes.c_uint32,
]
lib.ssnative_process_s16_interleaved.restype = ctypes.c_int

frames = 4096
samples = []
for index in range(frames):
    left = round(12000 * math.sin(2.0 * math.pi * 440.0 * index / 44100.0))
    right = round(9000 * math.sin(2.0 * math.pi * 880.0 * index / 44100.0))
    samples.extend((left, right))
buffer_type = ctypes.c_int16 * len(samples)
buffer = buffer_type(*samples)
context = lib.ssnative_create()
if not context:
    raise SystemExit("ssnative_create failed")
try:
    if lib.ssnative_load_dat(context, config_path.encode()) != 0:
        raise SystemExit("ssnative_load_dat failed")
    if lib.ssnative_process_s16_interleaved(context, buffer, frames, 2, 44100) != 0:
        raise SystemExit("ssnative_process_s16_interleaved failed")
finally:
    lib.ssnative_destroy(context)

print(
    f"Verified SoundSolution {version}, mode={mode}, "
    f"state={state_layout}, control={control_layout}, frames={frames}"
)
PY
}

write_checksums() {
    local output_file=$1
    shift
    (
        cd -- "${DIST_DIR}"
        sha256sum "$@" > "$(basename -- "${output_file}")"
    )
}

install_amd64_dependencies() {
    if [[ "${INSTALL_DEPS}" != "1" ]]; then
        return 0
    fi
    require_command apt-get
    local sudo_command=()
    if [[ ${EUID} -ne 0 ]]; then
        require_command sudo
        sudo_command=(sudo)
    fi
    log "Installing AMD64 build dependencies"
    "${sudo_command[@]}" apt-get update
    "${sudo_command[@]}" apt-get install -y --no-install-recommends \
        build-essential cmake ninja-build python3 binutils time tar gzip ca-certificates
}

create_build_info() {
    local output_file=$1
    local target=$2
    local version=$3
    local source_input=$4
    local source_output=$5
    local runtime_input=$6
    local runtime_output=$7
    local mode=$8
    local source_hash runtime_hash

    source_hash="$(sha256_of "${source_output}")"
    runtime_hash="$(sha256_of "${runtime_output}")"

    cat > "${output_file}" <<INFO
Product: SoundSolution package set for Web Broadcaster
Script revision: ${SCRIPT_REVISION}
Target: ${target}
SoundSolution version: ${version}
Packaging mode: ${mode}
Build host: $(uname -a)
Build UTC: $(date -u +%Y-%m-%dT%H:%M:%SZ)
Original source input: ${source_input}
Normalized source output: ${source_output}
Source SHA-256: ${source_hash}
Original runtime input: ${runtime_input}
Normalized runtime output: ${runtime_output}
Runtime SHA-256: ${runtime_hash}
Archive content policy: normalized filenames only; archive bytes are not repacked or modified
INFO
}

verify_amd64_build_host() {
    [[ "$(uname -s)" == "Linux" ]] || fail "The AMD64 release must be built on Linux."
    [[ -r /etc/os-release ]] || fail "Unable to identify the AMD64 operating system."
    # shellcheck disable=SC1091
    . /etc/os-release
    [[ "${ID:-}" == "debian" ]] \
        || fail "The AMD64 release must be built on Debian Linux. Detected ID: ${ID:-unknown}."
    [[ "${VERSION_ID:-}" == "12" ]] \
        || fail "The AMD64 release must be built on Debian 12. Detected VERSION_ID: ${VERSION_ID:-unknown}."
}

build_amd64() {
    local source_archive source_extract source_root
    local built_runtime built_extract runtime_root
    local output_prefix source_output runtime_output info_output sums_output
    local report_dir

    verify_amd64_build_host
    install_amd64_dependencies
    for command_name in tar gzip sha256sum python3 cmake g++ cc readelf objdump ldd find awk sed cmp; do
        require_command "${command_name}"
    done

    source_archive="$(resolve_input "${AMD64_SOURCE_ARCHIVE:-}" "${AMD64_SOURCE_NAME}")"
    log "Verifying the final AMD64 source archive"
    verify_sha256 "${source_archive}" "${AMD64_SOURCE_SHA256}"

    source_extract="${WORK_DIR}/amd64-source"
    safe_extract_tar_gz "${source_archive}" "${source_extract}"
    source_root="$(single_top_directory "${source_extract}")"
    require_file "${source_root}/verify.sh"
    require_file "${source_root}/build.sh"

    log "Running the unmodified SoundSolution v6.1.0 source verification"
    (
        cd -- "${source_root}"
        ./verify.sh
    )

    log "Running the unmodified SoundSolution v6.1.0 release build"
    (
        cd -- "${source_root}"
        env SSNATIVE_BUILD_JOBS="${BUILD_JOBS}" ./build.sh
    )

    built_runtime="${source_root}/dist/${AMD64_ORIGINAL_RUNTIME_NAME}"
    require_file "${built_runtime}"

    report_dir="${WORK_DIR}/amd64-reports"
    built_extract="${WORK_DIR}/amd64-runtime"
    mkdir -p -- "${report_dir}"
    safe_extract_tar_gz "${built_runtime}" "${built_extract}"
    runtime_root="$(single_top_directory "${built_extract}")"

    log "Verifying the built AMD64 runtime"
    verify_amd64_runtime "${runtime_root}" "${report_dir}"

    output_prefix="wb-soundsolution-${AMD64_VERSION}-amd64"
    source_output="${DIST_DIR}/${output_prefix}-source.tar.gz"
    runtime_output="${DIST_DIR}/${output_prefix}-runtime.tar.gz"
    info_output="${DIST_DIR}/${output_prefix}-BUILD-INFO.txt"
    sums_output="${DIST_DIR}/${output_prefix}-SHA256SUMS.txt"

    log "Creating normalized AMD64 outputs without repacking archive contents"
    copy_archive_exact "${source_archive}" "${source_output}"
    copy_archive_exact "${built_runtime}" "${runtime_output}"

    create_build_info \
        "${info_output}" \
        "AMD64 / generic x86-64 / SSE2 baseline" \
        "${AMD64_VERSION}" \
        "${source_archive}" \
        "${source_output}" \
        "${built_runtime}" \
        "${runtime_output}" \
        "Built by the original unmodified v6.1.0 ./build.sh release flow"

    write_checksums "${sums_output}" \
        "$(basename -- "${source_output}")" \
        "$(basename -- "${runtime_output}")" \
        "$(basename -- "${info_output}")"

    printf '\nAMD64 package set completed successfully.\n'
    printf 'Source:     %s\n' "${source_output}"
    printf 'Runtime:    %s\n' "${runtime_output}"
    printf 'Build info: %s\n' "${info_output}"
    printf 'Checksums:  %s\n' "${sums_output}"
    printf 'Runtime SHA-256: %s\n' "$(sha256_of "${runtime_output}")"
    if [[ "$(sha256_of "${runtime_output}")" == "${AMD64_REFERENCE_RUNTIME_SHA256}" ]]; then
        printf 'Reference runtime archive match: yes\n'
    else
        printf 'Reference runtime archive match: no (archive metadata or toolchain output differs; runtime verification passed)\n'
    fi
}

package_rpi5() {
    local source_archive runtime_archive
    local source_extract source_root runtime_extract runtime_root
    local output_prefix source_output runtime_output info_output sums_output
    local report_dir

    for command_name in tar gzip sha256sum python3 cc readelf ldd find awk sed cmp; do
        require_command "${command_name}"
    done

    source_archive="$(resolve_input "${RPI5_SOURCE_ARCHIVE:-}" "${RPI5_SOURCE_NAME}")"
    runtime_archive="$(resolve_input "${RPI5_RUNTIME_ARCHIVE:-}" "${RPI5_RUNTIME_NAME}")"

    log "Verifying the exact Raspberry Pi 5 source archive"
    verify_sha256 "${source_archive}" "${RPI5_SOURCE_SHA256}"
    log "Verifying the exact validated Raspberry Pi 5 runtime archive"
    verify_sha256 "${runtime_archive}" "${RPI5_RUNTIME_SHA256}"

    source_extract="${WORK_DIR}/rpi5-source"
    safe_extract_tar_gz "${source_archive}" "${source_extract}"
    source_root="$(single_top_directory "${source_extract}")"
    require_file "${source_root}/verify.sh"
    require_file "${source_root}/build.sh"

    log "Running source-integrity verification only; the Raspberry Pi 5 DSP will not be rebuilt"
    (
        cd -- "${source_root}"
        ./verify.sh
    )

    report_dir="${WORK_DIR}/rpi5-reports"
    runtime_extract="${WORK_DIR}/rpi5-runtime"
    mkdir -p -- "${report_dir}"
    safe_extract_tar_gz "${runtime_archive}" "${runtime_extract}"
    runtime_root="$(single_top_directory "${runtime_extract}")"

    log "Verifying the validated Raspberry Pi 5 runtime identity"
    verify_rpi5_runtime "${runtime_root}" "${report_dir}"

    output_prefix="wb-soundsolution-${RPI5_VERSION}-arm64-rpi5"
    source_output="${DIST_DIR}/${output_prefix}-source.tar.gz"
    runtime_output="${DIST_DIR}/${output_prefix}-runtime.tar.gz"
    info_output="${DIST_DIR}/${output_prefix}-BUILD-INFO.txt"
    sums_output="${DIST_DIR}/${output_prefix}-SHA256SUMS.txt"

    log "Copying the Raspberry Pi 5 archives byte-for-byte"
    copy_archive_exact "${source_archive}" "${source_output}"
    copy_archive_exact "${runtime_archive}" "${runtime_output}"

    verify_sha256 "${source_output}" "${RPI5_SOURCE_SHA256}"
    verify_sha256 "${runtime_output}" "${RPI5_RUNTIME_SHA256}"

    create_build_info \
        "${info_output}" \
        "ARM64 / Raspberry Pi 5 / Cortex-A76" \
        "${RPI5_VERSION}" \
        "${source_archive}" \
        "${source_output}" \
        "${runtime_archive}" \
        "${runtime_output}" \
        "Validated prebuilt runtime copied byte-for-byte; no rebuild and no source modification"

    write_checksums "${sums_output}" \
        "$(basename -- "${source_output}")" \
        "$(basename -- "${runtime_output}")" \
        "$(basename -- "${info_output}")"

    printf '\nRaspberry Pi 5 package set completed successfully.\n'
    printf 'Source:     %s\n' "${source_output}"
    printf 'Runtime:    %s\n' "${runtime_output}"
    printf 'Build info: %s\n' "${info_output}"
    printf 'Checksums:  %s\n' "${sums_output}"
    printf 'Runtime SHA-256: %s\n' "$(sha256_of "${runtime_output}")"
    printf 'Validated runtime archive match: yes\n'
}

require_command uname
require_command mktemp
require_command rm
require_command mkdir
require_command cp
require_command cmp

mkdir -p -- "${DIST_DIR}"
WORK_DIR="$(mktemp -d "${SCRIPT_DIR}/.soundsolution-build.XXXXXX")"

HOST_MACHINE="$(uname -m)"
case "${HOST_MACHINE}" in
    x86_64|amd64)
        TARGET="amd64"
        ;;
    aarch64|arm64)
        MODEL=""
        if [[ -r /proc/device-tree/model ]]; then
            MODEL="$(tr -d '\0' < /proc/device-tree/model)"
        fi
        [[ "${MODEL}" == *"Raspberry Pi 5"* ]] \
            || fail "AArch64 is supported only on an actual Raspberry Pi 5. Detected model: ${MODEL:-unknown}."
        TARGET="rpi5"
        ;;
    *)
        fail "Unsupported host architecture: ${HOST_MACHINE}. Use native AMD64 Debian Linux or an actual Raspberry Pi 5."
        ;;
esac

log "SoundSolution package builder"
printf 'Script revision: %s\n' "${SCRIPT_REVISION}"
printf 'Detected target: %s\n' "${TARGET}"
printf 'Output directory: %s\n' "${DIST_DIR}"
printf 'Work directory: %s\n' "${WORK_DIR}"

case "${TARGET}" in
    amd64) build_amd64 ;;
    rpi5) package_rpi5 ;;
    *) fail "Internal target-selection error." ;;
esac
