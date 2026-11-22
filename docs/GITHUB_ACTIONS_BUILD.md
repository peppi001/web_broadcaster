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
The only application version to edit is APP_VERSION in app.py; the native
daemon version is injected into the staged C header during the build.

On every CI run, `.github/scripts/prepare-amd64-build.sh`:
1. Checks the committed version-independent builder and dependency archives
   against the Git-tracked SHA256 manifest.
2. Copies the versioned buildkit into an ephemeral `.ci-build/` directory.
3. Copies the tracked Git files directly into
   `.ci-build/buildkit/build_work/src/`, excluding `.github/`, `buildkit/`,
   retired icons, old C objects and one obsolete historical test.
4. Reads `APP_VERSION` from the staged `app.py`, and verifies that the source
   native header has the expected generated-version placeholder.
5. Passes the same source directory to `buildkit/build.sh`. No second copy is
   made in CI. The builder generates the native daemon version in the staged
   C header from `APP_VERSION` before compiling the C engine. Standalone
   builds still copy externally supplied source into their isolated workdir.
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

Release versioning: edit `APP_VERSION` in `app.py` and append the appropriate
change entry to `version.txt`. Do not edit the native C header to bump the
version. The CI build never increments a version automatically; it validates
and packages the version already selected in `app.py`.
