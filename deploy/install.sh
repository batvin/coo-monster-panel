#!/usr/bin/env bash
# Installer for Coo Monster Panel. Run from the repository root:
#   sudo bash deploy/install.sh
# Only the web panel password is asked. x-ui, Telegram bot and SOCKS5 settings
# can be entered later in the terminal panel (coo-monster) or the web panel.
# Advanced non-interactive example (everything except the password stays optional):
#   ADMIN_WEB_PASSWORD=... sudo -E bash deploy/install.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
PY="${PYTHON_BIN:-$(command -v python3 || true)}"

usage() {
  echo "Usage: sudo bash deploy/install.sh [--reconfigure] [--dry-run]"
  echo "  --reconfigure  ask for a new web panel password even if it is already set"
  echo "  --dry-run      validate and show the plan without writing or starting services"
  echo "  The only interactive question is the web panel password."
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

ask_secret() {
  local name="$1" label="$2" value=""
  if [ -n "${!name+x}" ]; then return 0; fi
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
VPN_PORT="${VPN_PORT:-3001}"

if [ -f config.env ] && [ "$RECONFIGURE" -eq 0 ]; then
  echo "Keeping existing config.env."
  SERVER_NAME="$(existing config.env SERVER_NAME)"
  XUI_HOST="$(existing config.env XUI_HOST)"
  XUI_BASE_PATH="$(existing config.env XUI_BASE_PATH)"
  PUBLIC_HOST="$(existing config.env PUBLIC_HOST)"
  XRAY_BIN="$(existing config.env XRAY_BIN)"
else
  echo "x-ui settings are not requested during install; configure them later in coo-monster."
fi
: "${SERVER_NAME:=Main server}"
: "${XUI_HOST:=http://127.0.0.1:2053}"
: "${XUI_BASE_PATH:=}"
: "${PUBLIC_HOST:=}"
: "${XRAY_BIN:=/usr/local/x-ui/bin/xray-linux-amd64}"

if [ -f xui.env ] && [ "$RECONFIGURE" -eq 0 ]; then
  echo "Keeping existing xui.env."
  XUI_API_TOKEN="$(existing xui.env XUI_API_TOKEN)"
else
  echo "XUI API token is not requested during install; add it later in the web panel."
fi
: "${XUI_API_TOKEN:=}"

if [ -f secrets.env ]; then
  BOT_TOKEN="$(existing secrets.env BOT_TOKEN)"
  ADMIN_ID="$(existing secrets.env ADMIN_ID)"
  TELEGRAM_SOCKS5="$(existing secrets.env TELEGRAM_SOCKS5)"
fi
if [ -f secrets.env ] && [ "$RECONFIGURE" -eq 0 ]; then
  echo "Keeping existing secrets.env (web password unchanged)."
  ADMIN_WEB_PASSWORD="$(existing secrets.env ADMIN_WEB_PASSWORD)"
else
  ask_secret ADMIN_WEB_PASSWORD "Web panel password (leave empty to generate)"
fi
: "${BOT_TOKEN:=}"
: "${ADMIN_ID:=}"
: "${TELEGRAM_SOCKS5:=}"

if [ -z "${ADMIN_WEB_PASSWORD:-}" ]; then
  ADMIN_WEB_PASSWORD="$($PY -c 'import secrets; print(secrets.token_urlsafe(18))')"
  GENERATED_PASSWORD=1
fi

XUI_HOST="${XUI_HOST%/}"
XUI_BASE_PATH="${XUI_BASE_PATH#/}"
XUI_BASE_PATH="${XUI_BASE_PATH%/}"
[[ -n "$XUI_BASE_PATH" ]] && XUI_BASE_PATH="/$XUI_BASE_PATH"
PUBLIC_HOST="${PUBLIC_HOST#http://}"
PUBLIC_HOST="${PUBLIC_HOST#https://}"
PUBLIC_HOST="${PUBLIC_HOST%/}"

if [ -n "${ADMIN_ID:-}" ] && ! [[ "$ADMIN_ID" =~ ^[0-9]+$ ]]; then
  echo "ERROR: ADMIN_ID must be numeric when provided." >&2; exit 1
fi
if ! [[ "$VPN_PORT" =~ ^[0-9]+$ ]]; then echo "ERROR: VPN_PORT must be numeric." >&2; exit 1; fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY RUN OK. Planned settings:"
  echo "  app dir:     $APP_DIR"
  echo "  service user:$APP_USER"
  echo "  panel port:  $VPN_PORT (bound to 127.0.0.1)"
  echo "  command:     coo-monster"
  echo "  password:    will be stored in secrets.env (not displayed)"
  echo "  x-ui/bot:    not configured during install; set later in coo-monster or the web panel"
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
TELEGRAM_SOCKS5=$TELEGRAM_SOCKS5
EOF

chmod 600 config.env xui.env secrets.env

"$PY" -m py_compile app.py store.py servers.py telegram_bot.py xui_client.py socks5.py control.py

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

mkdir -p /usr/local/bin
APP_DIR="$APP_DIR" "$PY" - <<'PY'
import os
from pathlib import Path

template = Path("deploy/coo-monster.sh.tpl").read_text(encoding="utf-8")
Path("/usr/local/bin/coo-monster").write_text(
    template.replace("__APP_DIR__", os.environ["APP_DIR"]), encoding="utf-8"
)
PY
chmod 755 /usr/local/bin/coo-monster

cp "$GENERATED_UNIT" "$UNIT"
systemctl daemon-reload
systemctl enable --now vpn-bot
rm -f "$GENERATED_UNIT"

for secret_file in servers.json vpn.db .web_secret; do
  [ -f "$secret_file" ] && chmod 600 "$secret_file"
done

sleep 2
if ! systemctl is-active --quiet vpn-bot; then
  echo "ERROR: service failed to start. Check: journalctl -u vpn-bot -n 50" >&2
  exit 1
fi
if command -v curl >/dev/null 2>&1; then
  curl -fsS "http://127.0.0.1:${VPN_PORT}/api/health" && echo
fi

echo "Install complete."
echo "  Panel port: ${VPN_PORT} (bound to 127.0.0.1 only)"
echo "  Open a tunnel from your computer:"
echo "    ssh -N -L ${VPN_PORT}:127.0.0.1:${VPN_PORT} YOUR_USER@YOUR_SERVER"
echo "  Then visit: http://127.0.0.1:${VPN_PORT}/admin"
if [ "$GENERATED_PASSWORD" -eq 1 ]; then
  echo "  Generated web password: $ADMIN_WEB_PASSWORD"
  echo "  Save it now; it is stored only in secrets.env."
else
  echo "  Password: stored in secrets.env (use the one you entered)."
fi
echo "  x-ui token, bot token, admin ID and SOCKS5 are set later in:"
echo "    coo-monster"
echo "  or in the web panel (Servers / Settings)."
echo "  Terminal panel: coo-monster (works from any directory)"
echo "  Alternative:    sudo bash $APP_DIR/deploy/panel.sh"
