#!/bin/sh
# Runs inside cage, as `gallery`: sets the output up, then keeps Chromium on
# the wall — relaunched at once if it exits, and killed (then relaunched) if the
# page stops reporting to the agent while the agent itself is healthy.
. /usr/lib/solpersona-gallery/app-root.sh
eval "$(/usr/bin/python3 -m solpersona_gallery.config --shell 2>/dev/null)"

# The output: rotation for a screen hung on its side, and the mode.
OUTPUT="$(wlr-randr 2>/dev/null | awk '/^[^ ]/ {print $1; exit}')"
if [ -n "$OUTPUT" ]; then
	case "${SPG_ROTATION:-0}" in
		90) TRANSFORM=90 ;; 180) TRANSFORM=180 ;; 270) TRANSFORM=270 ;; *) TRANSFORM=normal ;;
	esac
	MODE=""
	case "${SPG_RESOLUTION:-auto}" in
		1080p) MODE="--mode 1920x1080" ;;
		4k) MODE="--mode 3840x2160" ;;
		*)
			# A Pi 4 drives 4K at 30 Hz smoothly; leave 60 Hz to the Pi 5.
			if grep -q "Raspberry Pi 4" /proc/device-tree/model 2>/dev/null && wlr-randr | grep -q "3840x2160 px, 30"; then
				MODE="--mode 3840x2160@30"
			fi ;;
	esac
	# shellcheck disable=SC2086
	wlr-randr --output "$OUTPUT" --transform "$TRANSFORM" $MODE || wlr-randr --output "$OUTPUT" --transform "$TRANSFORM" || true
fi

BROWSER="$(command -v chromium || command -v chromium-browser)"
PROFILE=/run/solpersona-kiosk/chromium
ALIVE=/run/solpersona-gallery/player-alive
PAGE="file://$SPG_PLAYER_DIR/boot.html"

set -- \
	--kiosk --noerrdialogs --disable-infobars --no-first-run --disable-session-crashed-bubble \
	--ozone-platform=wayland --force-device-scale-factor=1 \
	--autoplay-policy=no-user-gesture-required \
	--ignore-gpu-blocklist --enable-gpu-rasterization \
	--disable-features=Translate,MediaRouter,DialMediaRouteProvider,HardwareMediaKeyHandling \
	--disable-pinch --overscroll-history-navigation=0 --hide-scrollbars \
	--disable-component-update --check-for-update-interval=31536000 --password-store=basic \
	--user-data-dir="$PROFILE" --disk-cache-dir="$PROFILE/cache" --disk-cache-size=33554432
if [ "${SPG_DEBUG:-0}" = "1" ]; then
	# DevTools on 127.0.0.1 only (ssh -L 9222:127.0.0.1:9222 pi to use it).
	set -- "$@" --remote-debugging-port=9222
fi

uptime_s() { cut -d. -f1 /proc/uptime; }

while :; do
	rm -rf "$PROFILE"
	mkdir -p "$PROFILE"
	"$BROWSER" "$@" "$PAGE" &
	PID=$!
	STARTED=$(uptime_s)
	while kill -0 "$PID" 2>/dev/null; do
		sleep 15
		NOW=$(uptime_s)
		LAST=$(cat "$ALIVE" 2>/dev/null || echo 0)
		[ "$LAST" -lt "$STARTED" ] && LAST=$STARTED
		if [ $((NOW - LAST)) -gt 120 ] && curl -sf -m 3 "http://127.0.0.1:${SPG_PORT:-8737}/healthz" >/dev/null 2>&1; then
			echo "kiosk: the page stopped answering; restarting Chromium"
			kill "$PID" 2>/dev/null
			sleep 5
			kill -9 "$PID" 2>/dev/null
		fi
	done
	wait "$PID" 2>/dev/null
	echo "kiosk: Chromium exited; starting it again"
	sleep 1
done
