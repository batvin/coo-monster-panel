#!/usr/bin/env bash
set -euo pipefail
systemctl disable --now vpn-bot 2>/dev/null || true
rm -f /etc/systemd/system/vpn-bot.service
systemctl daemon-reload
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "Service removed. Application files kept at: $APP_DIR"
echo "Delete that directory manually if you no longer need it."
