#!/usr/bin/env bash
# Press a button on the mock dashboard:  dev/mock.sh next | dev/mock.sh 'settings?fit="FRAME"'
curl -s -m 5 -X POST -H 'Accept: application/json' "http://localhost:${MOCK_PORT:-8094}/mock/$1"
echo
