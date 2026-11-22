# Git-based Web Broadcaster build dependencies

All release-build inputs are versioned in the repository's `main` branch.
Changes to the build workflows are first validated on the
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

On every CI run, `.github/scripts/prepare-git-build.sh`:
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

## Raspberry Pi 5 (ARM64) GitHub Actions build

`.github/workflows/build-linux-rpi5-arm64.yml` runs on GitHub's native
`ubuntu-24.04-arm` runner inside a Debian 12 ARM64 container. It uses the
same Git source staging helper and the same version-independent
`buildkit/build.sh` as the amd64 job, with all source regressions enabled.
The application version still comes only from `app.py`.

The validated Git-tracked `ffmpeg_build/` ARM64 Cortex-A76 SDK/runtime
(FFmpeg 7.1.5) and `soundsolution_build/` Raspberry Pi 5 runtime
(SoundSolution 6.0.0) are used without rebuilding or replacing them.
The original native builder retains `-mcpu=cortex-a76`,
`-mtune=cortex-a76`, its ELF checks, SoundSolution DSP smoke test,
native-daemon smoke test, package hash verification, and the Debian 12
GLIBC_2.36 ceiling for the CI artifact.

Only ARM64 Debian 12 CI jobs pass `WB_RPI5_CI_BUILD=1` to allow a
non-Raspberry-Pi ARM64 host. A normal local ARM64 build still requires
actual Raspberry Pi 5 hardware. CI verifies the ARM64 package and
native tests but cannot substitute for running on physical Raspberry Pi 5.

The ARM64 artifact is
`Web_Broadcaster_Linux_v<APP_VERSION>_arm64_rpi5.tar.gz` with its
`.sha256` verification file, distributed via the ARM64 workflow's
GitHub Actions Artifacts section.

Both amd64 and Raspberry Pi 5 ARM64 workflows run automatically after a push
to `main` (and also during continued testing on the development branch).
They can be launched manually with GitHub Actions `workflow_dispatch`.

## Automatic GitHub Release publication

The `Publish Web Broadcaster Linux Release` workflow runs when either the
amd64 or Raspberry Pi 5 ARM64 build completes on `main` (or manually through
`workflow_dispatch`). It uses a separate token with `actions: read` and
`contents: write` permissions; the build workflows remain read-only.

The publisher requires **both build workflows to succeed for the same Git
commit**, checks the `app.py` version against the last `version.txt` entry,
downloads both deployable TAR.GZ packages and matching `.sha256` files,
verifies their hashes and archive readability, and only then publishes.
It never mixes builds from different commits or overwrites existing release
assets. If one build is missing or failed, nothing is published; the later
successful build completion can trigger the publisher again.

For a new version it creates a `v<APP_VERSION>` tag pointing at the tested
`main` commit, creates a Release and attaches both archives and checksums.
For an existing Release it uploads only missing assets and preserves any
already published files.

The existing `v6085` Release predates this CI system: its tag remains on the
original v6085 commit, and its Buildkit ZIP is preserved. As a **one-time
historical exception**, the publisher may attach binaries built from the
current validated `main` v6085 commit. Future releases refuse tags pointing
at a different commit. The application version is not automatically bumped.
