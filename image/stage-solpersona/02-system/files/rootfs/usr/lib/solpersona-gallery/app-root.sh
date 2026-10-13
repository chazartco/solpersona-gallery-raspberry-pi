#!/bin/sh
# Sourced by the launchers: which copy of the app to run.
# An updated copy on the data partition when it is complete; otherwise the
# one on the read-only root, which is always there to fall back to.
FACTORY=/usr/lib/solpersona-gallery
CURRENT=/var/lib/solpersona-gallery-app/current
APP_ROOT="$FACTORY"
if [ -f "$CURRENT/VERSION" ] && [ -f "$CURRENT/agent/solpersona_gallery/__main__.py" ] && [ -f "$CURRENT/player/index.html" ]; then
	APP_ROOT="$CURRENT"
fi
export PYTHONPATH="$APP_ROOT/agent"
export SPG_PLAYER_DIR="$APP_ROOT/player"
