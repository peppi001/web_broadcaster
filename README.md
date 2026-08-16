# Web Broadcaster

<img width="887" height="477" alt="web_broadcaster_small" src="https://github.com/user-attachments/assets/c430886a-6fe1-4346-be73-a8d22036a16e" />

**Web Broadcaster** is a self-contained, browser-operated, multi-station radio automation and Icecast broadcasting system for Linux.

The project combines a Python/Flask control application with a dedicated native C audio daemon. The Python application owns the web interface, authentication, station configuration, SQLite persistence, queue management, AutoDJ, scheduling, history, user management and runtime orchestration. The native daemon owns the real-time audio path: direct FFmpeg/libav decoding, A/B decks, cueing, seeking, fades, transitions, mixing, in-process SoundSolution DSP, encoding and Icecast transport.

The current release is **Web Broadcaster v6042**.

> **Repository model**
>
> - This Git repository contains the unpacked, architecture-neutral Web Broadcaster source code, tests and documentation.
> - The complete native buildkit is published as a versioned Release asset.
> - FFmpeg and SoundSolution are fixed, prebuilt native dependencies. They are not stored in normal Git history and are not rebuilt by the regular Web Broadcaster build.
> - The recommended public deployment is nginx + HTTPS with Web Broadcaster listening only on loopback.

---

## Table of contents

