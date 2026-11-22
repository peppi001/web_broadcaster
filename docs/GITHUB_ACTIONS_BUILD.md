# Git-based Web Broadcaster build dependencies

All release-build inputs are versioned in this repository on the
`feature/github-actions-build` development branch.

- `buildkit/ffmpeg_build/`: original FFmpeg 7.1.5 runtime and SDK archives
  for amd64 and Raspberry Pi 5 ARM64, plus its build script.
- `buildkit/soundsolution_build/`: original SoundSolution 6.1.0 amd64 and
  6.0.0 Raspberry Pi 5 ARM64 source/runtime archives and its build script.
- `buildkit/build.sh`: validated original release builder.
- `buildkit/SHA256SUMS.txt`: hashes from the verified v6085 buildkit.

No source ZIP is created or stored. The builder accepts a clean source
directory and isolates all build-time changes from the Git checkout.

On every CI run, `.github/scripts/prepare-amd64-build.sh`:
1. Checks the committed version-independent builder and dependency archives
   against the Git-tracked SHA256 manifest.
2. Copies the versioned buildkit into an ephemeral `.ci-build/` directory.
3. Copies the tracked files from the checked-out Git commit into
   `.ci-build/source/`, excluding `.github/`, `buildkit/`, retired icons,
   old C objects and one obsolete historical test. No intermediate ZIP exists.
4. Validates `APP_VERSION` in the copied `app.py` against the native daemon.
5. Passes the clean source directory to `buildkit/build.sh`. The builder
   copies it to private `build_work/src/` before running any tests or
   PyInstaller transformations; the checked-out Git tree remains untouched.
6. Runs the full Debian 12 regression suite and uploads the deployable
   TAR.GZ with its SHA256 checksum.

No download from an older GitHub Release is needed on subsequent builds.

The system Debian FFmpeg is present only for regression test fixture
generation requiring `lavfi`; the actual native engine and the packaged
runtime use the committed, pinned FFmpeg 7.1.5 and SoundSolution binaries.

The original buildkit was imported once from the public v6085 GitHub
Release after verifying its overall SHA256:
`f056e2828bc327f68e39d64a311a26ed51de636b7eb0cc959f83eed9fecbe404`.

The previous Git-to-ZIP-to-extract path has been replaced by direct file
copying. Only the final deployable TAR.GZ package is archived.
