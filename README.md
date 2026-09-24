# Web Broadcaster

**Current version: v6085**

Web Broadcaster is a Linux-based, browser-operated radio automation and streaming application. It provides a multi-station Studio and Dashboard, a managed native audio engine, playlist and queue management, AutoDJ rotation, scheduled playback, scripted announcements, DSP, and Icecast output.

## Features

- **Multi-station operation:** manage independent stations from the browser-based Studio and Dashboard.
- **Native audio playback:** prebuffered A/B playback, crossfades, controlled interruptions, and handling of short IDs and faulty audio files.
- **Library, Queue and AutoDJ:** organize music, build queues, and configure automated rotations.
- **Scheduler and Scripts:** add media at scheduled times, including second-precision `HH:MM:SS` rules and immediate-play interruptions.
- **URL streams:** add HTTP/HTTPS streams to a queue or a Scheduler rule, with an optional `HH:MM:SS` duration; leaving duration blank allows indefinite playback.
- **Stream metadata:** forward supported upstream ICY and Ogg/Vorbis titles to Icecast and display them in Studio and Dashboard. An optional fixed custom title can instead be set for an individual queued URL or Scheduler URL rule.
- **Audio processing and output:** native DSP and multi-encoder Icecast streaming, with bundled FFmpeg and platform-specific SoundSolution components.

## What's new in v6085

Scheduler **Add URL** now accepts optional **Custom metadata**, matching the Queue Add URL dialog. When provided, the fixed title is associated with that station's Scheduler rule and applied to each queue occurrence created by the rule. It remains attached through rule editing and is preserved for Scheduler End, Next, and Immediate insertion. It does **not** modify the shared URL or track, nor does it affect other queue occurrences or stations. Leave the field empty to use dynamic upstream stream metadata.

Recent changes:

| Version | Change |
| --- | --- |
| **v6085** | Per-rule, station-specific fixed metadata for Scheduler URL insertions. |
| **v6084** | Serialized per-station AutoDJ refills to prevent queue overfill during concurrent refills and Manual Next. |
| **v6083** | Optional `HH:MM:SS` URL duration in the shared Queue/Scheduler Add URL dialog; empty means indefinite. |
| **v6082** | Optional fixed, per-queue-occurrence metadata for manually queued URLs. |
| **v6081** | Upstream stream titles displayed in Studio and Dashboard Now Playing. |
| **v6080** | Upstream ICY and Ogg/Vorbis stream metadata forwarded to Icecast. |
| **v6079** | Second-precision Scheduler times, with existing `HH:MM` rules still supported. |
| **v6078** | Native hard-handoff fix for very short station IDs and following tracks. |
| **v6075** | Scheduler Immediate interruption for streams, files and directory insertion after target preparation. |

The full historical change log is in `version.txt` inside the application source archive.

## Supported platforms

| Target | Release build | Bundled media components |
| --- | --- | --- |
| Linux x86-64 / amd64 | Built in a Debian 12 Podman container; intended for Debian 12 and Debian 13 | FFmpeg 7.1.5; SoundSolution Native 6.1.0 |
| Raspberry Pi 5, 64-bit arm64 | Native Raspberry Pi 5 build | FFmpeg 7.1.5; SoundSolution Native 6.0.0 |

Other operating systems, architectures and Raspberry Pi models are not covered by these release build paths.

## Download and run

Obtain the matching `Web_Broadcaster_Linux_v6085_*.tar.gz` release archive for your architecture. Extract it, open its `Web_Broadcaster_Linux_v6085` directory, and follow its bundled `README.txt` and `docs/DEPLOYMENT_GUIDE.md` for setup and startup instructions. The release includes `start.sh`, `bin/`, `html/`, `script/` and deployment documentation; runtime database and log directories are created as needed.

For an Internet-facing installation, **do not expose the application's internal HTTP port directly**. Use the nginx reverse-proxy/HTTPS deployment described in `docs/NGINX_PUBLIC_HTTPS.md` and `docs/nginx_web_broadcaster.conf.example`. The documented deployment uses nginx on public ports 80/443 and Web Broadcaster bound to `127.0.0.1:15000`.

## Build from the v6085 buildkit

The buildkit contains the architecture-neutral `Web_Broadcaster_v6085.zip` source archive, the release build script, native component packages, container definition and deployment guides. Extract the **buildkit** first; do not run the build script inside the application source ZIP.

### Linux amd64

Install Podman on the build host (including Debian 13 hosts):

```bash
sudo apt update
sudo apt install podman
```

From the extracted buildkit directory, run:

```bash
chmod +x build_v6085_linux.sh
./build_v6085_linux.sh
```

The script automatically creates or reuses its Debian 12 container image and performs the amd64 release build inside that controlled environment. No manual `podman run` is needed. The default build runs the source regression suite, including the JavaScript tests, and saves its output to `build_work/source-regression.log`. To **deliberately** skip source tests:

```bash
RUN_SOURCE_TESTS=0 ./build_v6085_linux.sh
```

A normal amd64 build produces:

```text
Web_Broadcaster_Linux_v6085_amd64.tar.gz
Web_Broadcaster_Linux_v6085_amd64.tar.gz.sha256
```

### Raspberry Pi 5 arm64

On a Raspberry Pi 5 running a 64-bit arm64 OS, run `./build_v6085_linux.sh` from the buildkit directory. This build path runs natively rather than inside the amd64 Podman image and produces:

```text
Web_Broadcaster_Linux_v6085_arm64_rpi5.tar.gz
Web_Broadcaster_Linux_v6085_arm64_rpi5.tar.gz.sha256
```

The builder uses the bundled architecture-matched FFmpeg and SoundSolution packages. Refer to the buildkit's `README.txt` for the component and build-contract details. On amd64, the builder also checks the Debian 12/Python 3.11 baseline and rejects packaged ELF components requiring GLIBC symbols newer than 2.36.

## Repository layout

The distributed buildkit has the following top-level structure:

```text
Web_Broadcaster_v6085_buildkit/
├── Web_Broadcaster_v6085.zip       # Application source, tests and version.txt
├── build_v6085_linux.sh            # Release build entry point
├── Containerfile.debian12          # Controlled amd64 build environment
├── README.txt                      # Detailed buildkit notes
├── SHA256SUMS.txt                   # Buildkit component checksums
├── docs/                            # Deployment and nginx/HTTPS guides
├── ffmpeg_build/                    # Platform-specific FFmpeg materials
└── soundsolution_build/            # Platform-specific SoundSolution materials
```

If publishing an extracted source tree instead of the buildkit, keep the corresponding source and deployment documentation together and adjust the build instructions above to the actual repository layout.

## Documentation

- `README.txt` — buildkit revision, version-by-version implementation notes and release instructions.
- `docs/DEPLOYMENT_GUIDE.md` — application setup and LAN/public deployment modes.
- `docs/NGINX_PUBLIC_HTTPS.md` — recommended public HTTPS reverse-proxy deployment.
- `docs/nginx_web_broadcaster.conf.example` — nginx configuration example.
- `native_engine/README.md` — native engine notes (inside the application source ZIP).

## Release integrity

Use the accompanying `.sha256` file to verify a generated release archive before deployment, and the buildkit's `SHA256SUMS.txt` to check its included components. Keep credentials, station databases, logs and other runtime-specific data out of public Git commits.
