# Web Broadcaster

**Web Broadcaster** is a self-contained, browser-operated, multi-station radio automation and Icecast broadcasting system for Linux.

The project combines a Python/Flask control application with a dedicated native C audio daemon. The Python application owns the web interface, station configuration, SQLite persistence, queue management, AutoDJ, scheduling, history, user management and runtime orchestration. The native daemon owns the real-time audio path: direct FFmpeg/libav decoding, A/B decks, cueing, seeking, fades, transitions, mixing, in-process SoundSolution DSP, encoding and Icecast transport.

The current release is **Web Broadcaster v6024**.

> **Repository model**
>
> - This Git repository contains the unpacked Web Broadcaster source code, tests and documentation.
> - The complete offline buildkit is published as a versioned Release asset.
> - FFmpeg and SoundSolution are treated as fixed, prebuilt binary dependencies. They are not stored in normal Git history and are not rebuilt by the regular Web Broadcaster build.

---

## Table of contents

- [Project status](#project-status)
- [Main features](#main-features)
- [Design goals](#design-goals)
- [System architecture](#system-architecture)
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
- [First-run setup](#first-run-setup)
- [Runtime configuration](#runtime-configuration)
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

---

## Project status

| Item | Current state |
|---|---|
| Web Broadcaster version | `6024` |
| Native daemon protocol/version | `6024` |
| Release buildkit revision | `build_v6024_linux-r4-active-probe-terminal-test` |
| AMD64 build target | Debian 12, generic x86-64/SSE2 baseline |
| ARM64 build target | Raspberry Pi 5, AArch64, Cortex-A76 |
| Audio backend | Native C daemon with direct FFmpeg 7.1.5 library integration |
| DSP backend | In-process SoundSolution Native shared library |
| Streaming output | Icecast |
| Python packaging | PyInstaller `onedir` |
| Default HTTP port | `15000` |
| Database | SQLite |

Version 6024 preserves the final v6023 runtime package layout while introducing one self-contained buildkit for both supported native platforms.

---

## Main features

### Multi-station operation

- Multiple independently configured stations.
- Station-specific databases, queues, playback state, output settings and AutoDJ state.
- Independent native runtime identities and station-scoped lifecycle management.
- Station creation, selection, rename and deletion from the browser interface.

### Browser-based studio

- Dashboard and studio views.
- Resizable/rearrangeable studio panels with saved layout state.
- Current deck, queue, history, playlist/library, encoders and automation controls.
- Server-sent UI events for queue, history and runtime state updates.
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

### SoundSolution DSP

- In-process SoundSolution processing through `libsoundsolution.so.2`.
- One independent DSP context per station.
- Station-specific `.dat` configuration.
- Live dry/DSP source switching without restarting the encoder or reconnecting Icecast.
- No DSP subprocess, pipe, FIFO, Wine, AppImage or FUSE dependency.
- AMD64 and Raspberry Pi 5 use architecture-specific validated SoundSolution runtimes.

### Users and persistence

- Initial setup flow.
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

### 6. Minimal runtime dependencies

The final package includes the Python runtime through PyInstaller and includes private FFmpeg and SoundSolution shared libraries. The target system only needs the normal compatible Linux runtime libraries, including glibc and GnuTLS dependencies used by the packaged native components.

### 7. Conservative release changes

A released tag is immutable. Any change to source, tests, build scripts or packaged runtime files requires a new Web Broadcaster version.

---

## System architecture

```text
Browser
   │
   │ HTTP / JSON / server-sent events
   ▼
Python / Flask Web Broadcaster
   ├── authentication and users
   ├── station registry
   ├── SQLite persistence
   ├── media library and categories
   ├── queue and history
   ├── AutoDJ
   ├── scheduler and scripts
   ├── encoder configuration
   └── native-engine lifecycle
          │
          │ local Unix socket protocol
          ▼
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
| Authentication | PCM conversion |
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

## Audio path

```text
local file / HTTP / HTTPS / HLS
        │
        ▼
libavformat demuxer
        │
        ▼
libavcodec audio decoder
        │
        ▼
libswresample
44.1 kHz / stereo / signed 16-bit little-endian PCM
        │
        ▼
native A/B deck buffers
        │
        ▼
cue / seek / fade / crossfade / mixer
        │
        ├── dry PCM
        │
        └── SoundSolution-processed PCM
                │
                ▼
shared multi-output encoder
        ├── libmp3lame
        └── libfdk_aac
                │
                ▼
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

`bin/web_broadcaster` is the PyInstaller-packaged Python/Flask application. It automatically starts, verifies, monitors and stops `bin/web_broadcaster_engine`.

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

Each package is built and tested natively on its target architecture.

---

## Component versions

| Component | Version / mode |
|---|---|
| Web Broadcaster | `6024` |
| Native audio daemon | `6024` |
| FFmpeg/libav | `7.1.5` |
| FFmpeg runtime ID | `7.1.5-for-web-broadcaster-r13` |
| AMD64 SoundSolution | `6.1.0`, `native-binary64-fast` |
| Raspberry Pi 5 SoundSolution | `6.0.0`, `native-double`, structured-process |
| Flask | `3.0.3` |
| Werkzeug | `3.0.3` |
| python-dotenv | `1.0.1` |
| Flask-Limiter | `3.6.0` |
| Mutagen | `1.47.0` |
| PyInstaller | `6.21.0` |
| pytest | `8.3.5` |

Python packages used during the official build are pinned by the build script.

---

## Repository layout

The Git repository contains the unpacked, architecture-neutral Web Broadcaster source.

```text
web-broadcaster/
├── app.py
├── requirements.txt
├── pytest.ini
├── version.txt
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
- `tests/` — Python and native integration/regression tests.
- `version.txt` — complete chronological project history. New releases must be appended; old entries must not be deleted.

### Files intentionally absent from Git

The source repository should not contain:

- FFmpeg SDK/runtime archives;
- SoundSolution runtime archives;
- generated native binaries;
- PyInstaller output;
- local databases;
- logs;
- media libraries;
- credentials or `.env` files;
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
- `version.txt`.

### Release assets

The Release for a Web Broadcaster version should contain:

```text
Web_Broadcaster_v6024_buildkit.zip
Web_Broadcaster_v6024_buildkit.zip.sha256
Web_Broadcaster_v6024.zip
Web_Broadcaster_v6024.zip.sha256
```

After successful native builds, the Release may also contain:

```text
Web_Broadcaster_Linux_v6024_amd64.tar.gz
Web_Broadcaster_Linux_v6024_amd64.tar.gz.sha256
Web_Broadcaster_Linux_v6024_arm64_rpi5.tar.gz
Web_Broadcaster_Linux_v6024_arm64_rpi5.tar.gz.sha256
```

The complete buildkit contains the fixed FFmpeg and SoundSolution packages required for offline native dependency staging. The Python build stage still requires network access to install the pinned Python packages unless a local Python package mirror/cache is provided.

### Current v6024 buildkit identity

```text
File:   Web_Broadcaster_v6024_buildkit.zip
SHA256: 9a485925512d35904f351a1e0c60e8368d2723d8f756f1ea733352dd5a2caa25
```

Do not replace a published asset under the same version and filename. Publish a new Web Broadcaster version for any changed artifact.

---

## Building from the complete buildkit

The Release buildkit is the authoritative and recommended build method.

### 1. Download and verify

Download:

```text
Web_Broadcaster_v6024_buildkit.zip
Web_Broadcaster_v6024_buildkit.zip.sha256
```

Verify:

```bash
sha256sum -c Web_Broadcaster_v6024_buildkit.zip.sha256
```

### 2. Extract

```bash
unzip Web_Broadcaster_v6024_buildkit.zip
cd Web_Broadcaster_v6024_buildkit
```

### 3. Start the native build

```bash
chmod +x build_v6024_linux.sh
./build_v6024_linux.sh
```

The same command is used on Debian 12 AMD64 and Raspberry Pi 5. The script detects the native architecture and selects the correct dependency set automatically.

### Optional source-test skip

```bash
RUN_SOURCE_TESTS=0 ./build_v6024_linux.sh
```

Skipping tests is intended only for diagnosis. Official Release packages should be built with the default:

```text
RUN_SOURCE_TESTS=1
```

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

The script also expects standard commands such as `ldd`, `sha256sum`, `find`, `sort` and `install`, which are part of a normal Debian installation.

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

The build script performs the following stages.

1. **Host validation**
   - verifies Linux;
   - identifies AMD64 or Raspberry Pi 5 AArch64;
   - rejects unsupported architectures.

2. **Buildkit integrity check**
   - verifies `SHA256SUMS.txt`;
   - refuses to continue if an included source or dependency archive has changed.

3. **Source extraction**
   - extracts `Web_Broadcaster_v6024.zip`;
   - verifies the architecture-neutral source layout;
   - confirms `APP_VERSION=6024` and native daemon version `6024`.

4. **Dependency selection**
   - selects the matching FFmpeg SDK/runtime;
   - selects the matching SoundSolution runtime;
   - checks package presence and hashes.

5. **Temporary dependency staging**
   - stages FFmpeg headers under `native_engine/ffmpeg_sdk/include/`;
   - stages the diagnostic FFmpeg executable as `bin/ffmpeg`;
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
   - installs pinned Python/PyInstaller/test dependencies;
   - verifies installed versions.

10. **Regression tests**
    - runs the complete source regression suite by default;
    - runs the optional JavaScript test when Node.js is available.

11. **Final flat-bin preparation**
    - patches the temporary build tree for the packaged application layout;
    - changes the native daemon final RPATH to `$ORIGIN`;
    - rebuilds the native daemon for the final package.

12. **PyInstaller build**
    - creates an `onedir` application;
    - places the Python runtime under `bin/_internal/`.

13. **Package assembly**
    - copies the application, native daemon and private libraries;
    - copies `html/` and `script/`;
    - generates `start.sh` and package `README.txt`.

14. **Package cleanup**
    - removes build-only files;
    - materializes all symlinks as regular files;
    - refuses to package any remaining symlink.

15. **Final verification**
    - verifies the exact allowed root and `bin/` entries;
    - checks file modes;
    - verifies SONAME and RPATH;
    - runs `ldd` and confirms private FFmpeg/SoundSolution resolution;
    - runs a packaged native-daemon smoke test;
    - verifies the `start.sh` contract.

16. **Archive creation**
    - creates the final `.tar.gz`;
    - verifies that the archive contains no symlinks;
    - writes a `.sha256` file.

---

## Build outputs

### AMD64

```text
Web_Broadcaster_Linux_v6024_amd64.tar.gz
Web_Broadcaster_Linux_v6024_amd64.tar.gz.sha256
```

### Raspberry Pi 5

```text
Web_Broadcaster_Linux_v6024_arm64_rpi5.tar.gz
Web_Broadcaster_Linux_v6024_arm64_rpi5.tar.gz.sha256
```

The top-level directory inside either archive is:

```text
Web_Broadcaster_Linux_v6024/
```

The architecture appears in the archive filename, while the extracted application directory remains consistent.

---

## Final package layout

The final v6024 package deliberately preserves the v6023 flat `bin/` contract.

```text
Web_Broadcaster_Linux_v6024/
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
└── script/
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

### 1. Verify the package

```bash
sha256sum -c Web_Broadcaster_Linux_v6024_amd64.tar.gz.sha256
```

or:

```bash
sha256sum -c Web_Broadcaster_Linux_v6024_arm64_rpi5.tar.gz.sha256
```

### 2. Extract

```bash
tar -xzf Web_Broadcaster_Linux_v6024_amd64.tar.gz
cd Web_Broadcaster_Linux_v6024
```

Use the ARM64 archive name on Raspberry Pi 5.

### 3. Review the two user settings

At the beginning of `start.sh`:

```bash
PORT="15000"
DEBUG_MODE="OFF"
```

Valid port range:

```text
1–65535
```

Valid debug values:

```text
OFF
ON
```

### 4. Start

```bash
./start.sh
```

Expected normal startup output:

```text
Web Broadcaster is starting on port 15000.

Open http://localhost:15000 in your browser.
```

Open the displayed address in a browser.

### 5. Stop

Use `Ctrl+C` in the terminal that runs `start.sh`, or stop the supervising service/process. The Python application shuts down its managed native daemon during normal termination.

---

## First-run setup

On a fresh database, Web Broadcaster presents its initial setup flow.

Typical setup order:

1. Create the first user account.
2. Create the first station.
3. Set the station name.
4. Set the base music directory.
5. Configure station playback and DSP settings.
6. Add or scan media into categories.
7. Configure AutoDJ rotation if required.
8. Create one or more Icecast encoder outputs.
9. Add queue items or allow AutoDJ to fill the queue.
10. Start the station audio engine and selected output(s).

The media library itself is not copied into the Web Broadcaster package. The configured base music directory must be readable by the user that runs Web Broadcaster.

---

## Runtime configuration

### Port

The packaged launch contract intentionally exposes only one port setting in `start.sh`:

```bash
PORT="15000"
```

The application listens on:

```text
0.0.0.0:<PORT>
```

### Debug mode

```bash
DEBUG_MODE="OFF"
```

Normal operation should use `OFF`.

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

Routine Python/Werkzeug warning traffic and native-daemon stdout are suppressed. Genuine Python/Werkzeug `ERROR`/`CRITICAL` records and native-daemon/libav stderr errors remain visible on the console.

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
2. Copy the complete `db/` directory.
3. Back up any custom `.dat`, script or layout files stored outside the package defaults.
4. Back up the external music library separately.
5. Record the exact Web Broadcaster package version and archive SHA-256.

Do not commit runtime databases or credentials to Git.

---

## Testing

The project contains a broad regression suite covering Python behavior and native integrations.

Major test areas include:

- architecture and package contracts;
- source version history;
- database bootstrap and current schema;
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
- final package layout and private-library resolution.

### Authoritative test command

The official test run occurs inside the buildkit:

```bash
./build_v6024_linux.sh
```

The build script stages the architecture-matched FFmpeg and SoundSolution dependencies before running:

```bash
python -m pytest -q
```

Running the full test suite directly from a clean Git checkout is not equivalent because the source repository intentionally does not contain the staged private native dependencies.

### v6024 r4 probe test correction

The r4 buildkit makes the one-deck audio-probe regression deterministic on loaded Debian 12 build hosts. A terminal probe event is accepted only after the exact active `native_audio_probe_started` identity has been observed, preventing a fast preload-generation EOF from waking the active-playback assertion prematurely.

This change affects regression synchronization only; it does not change runtime audio or DSP code.

---

## Integrity and reproducibility

### Buildkit manifest

At startup, the builder runs:

```bash
sha256sum -c SHA256SUMS.txt
```

Any changed or missing included artifact causes an immediate build failure.

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

The validated Raspberry Pi 5 SoundSolution archive is:

```text
soundsolution_v6.0.0_rPi5.tar.gz
SHA256: 84455ef8a15121b88835f28205a4bb325f19d80bd649ebf4ebffff4fe973f4d4
```

During a normal Web Broadcaster build it is:

- not rebuilt;
- not stripped;
- not patched;
- not relinked;
- not processed with `objcopy`;
- not repacked as a new SoundSolution archive;
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
```

The following values must match:

```text
APP_VERSION
WB_NATIVE_DAEMON_VERSION
release archive filenames
build script EXPECTED_APP_VERSION
```

### 2. Append `version.txt`

Do not rewrite or truncate the existing history. Append a new chronological entry describing:

- behavior changes;
- architecture changes;
- dependency changes;
- test/build changes;
- compatibility decisions;
- the final version update.

### 3. Run tests and native builds

A release is complete only after successful native builds on:

- Debian 12 AMD64;
- Raspberry Pi 5 AArch64.

### 4. Commit and tag

Example:

```bash
git add .
git commit -m "Release Web Broadcaster v6024"
git tag -a v6024 -m "Web Broadcaster v6024"
git push origin main
git push origin v6024
```

### 5. Publish Release assets

Attach the source ZIP, complete buildkit and checksums. Add the finished native application packages after successful builds.

### 6. Never move a published tag

Do not modify or force-move `v6024`. A correction must become a new application version, for example `v6025`, when it changes released source or output artifacts.

Buildkit-only revisions used during pre-release validation should be finalized before the immutable application Release is published.

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
7. Test AMD64 and Raspberry Pi 5 before tagging a release.
8. Append `version.txt` for every released version.

Example commit messages:

```text
Fix active native audio probe regression synchronization
Preserve private FFmpeg library resolution in flat package
Add station-scoped AutoDJ lifecycle guard
Release Web Broadcaster v6025
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
db/
temp/
logs/
*.db
*.sqlite
*.sqlite3
.env
.env.*

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

Remove the leading spaces before `db/`, `temp/` and `logs/` when copying this example into the actual `.gitignore` file; they are shown separated here only to emphasize runtime directories.

### Suggested `.gitattributes`

```gitattributes
*.sh text eol=lf
*.py text eol=lf
*.c text eol=lf
*.h text eol=lf
*.html text eol=lf
*.css text eol=lf
*.js text eol=lf
Makefile text eol=lf

*.dat binary
*.so binary
*.gz binary
*.zip binary
```

Ensure executable scripts retain their Git executable bit.

---

## Troubleshooting

### Buildkit checksum verification fails

Example:

```text
ERROR: Buildkit SHA-256 verification failed.
```

Actions:

1. Do not edit files inside the extracted buildkit.
2. Re-download the exact Release assets.
3. Verify the outer buildkit ZIP checksum.
4. Extract into a clean directory.
5. Confirm no sync/antivirus tool modified included scripts or archives.

### Required command not found

Install the missing Debian package. Common requirements are covered by:

```bash
sudo apt install -y build-essential python3 python3-venv python3-pip unzip tar binutils file
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

Use the current v6024 r4 buildkit. The correct source-build staging is:

```text
bin/ffmpeg       diagnostic executable
lib/*.so         private FFmpeg libraries
```

The final installable package is different and places required private libraries directly under `bin/`.

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

### Browser cannot connect from another computer

Check:

- the host firewall;
- the selected port;
- network routing;
- that `start.sh` is still running;
- the address of the Web Broadcaster host.

The application binds to `0.0.0.0`, but network access may still be blocked by the operating system or network infrastructure.

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

The application listens on all interfaces by default. Do not expose it directly to an untrusted public network without appropriate controls.

Recommended protections include:

- host firewall rules;
- a private/VPN network;
- a reverse proxy with TLS;
- strong application passwords;
- restricted filesystem permissions;
- regular database backups.

### Flask server

The packaged application uses the Flask/Werkzeug server as its application entry point. Treat it as an appliance-style service and protect network access appropriately. Review deployment architecture before placing it on a public Internet endpoint.

### Credentials

Never commit:

- Icecast source passwords;
- user databases;
- `.env` files;
- TLS private keys;
- production logs containing sensitive paths or configuration.

### Filesystem access

Web Broadcaster needs read access to the configured music library and write access to its own runtime directories. Run it as a dedicated, non-root user whenever possible.

---

## Known boundaries

- Linux only.
- Official AMD64 build host is Debian 12.
- Official ARM64 build host is Raspberry Pi 5 only.
- No cross-compilation workflow.
- No generic ARM64 package.
- No Windows or macOS package.
- No Liquidsoap compatibility layer.
- No live FFmpeg command-line subprocess.
- No SoundSolution subprocess.
- No Wine/AppImage/FUSE dependency.
- The source Git checkout alone is not the complete official native build environment; use the version-matched Release buildkit.
- Node.js-backed JavaScript regression execution is optional.
- Public redistribution of bundled binary dependencies requires a separate license/compliance review.

---

## Dependency manifest

The v6024 buildkit contains the following fixed component packages.

### FFmpeg 7.1.5

| Target | Package | SHA-256 |
|---|---|---|
| AMD64 | `wb-ffmpeg-7.1.5-amd64-runtime.tar.gz` | `011419ce5f232b0090cca53a922a7185b50819d95e981a3547542989def5515a` |
| AMD64 | `wb-ffmpeg-7.1.5-amd64-sdk.tar.gz` | `054fec02754fa0bdf4701aa739ab2174dead040cab5bfec7ae7ad5506d2ec91b` |
| Raspberry Pi 5 | `wb-ffmpeg-7.1.5-arm64-rpi5-runtime.tar.gz` | `29e7a320000fde4d3ee89aa0ce1301806bf2d38874455fcf583679dd20f64424` |
| Raspberry Pi 5 | `wb-ffmpeg-7.1.5-arm64-rpi5-sdk.tar.gz` | `03746b216af1f546759b15f1bd791e62b811bfb555d1e5d147bf656812b38258` |

### SoundSolution

| Target | Package | SHA-256 |
|---|---|---|
| AMD64 | `wb-soundsolution-6.1.0-amd64-runtime.tar.gz` | `cef86d444febc8e45a61718221d2cf2f5ffbaa4bf91d7d20f56907a94ea7c3ef` |
| AMD64 provenance source | `wb-soundsolution-6.1.0-amd64-source.tar.gz` | `a4d6d640e92cffe90de4ee1817a72ca293b600751148f44b73fed2d47693f8cc` |
| Raspberry Pi 5 | `wb-soundsolution-6.0.0-arm64-rpi5-runtime.tar.gz` | `84455ef8a15121b88835f28205a4bb325f19d80bd649ebf4ebffff4fe973f4d4` |
| Raspberry Pi 5 provenance source | `wb-soundsolution-6.0.0-arm64-rpi5-source.tar.gz` | `472ec25be3467a672e4f4ce69beae1eefe59a910cbf1e973d96ef5ceffd0e56c` |

The component source archives and component builder scripts are retained in the buildkit for provenance and recovery. The normal `build_v6024_linux.sh` path uses the prebuilt architecture-matched SDK/runtime packages and does not rebuild FFmpeg or SoundSolution.

### Buildkit source and scripts

| File | SHA-256 |
|---|---|
| `Web_Broadcaster_v6024.zip` | `8d8a55bad5d423dafdd4ae46bca911c807224777a8a2a62d89fe87b7a9c20f38` |
| `build_v6024_linux.sh` | `24c5974a1e679434f2eb37c4cbd0408bf84254b940e8b30ed26f539dca08e794` |
| `build_ffmpeg_7_1_5_for_web_broadcaster.sh` | `e124d204a892d26347ce51da4d9de27684b4455f2b8f42fdd51869e31e6fd431` |
| `build_soundsolution_for_web_broadcaster.sh` | `2d7fac4027593ad5db9eef845bafcfcb16a8151455731a3398bf5e4ab220fbb6` |

The buildkit's own `SHA256SUMS.txt` remains the authoritative complete internal manifest.

---

## License and redistribution

A project license is not declared by this README. Add a repository-level `LICENSE` file before presenting the project as open source.

Unless an explicit license grants additional rights, source availability alone should not be treated as permission to copy, modify or redistribute the project.

The Release buildkit also contains third-party components, including FFmpeg, FDK-AAC, LAME and SoundSolution materials. Those components retain their own licenses and distribution conditions. Review the exact build configuration, source-offer requirements, notices and component licenses before publishing binary Release assets publicly.

---

## Version history

The complete development history is stored in:

```text
version.txt
```

The file is intentionally retained in full and is part of the regression contract. Do not replace it with a shortened changelog.

For the latest release summary, see the final entry for:

```text
v6024 - 2026-07-28
```
