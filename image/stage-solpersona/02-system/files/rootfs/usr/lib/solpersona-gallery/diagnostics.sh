#!/bin/sh
# What the screen is doing, written where anybody can read it: the card's boot
# partition (the `bootfs` drive on any computer). For a screen with no
# keyboard and no SSH, this file is the way to see why something went wrong.
# It holds service logs, partitions and network state — never the screen's
# credential, which no service logs.
OUT=/boot/firmware/solpersona-gallery-log.txt
TMP="$OUT.tmp"
{
	echo "SolPersona Gallery — diagnostics, $(date -u +%FT%TZ), up $(cut -d' ' -f1 /proc/uptime) s"
	echo "app $(cat /var/lib/solpersona-gallery-app/current/VERSION 2>/dev/null || cat /usr/lib/solpersona-gallery/VERSION 2>/dev/null)"
	echo "model $(tr -d '\0' < /proc/device-tree/model 2>/dev/null)"
	echo "root $(findmnt -n -o SOURCE,FSTYPE /) · first boot done: $(cat /etc/solpersona-gallery/firstboot-done 2>/dev/null || echo no)"
	echo
	echo "== cmdline"; cat /proc/cmdline
	echo; echo "== partitions"; lsblk -o NAME,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINT
	echo; echo "== failed units"; systemctl --failed --no-legend --no-pager
	echo; echo "== network"; nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device 2>&1; rfkill list 2>&1 | head -20
	echo; echo "== panel"; for c in /sys/class/drm/card*-HDMI-A-*; do echo "$c $(cat "$c/status" 2>/dev/null) $(head -1 "$c/modes" 2>/dev/null)"; done
	for u in solpersona-firstboot solpersona-boot-config solpersona-agent solpersona-kiosk NetworkManager; do
		echo; echo "== $u"
		journalctl -b -u "$u" --no-pager -o short-monotonic 2>&1 | tail -150
	done
	echo; echo "== boot errors"; journalctl -b -p err --no-pager -o short-monotonic 2>&1 | tail -80
} > "$TMP" 2>&1
mv -f "$TMP" "$OUT"
sync
