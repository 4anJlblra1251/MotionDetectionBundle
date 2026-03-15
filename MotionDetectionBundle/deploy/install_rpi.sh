#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/motion-detection}
CONFIG_DIR=${CONFIG_DIR:-/etc/motion-detection}
CONFIG_PATH=${CONFIG_PATH:-$CONFIG_DIR/config.json}
SERVICE_NAME=${SERVICE_NAME:-motion-detection.service}
BUNDLE_TARBALL=${1:-${BUNDLE_TARBALL:-}}

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (sudo)."
  exit 1
fi

resolve_run_user() {
  local candidate="${RUN_USER:-${SUDO_USER:-}}"

  if [[ -z "$candidate" ]]; then
    candidate=$(logname 2>/dev/null || true)
  fi

  if [[ -z "$candidate" || "$candidate" == "root" ]]; then
    candidate=$(stat -c '%U' "$APP_DIR" 2>/dev/null || true)
  fi

  if [[ -z "$candidate" || "$candidate" == "root" ]]; then
    echo "nobody"
    return
  fi

  if id -u "$candidate" >/dev/null 2>&1; then
    echo "$candidate"
  else
    echo "nobody"
  fi
}

if [[ ! -f "$APP_DIR/app.py" ]]; then
  if [[ -z "$BUNDLE_TARBALL" || ! -f "$BUNDLE_TARBALL" ]]; then
    echo "Application sources were not found in $APP_DIR"
    echo "Pass path to bundle tar.gz as first argument, for example:"
    echo "  sudo $0 /tmp/motion-detection-rpi4.tar.gz"
    exit 1
  fi

  tmp_extract_dir=$(mktemp -d)
  tar -xzf "$BUNDLE_TARBALL" -C "$tmp_extract_dir"

  extracted_dir=$(find "$tmp_extract_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)
  if [[ -z "$extracted_dir" ]]; then
    echo "Failed to unpack $BUNDLE_TARBALL"
    exit 1
  fi

  mkdir -p "$APP_DIR"
  cp -a "$extracted_dir"/. "$APP_DIR"/
  rm -rf "$tmp_extract_dir"
fi

apt update
apt install -y python3 python3-venv python3-pip python3-opencv git

python3 -m venv --system-site-packages "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip wheel
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements-rpi.txt"

RUN_USER=$(resolve_run_user)

install -d -m 0755 "$CONFIG_DIR"
if [[ -f "$APP_DIR/config.json" && ! -f "$CONFIG_PATH" ]]; then
  cp "$APP_DIR/config.json" "$CONFIG_PATH"
fi

cat > /usr/local/bin/motion-detection <<EOF
#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$APP_DIR"
CONFIG_PATH="$CONFIG_PATH"
SERVICE_NAME="$SERVICE_NAME"

log_update() {
  local message="\$1"
  echo "\$message"
  if command -v logger >/dev/null 2>&1; then
    logger -t motion-detection-update "\$message"
  fi
}

run_update() {
  if ! git -C "\$APP_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    log_update "[update] Skip: \$APP_DIR is not a git repository."
    return 1
  fi

  local service_was_active=0
  if systemctl is-active --quiet "\$SERVICE_NAME"; then
    service_was_active=1
    log_update "[update] Stopping \$SERVICE_NAME for update."
    systemctl stop "\$SERVICE_NAME"
  else
    log_update "[update] \$SERVICE_NAME is already stopped."
  fi

  trap 'if [[ "\$service_was_active" -eq 1 ]]; then log_update "[update] Restarting \$SERVICE_NAME after failed update."; systemctl start "\$SERVICE_NAME"; fi' ERR

  log_update "[update] Pulling latest changes from git."
  git -C "\$APP_DIR" fetch --all --prune
  git -C "\$APP_DIR" pull --ff-only

  if [[ -f "\$APP_DIR/requirements-rpi.txt" && -x "\$APP_DIR/.venv/bin/pip" ]]; then
    log_update "[update] Installing Python dependencies."
    "\$APP_DIR/.venv/bin/pip" install -r "\$APP_DIR/requirements-rpi.txt"
  fi

  log_update "[update] Starting \$SERVICE_NAME after update."
  systemctl start "\$SERVICE_NAME"
  trap - ERR
  log_update "[update] Update completed successfully."
}

update_requested=0
forwarded_args=()
for arg in "\$@"; do
  if [[ "\$arg" == "--update" ]]; then
    update_requested=1
    continue
  fi
  forwarded_args+=("\$arg")
done

if [[ "\$update_requested" -eq 1 ]]; then
  if [[ "\${#forwarded_args[@]}" -gt 0 ]]; then
    log_update "[update] Ignoring extra args during update: \${forwarded_args[*]}"
  fi
  run_update
  exit \$?
fi

exec "\$APP_DIR/.venv/bin/python" "\$APP_DIR/app.py" --config "\$CONFIG_PATH" "\${forwarded_args[@]}"
EOF
chmod 0755 /usr/local/bin/motion-detection

install -m 0644 "$APP_DIR/deploy/systemd/motion-detection.service" "/etc/systemd/system/$SERVICE_NAME"
sed -i "s|^User=.*|User=$RUN_USER|" "/etc/systemd/system/$SERVICE_NAME"

if [[ "$RUN_USER" != "nobody" ]]; then
  chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"
  chown -R "$RUN_USER":"$RUN_USER" "$CONFIG_DIR"
else
  echo "Warning: could not detect a non-root local user, skipping chown."
fi

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

echo "Installed and started: $SERVICE_NAME (User=$RUN_USER)"
systemctl --no-pager --full status "$SERVICE_NAME" || true
