Web Broadcaster version-independent Linux buildkit
=============================================

Buildkit revision: web-broadcaster-linux-build-git-r1

v6085 enables optional fixed metadata on Scheduler Add URL, matching Queue Add URL.
Leave the field empty to forward dynamic ICY/Ogg/Vorbis upstream metadata.
A nonempty title is stored per Scheduler rule and station, retained through rule
editing, and transferred to that scheduled occurrence's queue item on each run.
End, Next and Immediate priorities preserve queue identity and save the fixed
metadata before any deck replan or audible handoff. The shared URL/track is
never modified. Existing Scheduler rules remain compatible without ALTER;
rule-title companion rows are cleaned up on rule deletion. Existing native
playback, manual URL insertion, AutoDJ and all fades are unchanged.

v6084 prevents AutoDJ queue overfill caused by concurrent refills after Manual Next.
The station-specific queue count, rotation selection and enqueue execute under
one per-station lock; the AutoDJ background worker and track_started handler
recheck the actual queue size in serial order. Manually inserted URL items
continue to count toward Keep number of songs, and previously queued items
are not discarded to force a lower count. Separate stations can refill
independently. Manual Next transitions and native audio behavior are unchanged.
The new regression tests reproduce parallel refills and repeated manual Next.

v6083 changes the shared Add URL dialog to accept an optional HH:MM:SS Duration.
An empty Duration means infinite playback; the previous Infinite checkbox is
removed. A positive HH:MM:SS value (for example 00:01:30) is converted to
the existing numeric seconds contract (90 in this example); invalid input
cannot be submitted. The modal starts with an empty duration. This applies
to queue URL addition and the Scheduler URL-action editor, which share the
same dialog; existing saved URL durations and server-side URL handling remain
unchanged. Optional queue-specific Custom metadata continues to work normally.

v6082 adds optional Custom metadata to the Add URL to Queue dialog. Leave it
blank for existing dynamic upstream ICY/Ogg/Vorbis metadata; enter a fixed
single-line text to show exactly that text on Icecast and in the Studio and
multi-station Dashboard for this queue occurrence only. Reusing the same URL
in another queue row or another station does not inherit the override.
The custom title is persisted in a queue-item companion SQLite table which is
created automatically for each station and cleaned up when its queue row is
deleted. Existing station databases and tracks are preserved without ALTER.
An explicit per-deck native custom-metadata flag prevents delayed upstream
updates from overwriting the fixed output title; normal URL metadata forwarding,
local-file metadata, Decoder, AutoDJ, Scheduler, Script, Manual Next, fade,
DSP and Icecast audio/output routing remain unchanged.
Only the Add URL to Queue modal exposes this override in v6082; Scheduler URL
rule creation and library/category URL addition keep their prior behavior.

v6081-r2 fixed the upstream metadata regression test's readiness/EOF race.
v6081 added active upstream titles to Studio and Dashboard Now Playing.
v6080 added ICY and Ogg/Vorbis metadata forwarding to Icecast.

v6079 added HH:MM:SS Scheduler time precision and retained HH:MM compatibility.
The v6079-r2 release-package fix (executable builder, updated README) is
retained in this release.

v6078 corrects a native hard-
handoff race affecting short IDs: the primed destination decoder is held until
the actual audible A/B boundary, even while the outgoing PCM FIFO continues
to drain. The decoder clock is retimed to that real boundary before the active
deck switches. If a reserved target terminates or is stopped before handoff,
its reservation is released so the station can prepare another queue item
without a manual Stop/Start. A native A -> 1.1-second ID -> following-track
integration test and dedicated handoff invariants cover the failure mode.

v6077's station-local Scheduler/URL script-protection fix is retained unchanged.
The Scheduler Immediate fade, operator Manual Next, script-interrupt, normal
crossfade, decoder, DSP/encoder and Icecast configuration behavior are otherwise
unchanged.

v6076's deterministic native audio-probe EOF regression-harness fix is retained
unchanged.

v6075 extends the Scheduler Immediate fade interrupt to every media target.
HTTP/HTTPS streams, a single local file, and the first file of an Immediate
directory insertion are fully prepared before the outgoing fade starts. Once
ready, the outgoing track fades from its current position and the target enters
at 100% gain, with no fade-in, when the outgoing smoothstep gain reaches 25%.
Now Playing, track_started and Icecast metadata switch at that actual entry
point. URL targets retain the v6074 live-preload reuse and 15-second startup
protection. Operator Manual Next, Scheduler Next/End, normal automatic crossfade
and the v6072 script-interrupt path remain unchanged.

