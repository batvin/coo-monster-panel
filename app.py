#!/usr/bin/env python3
"""Coo Monster Panel — web admin + Telegram bot (stdlib only)."""

import base64
import gzip
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import servers
import socks5
import store
import telegram_bot
import xui_client

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
HOST = os.environ.get("VPN_HOST", "127.0.0.1")
PORT = int(os.environ.get("VPN_PORT", "3001"))
SESSION_TTL = int(os.environ.get("ADMIN_SESSION_TTL", "28800"))
SECRET_FILE = ROOT / ".web_secret"


def _secret():
    if SECRET_FILE.exists():
        return SECRET_FILE.read_bytes().strip()
    value = secrets.token_bytes(32)
    SECRET_FILE.write_bytes(value)
    os.chmod(SECRET_FILE, 0o600)
    return value


WEB_SECRET = _secret()
SECRETS_FILE = ROOT / "secrets.env"
SERVICE = os.environ.get("VPN_SERVICE", "vpn-bot")

SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Cross-Origin-Resource-Policy": "same-origin",
}
COMPRESSIBLE_TYPES = {
    "application/json", "text/html", "text/css", "text/plain",
    "application/javascript", "text/javascript", "application/xml",
}
LOGIN_FAILURES = {}
LOGIN_LOCK = threading.Lock()
LOGIN_MAX_FAILURES = 8
LOGIN_WINDOW = 300


def mask_secret(value, keep=4):
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]


def read_secrets():
    telegram_bot.reload_secrets()
    return dict(telegram_bot.SECRETS)


