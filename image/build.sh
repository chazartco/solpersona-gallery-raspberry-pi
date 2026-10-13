#!/usr/bin/env bash
# Builds the SD card image with pi-gen, in Docker. Run from anywhere:
#
#   bash image/build.sh                       # → image/deploy/solpersona-gallery-<version>.img.xz
#   RELEASE_REPO=owner/solpersona-gallery-raspberry-pi bash image/build.sh
#
# Needs Docker and, on an x86 machine, qemu-user-static with binfmt (on Ubuntu:
# `sudo apt install qemu-user-static binfmt-support`). Takes 30–60 minutes.
#
# RELEASE_REPO   GitHub "owner/name" the image takes app updates from. Unset:
#                updates are off in this image.
# SIGNING_PUB    Path to the Ed25519 public key releases are signed with.
#                Unset: image/release-signing.pub if present, else updates off.
set -euo pipefail

PI_GEN_REF="${PI_GEN_REF:-2026-09-15-raspios-bookworm-arm64}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
WORK="${WORK:-$HERE/.work}"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$REPO/agent/solpersona_gallery/__init__.py")"

echo "SolPersona Gallery $VERSION on pi-gen $PI_GEN_REF"

# 1. The app: player built, agent copied, one tarball (the same bundle a release ships).
bash "$REPO/scripts/package-app.sh" "$WORK/app"
BUNDLE="$WORK/app/solpersona-gallery-app-$VERSION.tar.gz"

# 2. pi-gen at a pinned release.
if [ ! -d "$WORK/pi-gen/.git" ]; then
  git clone --depth 1 --branch "$PI_GEN_REF" https://github.com/RPi-Distro/pi-gen.git "$WORK/pi-gen"
fi
PG="$WORK/pi-gen"
rm -rf "$PG/stage-solpersona"
cp -a "$HERE/stage-solpersona" "$PG/stage-solpersona"
touch "$PG/stage2/SKIP_IMAGES"   # export only our stage
mkdir -p "$PG/stage-solpersona/01-app/files"
cp "$BUNDLE" "$PG/stage-solpersona/01-app/files/app.tar.gz"
echo "${RELEASE_REPO:-}" > "$PG/stage-solpersona/01-app/files/release-repo"
SIGNING_PUB="${SIGNING_PUB:-$HERE/release-signing.pub}"
if [ -f "$SIGNING_PUB" ]; then cp "$SIGNING_PUB" "$PG/stage-solpersona/01-app/files/release-signing.pub"; else : > "$PG/stage-solpersona/01-app/files/release-signing.pub"; fi

# 3. Build.
cp "$HERE/config" "$PG/config"
{
  echo "FIRST_USER_PASS=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
  echo "IMG_DATE=$(date -u +%Y-%m-%d)"
} >> "$PG/config"
(cd "$PG" && CONTINUE="${CONTINUE:-0}" PRESERVE_CONTAINER=0 ./build-docker.sh -c config)

# 4. Name it, sum it.
mkdir -p "$HERE/deploy"
IMG="$(ls -t "$PG"/deploy/*.img.xz | head -1)"
OUT="$HERE/deploy/solpersona-gallery-$VERSION.img.xz"
cp "$IMG" "$OUT"
cp "$BUNDLE" "$HERE/deploy/"   # the very bundle inside the image: what a release offers as its update
(cd "$HERE/deploy" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256")
echo "built $OUT"
