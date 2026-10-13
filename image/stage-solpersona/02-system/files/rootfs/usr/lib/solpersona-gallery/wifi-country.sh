#!/bin/sh
# Applies the Wi-Fi country (regulatory domain). Root is needed for `iw reg set`;
# the agent only writes the choice made on the screen or the setup page to
# /var/lib/solpersona-gallery/wifi-country. `wifi_country` in the settings file wins.
export PYTHONPATH=/usr/lib/solpersona-gallery/agent
eval "$(/usr/bin/python3 -m solpersona_gallery.config --shell 2>/dev/null)"
COUNTRY="${SPG_WIFI_COUNTRY:-}"
if [ -z "$COUNTRY" ] && [ -f /var/lib/solpersona-gallery/wifi-country ]; then
	COUNTRY="$(tr -dc 'A-Z' < /var/lib/solpersona-gallery/wifi-country | head -c 2)"
fi
if [ -n "$COUNTRY" ]; then
	iw reg set "$COUNTRY" && echo "Wi-Fi country $COUNTRY"
fi
# Raspberry Pi OS keeps the radio off until a country is set; the screen needs
# it for Wi-Fi and its setup network. (Unset: the world-wide rules, 2.4 GHz.)
rfkill unblock wlan 2>/dev/null || true
exit 0
