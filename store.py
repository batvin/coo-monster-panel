"""Tiny SQLite store for the VPN shop. No external dependencies."""

import json
import sqlite3
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "vpn.db"
_lock = threading.RLock()


def connect():
    con = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _ensure_column(con, table, column, definition):
    cols = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init():
    with _lock, connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT DEFAULT '',
                first_name TEXT DEFAULT '',
                balance INTEGER DEFAULT 0,
                role TEXT DEFAULT 'user',
                banned INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT 0,
                updated_at INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                days INTEGER NOT NULL,
                total_gb REAL NOT NULL,
                price INTEGER NOT NULL,
                inbound_ids TEXT NOT NULL DEFAULT '[1]',
                active INTEGER DEFAULT 1,
                sort INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                plan_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                email TEXT,
                note TEXT DEFAULT '',
                created_at INTEGER NOT NULL,
                approved_at INTEGER,
                approved_by INTEGER
            );

            CREATE TABLE IF NOT EXISTS configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                email TEXT NOT NULL UNIQUE,
                plan_id INTEGER,
                inbound_id INTEGER NOT NULL,
                uuid TEXT,
                sub_id TEXT,
                link TEXT,
                expiry_ts INTEGER NOT NULL,
                total_gb REAL NOT NULL,
                note TEXT DEFAULT '',
                active INTEGER DEFAULT 1,
                created_at INTEGER NOT NULL,
                deleted_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                balance_after INTEGER NOT NULL,
                kind TEXT NOT NULL,
                note TEXT DEFAULT '',
                actor TEXT DEFAULT '',
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS promos (
                code TEXT PRIMARY KEY,
                percent INTEGER DEFAULT 0,
                amount INTEGER DEFAULT 0,
                uses INTEGER DEFAULT 0,
                max_uses INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                sent_at INTEGER,
                sent_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT DEFAULT '',
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                subject TEXT DEFAULT '',
                status TEXT DEFAULT 'open',
                priority TEXT DEFAULT 'normal',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                closed_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS ticket_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                sender TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS customer_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                note TEXT NOT NULL,
                author TEXT DEFAULT '',
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                link TEXT DEFAULT '',
                read INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wallet_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL,
                decided_by TEXT DEFAULT '',
                decided_at INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
            CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(telegram_id);
            CREATE INDEX IF NOT EXISTS idx_configs_user ON configs(telegram_id);
            CREATE INDEX IF NOT EXISTS idx_configs_active ON configs(active);
            CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(telegram_id);
            CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
            CREATE INDEX IF NOT EXISTS idx_ticket_messages_ticket ON ticket_messages(ticket_id);
            CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(read);
            CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
            CREATE INDEX IF NOT EXISTS idx_wallet_requests_status ON wallet_requests(status);
            CREATE INDEX IF NOT EXISTS idx_audit_created ON audit(created_at);
            CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at);
            """
        )

        _ensure_column(con, "configs", "server_id", "INTEGER DEFAULT 1")
        _ensure_column(con, "configs", "links", "TEXT DEFAULT ''")
        _ensure_column(con, "configs", "enabled", "INTEGER DEFAULT 1")
        con.execute("CREATE INDEX IF NOT EXISTS idx_configs_enabled ON configs(enabled)")
        _ensure_column(con, "wallet_requests", "kind", "TEXT DEFAULT 'wallet'")
        _ensure_column(con, "wallet_requests", "config_id", "INTEGER DEFAULT 0")
        _ensure_column(con, "wallet_requests", "note", "TEXT DEFAULT ''")
        _ensure_column(con, "orders", "paid_amount", "INTEGER DEFAULT 0")
        _ensure_column(con, "orders", "wallet_used", "INTEGER DEFAULT 0")
        con.commit()

        count = con.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
        if count == 0:
            now = int(time.time())
            defaults = [
                ("۱ ماهه - استاندارد", 30, 20, 100000, '{"targets":[{"server_id":1,"inbound_ids":[1,2]}]}', 1),
                ("۳ ماهه - محبوب", 90, 60, 250000, '{"targets":[{"server_id":1,"inbound_ids":[1,2]}]}', 2),
                ("۱۲ ماهه - ویژه", 365, 200, 700000, '{"targets":[{"server_id":1,"inbound_ids":[1,2]}]}', 3),
                ("تست ۲۴ ساعته", 1, 2, 0, '{"targets":[{"server_id":1,"inbound_ids":[1]}]}', 4),
            ]
            con.executemany(
                "INSERT INTO plans(name,days,total_gb,price,inbound_ids,sort) VALUES(?,?,?,?,?,?)",
                defaults,
            )
            con.executemany(
                "INSERT INTO settings(key,value) VALUES(?,?)",
                [
                    ("welcome", "به کو مونستر خوش آمدی 👾\nاز منوی زیر یک پلن رو انتخاب کن."),
                    ("channel", ""),
                    ("support", ""),
                ],
            )
            con.execute(
                "INSERT INTO audit(actor,action,detail,created_at) VALUES(?,?,?,?)",
                ("system", "init", "database seeded", now),
            )
        con.commit()


def rows(sql, args=()):
    with _lock, connect() as con:
        return [dict(r) for r in con.execute(sql, args).fetchall()]


def row(sql, args=()):
    with _lock, connect() as con:
        result = con.execute(sql, args).fetchone()
        return dict(result) if result else None


def execute(sql, args=()):
    with _lock, connect() as con:
        cur = con.execute(sql, args)
        con.commit()
        return cur.lastrowid


def log(actor, action, detail=""):
    execute(
        "INSERT INTO audit(actor,action,detail,created_at) VALUES(?,?,?,?)",
        (str(actor), action, str(detail)[:500], int(time.time())),
    )


def get_setting(key, default=""):
    result = row("SELECT value FROM settings WHERE key=?", (key,))
    return result["value"] if result else default


def set_setting(key, value):
    execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def ensure_user(telegram_id, username="", first_name=""):
    now = int(time.time())
    execute(
        "INSERT INTO users(telegram_id,username,first_name,created_at,updated_at) "
        "VALUES(?,?,?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET "
        "username=excluded.username, first_name=excluded.first_name, updated_at=excluded.updated_at",
        (int(telegram_id), username or "", first_name or "", now, now),
    )
    return get_user(telegram_id)


def get_user(telegram_id):
    return row("SELECT * FROM users WHERE telegram_id=?", (int(telegram_id),))


def active_plans():
    return rows("SELECT * FROM plans WHERE active=1 ORDER BY sort,id")


def plan(plan_id):
    return row("SELECT * FROM plans WHERE id=?", (int(plan_id),))


def plan_targets(plan_row):
    """Accept both the old [1,2] format and the new per-server target format."""
    try:
        raw = json.loads((plan_row or {}).get("inbound_ids") or "[]")
    except Exception:
        raw = []

    if isinstance(raw, list) and all(isinstance(x, int) for x in raw):
        return [{"server_id": 1, "inbound_ids": raw}] if raw else []

    if isinstance(raw, dict):
        targets = raw.get("targets") or []
        result = []
        for item in targets:
            try:
                sid = int(item.get("server_id") or 1)
                ids = [int(i) for i in (item.get("inbound_ids") or []) if int(i) > 0]
            except (TypeError, ValueError):
                continue
            if ids:
                result.append({"server_id": sid, "inbound_ids": ids})
        return result

    return []


def plan_targets_label(plan_row):
    parts = []
    for target in plan_targets(plan_row):
        parts.append(f"srv{target['server_id']}:{','.join(str(i) for i in target['inbound_ids'])}")
    return " | ".join(parts) or "-"


def create_order(telegram_id, plan_id, email=None, note=""):
    return execute(
        "INSERT INTO orders(telegram_id,plan_id,status,email,note,created_at) VALUES(?,?,?,?,?,?)",
        (int(telegram_id), int(plan_id), "pending", email, note, int(time.time())),
    )


def order(order_id):
    return row("SELECT * FROM orders WHERE id=?", (int(order_id),))


def set_order_status(order_id, status, approved_by=0, paid_amount=None, wallet_used=None):
    fields = ["status=?", "approved_at=?", "approved_by=?"]
    args = [status, int(time.time()), int(approved_by or 0)]
    if paid_amount is not None:
        fields.append("paid_amount=?")
        args.append(int(paid_amount))
    if wallet_used is not None:
        fields.append("wallet_used=?")
        args.append(int(wallet_used))
    args.append(int(order_id))
    execute(f"UPDATE orders SET {', '.join(fields)} WHERE id=?", tuple(args))


def pending_orders():
    return rows(
        "SELECT o.*, u.username, u.first_name, u.balance, "
        "p.name AS plan_name, p.days, p.total_gb, p.price "
        "FROM orders o LEFT JOIN users u ON u.telegram_id=o.telegram_id "
        "LEFT JOIN plans p ON p.id=o.plan_id WHERE o.status='pending' ORDER BY o.id"
    )


def add_config(data):
    links = data.get("links") or []
    if isinstance(links, str):
        try:
            links = json.loads(links or "[]")
        except Exception:
            links = []
    if not links and data.get("link"):
        links = [data["link"]]

    cid = execute(
        "INSERT INTO configs(telegram_id,email,plan_id,server_id,inbound_id,uuid,sub_id,link,links,"
        "expiry_ts,total_gb,note,active,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            int(data["telegram_id"]),
            data["email"],
            data.get("plan_id"),
            int(data.get("server_id") or 1),
            int(data["inbound_id"]),
            data.get("uuid") or "",
            data.get("sub_id") or "",
            data.get("link") or (links[0] if links else ""),
            json.dumps(links, ensure_ascii=False),
            int(data["expiry_ts"]),
            float(data.get("total_gb", 0)),
            data.get("note", ""),
            1,
            int(time.time()),
        ),
    )
    return config(cid)


def refresh_config_links(server_id, public_host):
    """Rewrite stored share links for a server after its public host changes."""
    import xui_client

    items = rows(
        "SELECT id, link, links FROM configs WHERE server_id=? AND deleted_at IS NULL",
        (int(server_id),),
    )
    changed = 0
    for item in items:
        raw = item.get("links")
        if isinstance(raw, str):
            try:
                old = json.loads(raw or "[]")
            except Exception:
                old = []
        else:
            old = raw or []
        old = [str(link) for link in old if link]
        if not old and item.get("link"):
            old = [str(item["link"])]
        new = xui_client.retarget_links(old, public_host)
        if new == old:
            continue
        execute(
            "UPDATE configs SET link=?, links=? WHERE id=?",
            (new[0] if new else "", json.dumps(new, ensure_ascii=False), item["id"]),
        )
        changed += 1
    return changed


def user_configs(telegram_id, active_only=True):
    where = "telegram_id=?" + (" AND active=1 AND deleted_at IS NULL" if active_only else "")
    return rows(f"SELECT * FROM configs WHERE {where} ORDER BY id DESC", (int(telegram_id),))


def config(config_id):
    return row("SELECT * FROM configs WHERE id=?", (int(config_id),))


def deactivate_config(config_id):
    execute(
        "UPDATE configs SET active=0, deleted_at=? WHERE id=?",
        (int(time.time()), int(config_id)),
    )


def set_config_enabled(config_id, enabled):
    """Toggle a service on/off without hiding it from the customer's list."""
    execute(
        "UPDATE configs SET enabled=? WHERE id=?",
        (1 if enabled else 0, int(config_id)),
    )
    return config(config_id)


