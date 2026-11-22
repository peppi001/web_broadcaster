#!/usr/bin/env bash
#
# FFmpeg 7.1.5 builder for Web Broadcaster
# =========================================
#
# This single script builds the FFmpeg SDK and runtime packages used by
# Web Broadcaster. Run it directly on the machine for which the packages are
# intended. Cross-compilation and multi-architecture builds are intentionally
# disabled so that every release package is built and validated natively.
#
# Supported build machines
# ------------------------
#
# 1. AMD64 / x86-64 Debian Linux
#
#    Run the script on a 64-bit x86-64 Debian Linux computer. Debian 12 is the
#    validated build environment for this project. The script automatically
#    selects the generic AMD64 profile that remains compatible with the proven
#    Web Broadcaster r9 FFmpeg build.
#
# 2. Raspberry Pi 5 / ARM64
#
#    Run the script directly on a Raspberry Pi 5 using a 64-bit operating
#    system. The script verifies the Raspberry Pi model before building and
#    automatically selects the ARM64 profile optimized for the Pi 5 Cortex-A76
#    CPU. This profile preserves the proven Web Broadcaster r10 settings.
#
# Requirements on both machines
# -----------------------------
#
# - A working Internet connection
# - Docker Engine
# - Docker Buildx
# - Sufficient free disk space for the source files, Docker image, and output
#
# Usage
# -----
#
# Run the same commands on the AMD64 Debian Linux machine and on the
# Raspberry Pi 5:
#
#   chmod +x build_ffmpeg_7_1_5_for_web_broadcaster.sh
#   ./build_ffmpeg_7_1_5_for_web_broadcaster.sh
#
# By default, all generated files are written to the dist/ directory next to
# this script. To use another output directory:
#
#   DIST_DIR=/home/user/ffmpeg-output ./build_ffmpeg_7_1_5_for_web_broadcaster.sh
#
# Optional environment variables
# ------------------------------
#
#   NO_CACHE=1 ./build_ffmpeg_7_1_5_for_web_broadcaster.sh
#       Build without the Docker cache for a completely clean rebuild.
#
#   KEEP_IMAGE=1 ./build_ffmpeg_7_1_5_for_web_broadcaster.sh
#       Keep the generated Docker image after a successful build.
#
# Output packages
# ---------------
#
# AMD64:
#
#   wb-ffmpeg-7.1.5-amd64-sdk.tar.gz
#   wb-ffmpeg-7.1.5-amd64-runtime.tar.gz
#
# Raspberry Pi 5:
#
#   wb-ffmpeg-7.1.5-arm64-rpi5-sdk.tar.gz
#   wb-ffmpeg-7.1.5-arm64-rpi5-runtime.tar.gz
#
# Important notes
# ---------------
#
# - Do not set BUILD_TARGETS or DOCKER_PLATFORM.
# - Build the AMD64 packages on the AMD64 machine.
# - Build the ARM64 Pi 5 packages directly on the Raspberry Pi 5.
# - This build enables libfdk-aac and FFmpeg's --enable-nonfree option.
#   Review all applicable FFmpeg, FDK-AAC, patent, and distribution terms
#   before redistributing the generated packages.
#
set -Eeuo pipefail

FFMPEG_VERSION="7.1.5"
SCRIPT_REVISION="7.1.5-for-web-broadcaster-r14-english-verified"
LAME_VERSION="${LAME_VERSION:-3.100}"
FDK_AAC_VERSION="${FDK_AAC_VERSION:-2.0.3}"
NO_CACHE="${NO_CACHE:-0}"
KEEP_IMAGE="${KEEP_IMAGE:-0}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="${DIST_DIR:-${SCRIPT_DIR}/dist}"

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

HOST_MACHINE="$(uname -m)"
case "${HOST_MACHINE}" in
    x86_64|amd64)
        TARGET="amd64"
        ;;
    aarch64|arm64)
        PI_MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
        [[ "${PI_MODEL}" == *"Raspberry Pi 5"* ]] \
            || fail "AArch64 builds are restricted to Raspberry Pi 5. Detected model: ${PI_MODEL:-unknown}"
        TARGET="rpi5"
        ;;
    *)
        fail "Unsupported host architecture: ${HOST_MACHINE}"
        ;;
esac

[[ -z "${BUILD_TARGETS:-}" ]] \
    || fail "BUILD_TARGETS is intentionally unsupported. Run this script natively on each target machine."
[[ -z "${DOCKER_PLATFORM:-}" ]] \
    || fail "DOCKER_PLATFORM is intentionally unsupported. Cross-compilation is disabled."

command -v docker >/dev/null 2>&1 || fail "Docker is not installed or is not in PATH."
docker info >/dev/null 2>&1 || fail "Docker daemon is not reachable."
docker buildx version >/dev/null 2>&1 || fail "Docker Buildx is required."

BUILD_DIR="$(mktemp -d -t wb-ffmpeg-build.XXXXXX)"
CURRENT_CONTAINER=""
CURRENT_IMAGE=""

cleanup() {
    if [[ -n "${CURRENT_CONTAINER}" ]]; then
        docker rm -f "${CURRENT_CONTAINER}" >/dev/null 2>&1 || true
    fi
    if [[ -n "${CURRENT_IMAGE}" && "${KEEP_IMAGE}" != "1" ]]; then
        docker image rm "${CURRENT_IMAGE}" >/dev/null 2>&1 || true
    fi
    rm -rf -- "${BUILD_DIR}"
}
trap cleanup EXIT INT TERM

