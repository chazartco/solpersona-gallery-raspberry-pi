#!/usr/bin/env bash
# The whole screen on a laptop, in Docker: the mock SolPersona and the agent
# (in Debian Bookworm, with Debian's Python packages, as on the Pi), serving
# the built player. Open http://localhost:8737/ in Chrome to see the wall, and
# http://localhost:${MOCK_PORT}/mock for the dashboard's controls.
#
#   dev/stack.sh up            # mock + agent
#   dev/stack.sh up --real URL # agent against a real development SolPersona instead
#   dev/stack.sh down          # and forget the screen's state
#
# The agent's state is in the volume spg-dev-data, so `docker restart spg-agent`
# behaves like a reboot; `docker pause spg-mock` like pulling the network.
#
# Build the player first: (cd player && npm ci && npm run build)
set -euo pipefail

MOCK_PORT="${MOCK_PORT:-8094}"
AGENT_PORT="${AGENT_PORT:-8737}"
IMAGE=spg-agent-test
NET=spg-dev
REPO="$(cd "$(dirname "$0")/.." && pwd)"

down() {
  docker rm -f spg-mock spg-agent >/dev/null 2>&1 || true
  docker volume rm spg-dev-data >/dev/null 2>&1 || true
}

up() {
  local base=""
  if [[ "${1:-}" == "--real" ]]; then
    base="${2:?--real needs the SolPersona address, e.g. http://host.docker.internal:8080}"
  fi
  down
  docker build -q -t "$IMAGE" -f "$REPO/agent/Dockerfile.test" "$REPO/agent" >/dev/null
  docker network create "$NET" >/dev/null 2>&1 || true
  local ports=(-p "${AGENT_PORT}:8737")
  local mock=0
  if [[ -z "$base" ]]; then
    # The mock shares the agent's network namespace, so http://localhost:PORT is
    # the same mock for the agent and for your browser (media URLs are absolute).
    base="http://localhost:${MOCK_PORT}"
    ports+=(-p "${MOCK_PORT}:${MOCK_PORT}")
    mock=1
  fi
  docker run -d --name spg-agent --network "$NET" "${ports[@]}" -v "$REPO":/src -w /src/agent \
    -v spg-dev-data:/data \
    -e SPG_DEV=1 -e SPG_DEBUG="${SPG_DEBUG:-1}" -e SPG_BIND=0.0.0.0 -e SPG_BASE_URL="$base" -e SPG_CONFIG=/nonexistent \
    -e SPG_STATE_DIR=/data/state -e SPG_CACHE_DIR=/data/cache -e SPG_RUN_DIR=/data/run \
    -e SPG_PLAYER_DIR=/src/player/dist -e SPG_DEV_ORIGINS="http://localhost:5173" \
    "$IMAGE" python3 -m solpersona_gallery >/dev/null
  if [[ "$mock" == 1 ]]; then
    docker run -d --name spg-mock --network container:spg-agent -v "$REPO":/src -w /src \
      "$IMAGE" python3 dev/mock_solpersona.py --port "$MOCK_PORT" --public-url "$base" >/dev/null
    echo "mock SolPersona: http://localhost:${MOCK_PORT}/mock"
  fi
  echo "agent → $base"
  echo "the wall: http://localhost:${AGENT_PORT}/"
}

case "${1:-}" in
  up) shift; up "$@" ;;
  down) down ;;
  *) echo "usage: $0 up [--real URL] | down" >&2; exit 2 ;;
esac