- [Project status](#project-status)
- [v6042 release summary](#v6042-release-summary)
- [Main features](#main-features)
- [Design goals](#design-goals)
- [System architecture](#system-architecture)
- [Deployment architecture](#deployment-architecture)
- [Audio path](#audio-path)
- [Process model](#process-model)
- [Supported platforms](#supported-platforms)
- [Component versions](#component-versions)
- [Repository layout](#repository-layout)
- [Source repository and Release assets](#source-repository-and-release-assets)
- [Building from the complete buildkit](#building-from-the-complete-buildkit)
- [Build prerequisites](#build-prerequisites)
- [Build process](#build-process)
- [Build outputs](#build-outputs)
- [Final package layout](#final-package-layout)
- [Installing and starting Web Broadcaster](#installing-and-starting-web-broadcaster)
- [Deployment modes](#deployment-modes)
- [First-run setup](#first-run-setup)
- [Runtime configuration](#runtime-configuration)
- [Web security model](#web-security-model)
- [Logging and diagnostics](#logging-and-diagnostics)
- [Runtime data and backup](#runtime-data-and-backup)
- [Testing](#testing)
- [Integrity and reproducibility](#integrity-and-reproducibility)
- [Release workflow](#release-workflow)
- [Development workflow](#development-workflow)
- [Troubleshooting](#troubleshooting)
- [Operational and security notes](#operational-and-security-notes)
- [Known boundaries](#known-boundaries)
- [Dependency manifest](#dependency-manifest)
- [License and redistribution](#license-and-redistribution)
- [Version history](#version-history)

---

## Project status

| Item | Current state |
|---|---|
| Web Broadcaster version | `6042` |
| Native daemon protocol/version | `6042` |
| Release buildkit revision | `build_v6042_linux-r1-id3-metadata-noise-class-filter` |
| AMD64 build target | Debian 12, generic x86-64/SSE2 baseline |
| ARM64 build target | Raspberry Pi 5, AArch64, Cortex-A76 |
| Audio backend | Native C daemon with direct FFmpeg 7.1.5 library integration |
| DSP backend | In-process SoundSolution Native shared library |
| Streaming output | Native Icecast source transport |
| Web server | Cheroot 11.1.2 WSGI server |
| Python packaging | PyInstaller `onedir` |
| Default HTTP port | `15000` |
| Default deployment mode | Trusted LAN, HTTP, `0.0.0.0:15000` |
| Recommended public mode | nginx HTTPS reverse proxy, backend `127.0.0.1:15000` |
| Database | SQLite |

v6042 preserves the established flat final `bin/` runtime contract while adding the current production web-security/deployment model and generalized suppression of harmless malformed-ID3 parser noise. The native audio PCM path, SoundSolution DSP path, encoder path, queue, scheduler and database schema are unchanged by the v6040-v6042 console-filter work.

---

## v6042 release summary

The current v6042 source includes all changes made after the original v6024 dual-platform buildkit release. Important user-visible and operational changes include:

- smoother Studio playback-position display using a monotonic browser clock without increasing backend status-poll load;
- CSS-only crossed hammer/screwdriver Configure icons and removal of obsolete SVG assets;
- public-Internet hardening of the Flask application, including deny-by-default authentication, CSRF protection, secure session handling, rate limiting, trusted-host support and first-run setup tokens;
- replacement of the Flask/Werkzeug development server with Cheroot;
- optional direct Cheroot TLS support;
- preferred nginx reverse-proxy deployment with Web Broadcaster bound to `127.0.0.1` and nginx terminating HTTPS;
- dedicated deployment documentation for trusted-LAN and nginx/Certbot/Let's Encrypt operation;
- embedded local-file artist/title metadata taking priority over filename parsing, with filename parsing retained as fallback;
- hardened trusted-host error handling under Flask 3.1;
- deterministic native regression synchronization on loaded build hosts;
- generalized filtering of known harmless FFmpeg 7.1.5 malformed-ID3 metadata diagnostics while preserving genuine libav/container/decoder/encoder/I/O errors.

The complete chronological history remains in `version.txt`.

---

## Main features

### Multi-station operation

- Multiple independently configured stations.
- Station-specific databases, queues, playback state, output settings and AutoDJ state.
- Independent native runtime identities and station-scoped lifecycle management.
- Station creation, selection, rename and deletion from the browser interface.

### Browser-based studio

- Dashboard and Studio views.
- Resizable/rearrangeable Studio panels with saved layout state.
- Current deck, queue, history, playlist/library, encoders and automation controls.
- Server-Sent Events for queue, history, console and runtime UI updates.
- Smoothed local playback-position display anchored to authoritative native status.
- Minimal normal console output.

### Native audio engine

- Direct FFmpeg/libav integration; no live `ffmpeg` subprocesses.
- Local file, HTTP, HTTPS and HLS input handling.
- Two native A/B decks.
- Preloading and candidate reuse with identity-safe lifecycle rules.
- Cue-in and cue-out handling.
- Seek support.
- Fade-in, fade-out and transition timing.
- Crossfade and trailing-silence handling.
- Native PCM analysis and duration verification.
- Corrupt-input and early-EOF recovery.
- Stable pause/resume and stop/start lifecycle handling.

### AutoDJ and queue management

- Database-backed queue.
- Drag-and-drop queue reordering.
- Manual queue additions from the media library.
- HTTP/HTTPS URL items.
- Category-based AutoDJ rotation.
- Recent-history and repeat protection.
- Queue refill and startup bootstrap.
- Playback history.
- Native event-driven queue removal and history commits.

### Scheduling and scripts

- Scheduled rules.
- Enable, disable, edit and delete scheduler rules.
- Script creation, editing, start and stop controls.
- Automatic on-air script start and off-air script stop behavior.
- Protection against scheduled scripts interrupting an already active URL source.
- `.wbs` script support.

### Encoding and streaming

- Multiple configured encoder/output branches.
- MP3 through `libmp3lame`.
- AAC-LC, HE-AAC and HE-AACv2 through `libfdk_aac`.
- Native Icecast source transport.
- Per-output connection state, encoded FIFO, reconnect handling and metadata state.
- Persistent Icecast stream identity fields.
- Metadata updates without spawning external encoder processes.
- Embedded artist/title tags preferred for local media; filename parsing remains a fallback.

### SoundSolution DSP

- In-process SoundSolution processing through `libsoundsolution.so.2`.
- One independent DSP context per station.
- Station-specific `.dat` configuration.
- Live dry/DSP source switching without restarting the encoder or reconnecting Icecast.
- No DSP subprocess, pipe, FIFO, Wine, AppImage or FUSE dependency.
- AMD64 and Raspberry Pi 5 use architecture-specific validated SoundSolution runtimes.

### Users and persistence

- Protected initial setup flow.
- User login and logout.
- User creation, password change and user deletion.
- Global user/station registry database.
- Station-local SQLite state.
- Persistent settings, queues, history, rotations, scheduler rules and encoder configurations.

---

## Design goals

Web Broadcaster follows several strict design principles.

### 1. Direct native audio processing

The live audio path does not depend on Liquidsoap or FFmpeg command-line subprocesses. Decoding, conversion, mixing, DSP, encoding and Icecast output are performed by one native daemon linked directly to private FFmpeg and SoundSolution libraries.

### 2. Private, relocatable runtime

The final application carries its required native libraries in its own `bin/` directory. The native daemon uses an `$ORIGIN` RPATH in the final package, preventing accidental use of a system-wide FFmpeg or SoundSolution installation.

### 3. Deterministic station lifecycle

Track loading, audible start, transition, EOF, history and queue ownership use explicit queue IDs and slot tokens. The design avoids relying on filenames alone, which is essential when the same media item appears repeatedly in a queue.

### 4. Real-time isolation

Network and file decoding do not run in the real-time mixer loop. Decoder workers feed native PCM buffers; the mixer owns deterministic audio timing.

### 5. Reproducible native dependencies

FFmpeg and SoundSolution are fixed release inputs, identified by exact versions and SHA-256 checksums. The normal Web Broadcaster build stages these packages but does not rebuild them.

### 6. Minimal target-system dependencies

The final package includes the Python runtime through PyInstaller and includes private FFmpeg and SoundSolution shared libraries. The target system only needs compatible normal Linux runtime libraries, including glibc and GnuTLS dependencies used by the packaged native components.

### 7. Fail-closed public deployment

Public-Internet mode requires either direct HTTPS or an explicitly trusted reverse proxy. Reverse-proxy trust is accepted only with a loopback listener. The launcher rejects a public-mode plain-HTTP configuration.

### 8. Conservative release changes

A released tag is immutable. Any change to source, tests, build scripts or packaged runtime files requires a new Web Broadcaster version.

---

## System architecture

```text
Browser
   |
   | HTTP on trusted LAN
   | or HTTPS through nginx
   v
Cheroot / Flask Web Broadcaster
   ├── authentication and users
   ├── CSRF/session/security policy
   ├── station registry
   ├── SQLite persistence
   ├── media library and categories
   ├── queue and history
   ├── AutoDJ
   ├── scheduler and scripts
   ├── encoder configuration
   └── native-engine lifecycle
          |
          | local Unix socket protocol
          v
web_broadcaster_engine
   ├── libavformat
   ├── libavcodec
   ├── libswresample
   ├── A/B decks and PCM buffers
   ├── native timing and mixer
   ├── SoundSolution DSP contexts
   ├── MP3/AAC encoders
   └── native Icecast outputs
```

### Responsibility split

| Python application | Native daemon |
|---|---|
| Web UI and API | File/network decoding |
| Authentication and web security | PCM conversion |
| Station registry | A/B deck state |
| SQLite persistence | Cue, seek, fade and transition timing |
| Queue planning | Real-time mixing |
| AutoDJ decisions | SoundSolution DSP processing |
| Scheduler/scripts | MP3/AAC encoding |
| History commits | Icecast transport |
| UI events | Native diagnostics and state |
| Daemon restart/cleanup | Audio-runtime events |

The Python side remains the owner of business logic. The native side remains the authoritative owner of audio timing and audio state.

---

## Deployment architecture

v6042 supports three web deployment arrangements. Two are intended as normal operating modes; direct TLS is available as an alternative.

### Trusted LAN

```text
Trusted LAN browser
        |
        | HTTP :15000
        v
0.0.0.0:15000
        |
        v
Web Broadcaster v6042 / Cheroot
```

This is the default generated `start.sh` mode. It requires no nginx, public domain, Certbot or TLS certificate. Because traffic is plain HTTP, use it only on a trusted private network and do not forward TCP 15000 from the Internet.

### Public Internet through nginx — recommended

```text
Internet
   |
   | HTTPS :443
   v
nginx on the same host
   | TLS termination
   | reverse proxy
   v
127.0.0.1:15000
   |
   v
Web Broadcaster v6042 / Cheroot
```

In this mode nginx is the only public HTTP/HTTPS endpoint. Web Broadcaster listens only on loopback. TCP 15000 must not be publicly forwarded.

The package contains:

```text
docs/DEPLOYMENT_GUIDE.md
docs/NGINX_PUBLIC_HTTPS.md
docs/nginx_web_broadcaster.conf.example
```

The nginx guide documents DNS, TCP 80/443, Certbot, Let's Encrypt, forwarded headers, Server-Sent Events buffering, certificate renewal, upgrades and validation.

### Direct Cheroot TLS — supported alternative

Web Broadcaster can serve HTTPS directly through Cheroot when `HTTPS_MODE="ON"` and valid certificate/private-key files are configured. For an always-on public deployment, nginx on the same host remains the preferred documented architecture.

---

## Audio path

```text
local file / HTTP / HTTPS / HLS
        |
        v
libavformat demuxer
        |
        v
libavcodec audio decoder
        |
        v
libswresample
44.1 kHz / stereo / signed 16-bit little-endian PCM
        |
        v
native A/B deck buffers
        |
        v
cue / seek / fade / crossfade / mixer
        |
        ├── dry PCM
        |
        └── SoundSolution-processed PCM
                |
                v
shared multi-output encoder
        ├── libmp3lame
        └── libfdk_aac
                |
                v
per-output FIFO and native Icecast connection
```

### Canonical internal PCM format

The native processing path normalizes decoded audio to:

```text
Sample rate: 44,100 Hz
Channels:    2 (stereo)
Sample type: signed 16-bit integer
Layout:      interleaved little-endian PCM
```

This is also the format passed to the in-process SoundSolution API.

---

## Process model

A normal packaged installation runs two primary executables:

```text
bin/web_broadcaster
bin/web_broadcaster_engine
```

`bin/web_broadcaster` is the PyInstaller-packaged Python/Flask application served by Cheroot. It automatically starts, verifies, monitors and stops `bin/web_broadcaster_engine`.

The live station path does **not** start:

- `ffmpeg` processes;
- `ffprobe` processes;
- Liquidsoap;
- a SoundSolution helper executable;
- Wine;
- AppImage;
- FUSE.

The complete native audio path exists in `web_broadcaster_engine` through linked shared libraries.

---

## Supported platforms

### Debian 12 AMD64

- Native build host: Debian 12 x86-64.
- Target architecture: generic x86-64 with an SSE2 baseline.
- FFmpeg: generic AMD64 build.
- SoundSolution: Native v6.1.0, `native-binary64-fast` arithmetic mode.
- No AVX, AVX2 or FMA requirement is introduced by the SoundSolution baseline.

### Raspberry Pi 5

- Native build host: a real Raspberry Pi 5.
- Operating system: 64-bit AArch64 Linux.
- CPU target: Cortex-A76.
- FFmpeg: Raspberry Pi 5/Cortex-A76 build.
- SoundSolution: Native v6.0.0 structured-process runtime.
- The builder rejects generic ARM64 systems that are not identified as Raspberry Pi 5.

### Native-only build policy

The official build script intentionally does not provide:

- cross-compilation;
- a generic ARM64 target;
- a build-all mode;
- Windows support;
- macOS support.

Each final package is built and tested natively on its target architecture.

---

## Component versions

| Component | Version / mode |
|---|---|
| Web Broadcaster | `6042` |
| Native audio daemon | `6042` |
| FFmpeg/libav | `7.1.5` |
| FFmpeg runtime ID | `7.1.5-for-web-broadcaster-r13` |
| AMD64 SoundSolution | `6.1.0`, `native-binary64-fast` |
| Raspberry Pi 5 SoundSolution | `6.0.0`, `native-double`, structured-process |
| Flask | `3.1.3` |
| Werkzeug | `3.1.8` |
| python-dotenv | `1.0.1` |
| Flask-Limiter | `4.1.1` |
| Cheroot | `11.1.2` |
| Mutagen | `1.47.0` |
| PyInstaller | `6.21.0` |
| pytest | `8.3.5` |

The build script pins the Python packages used by the official build.

---

## Repository layout

The Git repository contains the unpacked, architecture-neutral Web Broadcaster source.

```text
web-broadcaster/
├── app.py
├── requirements.txt
├── pytest.ini
├── version.txt
├── README.md
│
├── audio_engine/
│   ├── base.py
│   ├── events.py
│   ├── factory.py
│   ├── lifecycle.py
│   ├── native_engine.py
│   ├── protocol.py
│   └── runtime.py
│
├── autodj/
│   └── service.py
│
├── player/
│   └── orchestration.py
│
├── station/
│   └── service.py
│
├── storage/
│   └── playback_repository.py
│
├── native_engine/
│   ├── Makefile
│   ├── README.md
│   ├── include/
│   └── src/
│       ├── audio_analysis.c
│       ├── audio_probe.c
│       ├── daemon.c
│       ├── diagnostics.c
│       ├── engine.c
│       ├── icecast_output.c
│       ├── libav_bridge.c
│       ├── main.c
│       ├── native_timing.c
│       └── protocol.c
│
├── html/
│   ├── base.html
│   ├── broadcaster.html
│   ├── dashboard.html
│   ├── login.html
│   ├── no_stations.html
│   ├── setup.html
│   ├── Layout1_default.pos
│   ├── Layout2_default.pos
│   └── static/
│
├── script/
│   └── time.wbs
│
├── docs/
│   ├── DEPLOYMENT_GUIDE.md
│   ├── NGINX_PUBLIC_HTTPS.md
│   └── nginx_web_broadcaster.conf.example
│
└── tests/
```

### Important source directories

- `audio_engine/` — Python contract and lifecycle integration for the managed native daemon.
- `native_engine/` — C implementation of decoding, timing, mixing, DSP, encoding and Icecast output.
- `autodj/` — AutoDJ selection and repeat-protection service.
- `player/` — playback orchestration.
- `station/` — station lifecycle orchestration.
- `storage/` — queue, history and runtime persistence repository.
- `html/` — all templates and static assets. There are no legacy top-level `templates/` or `static/` directories.
- `docs/` — deployment-mode, nginx, HTTPS and Certbot documentation.
- `tests/` — Python and native integration/regression tests.
- `version.txt` — complete chronological project history. New releases must be appended; old entries must not be deleted.

### Files intentionally absent from Git

The source repository should not contain:

- FFmpeg SDK/runtime archives;
- SoundSolution runtime archives;
- generated native binaries;
- PyInstaller output;
- local databases;
- setup/session secret files;
- logs;
- media libraries;
- credentials or `.env` files;
- TLS private keys;
- final `.tar.gz` packages;
- complete buildkit ZIP files.

These belong in versioned Release assets or local ignored directories.

---

## Source repository and Release assets

The project uses two distribution layers.

### Git repository

The Git repository is the source of truth for:

- Web Broadcaster Python source;
- native C daemon source;
- HTML/CSS/JavaScript assets;
- tests;
- project documentation;
- `README.md`;
- `version.txt`.

### Release assets

The v6042 release artifacts currently have these identities:

| File | SHA-256 |
|---|---|
| `Web_Broadcaster_v6042.zip` | `5811aaf71e9f2a06a23bbed86a2821741454df4ad098be9e090b110b9abb5171` |
| `Web_Broadcaster_v6042_buildkit.zip` | `19b189e886e40862274bd0a44469b8662f81c52cfee8bd6b1d5f57e6596b91be` |
| `build_v6042_linux.sh` | `0dd2759c6e0d825314928a599114169f59ed0bfb066912fc16c4204092a69441` |

The build script is also included inside the complete buildkit. Publishing it separately is convenient for inspection, but the version-matched buildkit remains the authoritative build input.

After successful native builds, a Release may also contain:

```text
Web_Broadcaster_Linux_v6042_amd64.tar.gz
Web_Broadcaster_Linux_v6042_amd64.tar.gz.sha256
Web_Broadcaster_Linux_v6042_arm64_rpi5.tar.gz
Web_Broadcaster_Linux_v6042_arm64_rpi5.tar.gz.sha256
```

The complete buildkit contains the fixed FFmpeg and SoundSolution packages required for native dependency staging. The Python build stage still requires network access to install the pinned Python packages unless a local Python package mirror/cache provides them.

### Current v6042 buildkit identity

```text
File:   Web_Broadcaster_v6042_buildkit.zip
SHA256: 19b189e886e40862274bd0a44469b8662f81c52cfee8bd6b1d5f57e6596b91be
Revision: build_v6042_linux-r1-id3-metadata-noise-class-filter
```

Do not replace a published asset under the same version and filename. Publish a new Web Broadcaster version for any changed released artifact.

---

## Building from the complete buildkit

The Release buildkit is the authoritative and recommended build method.

### 1. Download and verify

Download:

```text
Web_Broadcaster_v6042_buildkit.zip
```

Verify against the published SHA-256:

```bash
sha256sum Web_Broadcaster_v6042_buildkit.zip
```

Expected:

```text
19b189e886e40862274bd0a44469b8662f81c52cfee8bd6b1d5f57e6596b91be
```

### 2. Extract

```bash
unzip Web_Broadcaster_v6042_buildkit.zip
cd Web_Broadcaster_v6042_buildkit
```

### 3. Start the native build

```bash
chmod +x build_v6042_linux.sh
./build_v6042_linux.sh
```

The same command is used on Debian 12 AMD64 and Raspberry Pi 5. The script detects the native architecture and selects the correct dependency set automatically.

### Optional source-test skip

```bash
RUN_SOURCE_TESTS=0 ./build_v6042_linux.sh
```

Skipping tests is intended only for diagnosis. Normal and release builds should keep the default:

```text
RUN_SOURCE_TESTS=1
```

The complete source regression suite is deliberately enabled by default.

---

## Build prerequisites

### Debian 12 AMD64

Install the normal build tools:

```bash
sudo apt update
sudo apt install -y \
  build-essential \
  python3 \
  python3-venv \
  python3-pip \
  unzip \
  tar \
  binutils \
  file \
  ca-certificates
```

The script also expects standard commands such as `ldd`, `sha256sum`, `find`, `sort`, `install` and `readelf`, which are part of a normal Debian build environment.

### Raspberry Pi 5

Use a 64-bit Raspberry Pi OS/Debian-compatible installation and install the same toolset:

```bash
sudo apt update
sudo apt install -y \
  build-essential \
  python3 \
  python3-venv \
  python3-pip \
  unzip \
  tar \
  binutils \
  file \
  ca-certificates
```

The builder reads `/proc/device-tree/model` and requires `Raspberry Pi 5` in the model string.

### Network requirement

The official builder creates an isolated Python virtual environment and installs pinned packages. Network access to the configured Python package index is therefore required unless those packages are already available from a local cache or mirror.

### Optional Node.js

Node.js is not a build dependency. When available, an additional JavaScript execution regression test runs. Without Node.js, that test is reported as skipped.

---

## Build process

The v6042 build script performs the following stages.

1. **Host validation**
   - verifies Linux;
   - identifies AMD64 or Raspberry Pi 5 AArch64;
   - rejects unsupported architectures.

2. **Buildkit integrity check**
   - verifies `SHA256SUMS.txt`;
   - refuses to continue if an included source or dependency archive has changed.

3. **Source extraction**
   - extracts `Web_Broadcaster_v6042.zip`;
   - verifies the architecture-neutral source layout;
   - confirms `APP_VERSION=6042` and native daemon version `6042`.

4. **Dependency selection**
   - selects the matching FFmpeg SDK/runtime;
   - selects the matching SoundSolution runtime;
   - checks package presence and hashes.

5. **Temporary dependency staging**
   - stages FFmpeg headers under `native_engine/ffmpeg_sdk/include/`;
   - stages the diagnostic FFmpeg executable as `bin/ffmpeg` for tests/build checks;
   - stages private FFmpeg shared libraries under `lib/`;
   - stages `libsoundsolution.so.2` and `ss18.dat` under `bin/soundsolution/`;
   - stages the matching SoundSolution API header.

6. **Architecture verification**
   - verifies ELF architecture for FFmpeg, SoundSolution and private libraries;
   - checks component versions and build IDs.

7. **Source-tree native engine build**
   - rebuilds `web_broadcaster_engine`;
   - verifies linked libraries and source-tree RPATH.

8. **SoundSolution smoke test**
   - loads the library through its C API;
   - verifies version and arithmetic mode;
   - loads `ss18.dat`;
   - processes real PCM;
   - validates Raspberry Pi 5 structured-process identity where applicable.

9. **Python build environment**
   - creates `build_work/venv`;
   - installs pinned PyInstaller/web/test dependencies;
   - verifies installed versions.

10. **Regression tests**
    - runs the complete source regression suite by default;
    - shows pytest progress live;
    - writes the complete output to `build_work/source-regression.log`;
    - runs the optional JavaScript execution test when Node.js is available.

11. **Final flat-bin preparation**
    - patches the temporary build tree for the packaged application layout;
    - changes the native daemon final RPATH to `$ORIGIN`;
    - rebuilds the native daemon for the final package.

12. **PyInstaller build**
    - creates an `onedir` application;
    - places the Python runtime under `bin/_internal/`.

13. **Package assembly**
    - copies the application, native daemon and private libraries;
    - copies `html/`, `script/` and `docs/`;
    - generates `start.sh` and package `README.txt`.

14. **Package cleanup**
    - removes build-only files;
    - removes diagnostic FFmpeg/ffprobe command-line binaries from the final application;
    - materializes all symlinks as regular files;
    - refuses to package any remaining symlink.

15. **Final verification**
    - verifies the exact allowed root and `bin/` entries;
    - checks required deployment documentation;
    - checks file modes;
    - verifies SONAME and RPATH;
    - runs `ldd` and confirms private FFmpeg/SoundSolution resolution;
    - runs a packaged native-daemon smoke test;
    - verifies the complete `start.sh` security/deployment contract.

16. **Archive creation**
    - creates the final `.tar.gz`;
    - verifies that the archive contains no symlinks;
    - verifies required deployment documentation in the archive;
    - writes a `.sha256` file.

---

## Build outputs

### AMD64

```text
Web_Broadcaster_Linux_v6042_amd64.tar.gz
Web_Broadcaster_Linux_v6042_amd64.tar.gz.sha256
```

### Raspberry Pi 5

```text
Web_Broadcaster_Linux_v6042_arm64_rpi5.tar.gz
Web_Broadcaster_Linux_v6042_arm64_rpi5.tar.gz.sha256
```

The top-level directory inside either archive is:

```text
Web_Broadcaster_Linux_v6042/
```

The architecture appears in the archive filename, while the extracted application directory remains consistent.

---

## Final package layout

The final v6042 package preserves the established flat `bin/` runtime contract and includes the deployment documentation introduced in the public-HTTPS release line.

```text
Web_Broadcaster_Linux_v6042/
├── start.sh
├── README.txt
├── bin/
│   ├── web_broadcaster
│   ├── _internal/
│   ├── web_broadcaster_engine
│   ├── libavformat.so.61
│   ├── libavcodec.so.61
│   ├── libswresample.so.5
│   ├── libavutil.so.59
│   ├── libfdk-aac.so.2
│   ├── libmp3lame.so.0
│   ├── libsoundsolution.so.2
│   └── ss18.dat
├── html/
│   ├── base.html
│   ├── broadcaster.html
│   ├── dashboard.html
│   ├── login.html
│   ├── no_stations.html
│   ├── setup.html
│   ├── Layout1_default.pos
│   ├── Layout2_default.pos
│   └── static/
├── script/
└── docs/
    ├── DEPLOYMENT_GUIDE.md
    ├── NGINX_PUBLIC_HTTPS.md
    └── nginx_web_broadcaster.conf.example
```

### Flat `bin/` rule

The only directory directly under `bin/` is:

```text
_internal/
```

There are no final runtime directories named:

```text
bin/ffmpeg/
bin/soundsolution/
bin/native_engine/
```

All native executables and private shared libraries are adjacent under `bin/`. The native daemon therefore uses:

```text
RPATH = $ORIGIN
```

### Files intentionally excluded from the final package

- FFmpeg SDK headers;
- FFmpeg/ffprobe command-line executables;
- SoundSolution source;
- SoundSolution helper executable;
- C object files;
- compiler output directories;
- PyInstaller build directories;
- PGO profiles;
- dependency archives;
- Liquidsoap;
- Wine;
- AppImage;
- symbolic links.

---

## Installing and starting Web Broadcaster

### 1. Verify the built package

AMD64 example:

```bash
sha256sum -c Web_Broadcaster_Linux_v6042_amd64.tar.gz.sha256
```

Raspberry Pi 5 example:

```bash
sha256sum -c Web_Broadcaster_Linux_v6042_arm64_rpi5.tar.gz.sha256
```

### 2. Extract

```bash
tar -xzf Web_Broadcaster_Linux_v6042_amd64.tar.gz
cd Web_Broadcaster_Linux_v6042
```

Use the ARM64 archive name on Raspberry Pi 5.

### 3. Review `start.sh`

v6042 exposes the deployment/security settings at the beginning of `start.sh`:

```bash
PORT="15000"
DEBUG_MODE="OFF"

PUBLIC_INTERNET_MODE="OFF"
BIND_HOST="0.0.0.0"
TRUSTED_HOSTS=""

HTTPS_MODE="OFF"
TLS_CERT_FILE=""
TLS_KEY_FILE=""

PROXY_COUNT="0"
```

The generated defaults are for a trusted private LAN.

### 4. Start

```bash
./start.sh
```

Expected trusted-LAN startup output includes:

```text
Web Broadcaster v6042 is starting with Cheroot on 0.0.0.0:15000.

Open http://localhost:15000 in your browser.
```

In the recommended nginx mode, the listener line instead shows `127.0.0.1:15000`.

### 5. Stop

Use `Ctrl+C` in the terminal that runs `start.sh`, or stop the supervising service/process. The Python application shuts down its managed native daemon during normal termination.

---

## Deployment modes

Read `docs/DEPLOYMENT_GUIDE.md` before changing network exposure. For public nginx deployment, also read `docs/NGINX_PUBLIC_HTTPS.md`.

### Mode A — trusted LAN only

Use:

```bash
PORT="15000"
DEBUG_MODE="OFF"

PUBLIC_INTERNET_MODE="OFF"
BIND_HOST="0.0.0.0"
TRUSTED_HOSTS=""

HTTPS_MODE="OFF"
TLS_CERT_FILE=""
TLS_KEY_FILE=""

PROXY_COUNT="0"
```

This exposes plain HTTP to the host's LAN interfaces. Do **not** create an Internet/NAT port-forward for TCP 15000.

### Mode B — public Internet through local nginx — recommended

Use:

```bash
PORT="15000"
DEBUG_MODE="OFF"

PUBLIC_INTERNET_MODE="ON"
BIND_HOST="127.0.0.1"
TRUSTED_HOSTS="radio.example.com"

HTTPS_MODE="OFF"
TLS_CERT_FILE=""
TLS_KEY_FILE=""

PROXY_COUNT="1"
```

Replace `radio.example.com` with the real public hostname.

In this mode:

- nginx listens publicly on TCP 80/443;
- nginx terminates HTTPS;
- Web Broadcaster listens only on `127.0.0.1:15000`;
- TCP 15000 is not publicly forwarded;
- `PROXY_COUNT=1` tells Flask to trust exactly one local reverse proxy;
- secure session cookies and HTTPS-aware URL handling are enabled by the proxy configuration.

The launcher rejects `PROXY_COUNT>0` unless the Web Broadcaster bind address is loopback.

### Mode C — direct Cheroot HTTPS

Direct TLS is supported with:

```bash
PUBLIC_INTERNET_MODE="ON"
BIND_HOST="0.0.0.0"
TRUSTED_HOSTS="radio.example.com"
HTTPS_MODE="ON"
TLS_CERT_FILE="/absolute/path/fullchain.pem"
TLS_KEY_FILE="/absolute/path/privkey.pem"
PROXY_COUNT="0"
```

The certificate and private key must exist and be readable. TLS 1.2 is the minimum protocol configured by the application when the runtime supports explicit minimum-version control.

For an always-on public service, the documented nginx deployment remains preferred.

---

## First-run setup

On a fresh database, Web Broadcaster protects first-user creation with a one-time setup token.

At first launch the token is:

- printed to the **local server console**;
- written to `db/.setup_token`;
- required by the Setup page;
- removed after the first user is created successfully.

The setup-token file is created with private owner permissions where supported.

Typical initial application setup order:

1. Start Web Broadcaster and read the one-time setup token from the local console.
2. Create the first user account.
3. Create the first station.
4. Set the station name.
5. Set the base music directory.
6. Configure station playback and DSP settings.
7. Add or scan media into categories.
8. Configure AutoDJ rotation if required.
9. Create one or more Icecast encoder outputs.
10. Add queue items or allow AutoDJ to fill the queue.
11. Start the station audio engine and selected output(s).

The media library itself is not copied into the Web Broadcaster package. The configured base music directory must be readable by the user that runs Web Broadcaster.

---

## Runtime configuration

### Port

```bash
PORT="15000"
```

Valid range:

```text
1-65535
```

### Debug mode

```bash
DEBUG_MODE="OFF"
```

Valid values:

```text
OFF
ON
```

Normal operation should use `OFF`.

### Public Internet policy

```bash
PUBLIC_INTERNET_MODE="OFF"
```

When set to `ON`, the launcher requires trusted hosts and refuses plain HTTP unless either direct TLS or an explicit trusted reverse proxy is configured.

### Bind address

```bash
BIND_HOST="0.0.0.0"
```

Trusted LAN mode normally uses `0.0.0.0`. The recommended local-nginx reverse-proxy mode must use `127.0.0.1` (or another accepted loopback form).

### Trusted hosts

```bash
TRUSTED_HOSTS=""
```

Public HTTPS/proxy mode requires at least one allowed Host value, for example:

```bash
TRUSTED_HOSTS="radio.example.com"
```

### Direct TLS

```bash
HTTPS_MODE="OFF"
TLS_CERT_FILE=""
TLS_KEY_FILE=""
```

These are used only when Cheroot itself terminates TLS.

### Reverse-proxy count

```bash
PROXY_COUNT="0"
```

Use `1` only for the documented single local nginx proxy. The allowed range is `0` to `4`, but the value must equal the exact number of explicitly trusted proxies in front of the application.

### Station settings

Station configuration is managed through the browser and stored in station-local SQLite databases. Settings include, among other items:

- station/radio identity;
- base music directory;
- playback behavior;
- cue/fade/transition settings;
- AutoDJ configuration;
- DSP enable state and `.dat` configuration;
- encoder and Icecast output configuration;
- metadata behavior;
- scheduler and script rules.

### Icecast

Each output has independent connection and encoder state. The source handshake can include persisted stream identity fields such as bitrate, description, genre and URL in addition to audio format information.

### DSP

The default packaged DSP configuration is:

```text
bin/ss18.dat
```

The application resolves legacy stored paths to the packaged native configuration where appropriate. The configured file must remain readable.

---

## Web security model

v6042 includes application-level controls intended to support safe HTTPS deployment. HTTPS is still required for public network exposure.

### Authentication

- Authentication is deny-by-default.
- Only login, first-run setup and static assets are intentionally public.
- API requests without an authenticated session return an unauthorized response instead of silently bypassing login.
- Passwords are stored as one-way Werkzeug password hashes, not plaintext user passwords.
- New and changed passwords must be **12 to 256 characters** in v6042.
- Login errors deliberately use a generic invalid-credentials message.

### Session protection

- The session cookie is named `wb_session`.
- `HttpOnly` is enabled.
- `SameSite=Lax` is enabled.
- `Secure` is enabled in direct-HTTPS or trusted-proxy mode.
- Permanent session lifetime is eight hours with rolling refresh.
- A cryptographically random Flask session-signing secret is generated automatically when no explicit secret is supplied.
- The generated secret is stored in `db/.session_secret` with private owner permissions where supported.

Preserve `db/.session_secret` during upgrades. Deleting it invalidates existing browser sessions and causes a new secret to be created on the next start.

### CSRF protection

Every state-changing HTTP method is protected by a session-bound CSRF token. Studio fetch requests send the token through `X-CSRF-Token`; forms use the same session token. Logout is a POST operation rather than an unprotected GET action.

### Request and form limits

The application configures:

```text
Maximum request body: 4 MiB
Maximum in-memory form data: 256 KiB
Maximum form parts: 100
```

The supplied nginx configuration keeps its edge body limit aligned with the application.

### Rate limiting

Flask-Limiter is enabled with:

- global ceiling: `600 per minute`;
- meta limit: `20 per hour`;
- login POST: `5 per 5 minutes; 20 per hour`;
- initial setup POST: `3 per 5 minutes; 10 per hour`;
- user-creation endpoint: `3 per 5 minutes`.

Reverse-proxy mode relies on the configured trusted proxy count so the application receives the correct client address from the local nginx proxy.

### Host and proxy validation

- Flask `TRUSTED_HOSTS` validation is supported and required for public HTTPS/proxy operation.
- v6038+ preserves Flask routing errors correctly, so an invalid Host returns the intended routing error rather than being converted into an authentication redirect failure.
- Reverse-proxy mode is accepted only with a loopback Web Broadcaster listener.
- Public-Internet mode refuses plain HTTP without direct TLS or an explicitly trusted reverse proxy.

### Security response headers

v6042 sets, among others:

- `X-Content-Type-Options: nosniff`;
- `X-Frame-Options: DENY`;
- `Referrer-Policy: no-referrer`;
- restrictive `Permissions-Policy`;
- `Cross-Origin-Opener-Policy: same-origin`;
- `Cross-Origin-Resource-Policy: same-origin`;
- Content Security Policy;
- `Strict-Transport-Security` on HTTPS-aware requests;
- `Cache-Control: no-store` for non-static responses.

The current CSP retains `'unsafe-inline'` for scripts/styles because the existing UI still contains inline content. It is therefore not equivalent to a nonce/hash-only CSP.

### Current authentication boundary

v6042 does **not** implement multi-factor authentication. For public deployment use a long, strong, unique password and protect the account credentials accordingly.

---

## Logging and diagnostics

### Normal mode

With:

```bash
DEBUG_MODE="OFF"
```

Web Broadcaster does not continuously write:

```text
logs/audio_engine_protocol.jsonl
logs/native_engine.log
```

Routine Python/Werkzeug/Cheroot warning traffic and native-daemon stdout are suppressed. Genuine Python, native-daemon and libav errors remain visible on the console.

### Malformed ID3 metadata noise filtering

v6042 deliberately suppresses a narrow class of known harmless FFmpeg 7.1.5 ID3 parser diagnostics that can occur while scanning malformed MP3 metadata. This includes the exact BOM/comment/lyrics parser messages and `Error reading frame <ID>, skipped` only when `<ID>` has the shape of a valid 3/4-character uppercase-alphanumeric ID3 frame identifier.

Examples covered by the regression tests include malformed:

```text
COMM
TENC
TCOP
TOPE
```

The same filtering is applied at the native libav callback and at the inherited process-console path so known metadata-only noise does not leak to the terminal, systemd output or Settings Console.

This filter does **not** intentionally suppress generic container, decoder, encoder, network or I/O errors.

### Debug mode

Set:

```bash
DEBUG_MODE="ON"
```

This restores the existing diagnostic streams:

```text
logs/audio_engine_protocol.jsonl
logs/native_engine.log
```

Debug mode does **not** enable:

- Flask debug mode;
- the Flask reloader;
- browser tracing;
- a new verbose protocol format;
- additional audio processing.

It only restores the diagnostic logging streams already supported by the application.

### Startup failure

If the managed native engine cannot start, Web Broadcaster exits with a clear message similar to:

```text
Web Broadcaster cannot start its internal audio runtime: <error>
```

Do not continue operating the UI when the native runtime has failed to initialize.

---

## Runtime data and backup

The packaged application creates runtime directories next to the application root.

### Database directory

```text
db/
```

The global registry database is:

```text
db/web-broadcaster.db
```

Station-specific databases are also stored under `db/`.

The same directory also contains security state such as:

```text
db/.session_secret
```

and, only until the first administrator is created:

```text
db/.setup_token
```

### Temporary runtime directory

```text
temp/
```

This directory contains generated runtime files such as station playlists and staging material.

### Logs

```text
logs/
```

This directory is used when debug logging is enabled.

### Backup procedure

For a consistent manual backup:

1. Stop Web Broadcaster.
2. Copy the complete `db/` directory, including hidden files such as `.session_secret`.
3. Back up any custom `.dat`, script or layout files stored outside the package defaults.
4. Back up the external music library separately.
5. Record the exact Web Broadcaster package version and archive SHA-256.
6. If using public nginx HTTPS, back up or document the nginx site configuration separately. Certbot/Let's Encrypt state lives outside the Web Broadcaster package.

Do not commit runtime databases, setup/session secrets, Icecast credentials or TLS private keys to Git.

---

## Testing

The project contains a broad regression suite covering Python behavior and native integrations.

Major test areas include:

- architecture and package contracts;
- source version history;
- database bootstrap and current schema;
- Internet security and protected-route behavior;
- Cheroot/server contract;
- trusted-host routing;
- LAN and nginx deployment documentation;
- station lifecycle;
- queue and history persistence;
- AutoDJ repeat rules and rotation;
- scheduler/script behavior;
- native engine protocol;
- deck load identity and preload reuse;
- cue, fade, seek and transition behavior;
- native PCM analysis;
- duration self-healing;
- corrupt input and early EOF handling;
- Icecast output;
- multiple encoder branches;
- SoundSolution integration;
- live dry/DSP switching;
- logging gates;
- client disconnect handling;
- final HTML layout;
- bundled FFmpeg runtime behavior;
- malformed-ID3 console filtering using real FFmpeg 7.1.5 parser paths;
- final package layout, documentation presence and private-library resolution.

### Authoritative test command

The official test run occurs inside the buildkit:

```bash
./build_v6042_linux.sh
```

The build script stages the architecture-matched FFmpeg and SoundSolution dependencies before running:

```bash
python -m pytest -q --tb=short
```

with live output also written to:

```text
build_work/source-regression.log
```

Running the full test suite directly from a clean Git checkout is not equivalent because the source repository intentionally does not contain the staged private native dependencies.

### Why the buildkit is authoritative

Several native regression tests require the exact packaged FFmpeg headers/libraries, architecture-specific SoundSolution runtime and generated native executable. The buildkit stages those dependencies first and then runs the suite in the environment that matches the release build.

---

## Integrity and reproducibility

### Buildkit manifest

At startup, the builder runs:

```bash
sha256sum -c SHA256SUMS.txt
```

Any changed or missing included artifact causes an immediate build failure.

### Source/build-script identity

The current v6042 buildkit manifest includes:

```text
Web_Broadcaster_v6042.zip
  5811aaf71e9f2a06a23bbed86a2821741454df4ad098be9e090b110b9abb5171

build_v6042_linux.sh
  0dd2759c6e0d825314928a599114169f59ed0bfb066912fc16c4204092a69441
```

### Private-library verification

The builder verifies:

- ELF architecture;
- FFmpeg runtime identity;
- SoundSolution version and arithmetic mode;
- SoundSolution SONAME;
- native-daemon RPATH;
- direct linkage against `libsoundsolution.so.2`;
- private FFmpeg/SoundSolution resolution through `ldd`;
- absence of unresolved libraries.

### Raspberry Pi 5 SoundSolution protection

The validated Raspberry Pi 5 SoundSolution runtime hash is:

```text
SHA256: 84455ef8a15121b88835f28205a4bb325f19d80bd649ebf4ebffff4fe973f4d4
```

During a normal Web Broadcaster build it is:

- not rebuilt;
- not stripped;
- not patched;
- not relinked;
- not processed with `objcopy`;
- not modified in place.

The required library and `ss18.dat` are copied as regular files and verified before and after package assembly.

### Symlink-free package

The final package contains no symbolic links. Build-time symlinks from dependency packages are resolved and materialized as regular files. The final archive is scanned before release.

---

## Release workflow

### 1. Update versions

For a new release, update at minimum:

```text
app.py
native_engine/include/engine.h
version.txt
build script
release documentation
```

The following values must agree:

```text
APP_VERSION
WB_NATIVE_DAEMON_VERSION
source/archive filenames
build script EXPECTED_APP_VERSION
current-version regression assertions
```

### 2. Append `version.txt`

Do not rewrite or truncate the existing history. Append a new chronological entry describing:

- behavior changes;
- architecture changes;
- dependency changes;
- security/deployment changes;
- test/build changes;
- compatibility decisions;
- the final version update.

### 3. Run native builds

A final binary release should be built and validated natively on:

- Debian 12 AMD64;
- Raspberry Pi 5 AArch64.

The normal build keeps `RUN_SOURCE_TESTS=1`.

### 4. Commit the source and documentation

The repository `main` branch should contain the unpacked current source, tests and documentation. `README.md` must describe the same release as `APP_VERSION` and `version.txt`.

When using the GitHub web interface, upload/replace the current source files, commit them to `main`, then create a version tag/release from the updated `main` state.

### 5. Tag the release

Use the immutable version tag:

```text
v6042
```

Release title:

```text
Web Broadcaster v6042
```

### 6. Publish Release assets

Attach the architecture-neutral source ZIP, complete buildkit and their published checksums/identities. Add the finished native Linux packages after successful native builds.

### 7. Never move a published tag

Do not modify or force-move `v6042` after publication. A correction that changes released source or artifacts must become a new Web Broadcaster version.

---

## Development workflow

A simple branch model is sufficient:

```text
main
feature/<name>
fix/<name>
```

Recommended rules:

1. Keep `main` buildable.
2. Make one logical change per commit.
3. Add or update a regression test for behavioral changes.
4. Preserve the current database schema policy unless a release explicitly changes it.
5. Preserve the flat final `bin/` package contract unless a release explicitly redesigns packaging.
6. Do not replace private FFmpeg or SoundSolution binaries casually.
7. Keep public-deployment documentation synchronized with `start.sh` and application security behavior.
8. Test AMD64 and Raspberry Pi 5 before publishing final native packages.
9. Append `version.txt` for every released version.
10. Keep the complete source regression suite enabled by default in the normal build.

Example commit messages:

```text
Harden trusted-host routing for nginx deployment
Preserve embedded ID3 artist/title metadata priority
Generalize malformed ID3 console-noise filtering
Release Web Broadcaster v6042
```

### Suggested `.gitignore`

```gitignore
# Python
__pycache__/
*.py[cod]
.pytest_cache/
.venv/
venv/

# Native/PyInstaller build output
native_engine/bin/
build_work/
build/
dist/
*.spec
*.o
*.a

# Runtime data
/db/
/temp/
/logs/
*.db
*.sqlite
*.sqlite3
.env
.env.*

# Runtime/security secrets
**/.session_secret
**/.setup_token

# Local dependency/buildkit material
ffmpeg_build/
soundsolution_build/
packages/

# Generated release archives
Web_Broadcaster_*.zip
Web_Broadcaster_*.tar.gz
*.tar.gz.sha256
*.zip.sha256

# Editors/OS
.vscode/
.idea/
.DS_Store
*~
```

### Suggested `.gitattributes`

```gitattributes
*.sh text eol=lf
*.py text eol=lf
*.c text eol=lf
*.h text eol=lf
*.html text eol=lf
*.css text eol=lf
*.js text eol=lf
*.md text eol=lf
Makefile text eol=lf

*.dat binary
*.so binary
*.gz binary
*.zip binary
```

Ensure executable scripts retain their Git executable bit where the repository tooling supports it.

---

## Troubleshooting

### Buildkit checksum verification fails

Example:

```text
ERROR: Buildkit SHA-256 verification failed.
```

Actions:

1. Do not edit files inside the extracted buildkit.
2. Re-download the exact Release asset.
3. Verify the outer buildkit ZIP checksum.
4. Extract into a clean directory.
5. Confirm no sync/antivirus tool modified included scripts or archives.

### Required command not found

Install the missing Debian package. Common requirements are covered by:

```bash
sudo apt install -y build-essential python3 python3-venv python3-pip unzip tar binutils file ca-certificates
```

### Python virtual environment cannot be created

Install:

```bash
sudo apt install -y python3-venv
```

Then remove `build_work/` and run the builder again.

### Python package installation fails

Check:

- network access;
- DNS;
- TLS certificates;
- configured Python package index;
- proxy settings;
- availability of the pinned package versions.

The builder intentionally uses pinned package versions.

### ARM64 build rejected

The official ARM64 target is restricted to a real Raspberry Pi 5. Check:

```bash
tr -d '\0' </proc/device-tree/model
uname -m
```

Expected architecture:

```text
aarch64
```

Expected model string includes:

```text
Raspberry Pi 5
```

### Regression test cannot find FFmpeg libraries

Use the current v6042 buildkit. The source-build staging layout intentionally differs from the final installable package:

```text
bin/ffmpeg       diagnostic executable used by build/tests
lib/*.so         private FFmpeg libraries during source-tree build
```

The final installable package places the required private shared libraries directly under `bin/` and does not include the FFmpeg command-line executable.

### Native engine has unresolved libraries

Run inside the extracted final package:

```bash
ldd bin/web_broadcaster_engine
```

The private libraries should resolve to files under the package's own `bin/` directory. Do not set a global `LD_LIBRARY_PATH` to work around a broken package; the packaged daemon must resolve correctly through its own RPATH.

### Port already in use

Edit the top of `start.sh`:

```bash
PORT="15001"
```

Use an unused port between 1 and 65535.

### Browser cannot connect in trusted-LAN mode

Check:

- the host firewall;
- the selected port;
- network routing;
- that `start.sh` is still running;
- the address of the Web Broadcaster host;
- that `BIND_HOST="0.0.0.0"` is still configured for LAN access.

### Public HTTPS does not work through nginx

Check in this order:

1. `PUBLIC_INTERNET_MODE="ON"`.
2. `BIND_HOST="127.0.0.1"`.
3. `TRUSTED_HOSTS` exactly matches the public hostname.
4. `HTTPS_MODE="OFF"` when nginx owns TLS.
5. `PROXY_COUNT="1"` for one local nginx proxy.
6. `ss -ltnp | grep ':15000'` shows only loopback for Web Broadcaster.
7. `sudo nginx -t` succeeds.
8. TCP 80/443 reach nginx.
9. TCP 15000 is not publicly forwarded.
10. The Let's Encrypt certificate is valid for the hostname.
11. The nginx SSE locations have proxy buffering disabled.

See `docs/NGINX_PUBLIC_HTTPS.md` for the complete procedure.

### Invalid Host returns HTTP 400

In public mode this is expected when the request Host is not included in `TRUSTED_HOSTS`. Do not weaken Host validation to make an incorrect hostname work; fix DNS, nginx `server_name` or `TRUSTED_HOSTS` instead.

### Music files cannot be opened

Confirm that the user running Web Broadcaster has read and directory traversal permission for the configured base music directory and every parent directory.

### DSP cannot load

Check:

```text
bin/libsoundsolution.so.2
bin/ss18.dat
```

Both files must exist and be readable. Use `DEBUG_MODE="ON"` and inspect native stderr/log output for the exact station-scoped DSP error.

### Icecast output does not connect

Verify:

- Icecast hostname and port;
- source password;
- mount point;
- encoder format and bitrate;
- TLS/network reachability where applicable;
- server availability.

Enable debug logging for detailed native output state.

### Database recovery

Do not edit an active SQLite database manually. Stop Web Broadcaster first, copy the complete `db/` directory, and perform recovery on a duplicate.

---

## Operational and security notes

### Network exposure

Do not expose the default trusted-LAN HTTP listener directly to the public Internet.

For public access, the recommended architecture is:

```text
Internet -> nginx HTTPS :443 -> 127.0.0.1:15000 -> Web Broadcaster
```

Keep TCP 15000 private and use the supplied nginx/Certbot deployment documentation.

### Web server

The packaged application uses **Cheroot 11.1.2**, not the Flask/Werkzeug development server. Cheroot owns the WSGI listener and can optionally own direct TLS. In the recommended public architecture nginx remains the public TLS endpoint.

### Credentials and secrets

Never commit or publish:

- Icecast source passwords;
- user databases;
- `db/.session_secret`;
- `db/.setup_token`;
- `.env` files;
- TLS private keys;
- production logs containing sensitive paths or configuration.

### Passwords

v6042 requires 12-256 characters for newly created or changed application passwords. For an Internet-facing instance, use a long, unique password that is not reused by another service.

### Multi-factor authentication

v6042 does not provide built-in MFA/2FA. If this is a requirement for a deployment, place additional access control in front of the application or implement MFA in a future application release rather than assuming password authentication provides MFA-equivalent protection.

### Filesystem access

Web Broadcaster needs read access to the configured music library and write access to its own runtime directories. Run it as a dedicated, non-root user whenever possible.

### nginx and Certbot state

nginx configuration and Let's Encrypt/Certbot certificate state live outside the Web Broadcaster release directory. Replacing the Web Broadcaster package during an upgrade does not normally replace those files. Preserve and validate them separately.

---

## Known boundaries

- Linux only.
- Official AMD64 build host is Debian 12.
- Official ARM64 build host is Raspberry Pi 5 only.
- No cross-compilation workflow.
- No generic ARM64 package.
- No Windows or macOS package.
- No Liquidsoap compatibility layer.
- No live FFmpeg command-line subprocess in the runtime audio path.
- No SoundSolution subprocess.
- No Wine/AppImage/FUSE dependency.
- No built-in MFA/2FA in v6042.
- Trusted-LAN mode uses plain HTTP and must not be exposed to an untrusted network.
- The source Git checkout alone is not the complete official native build environment; use the version-matched Release buildkit.
- Node.js-backed JavaScript regression execution is optional.
- Public redistribution of bundled binary dependencies requires a separate license/compliance review.

---

## Dependency manifest

The v6042 buildkit contains the following fixed native component packages.

### FFmpeg 7.1.5

| Target | Package | SHA-256 |
|---|---|---|
| AMD64 | `wb-ffmpeg-7.1.5-amd64-runtime.tar.gz` | `011419ce5f232b0090cca53a922a7185b50819d95e981a3547542989def5515a` |
| AMD64 | `wb-ffmpeg-7.1.5-amd64-sdk.tar.gz` | `054fec02754fa0bdf4701aa739ab2174dead040cab5bfec7ae7ad5506d2ec91b` |
| Raspberry Pi 5 | `wb-ffmpeg-7.1.5-arm64-rpi5-runtime.tar.gz` | `29e7a320000fde4d3ee89aa0ce1301806bf2d38874455fcf583679dd20f64424` |
| Raspberry Pi 5 | `wb-ffmpeg-7.1.5-arm64-rpi5-sdk.tar.gz` | `03746b216af1f546759b15f1bd791e62b811bfb555d1e5d147bf656812b38258` |

FFmpeg builder:

```text
build_ffmpeg_7_1_5_for_web_broadcaster.sh
SHA256: e124d204a892d26347ce51da4d9de27684b4455f2b8f42fdd51869e31e6fd431
```

### SoundSolution

| Target | Package | SHA-256 |
|---|---|---|
| AMD64 runtime | `wb-soundsolution-6.1.0-amd64-runtime.tar.gz` | `cef86d444febc8e45a61718221d2cf2f5ffbaa4bf91d7d20f56907a94ea7c3ef` |
| AMD64 source/provenance | `wb-soundsolution-6.1.0-amd64-source.tar.gz` | `a4d6d640e92cffe90de4ee1817a72ca293b600751148f44b73fed2d47693f8cc` |
| Raspberry Pi 5 runtime | `wb-soundsolution-6.0.0-arm64-rpi5-runtime.tar.gz` | `84455ef8a15121b88835f28205a4bb325f19d80bd649ebf4ebffff4fe973f4d4` |
| Raspberry Pi 5 source/provenance | `wb-soundsolution-6.0.0-arm64-rpi5-source.tar.gz` | `472ec25be3467a672e4f4ce69beae1eefe59a910cbf1e973d96ef5ceffd0e56c` |

SoundSolution builder:

```text
build_soundsolution_for_web_broadcaster.sh
SHA256: 2d7fac4027593ad5db9eef845bafcfcb16a8151455731a3398bf5e4ab220fbb6
```

### v6042 source and build script

| File | SHA-256 |
|---|---|
| `Web_Broadcaster_v6042.zip` | `5811aaf71e9f2a06a23bbed86a2821741454df4ad098be9e090b110b9abb5171` |
| `build_v6042_linux.sh` | `0dd2759c6e0d825314928a599114169f59ed0bfb066912fc16c4204092a69441` |

The buildkit's own `SHA256SUMS.txt` remains the authoritative complete internal manifest.

---

## License and redistribution

A project license is not declared by this README. Add a repository-level `LICENSE` file before presenting the project as open source under a specific license.

Unless an explicit license grants additional rights, source availability alone should not be treated as permission to copy, modify or redistribute the project.

The Release buildkit also contains third-party components, including FFmpeg, FDK-AAC, LAME and SoundSolution materials. Those components retain their own licenses and distribution conditions. Review the exact build configuration, source-offer requirements, notices and component licenses before publishing binary Release assets publicly.

---

## Version history

The complete development history is stored in:

```text
version.txt
```

The file is intentionally retained in full and is part of the regression contract. Do not replace it with a shortened changelog.

For the current release summary, see the final entry:

```text
v6042 - 2026-08-16
```

The v6042 entry documents the generalized malformed-ID3 metadata console-noise filter and the real FFmpeg 7.1.5 regression coverage added after long-running v6041 testing exposed additional malformed `COMM`, `TENC`, `TCOP` and `TOPE` metadata diagnostics.