The v6065 build baseline, retained unchanged in v6084, standardizes every amd64 release build on a Debian 12 userspace baseline. On an x86-64 host, the normal `./build.sh` command now transparently creates or reuses the bundled Podman builder image, mounts the buildkit into it, and re-enters the same build script inside Debian 12. The inner build refuses to compile unless `/etc/os-release` confirms Debian 12 and Python 3.11 is active, preventing an accidental Debian 13 glibc baseline from entering a release package.

The Debian 12 builder image contains the complete release toolchain used by the regression and packaging stages: Python 3.11 with the matching shared libpython, FFmpeg/FFprobe, Node.js, GCC/build-essential, binutils and the normal archive/file utilities. As a result, the full source regression suite runs in the controlled build environment without relying on whichever Python/FFmpeg/Node versions happen to be installed on the Debian 13 host. Raspberry Pi 5 arm64 builds remain native and unchanged.

v6064's on-air multi-output watchdog race fix is retained unchanged. A newly enabled live encoder branch receives its watchdog start timestamp atomically under the output lock before configuration becomes visible, so adding AAC+ beside an already-running MP3 output cannot spuriously restart the shared pipeline.

v6063 fixes missing singleton NoRules AutoDJ rotation entries such as the Tel Star one-second station ID. NoRules selection still prefers a distinct track when one is available, but when every candidate is already present in the lookahead queue it can repeat that candidate under a new queue identity instead of consuming and skipping the rotation slot. This preserves exact music/music/music/ID ordering without changing the v6055-v6062 short-track/native playback protections.

v6062 closes the remaining exact-time / hard-handoff race reproduced on Jampec. Native get_state now exposes an armed hard-handoff reservation, exact-time script interrupts wait for that reservation to settle, and a different load cannot overwrite the reserved target deck. Hard-handoff scheduling also revalidates both confirmed deck identities under the engine lock before publishing the reservation, making the arm/load decision atomic.

Manual Next also recovers a queue head that is only READY with playback_started=false instead of falsely reporting queue_head_is_currently_audible. The v6061 audible-deck guard, v6060 control-start station-key fix, v6059 backend Auto-script activation/global one-second dispatcher, v6058 deferred-tick replay, v6056/v6057 short-ID protections, v6055 short-track queue/history lifecycle fix and v6052 malformed-audio isolation remain unchanged.

v6052 prevents malformed audio from terminating the shared native daemon. Every
decoded frame is validated before libswresample receives it, and the per-file
input contract rejects unsafe channel-layout, sample-format, sample-rate or
channel-plane changes with a controlled decoder error instead of a process crash.

A fatal native PCM analysis result is marked terminal and skip-required. The
failed descriptor is never prepared, selected or transitioned to; Web Broadcaster
removes only that exact station queue row and replans the next item. If the bad
file is the first startup item, bootstrap retries with the following queue row.
Other stations remain running and retain their own prepared audio state.

Repeated failure events are deduplicated, startup recovery is bounded, and all
new recovery diagnostics follow the existing DEBUG=1 logging gate. Normal mode
does not emit new debug messages.

The v6051 per-station scheduler isolation remains in place: each station has its own daemon
script worker and lock, so a slow or failing script cannot delay another station. The v6058
lossless bounded deferred-tick replay remains in place, while v6059 fixes the global dispatch
cadence and backend station-start automation activation. The v6050 native need-next recovery
also remains unchanged.

The complete regression suite runs by default. Pytest progress is shown live on
the build console and the same output is saved to
build_work/source-regression.log.

Supported release build paths
-----------------------------

1. x86-64 / amd64 Linux host with Podman
   - The host may be Debian 13; the release itself is always built inside the bundled Debian 12 container baseline.
   - FFmpeg 7.1.5 generic amd64 SDK and runtime
   - SoundSolution Native v6.1.0 generic x86-64 SSE2 runtime
   - Intended runtime compatibility: Debian 12 and Debian 13 amd64

