#!/bin/bash -e
# The app on the read-only root: /usr/lib/solpersona-gallery. This copy is the
# "factory" version — updates install beside it on the data partition, and it
# is what the screen falls back to if an update is ever unusable.

APP="${ROOTFS_DIR}/usr/lib/solpersona-gallery"
rm -rf "${APP}"
install -d -m 755 "${APP}"
tar -xzf files/app.tar.gz -C "${APP}" --no-same-owner
install -m 644 files/release-repo "${APP}/release-repo"
install -m 644 files/release-signing.pub "${APP}/release-signing.pub"
chmod -R u=rwX,go=rX "${APP}"

on_chroot << 'EOF'
# The user everything on the wall runs as. Never logs in; owns only its own state.
# Its home is on the root, not the data partition: logind makes a login
# session depend on the home directory's mount, and the wall must come up
# even if the data partition does not.
if ! id gallery >/dev/null 2>&1; then
	adduser --system --group --home /var/lib/solpersona-kiosk --shell /usr/sbin/nologin gallery
fi
install -d -o gallery -g gallery -m 750 /var/lib/solpersona-kiosk
for GRP in video render input audio; do
	adduser gallery "$GRP"
done
install -d -o gallery -g gallery -m 750 /var/lib/solpersona-gallery /var/cache/solpersona-gallery
install -d -o root -g root -m 755 /var/lib/solpersona-gallery-app /data
install -d -o root -g root -m 700 /var/lib/solpersona-network
EOF