def update_config_identity(config_id, uuid_value, links):
    """Persist a regenerated UUID and its rewritten share links."""
    links = [str(link) for link in (links or []) if link]
    execute(
        "UPDATE configs SET uuid=?, link=?, links=? WHERE id=?",
        (
            str(uuid_value),
            links[0] if links else "",
            json.dumps(links, ensure_ascii=False),
            int(config_id),
        ),
    )
    return config(config_id)


def create_wallet_request(telegram_id, amount, kind="wallet", config_id=0, note=""):
    return execute(
        "INSERT INTO wallet_requests(telegram_id,amount,kind,config_id,note,status,created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (
            int(telegram_id), int(amount), str(kind), int(config_id or 0),
            str(note)[:300], "pending", int(time.time()),
        ),
    )


def wallet_request(request_id):
    return row("SELECT * FROM wallet_requests WHERE id=?", (int(request_id),))


def extra_charges(limit=150):
    """Pending and recent top-up requests for the admin 'شارژ اضافه' section."""
    return rows(
        "SELECT r.*, u.username, u.first_name, u.balance, "
        "c.email AS config_email, p.name AS plan_name "
        "FROM wallet_requests r "
        "LEFT JOIN users u ON u.telegram_id=r.telegram_id "
        "LEFT JOIN configs c ON c.id=r.config_id "
        "LEFT JOIN plans p ON p.id=c.plan_id "
        "ORDER BY (r.status='pending') DESC, r.id DESC LIMIT ?",
        (int(limit),),
    )


