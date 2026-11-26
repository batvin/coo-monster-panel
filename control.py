#!/usr/bin/env python3
"""Coo Monster Panel — terminal control panel.

Run after installation with::

    sudo bash deploy/panel.sh

The panel can start/stop/restart the service, read health and logs, and set the
Telegram bot token, admin ID, web password and an optional SOCKS5 proxy for
Telegram. Secrets are hidden on screen and stored with mode 600.
"""

import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

try:
    import grp
    import pwd
except ImportError:  # pragma: no cover - Windows dev machines only
    grp = None
    pwd = None

import socks5

ROOT = Path(__file__).resolve().parent
SECRETS_FILE = ROOT / "secrets.env"
UNIT_FILE = Path("/etc/systemd/system/vpn-bot.service")
SERVICE = os.environ.get("VPN_SERVICE", "vpn-bot")


def load_env(path):
    data = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def is_root():
    return hasattr(os, "geteuid") and os.geteuid() == 0


def app_identity():
    user = ""
    if UNIT_FILE.exists():
        match = re.search(r"^User=(\S+)", UNIT_FILE.read_text(encoding="utf-8", errors="replace"), re.M)
        if match:
            user = match.group(1)
    user = user or os.environ.get("SUDO_USER") or getpass.getuser()
    if not pwd or not grp:
        return None
    try:
        return pwd.getpwnam(user).pw_uid, grp.getgrnam(user).gr_gid
    except (KeyError, OSError):
        try:
            account = pwd.getpwnam(user)
            return account.pw_uid, account.pw_gid
        except (KeyError, OSError):
            return None


def save_env(path, updates):
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    result = []
    seen = set()
    for line in lines:
        stripped = line.strip()
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in updates and stripped and not stripped.startswith("#"):
            result.append("{}={}".format(key, updates[key]))
            seen.add(key)
        else:
            result.append(line)
    for key, value in updates.items():
        if key not in seen:
            result.append("{}={}".format(key, value))

    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text("\n".join(result) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    if is_root():
        identity = app_identity()
        if identity:
            try:
                os.chown(temporary, identity[0], identity[1])
            except OSError:
                pass
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def mask(value, keep=4):
    value = str(value or "")
    if not value:
        return "not set"
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]


def sudo_prefix():
    if is_root() or not shutil.which("sudo"):
        return []
    return ["sudo"]