2. Raspberry Pi 5 with a 64-bit aarch64 operating system
   - FFmpeg 7.1.5 Cortex-A76 SDK and runtime
   - SoundSolution Native v6.0.0 structured-process runtime
   - The validated SoundSolution runtime is copied byte-for-byte and is never
     rebuilt, stripped, patched, relinked or otherwise modified.

Build
-----

On an amd64 host, install Podman once:

  sudo apt update
  sudo apt install podman

For a prepared source ZIP, run:

  ./build.sh

No manual `podman run` command is required. On first use, or whenever the bundled
Containerfile changes, the script builds `localhost/web-broadcaster-builder:debian12`.
Later builds reuse that validated image. The buildkit directory is mounted
read/write so the final tar.gz and sha256 files appear beside the build script
with the normal user's ownership.

The complete source regression suite runs by default on every build.
RUN_SOURCE_TESTS remains enabled by default intentionally. Node.js is included in
the Debian 12 builder image, so the JavaScript execution regression is expected
to run instead of being skipped.

Optional explicit source-test skip, only when deliberately requested:

  RUN_SOURCE_TESTS=0 ./build.sh

The amd64 host itself no longer needs Python, FFmpeg, Node.js, GCC or PyInstaller
for a release build; those tools live in the Debian 12 builder image. Network
access is still required when the builder image is first created and when pip
installs the pinned Python packages during a build. The script verifies both the
buildkit checksums and the Debian 12/Python 3.11 baseline before compiling.
Before creating the final amd64 tar.gz it also scans every packaged ELF and
rejects the release if any component requires a GLIBC symbol newer than 2.36,
the Debian 12 ABI ceiling used by this release baseline.

Pinned Web build dependencies
-----------------------------

  Flask 3.1.3
  Werkzeug 3.1.8
  Flask-Limiter 4.1.1
  Cheroot 11.1.2
  python-dotenv 1.0.1
  mutagen 1.47.0
  PyInstaller 6.21.0
  pytest 8.3.5

Deployment documentation
------------------------

Start with:

  docs/DEPLOYMENT_GUIDE.md

Trusted LAN-only mode:

  PUBLIC_INTERNET_MODE="OFF"
  BIND_HOST="0.0.0.0"
  TRUSTED_HOSTS=""
  HTTPS_MODE="OFF"
  PROXY_COUNT="0"

For public Internet deployment, the preferred architecture remains:

  Internet TCP 80/443
          -> nginx on the same host
          -> HTTP 127.0.0.1:15000
          -> Web Broadcaster

Exact Web Broadcaster nginx mode:

  PUBLIC_INTERNET_MODE="ON"
  BIND_HOST="127.0.0.1"
  TRUSTED_HOSTS="radio.example.com"
  HTTPS_MODE="OFF"
  PROXY_COUNT="1"

Replace radio.example.com with the actual hostname. Keep TCP 15000 private;
only nginx should be exposed on public TCP 80/443.

Detailed public guide:

  docs/NGINX_PUBLIC_HTTPS.md

Ready-to-copy nginx virtual-host example:

  docs/nginx_web_broadcaster.conf.example

Output
------

On amd64:

  Web_Broadcaster_Linux_v<APP_VERSION>_amd64.tar.gz
  Web_Broadcaster_Linux_v<APP_VERSION>_amd64.tar.gz.sha256

On Raspberry Pi 5:

  Web_Broadcaster_Linux_v<APP_VERSION>_arm64_rpi5.tar.gz
  Web_Broadcaster_Linux_v<APP_VERSION>_arm64_rpi5.tar.gz.sha256

The archive contains one directory named Web_Broadcaster_Linux_v<APP_VERSION> with the
final package contract:

  start.sh
  README.txt
  bin/
  html/
  script/
  docs/
    DEPLOYMENT_GUIDE.md
    NGINX_PUBLIC_HTTPS.md
    nginx_web_broadcaster.conf.example

Runtime db/ and logs/ directories are created by the application when needed;
they are not pre-populated in the release package.

Component materials
-------------------

Containerfile.debian12 defines the controlled amd64 release userspace.
.containerignore keeps the Podman build context minimal; no Web Broadcaster source or binary payload is copied into the builder image.

ffmpeg_build/ and soundsolution_build/ are byte-for-byte inherited from v6051.
Web_Broadcaster_source.zip is architecture-neutral. The build script stages the
matching native packages into a temporary source tree before compilation and
regression testing.

