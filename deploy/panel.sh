#!/usr/bin/env bash
# Open the terminal control panel. Run from anywhere:
#   sudo bash /path/to/coo-monster-panel/deploy/panel.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
PY="${PYTHON_BIN:-$(command -v python3 || true)}"

if [ -z "$PY" ]; then
  echo "ERROR: python3 is required." >&2
  exit 1
fi

if [ "$EUID" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
  echo "برای کنترل سرویس از sudo استفاده میشود (در صورت نیاز رمزتان را وارد کنید)."
fi

exec "$PY" "$APP_DIR/control.py" "$@"