def run_command(args, capture=False):
    command = sudo_prefix() + list(args)
    try:
        return subprocess.run(
            command, check=False, text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except FileNotFoundError:
        if capture:
            return subprocess.CompletedProcess(command, 127, "", "command not found")
        print("command not found: {}".format(command[0]))
        return subprocess.CompletedProcess(command, 127, None, None)


def service_state():
    active = run_command(["systemctl", "is-active", SERVICE], capture=True)
    enabled = run_command(["systemctl", "is-enabled", SERVICE], capture=True)
    return (
        (active.stdout or "").strip() or "unknown",
        (enabled.stdout or "").strip() or "unknown",
    )


def panel_port():
    value = os.environ.get("VPN_PORT", "").strip()
    if value.isdigit():
        return int(value)
    if UNIT_FILE.exists():
        match = re.search(r"Environment=VPN_PORT=(\d+)", UNIT_FILE.read_text(encoding="utf-8", errors="replace"))
        if match:
            return int(match.group(1))
    return 3001


def health():
    url = "http://127.0.0.1:{}/api/health".format(panel_port())
    try:
        with urllib.request.urlopen(url, timeout=4) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def sanitize(text, secrets):
    text = str(text)
    for value in secrets:
        if value and len(value) > 3:
            text = text.replace(value, "***")
    return text[:300]


def secrets_values(secrets, proxy=""):
    values = [secrets.get("BOT_TOKEN", ""), secrets.get("ADMIN_WEB_PASSWORD", "")]
    if proxy:
        values.append(proxy)
        proxy_cfg = None
        try:
            proxy_cfg = socks5.parse_proxy(proxy)
        except socks5.Socks5Error:
            pass
        if proxy_cfg and proxy_cfg.get("password"):
            values.append(proxy_cfg["password"])
    return [v for v in values if v]


def test_telegram(token, proxy=""):
    secrets = load_env(SECRETS_FILE)
    secrets["BOT_TOKEN"] = token
    url = "https://api.telegram.org/bot{}/getMe".format(token)
    try:
        if proxy:
            status, body = socks5.request(url, timeout=12, proxy=proxy)
            text = body.decode("utf-8", "replace")
            if status >= 400:
                return False, "HTTP {}: {}".format(status, sanitize(text, secrets_values(secrets, proxy)))
            data = json.loads(text)
        else:
            with urllib.request.urlopen(url, timeout=12) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
        if data.get("ok"):
            return True, "Telegram connection OK — bot @{username}".format(
                username=data.get("result", {}).get("username", "?")
            )
        return False, sanitize(data.get("description") or "invalid response", secrets_values(secrets, proxy))
    except Exception as exc:
        return False, "{}: {}".format(
            type(exc).__name__, sanitize(exc, secrets_values(secrets, proxy))
        )


def ask_yes_no(prompt, default=True):
    suffix = " [y/n] (default: y): " if default else " [y/n] (default: n): "
    answer = input(prompt + suffix).strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes", "1")


def pause():
    try:
        input("\nPress Enter to continue...")
    except (EOFError, KeyboardInterrupt):
        print()


def restart_if_asked():
    if ask_yes_no("Restart the service now?", True):
        print("Restarting service...")
        run_command(["systemctl", "restart", SERVICE])
        time.sleep(2)
        active, _ = service_state()
        result = health()
        print("Service: {} | health: {}".format(active, "OK" if result.get("ok") else "ERROR"))
        if not result.get("ok"):
            print("Check logs: journalctl -u {} -n 50 --no-pager".format(SERVICE))
    pause()


def show_status():
    secrets = load_env(SECRETS_FILE)
    active, enabled = service_state()
    result = health()
    port = panel_port()
    print("\n=== Service status ===")
    print("Service:       {} / {}".format(active, enabled))
    print("Web panel:    http://127.0.0.1:{}/admin".format(port))
    print("SSH tunnel:   ssh -N -L {0}:127.0.0.1:{0} user@your-server".format(port))
    print("Telegram bot:  {}".format("active" if result.get("bot") else "not set"))
    print("Bot token:    {}".format(mask(secrets.get("BOT_TOKEN", ""), 6)))
    print("Admin ID:     {}".format(secrets.get("ADMIN_ID") or "not set"))
    print("Telegram proxy: {}".format(socks5.mask_proxy(secrets.get("TELEGRAM_SOCKS5", ""))))
    xui = result.get("xui") or {}
    if isinstance(xui, dict):
        print("x-ui:         {}".format(
            "OK ({} inbound)".format(xui.get("inbounds")) if xui.get("ok") else (xui.get("error") or "ERROR")
        ))
    print("Overall health: {}".format("OK" if result.get("ok") else (result.get("error") or "ERROR")))
    pause()


def service_menu():
    while True:
        active, enabled = service_state()
        print("\n=== Service control ({}) ===".format(active))
        print("1) Start")
        print("2) Stop")
        print("3) Restart")
        print("4) Detailed status")
        print("0) Back")
        choice = input("Choice: ").strip()
        if choice == "1":
            run_command(["systemctl", "start", SERVICE])
            print("Service started.")
        elif choice == "2":
            run_command(["systemctl", "stop", SERVICE])
            print("Service stopped.")
        elif choice == "3":
            run_command(["systemctl", "restart", SERVICE])
            time.sleep(2)
            print("Service restarted.")
        elif choice == "4":
            run_command(["systemctl", "status", SERVICE, "--no-pager", "-l"])
        elif choice == "0":
            return
        else:
            print("Invalid choice.")
        if choice in ("1", "2", "3"):
            pause()


def logs_menu():
    while True:
        print("\n=== Logs ===")
        print("1) Last 50 lines")
        print("2) Last 200 lines")
        print("3) Live follow (Ctrl+C to stop)")
        print("0) Back")
        choice = input("Choice: ").strip()
        if choice == "1":
            run_command(["journalctl", "-u", SERVICE, "-n", "50", "--no-pager"])
        elif choice == "2":
            run_command(["journalctl", "-u", SERVICE, "-n", "200", "--no-pager"])
        elif choice == "3":
            print("Press Ctrl+C to stop.")
            run_command(["journalctl", "-u", SERVICE, "-f"])
        elif choice == "0":
            return
        else:
            print("Invalid choice.")
        if choice in ("1", "2"):
            pause()


def bot_menu():
    while True:
        secrets = load_env(SECRETS_FILE)
        print("\n=== Bot settings ===")
        print("Token:       {}".format(mask(secrets.get("BOT_TOKEN", ""), 6)))
        print("Admin ID:    {}".format(secrets.get("ADMIN_ID") or "not set"))
        print("1) Set bot token and admin ID (recommended)")
        print("2) Change bot token")
        print("3) Change admin ID")
        print("4) Test Telegram connection")
        print("0) Back")
        choice = input("Choice: ").strip()

        if choice in ("1", "2", "3"):
            updates = {}
            if choice in ("1", "2"):
                value = getpass.getpass("Bot token from @BotFather: ").strip()
                if ":" not in value or len(value) < 20:
                    print("Token looks invalid. Expected format: 123456:ABC-...")
                    continue
                updates["BOT_TOKEN"] = value
            if choice in ("1", "3"):
                value = input("Numeric Telegram ID from @userinfobot: ").strip()
                if not value.isdigit():
                    print("Admin ID must be numeric.")
                    continue
                updates["ADMIN_ID"] = value
            save_env(SECRETS_FILE, updates)
            print("Bot settings saved.")
            restart_if_asked()
        elif choice == "4":
            secrets = load_env(SECRETS_FILE)
            token = secrets.get("BOT_TOKEN", "")
            if not token:
                print("Set the bot token first.")
            else:
                ok, message = test_telegram(token, secrets.get("TELEGRAM_SOCKS5", ""))
                print("{} — {}".format("OK" if ok else "FAILED", message))
            pause()
        elif choice == "0":
            return
        else:
            print("Invalid choice.")


def proxy_menu():
    while True:
        secrets = load_env(SECRETS_FILE)
        current = secrets.get("TELEGRAM_SOCKS5", "")
        print("\n=== SOCKS5 for Telegram ===")
        print("Current:     {}".format(socks5.mask_proxy(current)))
        print("This proxy is used only for Telegram API calls from the bot.")
        print("1) Set / change SOCKS5")
        print("2) Remove proxy")
        print("3) Test Telegram through proxy")
        print("0) Back")
        choice = input("Choice: ").strip()

        if choice == "1":
            print("Example: socks5://user:pass@1.2.3.4:1080  or  socks5://1.2.3.4:1080")
            value = input("SOCKS5 address: ").strip()
            try:
                socks5.parse_proxy(value)
            except socks5.Socks5Error as exc:
                print("Invalid address: {}".format(exc))
                continue
            save_env(SECRETS_FILE, {"TELEGRAM_SOCKS5": value})
            print("Proxy saved: {}".format(socks5.mask_proxy(value)))
            restart_if_asked()
        elif choice == "2":
            save_env(SECRETS_FILE, {"TELEGRAM_SOCKS5": ""})
            print("Proxy removed.")
            restart_if_asked()
        elif choice == "3":
            secrets = load_env(SECRETS_FILE)
            token = secrets.get("BOT_TOKEN", "")
            if not token:
                print("Set the bot token first.")
            elif not secrets.get("TELEGRAM_SOCKS5"):
                print("Set a SOCKS5 address first.")
            else:
                ok, message = test_telegram(token, secrets["TELEGRAM_SOCKS5"])
                print("{} — {}".format("OK" if ok else "FAILED", message))
            pause()
        elif choice == "0":
            return
        else:
            print("Invalid choice.")


def password_menu():
    print("\n=== Web panel password ===")
    value = getpass.getpass("New web panel password: ").strip()
    if len(value) < 8:
        print("Password must be at least 8 characters.")
        pause()
        return
    save_env(SECRETS_FILE, {"ADMIN_WEB_PASSWORD": value})
    print("Web panel password changed (not displayed).")
    restart_if_asked()


def servers_menu():
    print("\n=== x-ui servers ===")
    try:
        import servers
        items = servers.list_servers()
        if not items:
            print("No servers configured.")
        for server in items:
            public = servers.public_server(server)
            print(
                "#{} | {} | {} | public host: {} | token: {}".format(
                    public.get("id"), public.get("name"), public.get("host"),
                    public.get("public_host") or "-", public.get("token_mask") or "-",
                )
            )
    except Exception as exc:
        print("Error reading servers: {}".format(sanitize(exc, [])))
    pause()


def main():
    actions = {
        "1": show_status,
        "2": service_menu,
        "3": logs_menu,
        "4": bot_menu,
        "5": proxy_menu,
        "6": password_menu,
        "7": servers_menu,
    }
    while True:
        print("\n" + "=" * 54)
        print(" Coo Monster Panel - Terminal ".center(50))
        print("=" * 54)
        print("1) Service status and health")
        print("2) Service control (start / stop / restart)")
        print("3) View logs")
        print("4) Bot settings (token and admin ID)")
        print("5) SOCKS5 settings for Telegram")
        print("6) Change web panel password")
        print("7) List servers")
        print("0) Exit")
        try:
            choice = input("Choice: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye")
            return
        if choice == "0":
            print("Goodbye")
            return
        action = actions.get(choice)
        if action:
            try:
                action()
            except (EOFError, KeyboardInterrupt):
                print("\nBack to the main menu.")
        else:
            print("Invalid choice.")


if __name__ == "__main__":
    main()
