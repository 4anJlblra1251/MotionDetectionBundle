#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
OUT_DIR=${1:-"$ROOT_DIR/dist"}
BUNDLE_NAME=${2:-"motion-detection-rpi4"}
STAGE_DIR="$OUT_DIR/$BUNDLE_NAME"

rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"

cp "$ROOT_DIR"/app.py "$STAGE_DIR"/
cp "$ROOT_DIR"/detector.py "$STAGE_DIR"/
cp "$ROOT_DIR"/config.json "$STAGE_DIR"/
cp "$ROOT_DIR"/requirements-rpi.txt "$STAGE_DIR"/
cp -r "$ROOT_DIR"/templates "$STAGE_DIR"/
cp -r "$ROOT_DIR"/deploy "$STAGE_DIR"/

TARBALL="$OUT_DIR/$BUNDLE_NAME.tar.gz"
mkdir -p "$OUT_DIR"
tar -C "$OUT_DIR" -czf "$TARBALL" "$BUNDLE_NAME"

echo "Bundle created: $TARBALL"
echo "Deploy to Raspberry Pi and unpack to /opt/motion-detection"
