# Web Broadcaster native audio daemon — v6027

The native daemon is the authoritative multi-station audio backend. Version 6027
is based on the closed v5102 control and application architecture, but replaces
all FFmpeg command-line child processes in the live audio path with direct
FFmpeg 7.1.5 library integration.

## Audio path

```text
local file / HTTP / HTTPS / HLS
        ↓
libavformat + libavcodec + libswresample
        ↓
native A/B decks, timing, cue, seek, fade and mixer
        ↓
in-process SoundSolution DSP shared library when enabled
        ↓
libavcodec (libmp3lame or libfdk_aac) + native Icecast transport
```

One running station therefore does not spawn `ffmpeg` processes. The FFmpeg
code runs inside `web_broadcaster_engine` through the bundled shared libraries.
SoundSolution also runs inside `web_broadcaster_engine`; enabling DSP does not
create an additional process.

## Bundled FFmpeg/libav runtime

The daemon is linked directly to the private libraries in:

```text
bin/ffmpeg
lib/
```

The native binary contains an RPATH of `$ORIGIN/../../lib`, so it loads
the Web Broadcaster's FFmpeg 7.1.5 build rather than a system installation. The
bundled SDK headers under `native_engine/ffmpeg_sdk/include/` make the native
engine rebuild self-contained.

The retained `bin/ffmpeg` executable is a diagnostic/reference tool and
is not started by the live station path.

## Native SoundSolution DSP

Version 6027 links the daemon directly to the architecture-matched SoundSolution runtime through the regular ELF file `libsoundsolution.so.2`. The amd64 build uses final SoundSolution Native v6.1.0 SSE2-direct, while the Raspberry Pi 5 build uses the validated SoundSolution Native v6.0.0 structured-process runtime unchanged. Each
station owns an independent `ssnative_dsp` context,
loads its persisted `.dat` configuration and processes the mixed 44.1 kHz,
signed 16-bit little-endian stereo PCM block in place before encoding.

The minimal relocatable runtime is kept in:

```text
bin/soundsolution/
├── libsoundsolution.so.2  (regular ELF file)
└── ss18.dat
```

No DSP executable, stdin/stdout pipe, FIFO, helper thread, fork/exec path, Wine,
AppImage or FUSE component is used. The daemon RPATH contains both
`$ORIGIN/../../lib` and `$ORIGIN/../../bin/soundsolution`, so neither FFmpeg
nor SoundSolution needs to be installed system-wide. Live dry/DSP switching,
station isolation, restart-on-failure behavior and DSP-aware metadata delay are
retained.

## Application-managed runtime

Starting `app.py` starts and verifies the bundled daemon automatically,
reconnects/restarts it after an unexpected exit, and stops it when the Web
Broadcaster process ends. No separate daemon command or service is required.

Normal operation does not open `logs/native_engine.log` or
`logs/audio_engine_protocol.jsonl`. Routine Python/Werkzeug warnings and native
daemon stdout are also suppressed, while genuine Python/Werkzeug ERROR/CRITICAL
records and native daemon stderr remain visible on the console. Start the
application with `DEBUG=1` to restore those same pre-v6015 log streams:

```bash
DEBUG=1 python3 app.py -p 15000
```

The switch does not enable Flask debug mode, protocol verbose capture, browser
tracing or any additional diagnostics; it only restores the existing
warning/error, compact protocol and native daemon logs.

## Native decode, analysis and timing

Deck playback and local PCM analysis both use the direct libav decoder. Audio is
normalized to 44.1 kHz stereo signed 16-bit PCM before entering the existing
native ring buffers and mixer. The realtime mixer remains isolated from file and
network I/O.

The native timing worker continues to own audible start, play start, transition
trigger, audible end, trailing-silence handling, EOF and next-track requests.
Queue, AutoDJ, history and UI ownership remain in Python exactly as in v5102.

## Encoding and Icecast

The shared multi-output encoder is now an in-process libav worker. MP3 uses
`libmp3lame`; AAC-LC, HE-AAC and HE-AACv2 use `libfdk_aac`. Each configured
output keeps its own encoded FIFO, Icecast connection, metadata state, reconnect
backoff and diagnostics. The existing DSP startup gate and metadata delay are
preserved.

## Build

From `native_engine/`:

```bash
make clean
make
```

The build links against the bundled headers and SONAME libraries and writes:

```text
native_engine/bin/web_broadcaster_engine
```

## Icecast source metadata

Each configured output sends its own bitrate and persisted stream identity fields
in the source handshake through `Ice-Bitrate`, `Ice-Description`, `Ice-Genre`
and `Ice-URL`, in addition to the existing `Ice-Audio-Info` format tuple.
The optional HTTP `User-Agent` header is deliberately omitted from both the
source handshake and metadata update request, so Icecast does not publish a
redundant source-client `user_agent` statistic.