warn "The build uses libfdk-aac and --enable-nonfree. Treat the output as private/internal unless distribution has been legally reviewed."
mkdir -p -- "${DIST_DIR}"

cat > "${BUILD_DIR}/Dockerfile" <<'DOCKERFILE'
# syntax=docker/dockerfile:1
FROM debian:12-slim AS builder

ARG FFMPEG_VERSION
ARG LAME_VERSION
ARG FDK_AAC_VERSION
ARG WB_ARCH
ARG TARGET_PROFILE
ARG TARGET_CPU
ARG TARGET_CFLAGS
ARG TARGET_DEPS_CFLAGS

ENV DEBIAN_FRONTEND=noninteractive \
    PREFIX=/opt/wb-ffmpeg \
    DEPS_PREFIX=/opt/wb-deps \
    SRC_DIR=/build/src \
    OUT_DIR=/out

# Bash is intentional: dash does not support `read -d`, which made the old
# NUL-delimited library loop unreliable.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update && apt-get install -y --no-install-recommends \
        autoconf \
        automake \
        binutils \
        build-essential \
        ca-certificates \
        curl \
        file \
        libtool \
        libtool-bin \
        libgnutls28-dev \
        nasm \
        patchelf \
        pax-utils \
        pkg-config \
        python3-minimal \
        xz-utils \
        yasm \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p "${SRC_DIR}" "${PREFIX}" "${DEPS_PREFIX}" "${OUT_DIR}"
WORKDIR ${SRC_DIR}

RUN set -Eeuxo pipefail; \
    curl -fL --retry 5 --retry-delay 2 \
        "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" \
        -o "ffmpeg-${FFMPEG_VERSION}.tar.xz"; \
    curl -fL --retry 5 --retry-delay 2 \
        "https://downloads.sourceforge.net/project/lame/lame/${LAME_VERSION}/lame-${LAME_VERSION}.tar.gz" \
        -o "lame-${LAME_VERSION}.tar.gz"; \
    curl -fL --retry 5 --retry-delay 2 \
        "https://github.com/mstorsjo/fdk-aac/archive/refs/tags/v${FDK_AAC_VERSION}.tar.gz" \
        -o "fdk-aac-${FDK_AAC_VERSION}.tar.gz"; \
    sha256sum \
        "ffmpeg-${FFMPEG_VERSION}.tar.xz" \
        "lame-${LAME_VERSION}.tar.gz" \
        "fdk-aac-${FDK_AAC_VERSION}.tar.gz" \
        > SOURCE-SHA256SUMS.txt

RUN set -Eeuxo pipefail; \
    tar -xzf "lame-${LAME_VERSION}.tar.gz"; \
    cd "lame-${LAME_VERSION}"; \
    if [[ -n "${TARGET_DEPS_CFLAGS}" ]]; then export CFLAGS="${TARGET_DEPS_CFLAGS}"; fi; \
    ./configure \
        --prefix="${DEPS_PREFIX}" \
        --enable-shared \
        --disable-static \
        --disable-frontend; \
    make -j"$(nproc)"; \
    make install

RUN set -Eeuxo pipefail; \
    tar -xzf "fdk-aac-${FDK_AAC_VERSION}.tar.gz"; \
    cd "fdk-aac-${FDK_AAC_VERSION}"; \
    if [[ -n "${TARGET_DEPS_CFLAGS}" ]]; then export CFLAGS="${TARGET_DEPS_CFLAGS}"; export CXXFLAGS="${TARGET_DEPS_CFLAGS}"; fi; \
    autoreconf -fiv; \
    ./configure \
        --prefix="${DEPS_PREFIX}" \
        --enable-shared \
        --disable-static; \
    make -j"$(nproc)"; \
    make install