def set_wallet_request_status(request_id, status, actor=""):
    execute(
        "UPDATE wallet_requests SET status=?, decided_by=?, decided_at=? WHERE id=?",
        (status, str(actor), int(time.time()), int(request_id)),
    )
    return wallet_request(request_id)


def extend_config(config_id, days, gb=0):
    item = config(config_id)
    if not item:
        return None
    base = max(item["expiry_ts"], int(time.time()))
    execute(
        "UPDATE configs SET expiry_ts=?, total_gb=total_gb+?, active=1, deleted_at=NULL WHERE id=?",
        (base + int(days) * 86400, float(gb), int(config_id)),
    )
    return config(config_id)


def adjust_wallet(telegram_id, amount, kind, note="", actor="web"):
    """Change a balance and write a transaction. Raises when balance would go negative."""
    amount = int(amount)
    if amount == 0:
        return None
    with _lock, connect() as con:
        current = con.execute(
            "SELECT balance FROM users WHERE telegram_id=?", (int(telegram_id),)
        ).fetchone()
        if not current:
            raise ValueError("کاربر پیدا نشد")
        new_balance = int(current["balance"] or 0) + amount
        if new_balance < 0:
            raise ValueError("موجودی کافی نیست")
        con.execute(
            "UPDATE users SET balance=?, updated_at=? WHERE telegram_id=?",
            (new_balance, int(time.time()), int(telegram_id)),
        )
        con.execute(
            "INSERT INTO transactions(telegram_id,amount,balance_after,kind,note,actor,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (int(telegram_id), amount, new_balance, kind, str(note)[:200], str(actor), int(time.time())),
        )
        con.commit()
        return new_balance


