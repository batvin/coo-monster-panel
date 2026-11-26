"""Telegram Bot API polling loop (stdlib only)."""

import html
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import servers
import socks5
import store
import xui_client

ROOT = Path(__file__).resolve().parent
SECRETS = {}
SUPPORT_DRAFT = {}
PENDING_INPUT = {}


def _load_secrets():
    data = {}
    path = ROOT / "secrets.env"
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def reload_secrets():
    global SECRETS
    SECRETS = _load_secrets()


def token():
    return SECRETS.get("BOT_TOKEN", "")


def admin_id():
    try:
        return int(SECRETS.get("ADMIN_ID") or 0)
    except ValueError:
        return 0


def configured():
    value = token()
    return bool(value) and not value.startswith("__") and len(value) > 20


def proxy_url():
    return str(os.environ.get("TELEGRAM_SOCKS5") or SECRETS.get("TELEGRAM_SOCKS5") or "").strip()


def proxy_configured():
    try:
        return socks5.parse_proxy(proxy_url()) is not None
    except socks5.Socks5Error:
        return False


def api(method, params=None, timeout=18):
    if not configured():
        return None
    url = f"https://api.telegram.org/bot{token()}/{method}"
    data = None
    if params is not None:
        data = json.dumps(params).encode("utf-8")

    proxy = ""
    try:
        proxy = proxy_url()
    except Exception as exc:
        print(f"[telegram] SOCKS5 config error: {exc}", flush=True)

    if proxy:
        try:
            status, body = socks5.request(url, data=data, timeout=timeout, proxy=proxy)
            result = json.loads(body.decode("utf-8", "replace"))
            if status >= 400 and not result.get("ok"):
                print(f"[telegram] HTTP {status}: {str(result.get('description'))[:200]}", flush=True)
            return result
        except Exception as exc:
            print(f"[telegram] SOCKS5 {type(exc).__name__}: {exc}", flush=True)
        return None

    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        print(f"[telegram] HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}", flush=True)
    except Exception as exc:
        print(f"[telegram] {type(exc).__name__}: {exc}", flush=True)
    return None


def send(chat_id, text, markup=None, parse_mode="HTML"):
    params = {"chat_id": int(chat_id), "text": text[:4096]}
    if parse_mode:
        params["parse_mode"] = parse_mode
    if markup:
        params["reply_markup"] = json.dumps(markup)
    return api("sendMessage", params)


