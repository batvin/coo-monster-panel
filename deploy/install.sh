#!/usr/bin/env bash
# Installer for Coo Monster Panel. Run from the repository root:
#   sudo bash deploy/install.sh
# Non-interactive example:
#   XUI_BASE_PATH=/panelabc PUBLIC_HOST=1.2.3.4 XUI_API_TOKEN=... BOT_TOKEN=... ADMIN_ID=... \
#     sudo -E bash deploy/install.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
PY="${PYTHON_BIN:-$(command -v python3 || true)}"

usage() {
  echo "Usage: sudo bash deploy/install.sh [--reconfigure] [--dry-run]"
  echo "  --reconfigure  prompt again even if config files already exist"
  echo "  --dry-run      validate and show the plan without writing or starting services"
}

if [ -z "$PY" ]; then echo "ERROR: python3 is required." >&2; exit 1; fi
if ! command -v systemctl >/dev/null 2>&1; then echo "ERROR: systemd is required." >&2; exit 1; fi

RECONFIGURE=0
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --reconfigure) RECONFIGURE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $arg" >&2; usage >&2; exit 1 ;;
  esac
done

if [ "$EUID" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
  APP_USER="$SUDO_USER"
else
  APP_USER="$(id -un)"
fi
APP_GROUP="$(id -gn "$APP_USER")"
UNIT="/etc/systemd/system/vpn-bot.service"

ask() {
  local name="$1" label="$2" default="${3:-}" value=""
  if [ -n "${!name:-}" ]; then return 0; fi
  if [ -n "$default" ]; then
    read -r -p "$label [$default]: " value
    value="${value:-$default}"
  else
    read -r -p "$label: " value
  fi
  printf -v "$name" '%s' "$value"
}

ask_secret() {
  local name="$1" label="$2" value=""
  if [ -n "${!name:-}" ]; then return 0; fi
  read -r -s -p "$label: " value
  echo
  printf -v "$name" '%s' "$value"
}

existing() {
  local file="$1" key="$2"
  [ -f "$file" ] || return 0
  grep -m1 "^${key}=" "$file" | cut -d= -f2- || true
}

GENERATED_PASSWORD=0

if [ -f config.env ] && [ "$RECONFIGURE" -eq 0 ]; then
  echo "Keeping existing config.env (use --reconfigure to change it)."
  XUI_HOST="$(existing config.env XUI_HOST)"
  XUI_BASE_PATH="$(existing config.env XUI_BASE_PATH)"
  PUBLIC_HOST="$(existing config.env PUBLIC_HOST)"
  SERVER_NAME="$(existing config.env SERVER_NAME)"
  XRAY_BIN="$(existing config.env XRAY_BIN)"
else
  ask XUI_HOST "3x-ui API host" "http://127.0.0.1:2053"
  ask XUI_BASE_PATH "3x-ui base path from your panel URL (e.g. /panelabc123)"
  ask PUBLIC_HOST "Public IP or domain used in client config links"
  ask SERVER_NAME "Server name" "Main server"
  ask XRAY_BIN "Path to xray binary" "/usr/local/x-ui/bin/xray-linux-amd64"
fi

if [ -f xui.env ] && [ "$RECONFIGURE" -eq 0 ]; then
  echo "Keeping existing xui.env."
  XUI_API_TOKEN="$(existing xui.env XUI_API_TOKEN)"
else
  ask_secret XUI_API_TOKEN "3x-ui API token"
fi

if [ -f secrets.env ] && [ "$RECONFIGURE" -eq 0 ]; then
  echo "Keeping existing secrets.env."
  BOT_TOKEN="$(existing secrets.env BOT_TOKEN)"
  ADMIN_ID="$(existing secrets.env ADMIN_ID)"
  ADMIN_WEB_PASSWORD="$(existing secrets.env ADMIN_WEB_PASSWORD)"
else
  ask_secret BOT_TOKEN "Telegram bot token from @BotFather"
  ask ADMIN_ID "Your numeric Telegram ID from @userinfobot"
  ask_secret ADMIN_WEB_PASSWORD "Web panel password (leave empty to generate)"
fi

ask VPN_PORT "Local web panel port" "3001"

XUI_HOST="${XUI_HOST%/}"
[[ "$XUI_BASE_PATH" == /* ]] || XUI_BASE_PATH="/$XUI_BASE_PATH"
XUI_BASE_PATH="${XUI_BASE_PATH%/}"
PUBLIC_HOST="${PUBLIC_HOST#http://}"
PUBLIC_HOST="${PUBLIC_HOST#https://}"
PUBLIC_HOST="${PUBLIC_HOST%/}"

for required in XUI_BASE_PATH PUBLIC_HOST XUI_API_TOKEN BOT_TOKEN ADMIN_ID ADMIN_WEB_PASSWORD; do
  if [ -z "${!required:-}" ]; then echo "ERROR: $required must not be empty." >&2; exit 1; fi
done
if ! [[ "$ADMIN_ID" =~ ^[0-9]+$ ]]; then echo "ERROR: ADMIN_ID must be numeric." >&2; exit 1; fi
if ! [[ "$VPN_PORT" =~ ^[0-9]+$ ]]; then echo "ERROR: VPN_PORT must be numeric." >&2; exit 1; fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY RUN OK. Planned settings:"
  echo "  app dir:     $APP_DIR"
  echo "  service user:$APP_USER"
  echo "  x-ui host:   $XUI_HOST$XUI_BASE_PATH"
  echo "  public host: $PUBLIC_HOST"
  echo "  panel port:  $VPN_PORT"
  echo "  token/bot/password: provided (not displayed)"
  exit 0
fi

umask 077
cat > config.env <<EOF
SERVER_NAME=$SERVER_NAME
XUI_HOST=$XUI_HOST
XUI_BASE_PATH=$XUI_BASE_PATH
PUBLIC_HOST=$PUBLIC_HOST
XRAY_BIN=$XRAY_BIN
EOF

cat > xui.env <<EOF
XUI_API_TOKEN=$XUI_API_TOKEN
EOF

if [ -z "${ADMIN_WEB_PASSWORD:-}" ]; then
  ADMIN_WEB_PASSWORD="$($PY -c 'import secrets; print(secrets.token_urlsafe(18))')"
  GENERATED_PASSWORD=1
fi
cat > secrets.env <<EOF
BOT_TOKEN=$BOT_TOKEN
ADMIN_ID=$ADMIN_ID
ADMIN_WEB_PASSWORD=$ADMIN_WEB_PASSWORD
EOF

chmod 600 config.env xui.env secrets.env

"$PY" -m py_compile app.py store.py servers.py telegram_bot.py xui_client.py

GENERATED_UNIT="$APP_DIR/.vpn-bot.service.generated"
APP_USER="$APP_USER" APP_GROUP="$APP_GROUP" APP_DIR="$APP_DIR" PY="$PY" VPN_PORT="$VPN_PORT" \
  "$PY" - <<'PY'
import os
from pathlib import Path

template = Path("deploy/vpn-bot.service.tpl").read_text(encoding="utf-8")
for key in ("APP_USER", "APP_GROUP", "APP_DIR", "PY", "VPN_PORT"):
    template = template.replace("__" + key + "__", os.environ[key])
Path(os.environ["APP_DIR"] + "/.vpn-bot.service.generated").write_text(template, encoding="utf-8")
PY

chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"
cp "$GENERATED_UNIT" "$UNIT"
systemctl daemon-reload
systemctl enable --now vpn-bot
rm -f "$GENERATED_UNIT"

sleep 2
if ! systemctl is-active --quiet vpn-bot; then
  echo "ERROR: service failed to start. Check: journalctl -u vpn-bot -n 50" >&2
  exit 1
fi
if command -v curl >/dev/null 2>&1; then
  curl -fsS "http://127.0.0.1:${VPN_PORT}/api/health" && echo
fi

echo "Install complete."
echo "  Web panel: http://127.0.0.1:${VPN_PORT}/admin (open through an SSH tunnel)"
echo "  Tunnel:    ssh -N -L ${VPN_PORT}:127.0.0.1:${VPN_PORT} user@your-server"
if [ "$GENERATED_PASSWORD" -eq 1 ]; then
  echo "  Generated web password: $ADMIN_WEB_PASSWORD"
  echo "  Save it now; it is stored only in secrets.env."
fi
echo "  Telegram:  send /start to your bot"