# NOTE: raw PCM runtime format `s16le` is configured by the component name
# `pcm_s16le`; using `--enable-demuxer=s16le` is silently ignored by FFmpeg.
# `abuffer` and `abuffersink` are internal libavfilter API endpoints in FFmpeg
# 7.1.5, not configure-selectable filters. Do not pass them to
# --enable-filter; the FFmpeg CLI wires them internally when needed.
# Build a fixed FFmpeg 7.1.5 audio-only profile.  This deliberately avoids
# preflight --list-* parsing: known 7.1.5 component names are configured
# directly, and configure/make failures print their useful log tails.
RUN set -Eeuxo pipefail; \
    tar -xJf "ffmpeg-${FFMPEG_VERSION}.tar.xz"; \
    cd "ffmpeg-${FFMPEG_VERSION}"; \
    export PKG_CONFIG_PATH="${DEPS_PREFIX}/lib/pkgconfig"; \
    unset PKG_CONFIG_LIBDIR; \
    cfg=( \
        "--prefix=${PREFIX}" \
        --enable-shared \
        --disable-static \
        --enable-pic \
        --enable-small \
        --disable-debug \
        --disable-doc \
        --enable-network \
        --disable-autodetect \
        --disable-avdevice \
        --disable-swscale \
        --disable-postproc \
        --disable-everything \
        --disable-ffplay \
        --enable-avcodec \
        --enable-avformat \
        --enable-avfilter \
        --enable-swresample \
        --enable-libmp3lame \
        --enable-libfdk-aac \
        --enable-gnutls \
        --enable-nonfree \
        --enable-protocol=file \
        --enable-protocol=pipe \
        --enable-protocol=http \
        --enable-protocol=https \
        --enable-protocol=tcp \
        --enable-protocol=tls \
        --enable-protocol=httpproxy \
        --enable-protocol=crypto \
        --enable-protocol=icecast \
        --enable-demuxer=pcm_s16le \
        --enable-demuxer=mp3 \
        --enable-demuxer=aac \
        --enable-demuxer=wav \
        --enable-demuxer=flac \
        --enable-demuxer=ogg \
        --enable-demuxer=mov \
        --enable-demuxer=matroska \
        --enable-demuxer=hls \
        --enable-demuxer=mpegts \
        --enable-demuxer=aiff \
        --enable-demuxer=ape \
        --enable-demuxer=wv \
        --enable-demuxer=caf \
        --enable-demuxer=dsf \
        --enable-demuxer=tta \
        --enable-muxer=pcm_s16le \
        --enable-muxer=mp3 \
        --enable-muxer=adts \
        --enable-muxer=latm \
        --enable-muxer=wav \
        --enable-muxer=flac \
        --enable-muxer=ogg \
        --enable-muxer=ipod \
        --enable-muxer=matroska \
        --enable-muxer=hls \
        --enable-muxer=mpegts \
        --enable-decoder=pcm_s16le \
        --enable-decoder=pcm_s24le \
        --enable-decoder=pcm_s32le \
        --enable-decoder=pcm_f32le \
        --enable-decoder=pcm_s16be \
        --enable-decoder=pcm_s24be \
        --enable-decoder=pcm_s32be \
        --enable-decoder=pcm_f32be \
        --enable-decoder=pcm_u8 \
        --enable-decoder=pcm_s8 \
        --enable-decoder=pcm_alaw \
        --enable-decoder=pcm_mulaw \
        --enable-decoder=mp3 \
        --enable-decoder=mp3float \
        --enable-decoder=aac \
        --enable-decoder=libfdk_aac \
        --enable-decoder=flac \
        --enable-decoder=vorbis \
        --enable-decoder=opus \
        --enable-decoder=alac \
        --enable-decoder=ape \
        --enable-decoder=wavpack \
        --enable-decoder=tta \
        --enable-encoder=libmp3lame \
        --enable-encoder=libfdk_aac \
        --enable-encoder=pcm_s16le \
        --enable-parser=aac \
        --enable-parser=flac \
        --enable-parser=mpegaudio \
        --enable-parser=opus \
        --enable-parser=vorbis \
        --enable-bsf=aac_adtstoasc \
        --enable-filter=aformat \
        --enable-filter=aresample \
        --enable-filter=anull \
        --enable-filter=asetnsamples \
        --enable-filter=atrim \
        --enable-filter=volume \
        "--extra-cflags=-I${DEPS_PREFIX}/include ${TARGET_CFLAGS}" \
        "--extra-ldflags=-L${DEPS_PREFIX}/lib -Wl,-rpath-link,${DEPS_PREFIX}/lib -Wl,--gc-sections" \
    ); \
    if [[ -n "${TARGET_CPU}" ]]; then \
        cfg+=("--cpu=${TARGET_CPU}"); \
    fi; \
    printf '%q ' ./configure "${cfg[@]}" > /tmp/ffmpeg-configure-command.txt; \
    printf '\n' >> /tmp/ffmpeg-configure-command.txt; \
    if ! ./configure "${cfg[@]}" 2>&1 | tee /tmp/ffmpeg-configure-output.txt; then \
        echo '===== FFmpeg configure output (last 200 lines) =====' >&2; \
        tail -n 200 /tmp/ffmpeg-configure-output.txt >&2 || true; \
        echo '===== FFmpeg ffbuild/config.log (last 200 lines) =====' >&2; \
        tail -n 200 ffbuild/config.log >&2 || true; \
        exit 1; \
    fi; \
    if grep -E '^WARNING: Option .* did not match anything' /tmp/ffmpeg-configure-output.txt; then \
        echo 'FFmpeg configure ignored one or more requested components.' >&2; \
        echo 'Refusing to continue with an incomplete runtime.' >&2; \
        exit 1; \
    fi; \
    if ! make -j"$(nproc)" 2>&1 | tee /tmp/ffmpeg-make-output.txt; then \
        echo '===== FFmpeg make output (last 250 lines) =====' >&2; \
        tail -n 250 /tmp/ffmpeg-make-output.txt >&2 || true; \
        exit 1; \
    fi; \
    make install; \
    cp /tmp/ffmpeg-configure-command.txt "${PREFIX}/FFMPEG-CONFIGURE-COMMAND.txt"

