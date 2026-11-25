# Coo Monster Panel

Coo Monster Panel — self-hosted VPN selling system: a Telegram bot for customers, a neon 3D web admin panel, and a multi-server [3x-ui](https://github.com/MHSanaei/3x-ui) client manager. Python standard library only — no pip packages, no CDN.

Everything is installed with your own server details. The repository contains no real tokens, passwords, IPs, domains, customer data, or database.

## Features

- Telegram bot: plans, purchase orders, wallet, services, support, status, broadcast and admin commands
- Sectioned bot menus with dedicated pages and back buttons
- Per-service management: share links, renew, add days, add traffic, change UUID, on/off toggle, delete
- Approval-based wallet top-ups and service renewals, with a dedicated **شارژ اضافه** queue in the admin panel
- Web admin at `127.0.0.1:3001/admin` with animated glass/neon UI and bundled Three.js
- Multi-server registry: add/edit/delete x-ui servers, live inbound discovery, auto-sync and alerts
- Customers, orders, configs, plans, promo codes, notifications, audit log, insights and JSON backup
- No outbound CDN dependency; the panel is reachable only through an SSH tunnel

## Requirements

- Ubuntu 22.04 / 24.04 (or another systemd-based Linux distro)
- Python 3.8+ and `curl`
- A working 3x-ui installation with API access enabled
- A Telegram bot token from [@BotFather](https://t.me/BotFather) and your numeric ID from [@userinfobot](https://t.me/userinfobot)

## Quick install

```bash
git clone https://github.com/batvin/coo-monster-panel.git
cd coo-monster-panel
sudo bash deploy/install.sh
```

The installer asks for:

| Value | Where to get it |
| --- | --- |
| 3x-ui API host | Usually `http://127.0.0.1:2053` |
| 3x-ui base path | The hidden path in your panel URL, e.g. `/panelabc123` |
| Public host | Your server IP or domain — used in customer config links |
| 3x-ui API token | Your 3x-ui panel API token |
| Bot token | From @BotFather |
| Admin ID | Your numeric Telegram ID |
| Web password | Type your own, or leave empty to generate a strong one |
| Web port | Default `3001` |

It writes `config.env`, `secrets.env` and `xui.env` (all mode `600`), installs `/etc/systemd/system/vpn-bot.service`, starts `vpn-bot`, and checks `/api/health`.

Validate first without changing anything:

```bash
bash deploy/install.sh --dry-run
# provide values via env for a fully non-interactive check
XUI_BASE_PATH=/panelabc PUBLIC_HOST=1.2.3.4 XUI_API_TOKEN=x BOT_TOKEN=x ADMIN_ID=123 \
  ADMIN_WEB_PASSWORD=x bash deploy/install.sh --dry-run
```

Change settings later:

```bash
sudo bash deploy/install.sh --reconfigure
```

## Access the panel

The service binds to `127.0.0.1` only. Open a tunnel from your computer:

```bash
ssh -N -L 3001:127.0.0.1:3001 YOUR_USER@YOUR_SERVER
```

Then visit <http://127.0.0.1:3001/admin> and log in with `ADMIN_WEB_PASSWORD` from `secrets.env`.

## Telegram bot

Send `/start` to your bot. Useful commands:

```text
/plans  /buy <id>  /my  /balance  /support  /status
/orders /admin /servers /broadcast /sync /help
```

Customer flow for top-ups:

1. Customer requests a wallet charge, extra days, or extra GB.
2. A pending request appears in the web panel's **💳 شارژ اضافه** section and an inline approval message goes to the admin.
3. Admin records the payment code, then approves or rejects.
4. Only after approval is the wallet credited or the service extended in x-ui.

Viewing a service never triggers a "new service" alert to the admin.

## Changing the public host

Edit the server's **هاست عمومی** in the web panel. Existing VLESS and Shadowsocks links are rewritten automatically, and links are also re-targeted when shown to customers.

## Files

```text
app.py              Web server and admin API
telegram_bot.py     Bot and callback flows
store.py            SQLite schema and data helpers
servers.py          Multi-server registry
xui_client.py       3x-ui API client
static/             Admin UI (Three.js bundled in static/vendor)
deploy/install.sh   Interactive installer
deploy/uninstall.sh Service removal
```

Runtime files created on the server (`config.env`, `secrets.env`, `xui.env`, `servers.json`, `vpn.db`) are git-ignored. Never commit them.

## Backup and update

- Download JSON backup from the **پشتیبان** section of the panel.
- To update: `git pull`, then `sudo systemctl restart vpn-bot`.
- To remove the service: `sudo bash deploy/uninstall.sh`.

## Security notes

- Keep the panel bound to `127.0.0.1`; use an SSH tunnel, not a public port.
- Never expose `secrets.env`, `xui.env`, `servers.json` or the SQLite database.
- Change the web password from the generated default if you typed your own weak one.
- Ports `443` and `8443` belong to Xray / 3x-ui; this app only uses the local admin port.

## License

MIT — see [LICENSE](LICENSE).