def wallet_transactions(telegram_id=None, limit=100):
    if telegram_id:
        return rows(
            "SELECT * FROM transactions WHERE telegram_id=? ORDER BY id DESC LIMIT ?",
            (int(telegram_id), int(limit)),
        )
    return rows("SELECT * FROM transactions ORDER BY id DESC LIMIT ?", (int(limit),))


def wallet_summary():
    q = lambda sql: row(sql)["n"]
    return {
        "balance_total": q("SELECT COALESCE(SUM(balance),0) n FROM users"),
        "topped_up": q("SELECT COALESCE(SUM(amount),0) n FROM transactions WHERE amount>0"),
        "spent": q("SELECT COALESCE(-SUM(amount),0) n FROM transactions WHERE amount<0"),
        "transactions": q("SELECT COUNT(*) n FROM transactions"),
    }


def customers():
    return rows(
        """
        SELECT u.*,
          (SELECT COUNT(*) FROM orders o WHERE o.telegram_id=u.telegram_id) AS orders_count,
          (SELECT COUNT(*) FROM orders o WHERE o.telegram_id=u.telegram_id AND o.status='approved') AS purchases,
          (SELECT COUNT(*) FROM configs c WHERE c.telegram_id=u.telegram_id AND c.active=1 AND c.deleted_at IS NULL) AS active_services,
          (SELECT COALESCE(SUM(o.paid_amount),0) FROM orders o WHERE o.telegram_id=u.telegram_id AND o.status='approved') AS spent,
          (SELECT MAX(o.created_at) FROM orders o WHERE o.telegram_id=u.telegram_id) AS last_order_at
        FROM users u ORDER BY last_order_at DESC, u.created_at DESC
        """
    )


