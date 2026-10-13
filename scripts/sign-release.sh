#!/usr/bin/env bash
# Writes SHA256SUMS for the files in DIR and signs it (Ed25519, raw signature
# in SHA256SUMS.sig) with the key in $RELEASE_SIGNING_KEY (PEM text, from CI).
# The Pi checks both before it installs anything (agent/.../updater.py).
#   scripts/sign-release.sh DIR
set -euo pipefail
DIR="${1:?usage: sign-release.sh DIR}"
: "${RELEASE_SIGNING_KEY:?RELEASE_SIGNING_KEY is not set}"
cd "$DIR"
rm -f SHA256SUMS SHA256SUMS.sig
sha256sum -- * > SHA256SUMS
KEY="$(mktemp)"
trap 'rm -f "$KEY"' EXIT
printf '%s\n' "$RELEASE_SIGNING_KEY" > "$KEY"
openssl pkeyutl -sign -inkey "$KEY" -rawin -in SHA256SUMS -out SHA256SUMS.sig
cat SHA256SUMS
