#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/motion-detection}
SERVICE_NAME=${SERVICE_NAME:-motion-detection.service}
RUN_USER=${RUN_USER:-pi}

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (sudo)."
  exit 1
fi

if [[ ! -f "$APP_DIR/app.py" ]]; then
  echo "Application sources were not found in $APP_DIR"
  echo "Copy bundle first and rerun this installer."
  exit 1
fi

apt update
apt install -y python3 python3-venv python3-pip python3-opencv libatlas-base-dev

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip wheel
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements-rpi.txt"

install -m 0644 "$APP_DIR/deploy/systemd/motion-detection.service" "/etc/systemd/system/$SERVICE_NAME"
sed -i "s|^User=.*|User=$RUN_USER|" "/etc/systemd/system/$SERVICE_NAME"

chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo "Installed and started: $SERVICE_NAME"
systemctl --no-pager --full status "$SERVICE_NAME" || true