def customer_detail(telegram_id):
    user = get_user(telegram_id)
    if not user:
        return None
    return {
        "user": user,
        "orders": rows(
            "SELECT o.*, p.name AS plan_name FROM orders o "
            "LEFT JOIN plans p ON p.id=o.plan_id WHERE o.telegram_id=? ORDER BY o.id DESC",
            (int(telegram_id),),
        ),
        "configs": rows(
            "SELECT c.*, p.name AS plan_name FROM configs c "
            "LEFT JOIN plans p ON p.id=c.plan_id WHERE c.telegram_id=? ORDER BY c.id DESC",
            (int(telegram_id),),
        ),
        "transactions": wallet_transactions(telegram_id, 100),
        "notes": customer_notes(telegram_id),
    }


def overview():
    q = lambda sql: row(sql)["n"]
    return {
        "users": q("SELECT COUNT(*) n FROM users"),
        "customers": q("SELECT COUNT(DISTINCT telegram_id) n FROM orders"),
        "plans": q("SELECT COUNT(*) n FROM plans WHERE active=1"),
        "pending": q("SELECT COUNT(*) n FROM orders WHERE status='pending'"),
        "approved": q("SELECT COUNT(*) n FROM orders WHERE status='approved'"),
        "active_configs": q("SELECT COUNT(*) n FROM configs WHERE active=1 AND deleted_at IS NULL"),
        "expired_configs": q("SELECT COUNT(*) n FROM configs WHERE active=1 AND expiry_ts < strftime('%s','now')"),
        "revenue": q("SELECT COALESCE(SUM(paid_amount),0) n FROM orders WHERE status='approved'"),
        "wallet_total": q("SELECT COALESCE(SUM(balance),0) n FROM users"),
    }


def notify(kind, text, link=""):
    return execute(
        "INSERT INTO notifications(kind,text,link,created_at) VALUES(?,?,?,?)",
        (kind, str(text)[:300], str(link)[:100], int(time.time())),
    )