def write_secrets(updates):
    """Update secrets.env atomically, keeping mode 600 and never returning values."""
    lines = SECRETS_FILE.read_text(encoding="utf-8", errors="replace").splitlines() if SECRETS_FILE.exists() else []
    result = []
    seen = set()
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in updates and line.strip() and not line.strip().startswith("#"):
            result.append("{}={}".format(key, updates[key]))
            seen.add(key)
        else:
            result.append(line)
    for key, value in updates.items():
        if key not in seen:
            result.append("{}={}".format(key, value))

    temporary = SECRETS_FILE.with_name("secrets.env.tmp")
    temporary.write_text("\n".join(result) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    try:
        if SECRETS_FILE.exists() and hasattr(os, "chown"):
            current = SECRETS_FILE.stat()
            os.chown(temporary, current.st_uid, current.st_gid)
    except OSError:
        pass
    os.replace(temporary, SECRETS_FILE)
    os.chmod(SECRETS_FILE, 0o600)
    telegram_bot.reload_secrets()


def bot_settings_payload():
    secrets = read_secrets()
    proxy = secrets.get("TELEGRAM_SOCKS5", "")
    try:
        proxy_mask = socks5.mask_proxy(proxy) if proxy else ""
    except socks5.Socks5Error:
        proxy_mask = "invalid"
    return {
        "bot_token_set": bool(secrets.get("BOT_TOKEN")),
        "bot_token_mask": mask_secret(secrets.get("BOT_TOKEN", ""), 6),
        "admin_id": secrets.get("ADMIN_ID", ""),
        "proxy_set": bool(proxy),
        "proxy_mask": proxy_mask,
        "bot_active": telegram_bot.configured(),
        "proxy_active": telegram_bot.proxy_configured(),
    }


def sanitize_api_text(text, secret_values):
    text = str(text)
    for value in secret_values:
        if value and len(value) > 3:
            text = text.replace(value, "***")
    return text[:300]


def proxy_secret_values(proxy):
    values = []
    if proxy:
        values.append(proxy)
        try:
            parsed = socks5.parse_proxy(proxy)
        except socks5.Socks5Error:
            parsed = None
        if parsed and parsed.get("password"):
            values.append(parsed["password"])
    return values


def validate_bot_token(value):
    return bool(re.fullmatch(r"\d{6,12}:[A-Za-z0-9_-]{20,}", value or ""))


def test_telegram_token(token, proxy=""):
    """Call Telegram getMe without starting the bot loop and without echoing secrets."""
    url = "https://api.telegram.org/bot{}/getMe".format(token)
    secret_values = [token] + proxy_secret_values(proxy)
    try:
        if proxy:
            status, body = socks5.request(url, timeout=12, proxy=proxy)
            text = body.decode("utf-8", "replace")
            if status >= 400:
                return False, "HTTP {}: {}".format(status, sanitize_api_text(text, secret_values))
            data = json.loads(text)
        else:
            with urllib.request.urlopen(url, timeout=12) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
        if data.get("ok"):
            return True, "OK — bot @{}".format(data.get("result", {}).get("username", "?"))
        return False, sanitize_api_text(
            data.get("description") or "invalid response", secret_values
        )
    except Exception as exc:
        return False, "{}: {}".format(
            type(exc).__name__, sanitize_api_text(exc, secret_values)
        )


def restart_service_later():
    """Apply saved secrets shortly after the HTTP response has been sent."""
    def worker():
        commands = []
        if getattr(os, "geteuid", lambda: -1)() == 0:
            commands.append(["systemctl", "restart", SERVICE])
        else:
            commands.append(["sudo", "-n", "systemctl", "restart", SERVICE])
            commands.append(["systemctl", "restart", SERVICE])
        for command in commands:
            try:
                result = subprocess.run(
                    command, check=False, timeout=20,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                if result.returncode == 0:
                    return
            except Exception:
                continue
    threading.Timer(1.0, worker).start()
    return True


def admin_password():
    telegram_bot.reload_secrets()
    return telegram_bot.SECRETS.get("ADMIN_WEB_PASSWORD", "")


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign(data):
    return b64(hmac.new(WEB_SECRET, data.encode("ascii"), hashlib.sha256).digest())


def make_cookie():
    payload = json.dumps({"exp": int(time.time()) + SESSION_TTL}).encode()
    data = b64(payload)
    return f"{data}.{sign(data)}"


def valid_cookie(header):
    try:
        jar = SimpleCookie()
        jar.load(header or "")
        cookie = jar.get("coopanel_admin")
        if not cookie:
            return False
        data, signature = cookie.value.split(".", 1)
        if not hmac.compare_digest(signature, sign(data)):
            return False
        return int(json.loads(unb64(data)).get("exp", 0)) > int(time.time())
    except Exception:
        return False


def login_allowed(address):
    now = time.time()
    with LOGIN_LOCK:
        if len(LOGIN_FAILURES) > 1000:
            for key in list(LOGIN_FAILURES):
                if now - LOGIN_FAILURES[key][0] > LOGIN_WINDOW:
                    LOGIN_FAILURES.pop(key, None)
        record = LOGIN_FAILURES.get(address)
        if not record or now - record[0] > LOGIN_WINDOW:
            LOGIN_FAILURES[address] = (now, 0)
            return True
        return record[1] < LOGIN_MAX_FAILURES


def login_failed(address):
    now = time.time()
    with LOGIN_LOCK:
        record = LOGIN_FAILURES.get(address)
        count = record[1] if record and now - record[0] <= LOGIN_WINDOW else 0
        LOGIN_FAILURES[address] = (now, count + 1)


def login_succeeded(address):
    with LOGIN_LOCK:
        LOGIN_FAILURES.pop(address, None)


def normalized_targets(value):
    targets = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        try:
            server_id = int(item.get("server_id") or 0)
            inbound_ids = [int(i) for i in (item.get("inbound_ids") or []) if int(i) > 0]
        except (TypeError, ValueError):
            continue
        if server_id > 0 and inbound_ids:
            targets.append({"server_id": server_id, "inbound_ids": inbound_ids})
    return targets


def watch_inbounds():
    """Discover inbounds created in x-ui after startup."""
    while True:
        try:
            newly_seen = servers.sync_inbounds()
            for item in newly_seen:
                store.log(
                    "system", "new_inbound",
                    f"server={item['server_id']} inbound={item['inbound_id']} remark={item['remark']}",
                )
                if store.get_setting("notify_inbound", "1") != "0":
                    store.notify(
                        "inbound",
                        f"اینابند جدید در {item['server_name']}: #{item['inbound_id']} {item['remark']}",
                        "servers",
                    )
                    telegram_bot.notify_new_inbound(item)
        except Exception as exc:
            print(f"[inbound-watch] {type(exc).__name__}: {exc}", flush=True)
        time.sleep(20)


class Handler(BaseHTTPRequestHandler):
    server_version = "CooMonsterAdmin"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def _send(self, body, content_type, status=200, headers=None,
              cache="no-store", compressible=True):
        headers = dict(headers or {})
        for key, value in SECURITY_HEADERS.items():
            headers.setdefault(key, value)
        headers.setdefault("Cache-Control", cache)
        kind = content_type.split(";", 1)[0].strip().lower()
        if (compressible and self.command != "HEAD" and len(body) >= 1024
                and kind in COMPRESSIBLE_TYPES
                and "gzip" in self.headers.get("Accept-Encoding", "").lower()):
            body = gzip.compress(body, compresslevel=6)
            vary = headers.get("Vary")
            headers["Vary"] = (vary + ", Accept-Encoding") if vary else "Accept-Encoding"
            headers["Content-Encoding"] = "gzip"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status=200, headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(body, "application/json; charset=utf-8", status=status, headers=headers)

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        try:
            return urlparse(origin).netloc.lower() == (
                self.headers.get("Host") or ""
            ).lower()
        except Exception:
            return False

    def _file(self, path):
        if not path.is_file():
            self.send_error(404, "Not Found")
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        body = path.read_bytes()
        etag = '"' + hashlib.sha256(body).hexdigest()[:16] + '"'
        if path.name == "three.min.js":
            cache = "public, max-age=31536000, immutable"
        elif path.name in ("admin.html", "admin.js", "admin.css"):
            cache = "no-cache"
        else:
            cache = "public, max-age=3600"
        headers = {"ETag": etag}
        for key, value in SECURITY_HEADERS.items():
            headers.setdefault(key, value)
        headers["Cache-Control"] = cache
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache)
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.end_headers()
            return
        self._send(body, content_type, headers=headers, cache=cache)

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return {}
        if length <= 0 or length > 256 * 1024:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _auth(self):
        return valid_cookie(self.headers.get("Cookie"))

    def _cookie(self, value, max_age):
        return "; ".join([
            f"coopanel_admin={value}", "Path=/", f"Max-Age={max_age}", "HttpOnly", "SameSite=Lax",
        ])

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/api/health":
            return self._json({
                "ok": True,
                "service": "coo-monster-panel",
                "bot": telegram_bot.configured(),
                "telegram_proxy": telegram_bot.proxy_configured(),
                "xui": xui_client.health(),
                "servers": len(servers.list_servers()),
            })
        if path == "/api/admin/session":
            return self._json({"authenticated": self._auth()})
        if path in ("/", "/admin"):
            return self._file(STATIC / "admin.html")
        if path.startswith("/static/"):
            relative = unquote(path[len("/static/"):])
            target = (STATIC / relative).resolve()
            root = STATIC.resolve()
            if root not in target.parents and target != root:
                self.send_error(403)
                return
            return self._file(target)

        if not self._auth():
            return self._json({"error": "unauthorized"}, 401)

        if path == "/api/admin/bot-settings":
            return self._json(bot_settings_payload())

        if path == "/api/admin/overview":
            return self._json(store.overview())

        if path == "/api/admin/servers":
            return self._json(servers.api_servers())

        if path == "/api/admin/inbounds":
            return self._json({
                "items": xui_client.inbounds(),
                "servers": servers.api_servers()["items"],
            })

        if path == "/api/admin/plans":
            items = []
            for plan in store.rows("SELECT * FROM plans ORDER BY sort,id"):
                plan["targets"] = store.plan_targets(plan)
                items.append(plan)
            return self._json({"items": items})

        if path == "/api/admin/orders":
            return self._json({"items": store.rows(
                "SELECT o.*, u.username, u.first_name, u.balance, p.name AS plan_name, "
                "p.days, p.total_gb, p.price FROM orders o "
                "LEFT JOIN users u ON u.telegram_id=o.telegram_id "
                "LEFT JOIN plans p ON p.id=o.plan_id ORDER BY o.id DESC LIMIT 300"
            )})

        if path == "/api/admin/configs":
            items = store.rows(
                "SELECT c.*, u.username, p.name AS plan_name FROM configs c "
                "LEFT JOIN users u ON u.telegram_id=c.telegram_id "
                "LEFT JOIN plans p ON p.id=c.plan_id ORDER BY c.id DESC LIMIT 400"
            )
            for item in items:
                server = servers.get_server(item.get("server_id")) or {}
                item["server_name"] = server.get("name", f"srv{item.get('server_id')}")
                try:
                    item["links_list"] = json.loads(item.get("links") or "[]")
                except Exception:
                    item["links_list"] = []
            return self._json({"items": items})

        if path == "/api/admin/users":
            return self._json({"items": store.rows(
                "SELECT * FROM users ORDER BY created_at DESC LIMIT 300"
            )})

        if path == "/api/admin/customers":
            telegram_id = query.get("telegram_id", [None])[0]
            if telegram_id:
                detail = store.customer_detail(telegram_id)
                if not detail:
                    return self._json({"error": "not found"}, 404)
                for item in detail.get("configs") or []:
                    server = servers.get_server(item.get("server_id")) or {}
                    item["server_name"] = server.get("name", f"srv{item.get('server_id')}")
                    try:
                        item["links_list"] = json.loads(item.get("links") or "[]")
                    except Exception:
                        item["links_list"] = []
                return self._json(detail)
            return self._json({"items": store.customers()})

        if path == "/api/admin/wallet":
            return self._json({
                "summary": store.wallet_summary(),
                "users": store.rows(
                    "SELECT telegram_id, username, first_name, balance FROM users "
                    "WHERE balance<>0 OR telegram_id IN (SELECT DISTINCT telegram_id FROM transactions) "
                    "ORDER BY balance DESC LIMIT 300"
                ),
                "transactions": store.wallet_transactions(limit=150),
            })

        if path == "/api/admin/extra-charges":
            items = store.extra_charges()
            return self._json({
                "items": items,
                "pending": len([item for item in items if item["status"] == "pending"]),
            })

        if path == "/api/admin/promos":
            return self._json({"items": store.rows("SELECT * FROM promos ORDER BY created_at DESC")})

        if path == "/api/admin/settings":
            return self._json({
                "welcome": store.get_setting("welcome", ""),
                "channel": store.get_setting("channel", ""),
                "support": store.get_setting("support", ""),
                "support_greeting": store.get_setting("support_greeting", ""),
                "notify_inbound": store.get_setting("notify_inbound", "1"),
                "auto_notify_order": store.get_setting("auto_notify_order", "1"),
            })

        if path == "/api/admin/support":
            ticket_id = query.get("ticket_id", [None])[0]
            if ticket_id:
                item = store.ticket(ticket_id)
                if not item:
                    return self._json({"error": "not found"}, 404)
                user = store.get_user(item["telegram_id"]) or {}
                return self._json({
                    "ticket": item,
                    "user": user,
                    "messages": store.ticket_messages(ticket_id),
                })
            return self._json({
                "items": store.tickets(query.get("status", [None])[0]),
                "open": len(store.tickets("open")),
            })

        if path == "/api/admin/notifications":
            return self._json({
                "items": store.notifications(150),
                "unread": store.unread_notifications(),
            })

        if path == "/api/admin/insights":
            return self._json({
                "daily": store.daily_stats(14),
                "overview": store.overview(),
                "tickets_open": len(store.tickets("open")),
                "unread": store.unread_notifications(),
            })

        if path == "/api/admin/export":
            return self._json(store.export_data())

        if path == "/api/admin/audit":
            return self._json({"items": store.rows(
                "SELECT * FROM audit ORDER BY id DESC LIMIT 150"
            )})

        self.send_error(404, "Not Found")

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._origin_ok():
            return self._json({"error": "invalid origin"}, 403)

        if path == "/api/admin/login":
            address = self.client_address[0]
            if not login_allowed(address):
                time.sleep(1)
                return self._json({"error": "Too many attempts. Try again later."}, 429)
            data = self._body()
            if str(data.get("password", "")) == admin_password():
                login_succeeded(address)
                return self._json(
                    {"ok": True},
                    headers={"Set-Cookie": self._cookie(make_cookie(), SESSION_TTL)},
                )
            login_failed(address)
            time.sleep(0.4)
            return self._json({"ok": False, "error": "رمز اشتباه است"}, 401)

        if path == "/api/admin/logout":
            return self._json({"ok": True}, headers={"Set-Cookie": self._cookie("", 0)})

        if not self._auth():
            return self._json({"error": "unauthorized"}, 401)

        data = self._body()

        if path == "/api/admin/servers":
            action = data.get("action")
            xui_client.clear_health_cache()
            try:
                if action == "add":
                    item = servers.add_server(data)
                    store.log("web", "add_server", f"server={item['id']}")
                    return self._json({"ok": True, "item": item})
                if action == "update":
                    item = servers.update_server(data.get("id"), data)
                    refreshed = 0
                    if data.get("public_host"):
                        refreshed = store.refresh_config_links(item["id"], item["public_host"])
                    store.log(
                        "web", "update_server",
                        f"server={item['id']} links_refreshed={refreshed}",
                    )
                    return self._json({"ok": True, "item": item, "links_refreshed": refreshed})
                if action == "delete":
                    server_id = data.get("id")
                    used = store.row(
                        "SELECT COUNT(*) n FROM configs WHERE server_id=?", (int(server_id),)
                    )["n"]
                    if used:
                        return self._json({
                            "ok": False,
                            "error": f"این سرور {used} سرویس فعال دارد؛ ابتدا سرویسها را حذف کن.",
                        }, 400)
                    servers.delete_server(server_id)
                    store.log("web", "delete_server", f"server={server_id}")
                    return self._json({"ok": True})
                if action == "sync":
                    newly_seen = servers.sync_inbounds()
                    for item in newly_seen:
                        store.log("web", "new_inbound", f"srv={item['server_id']} id={item['inbound_id']}")
                        if store.get_setting("notify_inbound", "1") != "0":
                            store.notify(
                                "inbound",
                                f"اینابند جدید در {item['server_name']}: #{item['inbound_id']} {item['remark']}",
                                "servers",
                            )
                            telegram_bot.notify_new_inbound(item)
                    return self._json({"ok": True, "new": newly_seen})
            except Exception as exc:
                return self._json({"ok": False, "error": str(exc)}, 400)

        if path == "/api/admin/plans":
            action = data.get("action", "add")
            try:
                if action == "add":
                    plan_id = store.execute(
                        "INSERT INTO plans(name,days,total_gb,price,inbound_ids,active,sort) VALUES(?,?,?,?,?,?,?)",
                        (
                            str(data.get("name") or "پلن جدید"),
                            int(data.get("days") or 30),
                            float(data.get("total_gb") or 1),
                            int(data.get("price") or 0),
                            json.dumps({"targets": normalized_targets(data.get("targets"))}),
                            int(bool(data.get("active", True))),
                            int(data.get("sort") or 0),
                        ),
                    )
                    store.log("web", "add_plan", f"plan={plan_id}")
                    return self._json({"ok": True, "id": plan_id})
                if action == "update":
                    plan_id = int(data.get("id") or 0)
                    store.execute(
                        "UPDATE plans SET name=?,days=?,total_gb=?,price=?,inbound_ids=?,active=?,sort=? WHERE id=?",
                        (
                            str(data.get("name") or "پلن"),
                            int(data.get("days") or 30),
                            float(data.get("total_gb") or 1),
                            int(data.get("price") or 0),
                            json.dumps({"targets": normalized_targets(data.get("targets"))}),
                            int(bool(data.get("active", True))),
                            int(data.get("sort") or 0),
                            plan_id,
                        ),
                    )
                    store.log("web", "update_plan", f"plan={plan_id}")
                    return self._json({"ok": True})
                if action == "delete":
                    plan_id = int(data.get("id") or 0)
                    used = store.row(
                        "SELECT (SELECT COUNT(*) FROM orders WHERE plan_id=?) + "
                        "(SELECT COUNT(*) FROM configs WHERE plan_id=?) n",
                        (plan_id, plan_id),
                    )["n"]
                    if used:
                        store.execute("UPDATE plans SET active=0 WHERE id=?", (plan_id,))
                        store.log("web", "deactivate_plan", f"plan={plan_id} used={used}")
                        return self._json({
                            "ok": True,
                            "message": f"پلن {used} بار استفاده شده؛ فقط غیرفعال شد.",
                        })
                    store.execute("DELETE FROM plans WHERE id=?", (plan_id,))
                    store.log("web", "delete_plan", str(plan_id))
                    return self._json({"ok": True, "message": "پلن حذف شد."})
                if action == "toggle":
                    store.execute(
                        "UPDATE plans SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?",
                        (int(data.get("id") or 0),),
                    )
                    return self._json({"ok": True})
            except Exception as exc:
                return self._json({"ok": False, "error": str(exc)}, 400)

        if path == "/api/admin/orders":
            order_id = int(data.get("id") or 0)
            action = data.get("action")
            if action in ("approve", "walletapprove"):
                result = telegram_bot.approve_order(
                    order_id, 0, use_wallet=(action == "walletapprove")
                )
                return self._json({"ok": "✅" in result, "message": result})
            if action == "reject":
                store.set_order_status(order_id, "rejected", 0)
                order = store.order(order_id)
                if order:
                    telegram_bot.send(order["telegram_id"], f"سفارش #{order_id} رد شد.")
                store.log("web", "reject_order", f"order={order_id}")
                return self._json({"ok": True})

        if path == "/api/admin/configs":
            config_id = int(data.get("id") or 0)
            action = data.get("action")
            item = store.config(config_id)
            if not item:
                return self._json({"ok": False, "error": "not found"}, 404)
            server = servers.get_server(item.get("server_id"))
            if not server:
                return self._json({"ok": False, "error": "server not found"}, 404)
            if action == "delete":
                try:
                    xui_client.delete_client(item["email"], server=server)
                except Exception as exc:
                    return self._json({"ok": False, "error": str(exc)}, 502)
                store.deactivate_config(config_id)
                store.log("web", "delete_config", f"config={config_id}")
                return self._json({"ok": True})
            if action == "extend":
                days = int(data.get("days") or 30)
                gb = float(data.get("gb") or 0)
                try:
                    xui_client.renew_client(item["email"], days, gb, server=server)
                    updated = store.extend_config(config_id, days, gb)
                    return self._json({"ok": True, "item": updated})
                except Exception as exc:
                    return self._json({"ok": False, "error": str(exc)}, 502)

        if path == "/api/admin/users":
            telegram_id = int(data.get("telegram_id") or 0)
            action = data.get("action")
            if action in ("ban", "unban"):
                store.execute(
                    "UPDATE users SET banned=? WHERE telegram_id=?",
                    (1 if action == "ban" else 0, telegram_id),
                )
                store.log("web", action + "_user", str(telegram_id))
                return self._json({"ok": True})

        if path == "/api/admin/wallet":
            telegram_id = int(data.get("telegram_id") or 0)
            amount = abs(int(data.get("amount") or 0))
            if not telegram_id or not amount:
                return self._json({"ok": False, "error": "کاربر و مبلغ را مشخص کن"}, 400)
            action = data.get("action")
            signed = amount if action == "charge" else -amount
            kind = "admin_charge" if action == "charge" else "admin_deduct"
            try:
                balance = store.adjust_wallet(
                    telegram_id, signed, kind,
                    str(data.get("note") or ""), "web",
                )
                store.log("web", kind, f"user={telegram_id} amount={signed}")
                store.notify(
                    "wallet",
                    f"کیف پول {telegram_id}: {'+' if signed > 0 else ''}{signed:,} تومان",
                    "wallet",
                )
                telegram_bot.send(
                    telegram_id,
                    f"👛 کیف پول: {'+' if signed > 0 else ''}{signed:,} تومان\nموجودی فعلی: {balance:,} تومان",
                )
                return self._json({"ok": True, "balance": balance})
            except Exception as exc:
                return self._json({"ok": False, "error": str(exc)}, 400)

        if path == "/api/admin/extra-charges":
            request_id = int(data.get("id") or 0)
            action = data.get("action")
            item = store.wallet_request(request_id)
            if not item:
                return self._json({"ok": False, "error": "درخواست پیدا نشد"}, 404)
            if item["status"] != "pending":
                return self._json(
                    {"ok": False, "error": "این درخواست قبلاً بررسی شده است"}, 400,
                )
            if action == "reject":
                store.set_wallet_request_status(request_id, "rejected", "web")
                store.log("web", "reject_extra_charge", f"request={request_id}")
                store.notify("wallet", f"شارژ اضافه #{request_id} رد شد", "extra")
                telegram_bot.send(
                    item["telegram_id"], f"❌ درخواست شارژ اضافه #{request_id} رد شد."
                )
                return self._json({"ok": True})
            if action != "approve":
                return self._json({"ok": False, "error": "عمل نامعتبر"}, 400)

            note = str(data.get("note") or "").strip()
            amount = int(item["amount"])
            kind = item.get("kind") or "wallet"
            try:
                if kind == "wallet":
                    balance = store.adjust_wallet(
                        item["telegram_id"], amount, "admin_charge",
                        f"شارژ اضافه #{request_id}" + (f" — {note}" if note else ""),
                        "web",
                    )
                    telegram_bot.send(
                        item["telegram_id"],
                        f"✅ شارژ اضافه #{request_id} تایید شد.\n"
                        f"💳 مبلغ: {amount:,} تومان\n👛 موجودی فعلی: {balance:,} تومان",
                        telegram_bot.wallet_keyboard(),
                    )
                    message = f"شارژ کیف پول #{request_id} — {amount:,} تومان"
                else:
                    config_id = int(item.get("config_id") or 0)
                    cfg = store.config(config_id)
                    if not cfg or cfg["telegram_id"] != item["telegram_id"]:
                        raise RuntimeError("سرویس مربوط به درخواست پیدا نشد")
                    server = servers.get_server(cfg.get("server_id"))
                    if not server:
                        raise RuntimeError("سرور سرویس پیدا نشد")
                    days = amount if kind == "days" else 0
                    gb = float(amount) if kind == "gb" else 0
                    xui_client.renew_client(cfg["email"], days, gb, server=server)
                    store.extend_config(config_id, days, gb)
                    if note:
                        store.execute(
                            "UPDATE configs SET note=? WHERE id=?",
                            (
                                f"{cfg.get('note') or ''} | شارژ #{request_id}: {note}"[:500],
                                config_id,
                            ),
                        )
                    label = f"{days} روز" if kind == "days" else f"{gb:g} GB"
                    telegram_bot.send_config_detail(
                        item["telegram_id"], None, config_id,
                        notice=f"✅ شارژ اضافه #{request_id} تایید شد: {label}",
                    )
                    message = f"شارژ سرویس #{config_id} ({label}) — درخواست #{request_id}"
                store.set_wallet_request_status(request_id, "approved", "web")
                store.log(
                    "web", "approve_extra_charge",
                    f"request={request_id} kind={kind} amount={amount}",
                )
                store.notify("wallet", message, "extra")
                return self._json({"ok": True, "message": message})
            except Exception as exc:
                return self._json({"ok": False, "error": str(exc)}, 502)

        if path == "/api/admin/promos":
            if data.get("action") == "add":
                store.execute(
                    "INSERT INTO promos(code,percent,amount,max_uses,active,created_at) VALUES(?,?,?,?,1,?)",
                    (
                        str(data.get("code") or "").upper(),
                        int(data.get("percent") or 0),
                        int(data.get("amount") or 0),
                        int(data.get("max_uses") or 0),
                        int(time.time()),
                    ),
                )
                return self._json({"ok": True})
            if data.get("action") == "delete":
                store.execute("DELETE FROM promos WHERE code=?", (str(data.get("code") or "").upper(),))
                return self._json({"ok": True})

        if path == "/api/admin/broadcast":
            text = str(data.get("text") or "").strip()
            if not text:
                return self._json({"ok": False, "error": "متن خالی است"}, 400)
            store.add_broadcast(text)
            store.log("web", "broadcast", text[:80])
            return self._json({"ok": True, "message": "پیام به صف ارسال اضافه شد"})

        if path == "/api/admin/bot-settings":
            action = data.get("action") or "save"
            secrets = read_secrets()

            if action == "test":
                token = str(data.get("bot_token") or "").strip() or secrets.get("BOT_TOKEN", "")
                if not token:
                    return self._json({"ok": False, "error": "Bot token is not set."}, 400)
                proxy = str(data.get("proxy") or "").strip() or secrets.get("TELEGRAM_SOCKS5", "")
                ok, message = test_telegram_token(token, proxy)
                return self._json({"ok": ok, "message": message}, 200 if ok else 400)

            if action == "remove_proxy":
                write_secrets({"TELEGRAM_SOCKS5": ""})
                store.log("web", "remove_bot_proxy", "proxy=removed")
                restart_service_later()
                return self._json({
                    "ok": True, "message": "Proxy removed. Service is restarting.",
                    "settings": bot_settings_payload(),
                })

            updates = {}
            token = str(data.get("bot_token") or "").strip()
            if token:
                if not validate_bot_token(token):
                    return self._json({
                        "ok": False,
                        "error": "Token looks invalid. Expected 123456:ABC-...",
                    }, 400)
                updates["BOT_TOKEN"] = token
            admin_id = str(data.get("admin_id") or "").strip()
            if admin_id:
                if not admin_id.isdigit():
                    return self._json({"ok": False, "error": "Admin ID must be numeric."}, 400)
                updates["ADMIN_ID"] = admin_id
            proxy = str(data.get("proxy") or "").strip()
            if proxy:
                try:
                    socks5.parse_proxy(proxy)
                except socks5.Socks5Error as exc:
                    return self._json({"ok": False, "error": str(exc)}, 400)
                updates["TELEGRAM_SOCKS5"] = proxy
            if not updates:
                return self._json({"ok": False, "error": "Nothing to save."}, 400)
            write_secrets(updates)
            store.log("web", "update_bot_settings", ", ".join(sorted(updates)))
            restart_service_later()
            return self._json({
                "ok": True,
                "message": "Saved. Service is restarting to apply the new settings.",
                "settings": bot_settings_payload(),
            })

        if path == "/api/admin/settings":
            for key in ("welcome", "channel", "support", "support_greeting", "notify_inbound", "auto_notify_order"):
                if key in data:
                    store.set_setting(key, data[key])
            return self._json({"ok": True, "message": "تنظیمات ذخیره شد."})

        if path == "/api/admin/support":
            action = data.get("action")
            ticket_id = int(data.get("id") or 0)
            try:
                if action == "reply":
                    item = store.ticket(ticket_id)
                    if not item:
                        return self._json({"error": "تیکت پیدا نشد"}, 404)
                    text = str(data.get("message") or "").strip()
                    if not text:
                        return self._json({"error": "پیام خالی است"}, 400)
                    store.add_ticket_message(ticket_id, "admin", text)
                    store.set_ticket_status(ticket_id, "open")
                    telegram_bot.send(
                        item["telegram_id"],
                        f"🎧 پاسخ پشتیبانی #{ticket_id}:\n{text}",
                    )
                    store.log("web", "support_reply", f"ticket={ticket_id}")
                    return self._json({"ok": True})
                if action == "status":
                    status = "closed" if data.get("status") == "closed" else "open"
                    store.set_ticket_status(ticket_id, status)
                    store.log("web", "support_status", f"ticket={ticket_id} status={status}")
                    return self._json({"ok": True})
                if action == "delete":
                    store.delete_ticket(ticket_id)
                    store.log("web", "support_delete", f"ticket={ticket_id}")
                    return self._json({"ok": True})
            except Exception as exc:
                return self._json({"ok": False, "error": str(exc)}, 400)

        if path == "/api/admin/notifications":
            action = data.get("action")
            if action == "read":
                store.mark_notification(int(data.get("id") or 0))
            elif action == "read_all":
                store.mark_all_notifications()
            elif action == "test":
                store.notify("system", "اعلان تست کو مونستر — همه سیستمها سالم است.", "overview")
            else:
                return self._json({"error": "عمل نامعتبر"}, 400)
            return self._json({"ok": True, "unread": store.unread_notifications()})

        if path == "/api/admin/customer-notes":
            action = data.get("action")
            if action == "add":
                telegram_id = int(data.get("telegram_id") or 0)
                note = str(data.get("note") or "").strip()
                if not telegram_id or not note:
                    return self._json({"error": "کاربر و یادداشت لازم است"}, 400)
                store.add_customer_note(telegram_id, note, "web")
                store.log("web", "customer_note", f"user={telegram_id}")
                return self._json({"ok": True})
            if action == "delete":
                store.delete_customer_note(int(data.get("id") or 0))
                return self._json({"ok": True})

        self.send_error(404, "Not Found")


def main():
    os.umask(0o077)
    store.init()
    telegram_bot.reload_secrets()
    threading.Thread(target=watch_inbounds, daemon=True, name="inbound-watch").start()
    if telegram_bot.configured():
        threading.Thread(target=telegram_bot.run, daemon=True, name="telegram-bot").start()
    else:
        print("[main] Telegram bot disabled: fill secrets.env", flush=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"VPN shop admin listening on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
