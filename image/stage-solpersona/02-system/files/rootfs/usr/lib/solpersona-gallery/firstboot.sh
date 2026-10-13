#!/bin/sh
# Once, on the first start (after Raspberry Pi Imager's own first-run step, if
# the person used its settings). Prepares the card for a life of power cuts:
#
#   1. the root partition grows to a fixed size, with room for years of
#      security updates (not to the whole card, as Raspberry Pi OS does), and
#      the rest of the card becomes the data partition `spgdata` — written to
#      the partition table, then a reboot, so the kernel reads the new table
#      fresh instead of resizing the disk it is running from;
#   2. after that reboot: the root file system grows, `spgdata` is formatted
#      and mounted at /data (the gallery, the screen's credential, saved
#      Wi-Fi, app updates), Wi-Fi that Imager set up moves onto it, and the
#      root becomes a read-only overlay from the next boot on.
#
# Every step can run again safely if power is cut midway. Each step is also
# written to solpersona-gallery-firstboot.txt on the boot partition, which
# any computer can read.
set -u

MARK_DIR=/etc/solpersona-gallery
LOG=/boot/firmware/solpersona-gallery-firstboot.txt

say() {
	printf '\033[2J\033[H\n\n\n     SolPersona Gallery\n\n     %s\n' "$1" > /dev/tty1 2>/dev/null || true
	echo "firstboot: $1"
	echo "$(date -u +%FT%TZ) $1" >> "$LOG" 2>/dev/null || true
}
fail() {
	say "Could not prepare the card: $1 (the screen still starts; see $LOG)"
	{ lsblk -o NAME,SIZE,TYPE,LABEL,MOUNTPOINT; cat /proc/cmdline; } >> "$LOG" 2>&1
	exit 1
}
run() {
	echo "\$ $*" >> "$LOG" 2>/dev/null
	"$@" >> "$LOG" 2>&1 || fail "$* failed"
}

say "Preparing this screen. This happens once and takes a minute or two."

ROOT_PART="$(findmnt -n -o SOURCE /)"
ROOT_NAME="$(basename "$ROOT_PART")"
DISK_NAME="$(lsblk -no pkname "$ROOT_PART")"
DISK="/dev/$DISK_NAME"
ROOT_NUM="$(cat "/sys/class/block/$ROOT_NAME/partition")"
[ -n "$DISK_NAME" ] && [ "$ROOT_NUM" = 2 ] || fail "unexpected layout ($ROOT_PART on $DISK_NAME)"
SECTOR=512
GiB_S=$((1024 * 1024 * 1024 / SECTOR))
ALIGN=$((8 * 1024 * 1024 / SECTOR))

part_path() { lsblk -lnpo NAME,TYPE "$DISK" | awk -v n="$1" '$2 == "part" {i++; if (i == n) print $1}'; }
table_has_p3() { sfdisk -d "$DISK" 2>/dev/null | grep -q "^${DISK}p\?3 "; }

# Phase 1: the partition table.
if ! blkid -L spgdata >/dev/null 2>&1 && ! table_has_p3; then
	DISK_S="$(cat "/sys/block/$DISK_NAME/size")"
	ROOT_START="$(cat "/sys/class/block/$ROOT_NAME/start")"
	ROOT_SIZE="$(cat "/sys/class/block/$ROOT_NAME/size")"
	# 6 GiB of root on cards of 16 GB and more, 4.5 GiB on smaller ones.
	if [ "$DISK_S" -ge $((14 * GiB_S)) ]; then WANT=$((6 * GiB_S)); else WANT=$((9 * GiB_S / 2)); fi
	[ "$WANT" -lt "$ROOT_SIZE" ] && WANT="$ROOT_SIZE"
	WANT=$(( (WANT + ALIGN - 1) / ALIGN * ALIGN ))
	DATA_START=$(( ROOT_START + WANT ))
	[ $(( DISK_S - DATA_START )) -ge $((1 * GiB_S)) ] || fail "the card is too small; use one of 8 GB or more"
	say "Making room for the gallery…"
	run sh -c "echo '$ROOT_START,$WANT' | sfdisk --no-reread --no-tell-kernel --force -N 2 '$DISK'"
	run sh -c "echo '$DATA_START,+,L' | sfdisk --no-reread --no-tell-kernel --force --append '$DISK'"
	sync
	say "Restarting to use the new layout…"
	sleep 2
	systemctl reboot
	exit 0
fi

# Phase 2: the file systems (the kernel has read the new table at boot).
DATA="$(part_path 3)"
[ -n "$DATA" ] || fail "the data partition is in the table but not visible"
run resize2fs "$ROOT_PART"
if ! blkid -L spgdata >/dev/null 2>&1; then
	say "Formatting the gallery's space…"
	run mkfs.ext4 -F -q -L spgdata -E lazy_itable_init=1,lazy_journal_init=1 "$DATA"
	udevadm settle
fi

mkdir -p /data
mountpoint -q /data || run mount "$DATA" /data
mkdir -p /data/lib /data/cache /data/app /data/network /data/timesync
chown gallery:gallery /data/lib /data/cache
chmod 750 /data/lib /data/cache
chmod 700 /data/network
chown systemd-timesync:systemd-timesync /data/timesync 2>/dev/null || true
mount -a 2>>"$LOG" || true

# Wi-Fi from Raspberry Pi Imager (or added by hand) → the data partition.
for f in /etc/NetworkManager/system-connections/*.nmconnection; do
	[ -e "$f" ] || continue
	install -m 600 "$f" /var/lib/solpersona-network/
	rm -f "$f"
done

# Read-only from the next boot on.
run /usr/lib/solpersona-gallery/overlay.sh on

mkdir -p "$MARK_DIR"
date -u +%FT%TZ > "$MARK_DIR/firstboot-done"
sync
say "Ready. Starting the gallery…"
sleep 2
systemctl reboot