def notifications(limit=120):
    return rows("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (int(limit),))


def unread_notifications():
    return row("SELECT COUNT(*) n FROM notifications WHERE read=0")["n"]


def mark_notification(nid, value=1):
    execute("UPDATE notifications SET read=? WHERE id=?", (int(value), int(nid)))


def mark_all_notifications():
    execute("UPDATE notifications SET read=1 WHERE read=0")


def tickets(status=None):
    where = "WHERE t.status=?" if status else ""
    args = (status,) if status else ()
    return rows(
        "SELECT t.*, u.username, u.first_name, "
        "(SELECT COUNT(*) FROM ticket_messages m WHERE m.ticket_id=t.id) AS messages "
        f"FROM tickets t LEFT JOIN users u ON u.telegram_id=t.telegram_id {where} "
        "ORDER BY CASE t.status WHEN 'open' THEN 0 ELSE 1 END, t.id DESC",
        args,
    )


def ticket(ticket_id):
    return row("SELECT * FROM tickets WHERE id=?", (int(ticket_id),))


def user_tickets(telegram_id):
    return rows("SELECT * FROM tickets WHERE telegram_id=? ORDER BY id DESC", (int(telegram_id),))


def create_ticket(telegram_id, subject, message):
    now = int(time.time())
    tid = execute(
        "INSERT INTO tickets(telegram_id,subject,status,priority,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        (int(telegram_id), str(subject or "پیام پشتیبانی")[:120], "open", "normal", now, now),
    )
    add_ticket_message(tid, "user", message)
    return ticket(tid)


def ticket_messages(ticket_id):
    return rows(
        "SELECT * FROM ticket_messages WHERE ticket_id=? ORDER BY id",
        (int(ticket_id),),
    )


def add_ticket_message(ticket_id, sender, message):
    now = int(time.time())
    mid = execute(
        "INSERT INTO ticket_messages(ticket_id,sender,message,created_at) VALUES(?,?,?,?)",
        (int(ticket_id), str(sender)[:40], str(message)[:4000], now),
    )
    execute("UPDATE tickets SET updated_at=?, status=CASE WHEN ?='user' THEN 'open' ELSE status END WHERE id=?",
            (now, str(sender), int(ticket_id)))
    return mid


def set_ticket_status(ticket_id, status):
    now = int(time.time())
    execute(
        "UPDATE tickets SET status=?, updated_at=?, closed_at=CASE WHEN ?='closed' THEN ? ELSE NULL END WHERE id=?",
        (status, now, status, now, int(ticket_id)),
    )
    return ticket(ticket_id)


def delete_ticket(ticket_id):
    execute("DELETE FROM ticket_messages WHERE ticket_id=?", (int(ticket_id),))
    execute("DELETE FROM tickets WHERE id=?", (int(ticket_id),))


def customer_notes(telegram_id):
    return rows(
        "SELECT * FROM customer_notes WHERE telegram_id=? ORDER BY id DESC",
        (int(telegram_id),),
    )


def add_customer_note(telegram_id, note, author="web"):
    return execute(
        "INSERT INTO customer_notes(telegram_id,note,author,created_at) VALUES(?,?,?,?)",
        (int(telegram_id), str(note)[:600], str(author)[:40], int(time.time())),
    )


def delete_customer_note(note_id):
    execute("DELETE FROM customer_notes WHERE id=?", (int(note_id),))


def daily_stats(days=14):
    out = []
    for offset in range(int(days) - 1, -1, -1):
        day = time.strftime("%Y-%m-%d", time.localtime(time.time() - offset * 86400))
        item = row(
            "SELECT COUNT(*) orders, COALESCE(SUM(paid_amount),0) revenue "
            "FROM orders WHERE date(created_at,'unixepoch','localtime')=?",
            (day,),
        )
        out.append({
            "day": day,
            "orders": item["orders"],
            "revenue": item["revenue"],
        })
    return out


def export_data():
    tables = [
        "users", "plans", "orders", "configs", "transactions", "promos",
        "broadcasts", "audit", "tickets", "ticket_messages", "customer_notes",
        "notifications",
    ]
    data = {"exported_at": int(time.time()), "version": 3}
    for name in tables:
        data[name] = rows(f"SELECT * FROM {name}")
    data["settings"] = rows("SELECT key,value FROM settings")
    return data


def add_broadcast(text):
    return execute("INSERT INTO broadcasts(text,created_at) VALUES(?,?)", (text, int(time.time())))


def pending_broadcast():
    return row("SELECT * FROM broadcasts WHERE sent_at IS NULL ORDER BY id LIMIT 1")


def mark_broadcast(bid, sent_count):
    execute("UPDATE broadcasts SET sent_at=?, sent_count=? WHERE id=?", (int(time.time()), int(sent_count), int(bid)))


def init_lock():
    """Prevent two service processes from both polling Telegram."""
    lock = ROOT / "bot.lock"
    try:
        fd = lock.open("x")
        fd.write(str(time.time()))
        fd.close()
        return True
    except FileExistsError:
        try:
            age = time.time() - lock.stat().st_mtime
        except OSError:
            age = 9999
        if age > 120:
            lock.unlink(missing_ok=True)
            return init_lock()
        return False