# Create an SDK first. The runtime package is made from it by removing headers
# and pkg-config metadata.
RUN set -Eeuxo pipefail; \
    BASE="wb-ffmpeg-${FFMPEG_VERSION}-${WB_ARCH}"; \
    SDK="${OUT_DIR}/${BASE}-sdk"; \
    RUNTIME="${OUT_DIR}/${BASE}-runtime"; \
    mkdir -p \
        "${SDK}/bin" \
        "${SDK}/lib/pkgconfig" \
        "${SDK}/include" \
        "${SDK}/licenses/ffmpeg" \
        "${SDK}/licenses/lame" \
        "${SDK}/licenses/fdk-aac"; \
    cp -a "${PREFIX}/bin/ffmpeg" "${PREFIX}/bin/ffprobe" "${SDK}/bin/"; \
    cp -a "${PREFIX}/lib/"*.so* "${SDK}/lib/"; \
    cp -a "${DEPS_PREFIX}/lib/libmp3lame.so"* "${SDK}/lib/"; \
    cp -a "${DEPS_PREFIX}/lib/libfdk-aac.so"* "${SDK}/lib/"; \
    mkdir -p "${SDK}/licenses/system"; \
    : > "${SDK}/BUNDLED-SYSTEM-LIBRARIES.txt"; \
    while IFS= read -r dependency; do \
        [[ -f "${dependency}" ]] || continue; \
        base="$(basename "${dependency}")"; \
        case "${base}" in \
            linux-vdso.so.*|ld-linux*.so.*|libc.so.*|libm.so.*|libpthread.so.*|libdl.so.*|librt.so.*|libresolv.so.*) continue ;; \
        esac; \
        case "${dependency}" in \
            "${PREFIX}"/*|"${DEPS_PREFIX}"/*) continue ;; \
        esac; \
        cp -Lf "${dependency}" "${SDK}/lib/${base}"; \
        package="$(dpkg-query -S "${dependency}" 2>/dev/null | head -n1 | cut -d: -f1 || true)"; \
        printf '%s <- %s (%s)\n' "${base}" "${package:-unknown-package}" "${dependency}" >> "${SDK}/BUNDLED-SYSTEM-LIBRARIES.txt"; \
        if [[ -n "${package}" && -f "/usr/share/doc/${package}/copyright" ]]; then \
            cp -Lf "/usr/share/doc/${package}/copyright" "${SDK}/licenses/system/${package}.copyright" || true; \
        fi; \
    done < <({ lddtree -l "${PREFIX}/bin/ffmpeg"; lddtree -l "${PREFIX}/bin/ffprobe"; } | sort -u); \
    cp -a "${PREFIX}/include/." "${SDK}/include/"; \
    cp -a "${DEPS_PREFIX}/include/." "${SDK}/include/"; \
    cp -a "${PREFIX}/lib/pkgconfig/." "${SDK}/lib/pkgconfig/"; \
    cp -a "${DEPS_PREFIX}/lib/pkgconfig/." "${SDK}/lib/pkgconfig/"; \
    cp -a "${SRC_DIR}/SOURCE-SHA256SUMS.txt" "${SDK}/"; \
    cp -a "${PREFIX}/FFMPEG-CONFIGURE-COMMAND.txt" "${SDK}/"; \
    cp -a "${SRC_DIR}/ffmpeg-${FFMPEG_VERSION}/LICENSE.md" "${SDK}/licenses/ffmpeg/"; \
    cp -a "${SRC_DIR}/ffmpeg-${FFMPEG_VERSION}/COPYING.LGPLv2.1" "${SDK}/licenses/ffmpeg/"; \
    cp -a "${SRC_DIR}/ffmpeg-${FFMPEG_VERSION}/COPYING.LGPLv3" "${SDK}/licenses/ffmpeg/"; \
    if [[ -f "${SRC_DIR}/lame-${LAME_VERSION}/COPYING" ]]; then \
        cp -a "${SRC_DIR}/lame-${LAME_VERSION}/COPYING" "${SDK}/licenses/lame/"; \
    fi; \
    if [[ -f "${SRC_DIR}/lame-${LAME_VERSION}/LICENSE" ]]; then \
        cp -a "${SRC_DIR}/lame-${LAME_VERSION}/LICENSE" "${SDK}/licenses/lame/"; \
    fi; \
    cp -a "${SRC_DIR}/fdk-aac-${FDK_AAC_VERSION}/NOTICE" "${SDK}/licenses/fdk-aac/"; \
    printf '%s\n' \
        'IMPORTANT: FFmpeg + FDK-AAC BUILD' \
        '' \
        'This bundle was configured with --enable-libfdk-aac and --enable-nonfree.' \
        'Treat it as private/internal unless a qualified review confirms that the' \
        'intended distribution satisfies FFmpeg, FDK-AAC and applicable patent terms.' \
        > "${SDK}/LICENSE-WARNING.txt"; \
    printf '%s\n' \
        "FFmpeg version: ${FFMPEG_VERSION}" \
        "LAME version: ${LAME_VERSION}" \
        "FDK-AAC version: ${FDK_AAC_VERSION}" \
        'Build container: Debian 12 slim' \
        'Build profile: audio-only + HTTP/HTTPS/ICY/HLS + Icecast output; no real video encoders/decoders' \
        "Target architecture: ${WB_ARCH}" \
        "Target profile: ${TARGET_PROFILE}" \
        "Target CPU: ${TARGET_CPU:-auto}" \
        "FFmpeg compiler flags: ${TARGET_CFLAGS}" \
        "Dependency compiler flags: ${TARGET_DEPS_CFLAGS:-toolchain-default}" \
        "Builder script revision: 7.1.5-for-web-broadcaster-r13" \
        "Build date (UTC): $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        > "${SDK}/BUILD-INFO.txt"; \
    cp -a "${SDK}" "${RUNTIME}"; \
    rm -rf "${RUNTIME}/include" "${RUNTIME}/lib/pkgconfig"

# Make pkg-config files relocatable.
RUN set -Eeuxo pipefail; \
    BASE="wb-ffmpeg-${FFMPEG_VERSION}-${WB_ARCH}"; \
    SDK="${OUT_DIR}/${BASE}-sdk"; \
    shopt -s nullglob; \
    for pc in "${SDK}/lib/pkgconfig/"*.pc; do \
        sed -i \
            -e 's|^prefix=.*|prefix=${pcfiledir}/../..|' \
            -e 's|^exec_prefix=.*|exec_prefix=${prefix}|' \
            -e 's|^libdir=.*|libdir=${prefix}/lib|' \
            -e 's|^includedir=.*|includedir=${prefix}/include|' \
            "${pc}"; \
    done

# Patch both bundles. Bash + find -print0 avoids the old dash/read -d issue.
RUN set -Eeuxo pipefail; \
    BASE="wb-ffmpeg-${FFMPEG_VERSION}-${WB_ARCH}"; \
    for BUNDLE in "${OUT_DIR}/${BASE}-sdk" "${OUT_DIR}/${BASE}-runtime"; do \
        patchelf --set-rpath '$ORIGIN/../lib' "${BUNDLE}/bin/ffmpeg"; \
        patchelf --set-rpath '$ORIGIN/../lib' "${BUNDLE}/bin/ffprobe"; \
        while IFS= read -r -d '' library; do \
            if file "${library}" | grep -q 'ELF'; then \
                patchelf --set-rpath '$ORIGIN' "${library}"; \
            fi; \
        done < <(find "${BUNDLE}/lib" -maxdepth 1 -type f -name '*.so*' -print0); \
        strip --strip-unneeded "${BUNDLE}/bin/ffmpeg" "${BUNDLE}/bin/ffprobe"; \
        while IFS= read -r -d '' library; do \
            if file "${library}" | grep -q 'ELF'; then \
                strip --strip-unneeded "${library}"; \
            fi; \
        done < <(find "${BUNDLE}/lib" -maxdepth 1 -type f -name '*.so*' -print0); \
    done

# Runtime diagnostics, codec tests, local URL/HLS tests, and Icecast checks. Every test writes
# its own log and prints that log on failure, so Docker no longer hides the
# actual FFmpeg error behind a numeric exit status such as 234.
RUN set -Eeuxo pipefail; \
    BASE="wb-ffmpeg-${FFMPEG_VERSION}-${WB_ARCH}"; \
    BUNDLE="${OUT_DIR}/${BASE}-runtime"; \
    FFMPEG="${BUNDLE}/bin/ffmpeg"; \
    FFPROBE="${BUNDLE}/bin/ffprobe"; \
    TEST_DIR=/tmp/wb-audio-tests; \
    mkdir -p "${TEST_DIR}"; \
    run_test() { \
        local name="$1"; shift; \
        local log="${TEST_DIR}/${name}.log"; \
        echo "===== SELFTEST: ${name} ====="; \
        set +e; \
        env -u LD_LIBRARY_PATH "$@" >"${log}" 2>&1; \
        local status=$?; \
        set -e; \
        if [[ ${status} -ne 0 ]]; then \
            echo "SELFTEST FAILED: ${name} (exit ${status})" >&2; \
            cat "${log}" >&2 || true; \
            exit 1; \
        fi; \
        cat "${log}" || true; \
    }; \
    { \
        echo '=== ffmpeg RUNPATH ==='; \
        patchelf --print-rpath "${FFMPEG}"; \
        echo '=== ffmpeg dependencies ==='; \
        ldd "${FFMPEG}"; \
        echo '=== ffprobe dependencies ==='; \
        ldd "${FFPROBE}"; \
    } | tee "${BUNDLE}/RUNTIME-DEPENDENCIES.txt"; \
    if grep -q 'not found' "${BUNDLE}/RUNTIME-DEPENDENCIES.txt"; then \
        echo 'A runtime dependency is missing.' >&2; \
        exit 1; \
    fi; \
    while IFS= read -r -d '' library; do \
        if file "${library}" | grep -q 'ELF'; then \
            if ldd "${library}" | tee /tmp/current-library-ldd.txt | grep -q 'not found'; then \
                echo "Missing dependency for ${library}:" >&2; \
                cat /tmp/current-library-ldd.txt >&2; \
                exit 1; \
            fi; \
        fi; \
    done < <(find "${BUNDLE}/lib" -maxdepth 1 -type f -name '*.so*' -print0); \
    run_test version "${FFMPEG}" -hide_banner -version; \
    cp "${TEST_DIR}/version.log" "${BUNDLE}/FFMPEG-VERSION.txt"; \
    run_test buildconf "${FFMPEG}" -hide_banner -buildconf; \
    cp "${TEST_DIR}/buildconf.log" "${BUNDLE}/FFMPEG-BUILDCONF.txt"; \
    run_test encoders "${FFMPEG}" -hide_banner -encoders; \
    run_test decoders "${FFMPEG}" -hide_banner -decoders; \
    run_test protocols "${FFMPEG}" -hide_banner -protocols; \
    run_test demuxers "${FFMPEG}" -hide_banner -demuxers; \
    run_test muxers "${FFMPEG}" -hide_banner -muxers; \
    cp "${TEST_DIR}/encoders.log" /tmp/encoders.txt; \
    cp "${TEST_DIR}/decoders.log" /tmp/decoders.txt; \
    cp "${TEST_DIR}/protocols.log" /tmp/protocols.txt; \
    cp "${TEST_DIR}/demuxers.log" /tmp/demuxers.txt; \
    cp "${TEST_DIR}/muxers.log" /tmp/muxers.txt; \
    grep -q 'libmp3lame' /tmp/encoders.txt; \
    grep -q 'libfdk_aac' /tmp/encoders.txt; \
    grep -q 'pcm_s16le' /tmp/decoders.txt; \
    for protocol in http https tcp tls httpproxy crypto icecast; do \
        grep -Eq "^[[:space:]]*${protocol}[[:space:]]*$" /tmp/protocols.txt || { \
            echo "Missing protocol: ${protocol}" >&2; \
            echo 'Available protocols:' >&2; cat /tmp/protocols.txt >&2; exit 1; \
        }; \
    done; \
    for demuxer in s16le hls mpegts; do \
        grep -Eq "^[[:space:]]*D[[:space:]]+${demuxer}([[:space:]]|$)" /tmp/demuxers.txt || { \
            echo "Missing demuxer: ${demuxer}" >&2; cat /tmp/demuxers.txt >&2; exit 1; \
        }; \
    done; \
    for muxer in s16le mp3 adts hls mpegts; do \
        grep -Eq "^[[:space:]]*E[[:space:]]+${muxer}([[:space:]]|$)" /tmp/muxers.txt || { \
            echo "Missing muxer: ${muxer}" >&2; cat /tmp/muxers.txt >&2; exit 1; \
        }; \
    done; \
    # FFmpeg may auto-enable wrapped_avframe when avfilter is enabled. It is an \
    # internal frame wrapper, not a usable video codec, so allow only that \
    # pseudo-codec and fail on every other video encoder/decoder. \
    if awk '$1 ~ /^V/ && $2 != "=" && $2 != "wrapped_avframe" { found=1; print } END { exit !found }' /tmp/encoders.txt > /tmp/video-encoders.txt; then \
        echo 'Unexpected real video encoder found in audio-only build:' >&2; \
        cat /tmp/video-encoders.txt >&2; \
        exit 1; \
    fi; \
    if awk '$1 ~ /^V/ && $2 != "=" && $2 != "wrapped_avframe" { found=1; print } END { exit !found }' /tmp/decoders.txt > /tmp/video-decoders.txt; then \
        echo 'Unexpected real video decoder found in audio-only build:' >&2; \
        cat /tmp/video-decoders.txt >&2; \
        exit 1; \
    fi; \
    head -c $((44100 * 2 * 2 * 4)) /dev/zero > "${TEST_DIR}/silence-s16le-stereo.raw"; \
    run_test encode-mp3 "${FFMPEG}" -hide_banner -loglevel error -y \
        -f s16le -ar 44100 -ac 2 -i "${TEST_DIR}/silence-s16le-stereo.raw" \
        -c:a libmp3lame -b:a 128k "${TEST_DIR}/test.mp3"; \
    run_test decode-mp3-to-s16le "${FFMPEG}" -nostdin -hide_banner -loglevel error -y \
        -i "${TEST_DIR}/test.mp3" -vn -sn -dn \
        -ac 2 -ar 44100 -acodec pcm_s16le -f s16le \
        "${TEST_DIR}/decoded-s16le-stereo.raw"; \
    test -s "${TEST_DIR}/decoded-s16le-stereo.raw"; \
    run_test encode-aac-lc "${FFMPEG}" -hide_banner -loglevel error -y \
        -f s16le -ar 44100 -ac 2 -i "${TEST_DIR}/silence-s16le-stereo.raw" \
        -c:a libfdk_aac -profile:a aac_low -b:a 128k -f adts \
        "${TEST_DIR}/test-aac-lc.aac"; \
    run_test encode-he-aac "${FFMPEG}" -hide_banner -loglevel error -y \
        -f s16le -ar 44100 -ac 2 -i "${TEST_DIR}/silence-s16le-stereo.raw" \
        -c:a libfdk_aac -profile:a aac_he -b:a 64k \
        "${TEST_DIR}/test-he-aac.m4a"; \
    run_test encode-he-aac-v2 "${FFMPEG}" -hide_banner -loglevel error -y \
        -f s16le -ar 44100 -ac 2 -i "${TEST_DIR}/silence-s16le-stereo.raw" \
        -c:a libfdk_aac -profile:a aac_he_v2 -b:a 32k \
        "${TEST_DIR}/test-he-aac-v2.m4a"; \
    test -s "${TEST_DIR}/test.mp3"; \
    test -s "${TEST_DIR}/test-aac-lc.aac"; \
    test -s "${TEST_DIR}/test-he-aac.m4a"; \
    test -s "${TEST_DIR}/test-he-aac-v2.m4a"; \
    mkdir -p "${TEST_DIR}/hls"; \
    run_test create-audio-hls "${FFMPEG}" -hide_banner -loglevel error -y \
        -f s16le -ar 44100 -ac 2 -i "${TEST_DIR}/silence-s16le-stereo.raw" \
        -c:a libfdk_aac -profile:a aac_low -b:a 96k \
        -f hls -hls_segment_type mpegts -hls_time 1 -hls_list_size 0 \
        -hls_segment_filename "${TEST_DIR}/hls/segment-%03d.ts" \
        "${TEST_DIR}/hls/radio.m3u8"; \
    python3 -m http.server 18080 --bind 127.0.0.1 --directory "${TEST_DIR}" \
        >"${TEST_DIR}/http-server.log" 2>&1 & \
    HTTP_PID=$!; \
    trap 'kill "${HTTP_PID}" >/dev/null 2>&1 || true' EXIT; \
    for attempt in {1..20}; do \
        curl -fsS "http://127.0.0.1:18080/test.mp3" -o /dev/null && break; \
        sleep 0.2; \
    done; \
    run_test probe-http-mp3 "${FFPROBE}" -v error \
        -show_entries format=format_name,duration \
        -of default=noprint_wrappers=1 \
        "http://127.0.0.1:18080/test.mp3"; \
    run_test probe-http-hls "${FFPROBE}" -v error \
        -protocol_whitelist file,http,https,tcp,tls,crypto \
        -show_entries format=format_name,duration \
        -of default=noprint_wrappers=1 \
        "http://127.0.0.1:18080/hls/radio.m3u8"; \
    kill "${HTTP_PID}" >/dev/null 2>&1 || true; \
    trap - EXIT; \
    cp "${TEST_DIR}/probe-http-mp3.log" "${BUNDLE}/SELFTEST.txt"; \
    printf '%s\n' \
        'Audio-only encoder/decoder list: OK' \
        'No real video encoder or decoder enabled (wrapped_avframe pseudo-codec allowed): OK' \
        'libmp3lame encoder: OK' \
        'libfdk_aac AAC-LC encoder: OK' \
        'libfdk_aac HE-AAC encoder: OK' \
        'libfdk_aac HE-AACv2 encoder: OK' \
        'HTTP direct MP3 input: OK' \
        'HTTP HLS audio input: OK' \
        'HTTPS/TLS protocols compiled: OK' \
        'Icecast output protocol compiled: OK' \
        'Relocatable RUNPATH without LD_LIBRARY_PATH: OK' \
        >> "${BUNDLE}/SELFTEST.txt"; \
    cp "${BUNDLE}/FFMPEG-VERSION.txt" "${OUT_DIR}/${BASE}-sdk/"; \
    cp "${BUNDLE}/FFMPEG-BUILDCONF.txt" "${OUT_DIR}/${BASE}-sdk/"; \
    cp "${BUNDLE}/RUNTIME-DEPENDENCIES.txt" "${OUT_DIR}/${BASE}-sdk/"; \
    cp "${BUNDLE}/SELFTEST.txt" "${OUT_DIR}/${BASE}-sdk/"

COPY README.runtime.txt /tmp/README.runtime.txt
COPY README.sdk.txt /tmp/README.sdk.txt

RUN set -Eeuxo pipefail; \
    BASE="wb-ffmpeg-${FFMPEG_VERSION}-${WB_ARCH}"; \
    RUNTIME="${OUT_DIR}/${BASE}-runtime"; \
    SDK="${OUT_DIR}/${BASE}-sdk"; \
    cp /tmp/README.runtime.txt "${RUNTIME}/README.txt"; \
    cp /tmp/README.sdk.txt "${SDK}/README.txt"; \
    tar -C "${OUT_DIR}" -czf "${OUT_DIR}/${BASE}-runtime.tar.gz" "${BASE}-runtime"; \
    tar -C "${OUT_DIR}" -czf "${OUT_DIR}/${BASE}-sdk.tar.gz" "${BASE}-sdk"; \
    sha256sum "${OUT_DIR}/${BASE}-runtime.tar.gz" > "${OUT_DIR}/${BASE}-runtime.tar.gz.sha256"; \
    sha256sum "${OUT_DIR}/${BASE}-sdk.tar.gz" > "${OUT_DIR}/${BASE}-sdk.tar.gz.sha256"; \
    { \
        echo 'Uncompressed sizes:'; \
        du -sh "${RUNTIME}" "${SDK}"; \
        echo; \
        echo 'Compressed archive sizes:'; \
        du -sh "${OUT_DIR}/${BASE}-runtime.tar.gz" "${OUT_DIR}/${BASE}-sdk.tar.gz"; \
    } | tee "${OUT_DIR}/${BASE}-SIZE-REPORT.txt"

FROM debian:12-slim AS export
COPY --from=builder /out/ /out/
DOCKERFILE

cat > "${BUILD_DIR}/README.runtime.txt" <<'README'
Web Broadcaster FFmpeg 7.1.5 audio + URL + Icecast runtime
===============================================

bin/ contains relocatable ffmpeg and ffprobe tools.
lib/ contains their application-local shared libraries, including
libmp3lame and libfdk-aac.

No system FFmpeg installation is required. No real video encoder or decoder is
included. HTTP, HTTPS, ICY radio streams, audio-only HLS, and Icecast output are enabled. The
runtime is intended for Debian 12 on the architecture shown in BUILD-INFO.txt.

Test:
  ./bin/ffmpeg -version
  ./bin/ffmpeg -encoders
  ./bin/ffmpeg -decoders

Read LICENSE-WARNING.txt before distribution.
README

cat > "${BUILD_DIR}/README.sdk.txt" <<'README'
Web Broadcaster FFmpeg 7.1.5 audio + URL + Icecast SDK
===========================================

This package contains the complete runtime plus C headers and relocatable
pkg-config metadata for compiling the Web Broadcaster native audio engine.

Example:
  export PKG_CONFIG_PATH="$(pwd)/lib/pkgconfig"
  cc engine.c -o wb_audio_engine \
    $(pkg-config --cflags --libs libavformat libavcodec libavfilter libavutil libswresample) \
    -Wl,-rpath,'$ORIGIN/lib'

The final Web Broadcaster installation normally needs only the runtime files,
not include/ or lib/pkgconfig/.

Read LICENSE-WARNING.txt before distribution.
README

target_settings() {
    local target="$1"
    case "${target}" in
        amd64)
            TARGET_PLATFORM="linux/amd64"
            TARGET_ARCH_LABEL="amd64"
            TARGET_PROFILE="generic-amd64-r9-compatible"
            TARGET_CPU=""
            TARGET_CFLAGS="-O2 -fPIC -ffunction-sections -fdata-sections"
            # The established r9 script did not override LAME/FDK-AAC CFLAGS.
            TARGET_DEPS_CFLAGS=""
            ;;
        rpi5)
            TARGET_PLATFORM="linux/arm64"
            TARGET_ARCH_LABEL="arm64-rpi5"
            TARGET_PROFILE="raspberry-pi-5-cortex-a76"
            TARGET_CPU="cortex-a76"
            TARGET_CFLAGS="-O3 -mcpu=cortex-a76 -mtune=cortex-a76 -fPIC -ffunction-sections -fdata-sections"
            TARGET_DEPS_CFLAGS="${TARGET_CFLAGS}"
            ;;
        *)
            fail "Internal error: unsupported native target ${target}."
            ;;
    esac
}

build_one_target() {
    local target="$1"
    target_settings "${target}"

    local base_name="wb-ffmpeg-${FFMPEG_VERSION}-${TARGET_ARCH_LABEL}"
    local runtime_name="${base_name}-runtime"
    local sdk_name="${base_name}-sdk"
    local runtime_archive="${runtime_name}.tar.gz"
    local sdk_archive="${sdk_name}.tar.gz"
    local size_report="${base_name}-SIZE-REPORT.txt"
    local image_tag="wb-ffmpeg-builder:${FFMPEG_VERSION}-${TARGET_ARCH_LABEL}-for-web-broadcaster-r13"
    local container_name="wb-ffmpeg-export-${target}-$$"

    rm -rf -- \
        "${DIST_DIR:?}/${runtime_name}" \
        "${DIST_DIR:?}/${sdk_name}" \
        "${DIST_DIR:?}/${runtime_archive}" \
        "${DIST_DIR:?}/${runtime_archive}.sha256" \
        "${DIST_DIR:?}/${sdk_archive}" \
        "${DIST_DIR:?}/${sdk_archive}.sha256" \
        "${DIST_DIR:?}/${size_report}"

    local -a build_args=(
        --progress=plain
        --platform "${TARGET_PLATFORM}"
        --load
        --tag "${image_tag}"
        --build-arg "FFMPEG_VERSION=${FFMPEG_VERSION}"
        --build-arg "LAME_VERSION=${LAME_VERSION}"
        --build-arg "FDK_AAC_VERSION=${FDK_AAC_VERSION}"
        --build-arg "WB_ARCH=${TARGET_ARCH_LABEL}"
        --build-arg "TARGET_PROFILE=${TARGET_PROFILE}"
        --build-arg "TARGET_CPU=${TARGET_CPU}"
        --build-arg "TARGET_CFLAGS=${TARGET_CFLAGS}"
        --build-arg "TARGET_DEPS_CFLAGS=${TARGET_DEPS_CFLAGS}"
        --file "${BUILD_DIR}/Dockerfile"
    )

    if [[ "${NO_CACHE}" == "1" ]]; then
        build_args+=(--no-cache)
    fi

    CURRENT_IMAGE="${image_tag}"
    CURRENT_CONTAINER="${container_name}"

    log "Building ${target}: FFmpeg ${FFMPEG_VERSION}, ${TARGET_PROFILE}, Docker platform ${TARGET_PLATFORM}"
    docker buildx build "${build_args[@]}" "${BUILD_DIR}"

    log "Exporting ${target} artifacts to ${DIST_DIR}"
    docker create --name "${container_name}" "${image_tag}" >/dev/null
    docker cp "${container_name}:/out/." "${DIST_DIR}/"
    docker rm "${container_name}" >/dev/null
    CURRENT_CONTAINER=""

    [[ -x "${DIST_DIR}/${runtime_name}/bin/ffmpeg" ]] || fail "Exported ${target} runtime ffmpeg binary is missing."
    [[ -f "${DIST_DIR}/${runtime_archive}" ]] || fail "${target} runtime archive is missing."
    [[ -f "${DIST_DIR}/${sdk_archive}" ]] || fail "${target} SDK archive is missing."
    [[ -f "${DIST_DIR}/${size_report}" ]] || fail "${target} size report is missing."

    log "${target} build completed successfully"
    cat "${DIST_DIR}/${size_report}"
    printf '\n%s\n' \
        "Target:            ${target}" \
        "Runtime directory: ${DIST_DIR}/${runtime_name}" \
        "Runtime archive:   ${DIST_DIR}/${runtime_archive}" \
        "SDK directory:     ${DIST_DIR}/${sdk_name}" \
        "SDK archive:       ${DIST_DIR}/${sdk_archive}" \
        "Test command:      ${DIST_DIR}/${runtime_name}/bin/ffmpeg -version"

    if [[ "${KEEP_IMAGE}" != "1" ]]; then
        docker image rm "${image_tag}" >/dev/null 2>&1 || true
        CURRENT_IMAGE=""
    fi
}

if grep -Eq -- 'ffmpeg-8\.1\.2' "${BUILD_DIR}/Dockerfile"; then
    fail "Internal sanity check failed: the generated Dockerfile contains an old FFmpeg 8.1.2 setting."
fi

grep -q 'ARG FFMPEG_VERSION' "${BUILD_DIR}/Dockerfile" || fail "Generated Dockerfile is incomplete."
grep -q 'ARG TARGET_PROFILE' "${BUILD_DIR}/Dockerfile" || fail "Generated Dockerfile has no target-profile support."

log "Script revision: ${SCRIPT_REVISION}"
log "Script path: ${BASH_SOURCE[0]}"
log "Detected host: ${HOST_MACHINE}"
log "Selected native target: ${TARGET}"

build_one_target "${TARGET}"

warn "Do not redistribute the FFmpeg + FDK-AAC output without appropriate legal review."
