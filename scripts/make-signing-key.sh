#!/usr/bin/env bash
# Makes the Ed25519 key pair releases are signed with. Run once, on a trusted
# machine; keep the private key out of the repository.
#
#   bash scripts/make-signing-key.sh
#
# Then:
#   1. GitHub → Settings → Secrets → Actions: add RELEASE_SIGNING_KEY with the
#      whole content of release-signing.key (BEGIN … END lines included).
#   2. Commit image/release-signing.pub. Images built from then on accept only
#      releases signed by this key.
#   3. Store release-signing.key somewhere safe and delete it here.
#      Lose it, and screens in the field can no longer be updated (they keep
#      working; a newly flashed image with a new key is the way out).
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
[ -e release-signing.key ] && { echo "release-signing.key already exists" >&2; exit 1; }
openssl genpkey -algorithm ed25519 -out release-signing.key
openssl pkey -in release-signing.key -pubout -out image/release-signing.pub
chmod 644 image/release-signing.pub
echo "wrote release-signing.key (secret) and image/release-signing.pub (commit this)"
