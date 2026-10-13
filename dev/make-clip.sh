#!/usr/bin/env bash
# A 7-second H.264/AAC test film for the mock (dev/fixtures/clip-7s.mp4), made
# with ffmpeg in a throwaway container. The mock adds a video piece when it exists.
set -euo pipefail
OUT="$(cd "$(dirname "$0")" && pwd)/fixtures"
mkdir -p "$OUT"
docker run --rm -v "$OUT":/out alpine:3.20 sh -c '
  apk add --no-cache ffmpeg >/dev/null &&
  ffmpeg -loglevel error -y -f lavfi -i testsrc2=size=1280x720:rate=30 -f lavfi -i sine=frequency=440 -t 7 \
    -c:v libx264 -profile:v high -pix_fmt yuv420p -c:a aac -shortest -movflags +faststart /out/clip-7s.mp4'
ls -la "$OUT/clip-7s.mp4"
