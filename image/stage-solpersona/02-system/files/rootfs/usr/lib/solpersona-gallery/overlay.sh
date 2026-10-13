#!/bin/sh
# The read-only root: on | off | status. Takes effect at the next boot.
#
# Raspberry Pi OS's overlay (the `overlayroot` package, as raspi-config uses
# it) keeps every write to / in RAM, so a power cut can never corrupt the
# system. `recurse=0` limits it to / itself: /data (the gallery, the screen's
# credential, saved Wi-Fi) stays a real, writable disk. raspi-config writes the
# option without it, which would overlay /data too — so it is set here.
set -e
CMDLINE=/boot/firmware/cmdline.txt

writable_boot() { mount -o remount,rw /boot/firmware 2>/dev/null || true; }
strip() { sed -i -E 's/(^| )overlayroot=[^ ]*//g; s/^ +//' "$CMDLINE"; }

case "${1:-status}" in
	on)
		writable_boot
		strip
		sed -i 's/^/overlayroot=tmpfs:recurse=0 /' "$CMDLINE"
		sync
		;;
	off)
		writable_boot
		strip
		sync
		;;
	status)
		if [ "$(findmnt -n -o FSTYPE /)" = "overlay" ]; then echo on; else echo off; fi
		;;
	*)
		echo "usage: $0 on|off|status" >&2
		exit 2
		;;
esac
