#!/bin/bash -e
# Everything that makes Raspberry Pi OS Lite an appliance: services, the
# kiosk, the data partition, the watchdog, logs, Wi-Fi setup, updates.

cp -a files/rootfs/. "${ROOTFS_DIR}/"
chmod 755 "${ROOTFS_DIR}"/usr/lib/solpersona-gallery/*.sh "${ROOTFS_DIR}"/usr/lib/solpersona-gallery/run-*
chmod 600 "${ROOTFS_DIR}"/etc/NetworkManager/conf.d/50-solpersona.conf

# The settings file a person may edit from any computer that reads the card.
install -m 644 files/solpersona-gallery.toml "${ROOTFS_DIR}/boot/firmware/solpersona-gallery.toml"

# The data partition (created on first boot) and what lives on it. `nofail`:
# the very first boot happens before it exists.
cat >> "${ROOTFS_DIR}/etc/fstab" << 'EOF'
# SolPersona Gallery: the only writable part of the card (the root is a read-only overlay).
LABEL=spgdata   /data                            ext4  defaults,noatime,commit=30,nofail,x-systemd.device-timeout=15s  0  2
/data/lib       /var/lib/solpersona-gallery      none  bind,nofail,x-systemd.requires-mounts-for=/data  0  0
/data/cache     /var/cache/solpersona-gallery    none  bind,nofail,x-systemd.requires-mounts-for=/data  0  0
/data/app       /var/lib/solpersona-gallery-app  none  bind,nofail,x-systemd.requires-mounts-for=/data  0  0
/data/network   /var/lib/solpersona-network      none  bind,nofail,x-systemd.requires-mounts-for=/data  0  0
/data/timesync  /var/lib/systemd/timesync        none  bind,nofail,x-systemd.requires-mounts-for=/data  0  0
EOF

# Boot: quiet, no console blanking, no cursor; the hardware watchdog on.
CMDLINE="${ROOTFS_DIR}/boot/firmware/cmdline.txt"
sed -i 's/ console=serial0,115200//; s/$/ quiet loglevel=3 consoleblank=0 vt.global_cursor_default=0 logo.nologo/' "${CMDLINE}"
cat >> "${ROOTFS_DIR}/boot/firmware/config.txt" << 'EOF'

# --- SolPersona Gallery ---------------------------------------------------
# A hardware watchdog: if the system hangs, the Pi resets itself.
dtparam=watchdog=on
# The whole panel, edge to edge, and no rainbow at power-on.
disable_overscan=1
disable_splash=1
[pi4]
# 4K at 60 Hz needs this on a Pi 4 (the Pi 5 does it anyway).
hdmi_enable_4kp60=1
[all]
EOF

# Wi-Fi: stage2 leaves the radio off until a country is set. The setup
# network needs it on; the regulatory domain comes from Imager or
# `wifi_country` in the settings file (else the world domain, 2.4 GHz only).
cat > "${ROOTFS_DIR}/var/lib/NetworkManager/NetworkManager.state" << 'EOF'
[main]
WirelessEnabled=true
EOF

on_chroot << 'EOF'
passwd -l solpersona || true   # no password logins; Imager's customisation can set one
systemctl set-default graphical.target
systemctl disable getty@tty1.service
systemctl mask apt-daily.timer apt-daily-upgrade.timer man-db.timer
systemctl enable solpersona-firstboot.service solpersona-boot-config.service solpersona-agent.service \
	solpersona-kiosk.service solpersona-updater.timer solpersona-os-boot.service \
	solpersona-diagnostics.timer solpersona-wifi-country.path solpersona-wifi-country.service
EOF
