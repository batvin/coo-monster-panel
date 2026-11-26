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
        return "تنظیم نشده"
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
        print("دستور پیدا نشد: {}".format(command[0]))
        return subprocess.CompletedProcess(command, 127, None, None)


def service_state():
    active = run_command(["systemctl", "is-active", SERVICE], capture=True)
    enabled = run_command(["systemctl", "is-enabled", SERVICE], capture=True)
    return (
        (active.stdout or "").strip() or "نامشخص",
        (enabled.stdout or "").strip() or "نامشخص",
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
            return True, "اتصال تلگرام برقرار است — ربات @{username}".format(
                username=data.get("result", {}).get("username", "?")
            )
        return False, sanitize(data.get("description") or "پاسخ نامعتبر", secrets_values(secrets, proxy))
    except Exception as exc:
        return False, "{}: {}".format(
            type(exc).__name__, sanitize(exc, secrets_values(secrets, proxy))
        )


def ask_yes_no(prompt, default=True):
    suffix = " [ب/ن] (پیشفرض: ب): " if default else " [ب/ن] (پیشفرض: ن): "
    answer = input(prompt + suffix).strip().lower()
    if not answer:
        return default
    return answer in ("ب", "y", "yes", "1")


def pause():
    try:
        input("\nبرای ادامه Enter بزنید...")
    except (EOFError, KeyboardInterrupt):
        print()


def restart_if_asked():
    if ask_yes_no("سرویس همین حالا ریستارت شود؟", True):
        print("در حال ریستارت سرویس...")
        run_command(["systemctl", "restart", SERVICE])
        time.sleep(2)
        active, _ = service_state()
        result = health()
        print("وضعیت سرویس: {} | سلامت: {}".format(active, "OK" if result.get("ok") else "خطا"))
        if not result.get("ok"):
            print("برای دیدن دلیل خطا: journalctl -u {} -n 50 --no-pager".format(SERVICE))
    pause()


def show_status():
    secrets = load_env(SECRETS_FILE)
    active, enabled = service_state()
    result = health()
    port = panel_port()
    print("\n=== وضعیت سرویس ===")
    print("سرویس:        {} / {}".format(active, enabled))
    print("پنل وب:       http://127.0.0.1:{}/admin".format(port))
    print("تونل SSH:     ssh -N -L {0}:127.0.0.1:{0} user@your-server".format(port))
    print("ربات تلگرام:  {}".format("فعال" if result.get("bot") else "تنظیم نشده"))
    print("توکن ربات:    {}".format(mask(secrets.get("BOT_TOKEN", ""), 6)))
    print("آیدی ادمین:   {}".format(secrets.get("ADMIN_ID") or "تنظیم نشده"))
    print("پروکسی Telegram: {}".format(socks5.mask_proxy(secrets.get("TELEGRAM_SOCKS5", ""))))
    xui = result.get("xui") or {}
    if isinstance(xui, dict):
        print("x-ui:         {}".format(
            "OK ({} inbound)".format(xui.get("inbounds")) if xui.get("ok") else (xui.get("error") or "خطا")
        ))
    print("سلامت کلی:    {}".format("OK" if result.get("ok") else (result.get("error") or "خطا")))
    pause()


def service_menu():
    while True:
        active, enabled = service_state()
        print("\n=== کنترل سرویس ({}) ===".format(active))
        print("1) شروع")
        print("2) توقف")
        print("3) ریستارت")
        print("4) وضعیت دقیق")
        print("0) بازگشت")
        choice = input("انتخاب: ").strip()
        if choice == "1":
            run_command(["systemctl", "start", SERVICE])
            print("سرویس شروع شد.")
        elif choice == "2":
            run_command(["systemctl", "stop", SERVICE])
            print("سرویس متوقف شد.")
        elif choice == "3":
            run_command(["systemctl", "restart", SERVICE])
            time.sleep(2)
            print("سرویس ریستارت شد.")
        elif choice == "4":
            run_command(["systemctl", "status", SERVICE, "--no-pager", "-l"])
        elif choice == "0":
            return
        else:
            print("انتخاب نامعتبر است.")
        if choice in ("1", "2", "3"):
            pause()


def logs_menu():
    while True:
        print("\n=== لاگها ===")
        print("1) ۵۰ خط آخر")
        print("2) ۲۰۰ خط آخر")
        print("3) مشاهده زنده (برای خروج Ctrl+C)")
        print("0) بازگشت")
        choice = input("انتخاب: ").strip()
        if choice == "1":
            run_command(["journalctl", "-u", SERVICE, "-n", "50", "--no-pager"])
        elif choice == "2":
            run_command(["journalctl", "-u", SERVICE, "-n", "200", "--no-pager"])
        elif choice == "3":
            print("برای خروج Ctrl+C بزنید.")
            run_command(["journalctl", "-u", SERVICE, "-f"])
        elif choice == "0":
            return
        else:
            print("انتخاب نامعتبر است.")
        if choice in ("1", "2"):
            pause()


def bot_menu():
    while True:
        secrets = load_env(SECRETS_FILE)
        print("\n=== تنظیمات ربات ===")
        print("توکن:       {}".format(mask(secrets.get("BOT_TOKEN", ""), 6)))
        print("آیدی ادمین: {}".format(secrets.get("ADMIN_ID") or "تنظیم نشده"))
        print("1) تنظیم توکن و آیدی ادمین (پیشنهادی)")
        print("2) تغییر توکن ربات")
        print("3) تغییر آیدی ادمین")
        print("4) تست اتصال به تلگرام")
        print("0) بازگشت")
        choice = input("انتخاب: ").strip()

        if choice in ("1", "2", "3"):
            updates = {}
            if choice in ("1", "2"):
                value = getpass.getpass("توکن ربات از @BotFather: ").strip()
                if ":" not in value or len(value) < 20:
                    print("توکن به نظر نامعتبر میرسد. باید شبیه 123456:ABC-... باشد.")
                    continue
                updates["BOT_TOKEN"] = value
            if choice in ("1", "3"):
                value = input("آیدی عددی تلگرام از @userinfobot: ").strip()
                if not value.isdigit():
                    print("آیدی ادمین باید عدد باشد.")
                    continue
                updates["ADMIN_ID"] = value
            save_env(SECRETS_FILE, updates)
            print("تنظیمات ربات ذخیره شد.")
            restart_if_asked()
        elif choice == "4":
            secrets = load_env(SECRETS_FILE)
            token = secrets.get("BOT_TOKEN", "")
            if not token:
                print("اول توکن ربات را تنظیم کنید.")
            else:
                ok, message = test_telegram(token, secrets.get("TELEGRAM_SOCKS5", ""))
                print("{} — {}".format("موفق" if ok else "ناموفق", message))
            pause()
        elif choice == "0":
            return
        else:
            print("انتخاب نامعتبر است.")


def proxy_menu():
    while True:
        secrets = load_env(SECRETS_FILE)
        current = secrets.get("TELEGRAM_SOCKS5", "")
        print("\n=== SOCKS5 برای تلگرام ===")
        print("وضعیت فعلی: {}".format(socks5.mask_proxy(current)))
        print("از این پروکسی فقط برای ارتباط ربات با Telegram API استفاده میشود.")
        print("1) تنظیم / تغییر SOCKS5")
        print("2) حذف پروکسی")
        print("3) تست اتصال تلگرام با پروکسی")
        print("0) بازگشت")
        choice = input("انتخاب: ").strip()

        if choice == "1":
            print("مثال: socks5://user:pass@1.2.3.4:1080  یا  socks5://1.2.3.4:1080")
            value = input("آدرس SOCKS5: ").strip()
            try:
                socks5.parse_proxy(value)
            except socks5.Socks5Error as exc:
                print("آدرس نامعتبر: {}".format(exc))
                continue
            save_env(SECRETS_FILE, {"TELEGRAM_SOCKS5": value})
            print("پروکسی ذخیره شد: {}".format(socks5.mask_proxy(value)))
            restart_if_asked()
        elif choice == "2":
            save_env(SECRETS_FILE, {"TELEGRAM_SOCKS5": ""})
            print("پروکسی حذف شد.")
            restart_if_asked()
        elif choice == "3":
            secrets = load_env(SECRETS_FILE)
            token = secrets.get("BOT_TOKEN", "")
            if not token:
                print("اول توکن ربات را تنظیم کنید.")
            elif not secrets.get("TELEGRAM_SOCKS5"):
                print("اول آدرس SOCKS5 را تنظیم کنید.")
            else:
                ok, message = test_telegram(token, secrets["TELEGRAM_SOCKS5"])
                print("{} — {}".format("موفق" if ok else "ناموفق", message))
            pause()
        elif choice == "0":
            return
        else:
            print("انتخاب نامعتبر است.")


def password_menu():
    print("\n=== رمز پنل وب ===")
    value = getpass.getpass("رمز جدید پنل وب: ").strip()
    if len(value) < 8:
        print("رمز باید حداقل ۸ کاراکتر باشد.")
        pause()
        return
    save_env(SECRETS_FILE, {"ADMIN_WEB_PASSWORD": value})
    print("رمز پنل وب تغییر کرد (نمایش داده نمیشود).")
    restart_if_asked()


def servers_menu():
    print("\n=== سرورهای x-ui ===")
    try:
        import servers
        items = servers.list_servers()
        if not items:
            print("سروری ثبت نشده است.")
        for server in items:
            public = servers.public_server(server)
            print(
                "#{} | {} | {} | هاست عمومی: {} | توکن: {}".format(
                    public.get("id"), public.get("name"), public.get("host"),
                    public.get("public_host") or "-", public.get("token_mask") or "-",
                )
            )
    except Exception as exc:
        print("خطا در خواندن سرورها: {}".format(sanitize(exc, [])))
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
        print(" پنل ترمینالی کو مونستر ".center(50))
        print("=" * 54)
        print("1) وضعیت سرویس و سلامت")
        print("2) کنترل سرویس (شروع / توقف / ریستارت)")
        print("3) مشاهده لاگها")
        print("4) تنظیمات ربات (توکن و آیدی ادمین)")
        print("5) تنظیمات SOCKS5 برای تلگرام")
        print("6) تغییر رمز پنل وب")
        print("7) نمایش سرورها")
        print("0) خروج")
        try:
            choice = input("انتخاب: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nخداحافظ")
            return
        if choice == "0":
            print("خداحافظ")
            return
        action = actions.get(choice)
        if action:
            try:
                action()
            except (EOFError, KeyboardInterrupt):
                print("\nبه منوی اصلی برگشتیم.")
        else:
            print("انتخاب نامعتبر است.")


if __name__ == "__main__":
    main()
