#!/usr/bin/env bash
# The app bundle: the built player and the agent, one tarball. The same bundle
# goes into the image (as the factory copy) and into each GitHub release (as
# the update). Usage: scripts/package-app.sh OUT_DIR
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:?usage: package-app.sh OUT_DIR}"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$REPO/agent/solpersona_gallery/__init__.py")"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

(cd "$REPO/player" && npm ci --no-audit --no-fund && npm run build)

mkdir -p "$STAGE/agent" "$OUT"
cp -a "$REPO/player/dist" "$STAGE/player"
rsync -a --exclude '__pycache__' --exclude '*.pyc' "$REPO/agent/solpersona_gallery" "$STAGE/agent/"
echo "$VERSION" > "$STAGE/VERSION"

BUNDLE="$OUT/solpersona-gallery-app-$VERSION.tar.gz"
# Reproducible: fixed order, owner and times.
tar --sort=name --owner=0 --group=0 --numeric-owner --mtime='2026-01-01 00:00Z' -C "$STAGE" -czf "$BUNDLE" VERSION agent player
echo "$BUNDLE"