def answer(callback_id, text=""):
    return api("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})


def edit_message(chat_id, message_id, text, markup=None):
    return api(
        "editMessageText",
        {
            "chat_id": int(chat_id),
            "message_id": int(message_id),
            "text": text[:4096],
            "parse_mode": "HTML",
            "reply_markup": json.dumps(markup) if markup else None,
        },
    )


def inline(rows):
    return {"inline_keyboard": rows}


def main_keyboard():
    return inline([
        [{"text": "🛍️ خرید پلن", "callback_data": "plans"}],
        [
            {"text": "📦 سرویس های من", "callback_data": "my"},
            {"text": "👛 کیف پول", "callback_data": "wallet"},
        ],
        [
            {"text": "🎧 پشتیبانی", "callback_data": "support"},
            {"text": "📊 وضعیت", "callback_data": "status"},
        ],
        [{"text": "📘 راهنما", "callback_data": "help"}],
    ])


def esc(value):
    return html.escape(str(value if value is not None else ""), quote=False)


def money(value):
    return f"{int(value or 0):,}"


def parse_amount(value):
    normalized = str(value).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    normalized = normalized.replace(",", "").replace("،", "").strip()
    return int(normalized) if normalized.isdigit() else None


def config_links(config_row):
    links = config_row.get("links")
    if isinstance(links, str):
        try:
            links = json.loads(links or "[]")
        except Exception:
            links = []
    if not links and config_row.get("link"):
        links = [config_row["link"]]
    links = [str(link) for link in links if link]
    server = servers.get_server(config_row.get("server_id")) or {}
    if server.get("public_host"):
        links = xui_client.retarget_links(links, server["public_host"])
    return links


def plans_text(telegram_id=None):
    plans = store.active_plans()
    if not plans:
        return "فعلاً پلنی تعریف نشده."
    lines = []
    if telegram_id:
        user = store.get_user(telegram_id) or {}
        lines.append(f"💰 موجودی شما: <b>{money(user.get('balance'))}</b> تومان\n")
    lines.append("<b>پلن های موجود:</b>\n")
    for plan in plans:
        price = "رایگان" if not plan["price"] else f"{money(plan['price'])} تومان"
        targets = store.plan_targets(plan)
        servers_text = " + ".join(
            f"{(servers.get_server(t['server_id']) or {}).get('name', 'srv' + str(t['server_id']))}"
            for t in targets
        )
        lines.append(
            f"#{plan['id']} <b>{esc(plan['name'])}</b>\n"
            f"⏱ {plan['days']} روز | 💾 {plan['total_gb']} GB | 💰 {price}\n"
            f"🖥 {esc(servers_text)}"
        )
    return "\n\n".join(lines)


def plans_keyboard():
    buttons = [[{"text": f"{p['id']} - {p['name']}", "callback_data": f"buy:{p['id']}"}] for p in store.active_plans()]
    buttons.append([{"text": "↩️ بازگشت", "callback_data": "home"}])
    return inline(buttons)


def send_plans(chat_id, message_id=None):
    text = plans_text(chat_id)
    if message_id:
        edit_message(chat_id, message_id, text, plans_keyboard())
    else:
        send(chat_id, text, plans_keyboard())


def wallet_keyboard():
    return inline([
        [{"text": "💳 شارژ کیف پول", "callback_data": "wcharge"}],
        [{"text": "🧾 تراکنشها", "callback_data": "wtx"}],
        [{"text": "🛍️ خرید پلن", "callback_data": "plans"}],
        [{"text": "↩️ بازگشت", "callback_data": "home"}],
    ])


def send_wallet(chat_id, message_id=None):
    user = store.get_user(chat_id) or {}
    transactions = store.wallet_transactions(chat_id, 5)
    orders = store.customer_detail(chat_id)["orders"] if user else []
    purchases = len([order for order in orders if order["status"] == "approved"])
    lines = [
        "<b>👛 کیف پول</b>\n",
        f"موجودی: <b>{money(user.get('balance'))}</b> تومان\n",
        f"خریدهای تاییدشده: {purchases}\n",
    ]
    if transactions:
        lines.append("<b>آخرین تراکنشها:</b>")
        for item in transactions:
            sign = "+" if item["amount"] > 0 else ""
            lines.append(f"{sign}{money(item['amount'])} — {esc(item['note'] or item['kind'])}")
    if message_id:
        edit_message(chat_id, message_id, "\n".join(lines), wallet_keyboard())
    else:
        send(chat_id, "\n".join(lines), wallet_keyboard())


def send_wallet_transactions(chat_id, message_id=None):
    transactions = store.wallet_transactions(chat_id, 20)
    lines = ["<b>🧾 تراکنشهای کیف پول</b>\n"]
    if not transactions:
        lines.append("هنوز تراکنشهایی نداری.")
    for item in transactions:
        sign = "+" if item["amount"] > 0 else ""
        when = time.strftime("%Y-%m-%d", time.localtime(item["created_at"]))
        lines.append(
            f"{sign}{money(item['amount'])} تومان — {esc(item['note'] or item['kind'])} — {when}"
        )
    markup = inline([[{"text": "↩️ بازگشت به کیف پول", "callback_data": "wallet"}]])
    if message_id:
        edit_message(chat_id, message_id, "\n".join(lines), markup)
    else:
        send(chat_id, "\n".join(lines), markup)


def send_charge_prompt(chat_id, message_id=None):
    PENDING_INPUT[chat_id] = {"kind": "wallet_charge"}
    text = (
        "<b>💳 شارژ کیف پول</b>\n"
        "مبلغ دلخواه خودت رو به تومان بفرست (مثلاً ۵۰۰۰۰).\n"
        "بعد از ارسال، درخواستت برای ادمین میرود و بعد از تایید، موجودی آپدیت میشود.\n\n"
        "برای لغو /cancel بفرست."
    )
    markup = inline([[{"text": "↩️ بازگشت به کیف پول", "callback_data": "wallet"}]])
    if message_id:
        edit_message(chat_id, message_id, text, markup)
    else:
        send(chat_id, text, markup)


def service_status(item):
    if not item.get("active", 1) or item.get("deleted_at"):
        return "⚪️ حذفشده"
    return "🟢 روشن" if item.get("enabled", 1) else "🔴 خاموش"


def my_text(telegram_id):
    configs = store.user_configs(telegram_id)
    if not configs:
        return "شما هنوز سرویسی ندارید. از منوی خرید شروع کنید 👇"
    lines = ["<b>📦 سرویس های شما</b>\nبرای مدیریت هر سرویس، روی دکمه آن کلیک کن:\n"]
    for item in configs[:10]:
        days = max(0, (item["expiry_ts"] - int(time.time())) // 86400)
        server = servers.get_server(item.get("server_id")) or {}
        lines.append(
            f"#{item['id']} {esc(item['email'])}\n"
            f"🖥 {esc(server.get('name', 'srv' + str(item.get('server_id'))))} | "
            f"⏱ {days} روز | 💾 {item['total_gb']} GB | {service_status(item)}"
        )
    return "\n\n".join(lines)


def my_keyboard(telegram_id):
    buttons = []
    for item in store.user_configs(telegram_id)[:10]:
        buttons.append([{
            "text": f"⚙️ سرویس #{item['id']} — {service_status(item)}",
            "callback_data": f"detail:{item['id']}",
        }])
    buttons.append([{"text": "🛍️ خرید پلن", "callback_data": "plans"}])
    buttons.append([{"text": "↩️ بازگشت", "callback_data": "home"}])
    return inline(buttons)


def owned_config(chat_id, config_id):
    item = store.config(config_id)
    if not item or item["telegram_id"] != chat_id:
        return None
    if not item.get("active", 1) or item.get("deleted_at"):
        return None
    return item


def send_my(chat_id, message_id=None):
    if message_id:
        edit_message(chat_id, message_id, my_text(chat_id), my_keyboard(chat_id))
    else:
        send(chat_id, my_text(chat_id), my_keyboard(chat_id))


def config_detail_text(item, notice=""):
    days = max(0, (item["expiry_ts"] - int(time.time())) // 86400)
    server = servers.get_server(item.get("server_id")) or {}
    links = config_links(item)
    lines = []
    if notice:
        lines.append(f"<b>{esc(notice)}</b>\n")
    lines.extend([
        f"<b>⚙️ سرویس #{item['id']}</b>\n",
        f"🆔 نام: <code>{esc(item['email'])}</code>\n",
        f"🖥️ سرور: {esc(server.get('name', 'srv' + str(item.get('server_id'))))}\n",
        f"⏳ انقضاء: {time.strftime('%Y-%m-%d', time.localtime(item['expiry_ts']))} ({days} روز)\n",
        f"💾 حجم: {item['total_gb']} GB\n",
        f"🔑 UUID: <code>{esc(item.get('uuid') or '-')}</code>\n",
        f"⚡️ وضعیت: <b>{service_status(item)}</b>\n",
        f"🔗 لینک: {len(links)}\n",
        "\nاز دکمههای زیر برای مدیریت این سرویس استفاده کن.",
    ])
    return "\n".join(lines)


def config_detail_keyboard(item):
    cid = item["id"]
    enabled = item.get("enabled", 1)
    return inline([
        [{"text": "📋 لینکها و کانفینگ", "callback_data": f"clinks:{cid}"}],
        [
            {"text": "🔄 تمدید پلن", "callback_data": f"renew:{cid}"},
            {"text": "📆 شارژ روز", "callback_data": f"days:{cid}"},
        ],
        [
            {"text": "📶 شارژ حجم", "callback_data": f"gb:{cid}"},
            {"text": "🆕 تغییر UUID", "callback_data": f"uuidask:{cid}"},
        ],
        [{"text": "🔴 خاموش کردن" if enabled else "🟢 روشن کردن", "callback_data": f"toggle:{cid}"}],
        [{"text": "🗑️ حذف سرویس", "callback_data": f"delask:{cid}"}],
        [{"text": "↩️ بازگشت به سرویسها", "callback_data": "my"}],
    ])


def send_config_detail(chat_id, message_id, config_id, notice=""):
    item = owned_config(chat_id, config_id)
    if not item:
        text = "سرویس پیدا نشد یا حذف شده است."
        markup = inline([[{"text": "↩️ بازگشت به سرویسها", "callback_data": "my"}]])
    else:
        text = config_detail_text(item, notice)
        markup = config_detail_keyboard(item)
    if message_id:
        edit_message(chat_id, message_id, text, markup)
    else:
        send(chat_id, text, markup)


def send_config_links(chat_id, message_id, config_id):
    item = owned_config(chat_id, config_id)
    if not item:
        return send_config_detail(chat_id, message_id, config_id)
    links = config_links(item)
    lines = [f"<b>🔗 لینکهای سرویس #{item['id']}</b>\n"]
    if not links:
        lines.append("لینکی برای این سرویس ذخیره نشده است.")
    for index, link in enumerate(links, 1):
        lines.append(f"<b>لینک {index}:</b>\n<code>{esc(link)}</code>\n")
    lines.append("روی لینک کلیک کرده و در v2ray / v2box import کن.")
    markup = inline([[{"text": "↩️ بازگشت به سرویس", "callback_data": f"detail:{config_id}"}]])
    if message_id:
        edit_message(chat_id, message_id, "\n".join(lines), markup)
    else:
        send(chat_id, "\n".join(lines), markup)


def send_confirm(chat_id, message_id, config_id, kind):
    item = owned_config(chat_id, config_id)
    if not item:
        return send_config_detail(chat_id, message_id, config_id)
    if kind == "delete":
        text = f"<b>🗑️ حذف سرویس #{config_id}</b>\n\nاین سرویس از پنل x-ui هم حذف میشود و برگشتپذیر نیست."
        yes, yes_cb = "✅ بله، حذف کن", f"del:{config_id}"
    else:
        text = (
            f"<b>🆕 تغییر UUID سرویس #{config_id}</b>\n\n"
            "UUID جدید ساخته میشود و تمام لینکهای این سرویس آپدیت میشوند. "
            "لینک قدیمی دیگر کار نخواهد کرد."
        )
        yes, yes_cb = "✅ بله، تغییر بده", f"uuid:{config_id}"
    markup = inline([
        [{"text": yes, "callback_data": yes_cb}],
        [{"text": "❌ خیر، بازگشت", "callback_data": f"detail:{config_id}"}],
    ])
    if message_id:
        edit_message(chat_id, message_id, text, markup)
    else:
        send(chat_id, text, markup)


def send_amount_prompt(chat_id, message_id, config_id, kind):
    PENDING_INPUT[chat_id] = {"kind": kind, "config_id": config_id}
    label = "روز" if kind == "days" else "GB"
    example = "30" if kind == "days" else "10"
    text = (
        f"<b>➕ شارژ {label}</b>\n"
        f"تعداد {label} را بفرست (مثلاً {example}).\n"
        "بعد از ثبت، دوباره به صفحه همین سرویس برمیگردی.\n\n"
        "برای لغو /cancel بفرست."
    )
    markup = inline([[{"text": "↩️ بازگشت به سرویس", "callback_data": f"detail:{config_id}"}]])
    if message_id:
        edit_message(chat_id, message_id, text, markup)
    else:
        send(chat_id, text, markup)


def submit_charge_request(chat_id, message_id, config_id, kind, amount):
    """Queue a service top-up for admin approval instead of applying it instantly."""
    item = owned_config(chat_id, config_id)
    if not item:
        return send_config_detail(chat_id, message_id, config_id)
    request_id = store.create_wallet_request(
        chat_id, amount, kind=kind, config_id=config_id,
    )
    label = f"{amount} روز" if kind == "days" else f"{amount} GB"
    send_config_detail(
        chat_id, message_id, config_id,
        notice=(
            f"⏳ درخواست شارژ #{request_id} ثبت شد: {label}\n"
            "پرداخت را انجام بده؛ بعد از تایید ادمین آپدیت میشود."
        ),
    )
    store.notify("wallet", f"شارژ اضافه #{request_id} — {label} — سرویس #{config_id}", "extra")
    if admin_id() and int(chat_id) != admin_id():
        send(
            admin_id(),
            f"💳 شارژ اضافه #{request_id} از <code>{chat_id}</code>\n"
            f"سرویس #{config_id} — {label}\n"
            "تایید از پنل وب: بخش «شارژ اضافه»",
            inline([[
                {"text": "✅ تایید", "callback_data": f"wreq:{request_id}:approve"},
                {"text": "❌ رد", "callback_data": f"wreq:{request_id}:reject"},
            ]]),
        )


def show_message(chat_id, message_id, text, markup=None):
    if message_id:
        edit_message(chat_id, message_id, text, markup)
    else:
        send(chat_id, text, markup)


def send_home(chat_id, message_id=None, text="منوی اصلی:"):
    if message_id:
        edit_message(chat_id, message_id, text, main_keyboard())
    else:
        send(chat_id, text, main_keyboard())


def send_help(chat_id, message_id=None):
    text = (
        "<b>راهنما</b>\n"
        "/plans - لیست پلنها\n"
        "/buy &lt;id&gt; - خرید پلن\n"
        "/my - سرویسهای من و مدیریت هر سرویس\n"
        "/balance - موجودی و شارژ کیف پول\n"
        "/support - ارسال تیکت پشتیبانی\n"
        "/status - وضعیت حساب و سرویسها\n"
        "/orders - سفارشات (ادمین)\n"
        "/admin - آمار (ادمین)\n"
        "/servers - سرورها (ادمین)\n"
        "/reply &lt;id&gt; &lt;متن&gt; - پاسخ تیکت (ادمین)\n"
        "/broadcast متن - ارسال همگانی (ادمین)"
    )
    markup = inline([[{"text": "↩️ بازگشت", "callback_data": "home"}]])
    if message_id:
        edit_message(chat_id, message_id, text, markup)
    else:
        send(chat_id, text, markup)


def extend_service(chat_id, message_id, config_id, days, gb, notice):
    item = owned_config(chat_id, config_id)
    if not item:
        return send_config_detail(chat_id, message_id, config_id)
    server = servers.get_server(item.get("server_id"))
    try:
        xui_client.renew_client(item["email"], days, gb, server=server)
        store.extend_config(config_id, days, gb)
        store.log(chat_id, "extend_config", f"config={config_id} days={days} gb={gb}")
        send_config_detail(chat_id, message_id, config_id, notice=notice)
    except Exception as exc:
        show_message(
            chat_id, message_id,
            "<b>خطا در بروزرسانی سرویس</b>\n" + esc(exc),
            config_detail_keyboard(item),
        )


def toggle_service(chat_id, message_id, config_id):
    item = owned_config(chat_id, config_id)
    if not item:
        return send_config_detail(chat_id, message_id, config_id)
    enabled = not bool(item.get("enabled", 1))
    server = servers.get_server(item.get("server_id"))
    try:
        xui_client.set_client_enabled(item["email"], enabled, server=server)
        store.set_config_enabled(config_id, enabled)
        store.log(chat_id, "toggle_config", f"config={config_id} enabled={enabled}")
        send_config_detail(
            chat_id, message_id, config_id,
            notice="✅ سرویس روشن شد" if enabled else "🔌 سرویس خاموش شد",
        )
    except Exception as exc:
        show_message(
            chat_id, message_id,
            "<b>خطا در تغییر وضعیت</b>\n" + esc(exc),
            config_detail_keyboard(item),
        )


def change_uuid(chat_id, message_id, config_id):
    item = owned_config(chat_id, config_id)
    if not item:
        return send_config_detail(chat_id, message_id, config_id)
    new_uuid = str(uuid.uuid4())
    server = servers.get_server(item.get("server_id"))
    try:
        xui_client.change_client_uuid(item["email"], new_uuid, server=server)
        links = xui_client.replace_link_identity(
            config_links(item), item.get("uuid") or "", new_uuid,
        )
        store.update_config_identity(config_id, new_uuid, links)
        store.log(chat_id, "change_uuid", f"config={config_id}")
        send_config_detail(
            chat_id, message_id, config_id,
            notice="🔑 UUIDجدید"
        )
    except Exception as exc:
        show_message(
            chat_id, message_id,
            "<b>خطا در تغییر UUID</b>\n" + esc(exc),
            config_detail_keyboard(item),
        )


def delete_service(chat_id, message_id, config_id):
    item = owned_config(chat_id, config_id)
    if not item:
        return send_config_detail(chat_id, message_id, config_id)
    server = servers.get_server(item.get("server_id"))
    try:
        xui_client.delete_client(item["email"], server=server)
    except Exception as exc:
        show_message(
            chat_id, message_id,
            "<b>خطا در حذف از پنل</b>\n" + esc(exc),
            config_detail_keyboard(item),
        )
        return
    store.deactivate_config(config_id)
    store.log(chat_id, "delete_config", f"config={config_id}")
    show_message(
        chat_id, message_id,
        f"✅ سرویس #{config_id} حذف شد.",
        inline([[{"text": "↩️ بازگشت به سرویسها", "callback_data": "my"}]]),
    )


def send_support_prompt(chat_id, message_id=None):
    greeting = store.get_setting("support_greeting", "پیامت رو بنویس، تیم کو مونستر جواب میده 👾")
    SUPPORT_DRAFT[chat_id] = True
    text = f"<b>🎧 پشتیبانی</b>\n{esc(greeting)}\n\nبرای لغو /cancel بفرست."
    markup = inline([[{"text": "↩️ بازگشت", "callback_data": "home"}]])
    if message_id:
        edit_message(chat_id, message_id, text, markup)
    else:
        send(chat_id, text, markup)


def send_status(chat_id, message_id=None):
    user = store.get_user(chat_id) or {}
    configs = store.user_configs(chat_id)
    tickets = store.user_tickets(chat_id)
    active = [c for c in configs if c["expiry_ts"] > int(time.time())]
    lines = [
        "<b>📊 وضعیت حساب</b>\n",
        f"👤 {esc(user.get('first_name') or user.get('username') or chat_id)}\n",
        f"💰 موجودی: <b>{money(user.get('balance'))}</b> تومان\n",
        f"🔗 سرویس فعال: {len(active)} / {len(configs)}\n",
        f"🎧 تیکتهای باز: {len([t for t in tickets if t['status'] == 'open'])}\n",
    ]
    for item in active[:5]:
        days = max(0, (item["expiry_ts"] - int(time.time())) // 86400)
        lines.append(f"• #{item['id']} {esc(item['email'])} — {days} روز / {item['total_gb']} GB")
    markup = inline([
        [{"text": "🛍️ خرید پلن", "callback_data": "plans"}],
        [{"text": "🎧 پشتیبانی", "callback_data": "support"}],
        [{"text": "↩️ بازگشت", "callback_data": "home"}],
    ])
    if message_id:
        edit_message(chat_id, message_id, "\n".join(lines), markup)
    else:
        send(chat_id, "\n".join(lines), markup)


def send_config(chat_id, config_row, prefix="🎁 سرویس شما آماده شد", notify_new=True):
    links = config_links(config_row)
    server = servers.get_server(config_row.get("server_id")) or {}
    lines = [
        f"<b>{prefix}</b>\n",
        f"🆔 نام: <code>{esc(config_row['email'])}</code>\n",
        f"🖥️ سرور: {esc(server.get('name', 'srv' + str(config_row.get('server_id'))))}\n",
        f"⏳ انقضاء: {time.strftime('%Y-%m-%d', time.localtime(config_row['expiry_ts']))}\n",
        f"💾 حجم: {config_row['total_gb']} GB\n",
    ]
    for index, link in enumerate(links, 1):
        label = f"🔗 لینک اتصال {index}:" if len(links) > 1 else "🔗 لینک اتصال:"
        lines.append(f"{label}\n<code>{esc(link)}</code>")
    lines.append("\nروی لینک کلیک کرده و در v2ray / v2box import کنید.")
    config_id = config_row.get("id")
    rows = []
    if config_id:
        rows.append([{"text": "⚙️ مدیریت این سرویس", "callback_data": f"detail:{config_id}"}])
    rows.append([{"text": "↩️ منوی اصلی", "callback_data": "home"}])
    send(chat_id, "\n".join(lines), inline(rows))
    if notify_new and admin_id() and int(chat_id) != admin_id():
        send(admin_id(), f"📣 سرویس جدید برای <code>{esc(chat_id)}</code> ساخته شد: {esc(config_row['email'])}")


def notify_admins_pending(order_id):
    order = store.order(order_id)
    if not order:
        return
    plan = store.plan(order["plan_id"]) or {}
    user = store.get_user(order["telegram_id"]) or {}
    uname = user.get("username") or ""
    text = (
        f"🛒 سفارش جدید #{order_id}\n"
        f"👤 کاربر: <code>{order['telegram_id']}</code> {esc('@' + uname if uname else '')}\n"
        f"💰 موجودی: {money(user.get('balance'))} تومان\n"
        f"📦 پلن: {esc(plan.get('name', '?'))}\n"
        f"⏱ {plan.get('days', '?')} روز / 💾 {plan.get('total_gb', '?')} GB / 💵 {money(plan.get('price'))} تومان\n"
    )
    markup = inline([
        [
            {"text": "✅ تایید", "callback_data": f"approve:{order_id}"},
            {"text": "💰 تایید با کیف پول", "callback_data": f"walletapprove:{order_id}"},
        ],
        [{"text": "❌ رد", "callback_data": f"reject:{order_id}"}],
    ])
    for recipient in {admin_id(), order["telegram_id"]}:
        if recipient:
            is_admin = recipient == admin_id()
            send(
                recipient,
                text if is_admin else "سفارش شما ثبت شد و در انتظار تایید است.",
                markup if is_admin else None,
            )


def approve_order(order_id, approver, use_wallet=False):
    order = store.order(order_id)
    if not order or order["status"] != "pending":
        return "سفارش معتبر نیست یا قبلاً بررسی شده."
    plan = store.plan(order["plan_id"])
    if not plan:
        return "پلن پیدا نشد."

    targets = store.plan_targets(plan)
    if not targets:
        return "برای این پلن سرور/اینابندی تنظیم نشده."

    price = int(plan["price"] or 0)
    if use_wallet:
        user = store.get_user(order["telegram_id"]) or {}
        if int(user.get("balance") or 0) < price:
            return f"موجودی کافی نیست؛ موجودی {money(user.get('balance'))} تومان و قیمت {money(price)} تومان است."

    base_email = order.get("email") or f"user-{order['telegram_id']}-{int(time.time())}@shop.local"
    created = []
    try:
        for target in targets:
            server = servers.get_server(target["server_id"])
            if not server or not server.get("token"):
                raise RuntimeError(f"سرور {target['server_id']} در دسترس نیست")
            email = base_email if len(targets) == 1 else f"{base_email}-s{server['id']}"
            result = xui_client.add_client(
                email=email,
                days=plan["days"],
                total_gb=plan["total_gb"],
                inbound_ids=target["inbound_ids"],
                tg_id=order["telegram_id"],
                comment=f"order {order_id} srv {server['id']}",
                server=server,
            )
            client = result.get("client") or {}
            config_row = store.add_config({
                "telegram_id": order["telegram_id"],
                "email": email,
                "plan_id": plan["id"],
                "server_id": server["id"],
                "inbound_id": result["inbound_id"],
                "uuid": client.get("uuid") or client.get("id", ""),
                "sub_id": client.get("subId", ""),
                "link": result["link"],
                "links": result["links"],
                "expiry_ts": result["expiry_ts"],
                "total_gb": result["total_gb"],
                "note": f"order {order_id} / {server.get('name', '')}",
            })
            created.append((config_row, server))
    except Exception as exc:
        for config_row, server in created:
            try:
                xui_client.delete_client(config_row["email"], server=server)
            except Exception:
                pass
            store.deactivate_config(config_row["id"])
        return f"خطا در ساخت سرویس: {esc(exc)}"

    if use_wallet and price > 0:
        try:
            store.adjust_wallet(
                order["telegram_id"], -price, "purchase",
                f"سفارش #{order_id} - {plan['name']}", approver or "bot",
            )
        except Exception as exc:
            return f"سرویس ساخته شد ولی کسر از کیف پول ناموفق بود: {esc(exc)}"

    store.set_order_status(
        order_id, "approved", approver,
        paid_amount=price if use_wallet else 0,
        wallet_used=1 if use_wallet else 0,
    )
    store.log(approver, "approve_order", f"order={order_id} configs={len(created)} wallet={int(use_wallet)}")
    if store.get_setting("auto_notify_order", "1") != "0":
        store.notify("order", f"سفارش #{order_id} تایید شد — {len(created)} سرویس", "orders")
    for config_row, _ in created:
        send_config(order["telegram_id"], config_row)
    return f"✅ سفارش #{order_id} تایید و {len(created)} سرویس ساخته شد."


def _do_buy(chat_id, plan_id):
    plan = store.plan(plan_id)
    if not plan or not plan["active"]:
        send(chat_id, "پلن پیدا نشد.")
        return
    order_id = store.create_order(chat_id, plan_id)
    notify_admins_pending(order_id)


def show_orders(chat_id):
    orders = store.pending_orders()
    if not orders:
        send(chat_id, "سفارش در انتظاری نیست.")
        return
    for order in orders[:20]:
        markup = inline([
            [
                {"text": "✅ تایید", "callback_data": f"approve:{order['id']}"},
                {"text": "👛 کیف پول", "callback_data": f"walletapprove:{order['id']}"},
            ],
            [{"text": "❌ رد", "callback_data": f"reject:{order['id']}"}],
        ])
        send(
            chat_id,
            f"#{order['id']} | {esc(order['plan_name'])} | کاربر <code>{order['telegram_id']}</code>\n"
            f"موجودی: {money(order.get('balance'))} / قیمت: {money(order.get('price'))} تومان",
            markup,
        )


def handle_message(message):
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return
    from_user = message.get("from") or {}
    store.ensure_user(chat_id, from_user.get("username", ""), from_user.get("first_name", ""))
    text = (message.get("text") or "").strip()
    if not text:
        return

    if text == "/cancel" and (chat_id in SUPPORT_DRAFT or chat_id in PENDING_INPUT):
        pending = PENDING_INPUT.pop(chat_id, None)
        SUPPORT_DRAFT.pop(chat_id, None)
        if pending and pending.get("config_id"):
            markup = inline([[{
                "text": "↩️ بازگشت به سرویس",
                "callback_data": f"detail:{pending['config_id']}",
            }]])
        elif pending:
            markup = wallet_keyboard()
        else:
            markup = main_keyboard()
        send(chat_id, "درخواست لغو شد.", markup)
        return

    pending = PENDING_INPUT.get(chat_id)
    if pending and not text.startswith("/"):
        if pending["kind"] == "wallet_charge":
            amount = parse_amount(text)
            if not amount or amount < 1000 or amount > 100000000:
                send(
                    chat_id,
                    "مبلغ معتبر بفرست (بین ۱,۰۰۰ تا ۱۰۰,۰۰۰,۰۰۰ تومان).",
                    inline([[{"text": "↩️ بازگشت به کیف پول", "callback_data": "wallet"}]]),
                )
                return
            PENDING_INPUT.pop(chat_id, None)
            request_id = store.create_wallet_request(chat_id, amount)
            send(
                chat_id,
                f"⏳ درخواست شارژ #{request_id} به مبلغ {money(amount)} تومان ثبت شد.\n"
                "پرداخت را انجام بده؛ بعد از تایید ادمین موجودی آپدیت میشود.",
                wallet_keyboard(),
            )
            if admin_id() and int(chat_id) != admin_id():
                send(
                    admin_id(),
                    f"💳 درخواست شارژ #{request_id} از <code>{chat_id}</code>: {money(amount)} تومان",
                    inline([
                        [
                            {"text": "✅ تایید", "callback_data": f"wreq:{request_id}:approve"},
                            {"text": "❌ رد", "callback_data": f"wreq:{request_id}:reject"},
                        ]
                    ]),
                )
            store.notify(
                "wallet",
                f"درخواست شارژ #{request_id} — {chat_id} — {money(amount)} تومان",
                "extra",
            )
            return

        if pending["kind"] in ("days", "gb"):
            config_id = int(pending.get("config_id") or 0)
            back = inline([[{
                "text": "↩️ بازگشت به سرویس",
                "callback_data": f"detail:{config_id}",
            }]])
            if not owned_config(chat_id, config_id):
                PENDING_INPUT.pop(chat_id, None)
                send_config_detail(chat_id, None, config_id)
                return
            limit = 3650 if pending["kind"] == "days" else 10000
            value = parse_amount(text)
            if not value or value < 1 or value > limit:
                send(chat_id, f"عدد معتبر بفرست (۱ تا {limit}).", back)
                return
            PENDING_INPUT.pop(chat_id, None)
            submit_charge_request(chat_id, None, config_id, pending["kind"], value)
            return

    if chat_id in SUPPORT_DRAFT:
        message_text = text
        SUPPORT_DRAFT.pop(chat_id, None)
        ticket = store.create_ticket(chat_id, "پیام پشتیبانی", message_text)
        store.notify(
            "support",
            f"تیکت #{ticket['id']} از {chat_id}: {message_text[:80]}",
            "support",
        )
        send(chat_id, f"✅ تیکت #{ticket['id']} ثبت شد. بزودی جواب میدیم.", main_keyboard())
        if admin_id() and int(chat_id) != admin_id():
            send(
                admin_id(),
                f"🎧 تیکت جدید #{ticket['id']} از <code>{chat_id}</code>:\n{esc(message_text[:800])}\n"
                f"پاسخ: /reply {ticket['id']} متن پیام",
            )
        return

    if text.startswith("/start"):
        welcome = store.get_setting("welcome", "به کو مونستر خوش آمدی 👾")
        send(chat_id, esc(welcome) + "\n\nدستورها: /plans /buy /my /balance /help", main_keyboard())
    elif text in ("plans", "/plans", "خرید", "پلن ها"):
        send_plans(chat_id)
    elif text.startswith("/buy") or text.startswith("buy"):
        parts = text.replace("/buy", "buy").split()
        if len(parts) > 1 and parts[1].isdigit():
            _do_buy(chat_id, int(parts[1]))
        else:
            send_plans(chat_id)
    elif text in ("my", "/my", "سرویس های من"):
        send_my(chat_id)
    elif text in ("balance", "/balance", "wallet", "/wallet", "کیف پول"):
        send_wallet(chat_id)
    elif text in ("help", "/help", "راهنما"):
        send_help(chat_id)
    elif text in ("support", "/support", "پشتیبانی"):
        send_support_prompt(chat_id)
    elif text in ("status", "/status", "وضعیت"):
        send_status(chat_id)
    elif chat_id == admin_id() and text.startswith("/reply"):
        parts = text.split(maxsplit=2)
        if len(parts) >= 3 and parts[1].isdigit():
            ticket_id = int(parts[1])
            item = store.ticket(ticket_id)
            if not item:
                send(chat_id, "تیکت پیدا نشد.")
            else:
                store.add_ticket_message(ticket_id, "admin", parts[2])
                store.set_ticket_status(ticket_id, "open")
                store.notify("support", f"پاسخ به تیکت #{ticket_id}", "support")
                send(item["telegram_id"], f"🎧 پاسخ پشتیبانی #{ticket_id}:\n{esc(parts[2])}")
                send(chat_id, f"✅ پاسخ تیکت #{ticket_id} ارسال شد.")
        else:
            send(chat_id, "استفاده: /reply <id> متن پاسخ")
    elif chat_id == admin_id() and text.startswith("/orders"):
        show_orders(chat_id)
    elif chat_id == admin_id() and text.startswith("/servers"):
        data = servers.api_servers()
        lines = ["<b>سرورهای x-ui:</b>"]
        for item in data["items"]:
            lines.append(
                f"{'✅' if item['ok'] else '❌'} {esc(item['name'])} — "
                f"{len(item['inbounds'])} inbound | {esc(item.get('error', ''))}"
            )
        send(chat_id, "\n".join(lines))
    elif chat_id == admin_id() and text.startswith("/admin"):
        overview = store.overview()
        health = xui_client.health()
        send(
            chat_id,
            f"<b>پنل ادمین کو مونستر</b>\n\n"
            f"👤 کاربران: {overview['users']} / مشتریان: {overview['customers']}\n"
            f"🛒 در انتظار: {overview['pending']}\n"
            f"📦 سرویس فعال: {overview['active_configs']}\n"
            f"💰 فروش: {money(overview['revenue'])} تومان\n"
            f"👛 موجودی کیفها: {money(overview['wallet_total'])} تومان\n"
            f"🖥 x-ui: {'OK (' + str(health['inbounds']) + ' inbound)' if health.get('ok') else health.get('error')}\n\n"
            f"🌐 پنل وب: http://127.0.0.1:{os.environ.get('VPN_PORT', '3001')}/admin",
        )
    elif chat_id == admin_id() and text.startswith("/broadcast"):
        value = text[len("/broadcast"):].strip()
        if value:
            store.add_broadcast(value)
            send(chat_id, f"پیام به صف ارسال همگانی اضافه شد ({esc(value[:60])})")
        else:
            send(chat_id, "استفاده: /broadcast متن پیام")
    elif chat_id == admin_id() and text.startswith("/sync"):
        items = servers.api_servers()["items"]
        summary = " | ".join(
            f"{item['name']}: {'OK' if item['ok'] else 'OFF'} ({len(item['inbounds'])})"
            for item in items
        )
        send(chat_id, "x-ui sync — " + esc(summary))
    else:
        send(chat_id, "از منوی زیر استفاده کن 👇", main_keyboard())


def handle_callback(callback):
    callback_id = callback.get("id", "")
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    data = callback.get("data") or ""
    from_user = callback.get("from") or {}
    uid = from_user.get("id", chat_id)
    is_admin = int(uid or 0) == admin_id()

    PENDING_INPUT.pop(chat_id, None)

    if data == "home":
        SUPPORT_DRAFT.pop(chat_id, None)
        answer(callback_id)
        send_home(chat_id, message_id)
    elif data == "support":
        answer(callback_id)
        send_support_prompt(chat_id, message_id)
    elif data == "status":
        answer(callback_id)
        send_status(chat_id, message_id)
    elif data == "plans":
        answer(callback_id)
        send_plans(chat_id, message_id)
    elif data == "my":
        answer(callback_id)
        send_my(chat_id, message_id)
    elif data == "wallet":
        answer(callback_id)
        send_wallet(chat_id, message_id)
    elif data == "wtx":
        answer(callback_id)
        send_wallet_transactions(chat_id, message_id)
    elif data == "wcharge":
        answer(callback_id)
        send_charge_prompt(chat_id, message_id)
    elif data.startswith("wreq:"):
        answer(callback_id)
        if not is_admin:
            answer(callback_id, "فقط ادمین میتواند.")
            return
        _, raw_id, decision = data.split(":", 2)
        request = store.wallet_request(int(raw_id))
        if not request or request["status"] != "pending":
            edit_message(chat_id, message_id, "این درخواست قبلاً بررسی شده است.")
            return
        amount = int(request["amount"])
        kind = request.get("kind") or "wallet"
        if decision == "approve":
            try:
                if kind == "wallet":
                    balance = store.adjust_wallet(
                        request["telegram_id"], amount, "admin_charge",
                        f"شارژ اضافه #{raw_id}", str(uid),
                    )
                    store.set_wallet_request_status(raw_id, "approved", uid)
                    send(
                        request["telegram_id"],
                        f"✅ شارژ اضافه #{raw_id} تایید شد.\n"
                        f"💳 مبلغ: {money(amount)} تومان\n👛 موجودی: {money(balance)} تومان",
                        wallet_keyboard(),
                    )
                    result = f"✅ درخواست #{raw_id} تایید شد — {money(amount)} تومان"
                    notify_text = f"شارژ کیف پول #{raw_id} — {money(amount)} تومان"
                else:
                    cfg = store.config(int(request.get("config_id") or 0))
                    if not cfg or cfg["telegram_id"] != request["telegram_id"]:
                        raise RuntimeError("سرویس مربوط به درخواست پیدا نشد")
                    server = servers.get_server(cfg.get("server_id"))
                    days = amount if kind == "days" else 0
                    gb = float(amount) if kind == "gb" else 0
                    xui_client.renew_client(cfg["email"], days, gb, server=server)
                    store.extend_config(cfg["id"], days, gb)
                    store.set_wallet_request_status(raw_id, "approved", uid)
                    label = f"{days} روز" if kind == "days" else f"{gb:g} GB"
                    send_config_detail(
                        request["telegram_id"], None, cfg["id"],
                        notice=f"✅ شارژ اضافه #{raw_id} تایید شد: {label}",
                    )
                    result = f"✅ درخواست #{raw_id} تایید شد — {label}"
                    notify_text = f"شارژ سرویس #{cfg['id']} ({label}) — درخواست #{raw_id}"
                store.log(uid, "approve_extra_charge", f"request={raw_id} kind={kind} amount={amount}")
                store.notify("wallet", notify_text, "extra")
            except Exception as exc:
                edit_message(chat_id, message_id, "خطا در تایید شارژ: " + esc(exc))
                return
        else:
            store.set_wallet_request_status(raw_id, "rejected", uid)
            store.notify("wallet", f"شارژ #{raw_id} رد شد", "extra")
            send(request["telegram_id"], f"❌ درخواست شارژ #{raw_id} رد شد.", wallet_keyboard())
            result = f"❌ درخواست #{raw_id} رد شد"
        edit_message(chat_id, message_id, result)
    elif data == "help":
        answer(callback_id)
        send_help(chat_id, message_id)
    elif data.startswith("buy:"):
        answer(callback_id)
        try:
            _do_buy(chat_id, int(data.split(":", 1)[1]))
        except Exception:
            send(chat_id, "خطا در ثبت سفارش.")
    elif data.startswith("approve:") or data.startswith("walletapprove:") or data.startswith("reject:"):
        if not is_admin:
            answer(callback_id, "فقط ادمین میتواند.")
            return
        action, raw_id = data.split(":", 1)
        order_id = int(raw_id)
        if action == "approve":
            result = approve_order(order_id, uid)
        elif action == "walletapprove":
            result = approve_order(order_id, uid, use_wallet=True)
        else:
            store.set_order_status(order_id, "rejected", uid)
            store.log(uid, "reject_order", f"order={order_id}")
            order = store.order(order_id)
            if order:
                send(order["telegram_id"], f"سفارش #{order_id} رد شد.")
            result = f"سفارش #{order_id} رد شد."
        answer(callback_id)
        edit_message(chat_id, message_id, esc(result))
    elif data.startswith("detail:"):
        answer(callback_id)
        send_config_detail(chat_id, message_id, int(data.split(":", 1)[1]))
    elif data.startswith("clinks:"):
        answer(callback_id)
        send_config_links(chat_id, message_id, int(data.split(":", 1)[1]))
    elif data.startswith("renew:"):
        answer(callback_id)
        config_id = int(data.split(":", 1)[1])
        item = owned_config(chat_id, config_id)
        if not item:
            return send_config_detail(chat_id, message_id, config_id)
        plan = store.plan(item["plan_id"]) if item["plan_id"] else None
        days = plan["days"] if plan else 30
        submit_charge_request(chat_id, message_id, config_id, "days", days)
    elif data in ("days:", "gb:") or data.startswith("days:") or data.startswith("gb:"):
        answer(callback_id)
        kind, raw_id = data.split(":", 1)
        send_amount_prompt(chat_id, message_id, int(raw_id), kind)
    elif data.startswith("uuidask:") or data.startswith("delask:"):
        answer(callback_id)
        kind, raw_id = data.split(":", 1)
        send_confirm(chat_id, message_id, int(raw_id), "uuid" if kind == "uuidask" else "delete")
    elif data.startswith("uuid:"):
        answer(callback_id)
        change_uuid(chat_id, message_id, int(data.split(":", 1)[1]))
    elif data.startswith("toggle:"):
        answer(callback_id)
        toggle_service(chat_id, message_id, int(data.split(":", 1)[1]))
    elif data.startswith("del:"):
        answer(callback_id)
        delete_service(chat_id, message_id, int(data.split(":", 1)[1]))
    else:
        answer(callback_id)


def notify_new_inbound(item):
    if not configured() or not admin_id():
        return
    send(
        admin_id(),
        f"🆕 اینابند جدید در سرور <b>{esc(item['server_name'])}</b> شناسایی شد:\n"
        f"#{item['inbound_id']} {esc(item['remark'])} — پورت {item['port']}\n"
        "از بخش سرورها در پنل وب میتوانی آن را ببینی.",
    )


def process_updates():
    offset = int(store.get_setting("tg_offset", "0") or 0)
    params = {"offset": offset, "timeout": 20, "allowed_updates": json.dumps(["message", "callback_query"])}
    result = api("getUpdates", params, timeout=26)
    if not result or not result.get("ok"):
        return
    for update in result.get("result") or []:
        offset = max(offset, int(update.get("update_id", 0)) + 1)
        try:
            if "message" in update:
                handle_message(update["message"])
            elif "callback_query" in update:
                handle_callback(update["callback_query"])
        except Exception as exc:
            print(f"[telegram] handler error: {type(exc).__name__}: {exc}", flush=True)
    if offset:
        store.set_setting("tg_offset", str(offset))


def process_broadcasts():
    item = store.pending_broadcast()
    if not item:
        return
    users = store.rows("SELECT telegram_id FROM users WHERE banned=0")
    sent = 0
    for user in users:
        if send(user["telegram_id"], item["text"], parse_mode=None):
            sent += 1
    store.mark_broadcast(item["id"], sent)


def run():
    reload_secrets()
    if not configured():
        print("[telegram] BOT_TOKEN not configured — bot polling disabled", flush=True)
        return
    print("[telegram] polling started", flush=True)
    while True:
        try:
            process_updates()
            process_broadcasts()
        except Exception as exc:
            print(f"[telegram] loop error: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(3)
