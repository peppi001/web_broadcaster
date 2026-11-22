# Git-based Web Broadcaster build dependencies

All release-build inputs are versioned in this repository on the
`feature/github-actions-build` development branch.

- `buildkit/ffmpeg_build/`: original FFmpeg 7.1.5 runtime and SDK archives
  for amd64 and Raspberry Pi 5 ARM64, plus its build script.
- `buildkit/soundsolution_build/`: original SoundSolution 6.1.0 amd64 and
  6.0.0 Raspberry Pi 5 ARM64 source/runtime archives and its build script.
- `buildkit/build.sh`: validated original release builder.
- `buildkit/SHA256SUMS.txt`: hashes from the verified v6085 buildkit.

The embedded historical `Web_Broadcaster_source.zip` is intentionally
**not** stored in the repository.

On every CI run, `.github/scripts/prepare-amd64-build.sh`:
1. Checks the committed version-independent builder and dependency archives
   against the Git-tracked SHA256 manifest.
2. Copies the versioned buildkit into an ephemeral `.ci-build/` directory.
3. Exports the exact checked-out Git commit into the source ZIP. It excludes
   `.github/`, `buildkit/`, retired icons, old C objects and an obsolete test.
4. Validates the `APP_VERSION` in `app.py` against the native daemon version,
   without any hard-coded application release pin. Adds the generated source
   ZIP checksum to the temporary build manifest.
5. Runs the original builder in a Debian 12 job and uploads a complete,
   deployable TAR.GZ with its checksum.

No download from an older GitHub Release is needed on subsequent builds.

The system Debian FFmpeg is present only for regression test fixture
generation requiring `lavfi`; the actual native engine and the packaged
runtime use the committed, pinned FFmpeg 7.1.5 and SoundSolution binaries.

The original buildkit was imported once from the public v6085 GitHub
Release after verifying its overall SHA256:
`f056e2828bc327f68e39d64a311a26ed51de636b7eb0cc959f83eed9fecbe404`.
