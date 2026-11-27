#!/usr/bin/env bash
set -euo pipefail

PURGE=0
case "${1:-}" in
  "") ;;
  --purge|--all) PURGE=1 ;;
  -h|--help)
    echo "Usage: sudo bash deploy/uninstall.sh [--purge]"
    echo "  --purge  also delete the application directory, config, secrets and database"
    exit 0
    ;;
  *)
    echo "ERROR: unknown option: $1" >&2
    exit 1
    ;;
esac

systemctl disable --now vpn-bot 2>/dev/null || true
rm -f /etc/systemd/system/vpn-bot.service || true
rm -f /usr/local/bin/coo-monster || true
systemctl daemon-reload || true

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "$PURGE" -eq 1 ]; then
  echo "This will permanently delete:"
  echo "  $APP_DIR"
  echo "including config.env, secrets.env, xui.env, servers.json and vpn.db."
  printf 'Type DELETE to confirm: '
  read -r answer
  if [ "$answer" = "DELETE" ]; then
    cd /
    rm -rf -- "$APP_DIR"
    echo "Installation removed. Run 'cd ~' to reset your shell directory."
  else
    echo "Cancelled. Service removed but files were kept at: $APP_DIR"
  fi
else
  echo "Service removed. Application files kept at: $APP_DIR"
  echo "Run with --purge to delete the directory and all secrets."
fi
