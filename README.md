# Coo Monster Panel

Coo Monster Panel — self-hosted VPN selling system: a Telegram bot for customers, a neon 3D web admin panel, and a multi-server [3x-ui](https://github.com/MHSanaei/3x-ui) client manager. Python standard library only — no pip packages, no CDN.

Everything is installed with your own server details. The repository contains no real tokens, passwords, IPs, domains, customer data, or database.

## Features

- Telegram bot: plans, purchase orders, wallet, services, support, status, broadcast and admin commands
- Sectioned bot menus with dedicated pages and back buttons
- Per-service management: share links, renew, add days, add traffic, change UUID, on/off toggle, delete
- Approval-based wallet top-ups and service renewals, with a dedicated **شارژ اضافه** queue in the admin panel
- Web admin at `127.0.0.1:3001/admin` with animated glass/neon UI and bundled Three.js
- Terminal control panel for service control, logs, health, bot credentials and proxy settings
- Optional SOCKS5 proxy for Telegram API, for servers that cannot reach Telegram directly
- Multi-server registry: add/edit/delete x-ui servers, live inbound discovery, auto-sync and alerts
- Customers, orders, configs, plans, promo codes, notifications, audit log, insights and JSON backup
- No outbound CDN dependency; the panel is reachable only through an SSH tunnel

## Requirements

- Ubuntu 22.04 / 24.04 (or another systemd-based Linux distro)
- Python 3.8+ and `curl`
- A working 3x-ui installation with API access enabled (details entered after install)
- A Telegram bot token from [@BotFather](https://t.me/BotFather) and your numeric ID from [@userinfobot](https://t.me/userinfobot) — both entered after install

## Quick install

```bash
git clone https://github.com/batvin/coo-monster-panel.git
cd coo-monster-panel
sudo bash deploy/install.sh
```

The installer asks for **only one thing**: the web panel password. Type your own, or leave it empty to generate a strong one. It never asks for x-ui details, bot token, admin ID, SOCKS5 or the port.

After install it prints the local port and the exact SSH tunnel command to reach the panel.

x-ui host/path/token, public host, bot token, admin ID and SOCKS5 are configured later in:

- the terminal panel: `coo-monster`
- the web panel: **Servers** and **Settings**

It writes `config.env`, `secrets.env` and `xui.env` (all mode `600`), installs `/etc/systemd/system/vpn-bot.service`, starts `vpn-bot`, and checks `/api/health`.

Validate first without changing anything:

```bash
ADMIN_WEB_PASSWORD=dummy bash deploy/install.sh --dry-run
```

`--reconfigure` only asks for a new web panel password; it keeps existing x-ui, bot and proxy settings:

```bash
sudo bash deploy/install.sh --reconfigure
```

## Access the panel

The service binds to `127.0.0.1` only. Open a tunnel from your computer:

```bash
ssh -N -L 3001:127.0.0.1:3001 YOUR_USER@YOUR_SERVER
```

Then visit <http://127.0.0.1:3001/admin> and log in with `ADMIN_WEB_PASSWORD` from `secrets.env`.

Inside the web panel, open **Settings → Bot credentials and Telegram connection**. Enter the bot token and numeric admin ID there (leave a field empty to keep its current value), optionally add a SOCKS5 address, then press **Save**. The service is restarted automatically so the bot picks up the new values. **Test Telegram** calls `getMe` without exposing the token, and **Remove proxy** clears the SOCKS5 setting.

## Terminal control panel

Everything needed for day-to-day control is also available in an English-language terminal panel, without opening a browser:

After installation, the shortcut is available from any directory:

```bash
coo-monster
```

The installer writes `/usr/local/bin/coo-monster` to point at the installed panel. You can still open it directly:

```bash
sudo bash deploy/panel.sh
# or
sudo python3 control.py
```

Menu:

1. Service status and health (bot configured, proxy state, x-ui state)
2. Start / stop / restart the service
3. View logs (`journalctl`; includes a live-follow option)
4. Telegram bot settings — set the **bot token** and **admin ID**, or test the connection
5. SOCKS5 settings
6. Change the web panel password
7. List configured x-ui servers (tokens are masked)

If you skipped the bot token and admin ID during installation, choose **4** after installation, enter both values, and accept the restart prompt. The bot becomes active right away. Secrets are hidden while typing, masked on screen, and written to `secrets.env` with mode `600`.

## SOCKS5 for Telegram

If the server cannot reach `api.telegram.org` directly, add a SOCKS5 proxy in menu **5**:

```text
socks5://user:pass@1.2.3.4:1080
socks5://1.2.3.4:1080
```

The proxy is used **only** for Telegram API calls; x-ui and the web panel are unaffected. Use menu **3** in that section to test `getMe` through the proxy. To stop using it, choose **2** (remove proxy) and restart the service.

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
socks5.py           Minimal SOCKS5 client for Telegram (stdlib only)
control.py          Terminal control panel
static/             Admin UI (Three.js bundled in static/vendor)
deploy/install.sh   Interactive installer
deploy/panel.sh     Launcher for the terminal panel
deploy/uninstall.sh Service removal
```

Runtime files created on the server (`config.env`, `secrets.env`, `xui.env`, `servers.json`, `vpn.db`) are git-ignored. Never commit them.

## Backup and update

- Download JSON backup from the **پشتیبان** section of the panel.
- To update: `git pull`, then `sudo systemctl restart vpn-bot`, or use menu **2 → Restart** in the terminal panel.
- To remove the service: `sudo bash deploy/uninstall.sh`.

## Security notes

- Keep the panel bound to `127.0.0.1`; use an SSH tunnel, not a public port.
- Never expose `secrets.env`, `xui.env`, `servers.json` or the SQLite database.
- Change the web password from the generated default if you typed your own weak one.
- Ports `443` and `8443` belong to Xray / 3x-ui; this app only uses the local admin port.

## License

MIT — see [LICENSE](LICENSE).
