#!/bin/sh
# Once, on the first start (after Raspberry Pi Imager's own first-run step, if
# the person used its settings). Prepares the card for a life of power cuts:
#
#   1. the root partition grows to a fixed size, with room for years of
#      security updates (not to the whole card, as Raspberry Pi OS does);
#   2. the rest of the card becomes the data partition `spgdata`, mounted at
#      /data: the gallery, the screen's credential, saved Wi-Fi, app updates;
#   3. Wi-Fi that Imager set up moves onto it (NetworkManager reads it there);
#   4. the root becomes a read-only overlay from the next boot on.
#
# Then it reboots. Every step can be run again safely if power is cut midway.
set -eu

MARK_DIR=/etc/solpersona-gallery
say() {
	printf '\033[2J\033[H\n\n\n     SolPersona Gallery\n\n     %s\n' "$1" > /dev/tty1 2>/dev/null || true
	echo "firstboot: $1"
}

say "Preparing this screen. This happens once and takes a minute or two."

ROOT_PART="$(findmnt -n -o SOURCE /)"
ROOT_NAME="$(basename "$ROOT_PART")"
DISK_NAME="$(lsblk -no pkname "$ROOT_PART")"
DISK="/dev/$DISK_NAME"
ROOT_NUM="$(cat "/sys/class/block/$ROOT_NAME/partition")"
SECTOR=512
GiB_S=$((1024 * 1024 * 1024 / SECTOR))
ALIGN=$((8 * 1024 * 1024 / SECTOR))

part_path() { lsblk -lnpo NAME,TYPE "$DISK" | awk -v n="$1" '$2 == "part" {i++; if (i == n) print $1}'; }

if ! blkid -L spgdata >/dev/null 2>&1; then
	DISK_S="$(cat "/sys/block/$DISK_NAME/size")"
	ROOT_START="$(cat "/sys/class/block/$ROOT_NAME/start")"
	ROOT_SIZE="$(cat "/sys/class/block/$ROOT_NAME/size")"
	# 6 GiB of root on cards of 16 GB and more, 4.5 GiB on smaller ones.
	if [ "$DISK_S" -ge $((14 * GiB_S)) ]; then WANT=$((6 * GiB_S)); else WANT=$((9 * GiB_S / 2)); fi
	[ "$WANT" -lt "$ROOT_SIZE" ] && WANT="$ROOT_SIZE"
	WANT=$(( (WANT + ALIGN - 1) / ALIGN * ALIGN ))
	DATA_START=$(( ROOT_START + WANT ))
	if [ $(( DISK_S - DATA_START )) -lt $((1 * GiB_S)) ]; then
		say "This card is too small. Use a card of 8 GB or more."
		exit 1
	fi
	if [ "$WANT" -gt "$ROOT_SIZE" ]; then
		echo "$ROOT_START,$WANT" | sfdisk --no-reread --no-tell-kernel --force -N "$ROOT_NUM" "$DISK"
		partx -u -n "$ROOT_NUM" "$DISK"
		resize2fs "$ROOT_PART"
	fi
	if [ -z "$(part_path 3)" ]; then
		echo "$DATA_START,+,L" | sfdisk --no-reread --no-tell-kernel --force --append "$DISK"
	fi
	partx -a -n 3 "$DISK" 2>/dev/null || partx -u -n 3 "$DISK" || true
	udevadm settle
	DATA="$(part_path 3)"
	mkfs.ext4 -F -q -L spgdata -E lazy_itable_init=1,lazy_journal_init=1 "$DATA"
	udevadm settle
fi

mkdir -p /data
mountpoint -q /data || mount /data
mkdir -p /data/lib /data/cache /data/app /data/network /data/timesync
chown gallery:gallery /data/lib /data/cache
chmod 750 /data/lib /data/cache
chmod 700 /data/network
chown systemd-timesync:systemd-timesync /data/timesync 2>/dev/null || true
mount -a 2>/dev/null || true

# Wi-Fi from Raspberry Pi Imager (or added by hand) → the data partition.
for f in /etc/NetworkManager/system-connections/*.nmconnection; do
	[ -e "$f" ] || continue
	install -m 600 "$f" /var/lib/solpersona-network/
	rm -f "$f"
done

# Read-only from the next boot on.
/usr/lib/solpersona-gallery/overlay.sh on

mkdir -p "$MARK_DIR"
date -u +%FT%TZ > "$MARK_DIR/firstboot-done"
sync
say "Ready. Starting the gallery…"
sleep 2
systemctl reboot
