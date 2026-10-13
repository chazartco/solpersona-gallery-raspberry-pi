#!/bin/sh
# Every boot, as root, before the network and the wall: applies the settings
# file that need root. The root is a RAM overlay, so these are re-applied each
# boot rather than written once.
export PYTHONPATH=/usr/lib/solpersona-gallery/agent
eval "$(/usr/bin/python3 -m solpersona_gallery.config --shell 2>/dev/null)"

if [ -n "${SPG_TIMEZONE:-}" ] && [ -f "/usr/share/zoneinfo/$SPG_TIMEZONE" ]; then
	ln -sf "/usr/share/zoneinfo/$SPG_TIMEZONE" /etc/localtime
	echo "$SPG_TIMEZONE" > /etc/timezone
fi

/usr/lib/solpersona-gallery/wifi-country.sh

mkdir -p /run/solpersona-gallery
chown gallery:gallery /run/solpersona-gallery
exit 0
