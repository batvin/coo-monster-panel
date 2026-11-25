"""Registry for one or more 3x-ui servers.

The primary server is seeded from config.env/xui.env. Additional servers can be
added from the web admin; their API tokens are stored only in servers.json
(mode 600) and never returned by the API.
"""

import json
import os
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVERS_FILE = ROOT / "servers.json"
STATE_FILE = ROOT / "inbound_state.json"

_lock = threading.RLock()


def _load_env(path):
    data = {}
    p = Path(path)
    if not p.exists():
        return data
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


CONF = _load_env(ROOT / "config.env")
LEGACY_TOKEN = _load_env(ROOT / "xui.env").get("XUI_API_TOKEN", "")


def _default_server():
    return {
        "id": 1,
        "name": CONF.get("SERVER_NAME", "سرور اصلی"),
        "host": CONF.get("XUI_HOST", "http://127.0.0.1:2053").rstrip("/"),
        "base_path": CONF.get("XUI_BASE_PATH", "/panel").rstrip("/"),
        "public_host": CONF.get("PUBLIC_HOST", "").strip(),
        "token": LEGACY_TOKEN,
        "xray_bin": CONF.get("XRAY_BIN", "/usr/local/x-ui/bin/xray-linux-amd64"),
        "enabled": True,
    }


def _read_json(path, fallback):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else fallback
    except Exception:
        return fallback


def _write_json(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def list_servers():
    with _lock:
        data = _read_json(SERVERS_FILE, {})
        servers = data.get("servers")
        if not isinstance(servers, list) or not servers:
            servers = [_default_server()]
            _write_json(SERVERS_FILE, {"servers": servers})
        return servers


def get_server(server_id):
    try:
        wanted = str(int(server_id))
    except (TypeError, ValueError):
        return None
    for server in list_servers():
        if str(server.get("id")) == wanted:
            return server
    return None


def default_server():
    return get_server(1) or _default_server()


def public_server(server):
    """Return a API-safe copy; never expose the token itself."""
    data = {
        "id": server.get("id"),
        "name": server.get("name", ""),
        "host": server.get("host", ""),
        "base_path": server.get("base_path", ""),
        "public_host": server.get("public_host", ""),
        "xray_bin": server.get("xray_bin", ""),
        "enabled": bool(server.get("enabled", True)),
    }
    token = str(server.get("token") or "")
    data["token_set"] = bool(token)
    data["token_mask"] = ("*" * max(0, len(token) - 4) + token[-4:]) if token else ""
    return data


def _normalize(server):
    server["id"] = int(server.get("id") or 0)
    server["name"] = str(server.get("name") or f"سرور {server['id']}")
    server["host"] = str(server.get("host") or "http://127.0.0.1:2053").rstrip("/")
    base = str(server.get("base_path") or "").strip()
    if base and not base.startswith("/"):
        base = "/" + base
    server["base_path"] = base.rstrip("/")
    server["public_host"] = str(server.get("public_host") or "").strip()
    server["token"] = str(server.get("token") or "").strip()
    server["xray_bin"] = str(server.get("xray_bin") or "/usr/local/x-ui/bin/xray-linux-amd64").strip()
    server["enabled"] = bool(server.get("enabled", True))
    return server


def add_server(data):
    servers = list_servers()
    next_id = max([int(s.get("id") or 0) for s in servers] or [0]) + 1
    server = _normalize({
        "id": next_id,
        "name": data.get("name"),
        "host": data.get("host"),
        "base_path": data.get("base_path"),
        "public_host": data.get("public_host"),
        "token": data.get("token"),
        "xray_bin": data.get("xray_bin"),
        "enabled": data.get("enabled", True),
    })
    if not server["token"] and next_id != 1:
        raise ValueError("توکن API سرور الزامی است")
    if not server["public_host"]:
        server["public_host"] = server["host"].split("://")[-1].split(":")[0]
    with _lock:
        servers.append(server)
        _write_json(SERVERS_FILE, {"servers": servers})
    return public_server(server)


def update_server(server_id, data):
    servers = list_servers()
    target = None
    for server in servers:
        if str(server.get("id")) == str(server_id):
            target = server
            break
    if not target:
        raise ValueError("سرور پیدا نشد")
    for key in ("name", "host", "base_path", "public_host", "xray_bin", "enabled"):
        if key in data:
            target[key] = data[key]
    if str(data.get("token") or "").strip():
        target["token"] = str(data["token"]).strip()
    _normalize(target)
    with _lock:
        _write_json(SERVERS_FILE, {"servers": servers})
    return public_server(target)


def delete_server(server_id):
    if str(server_id) == "1":
        raise ValueError("سرور اصلی قابل حذف نیست")
    servers = list_servers()
    remaining = [s for s in servers if str(s.get("id")) != str(server_id)]
    if len(remaining) == len(servers):
        raise ValueError("سرور پیدا نشد")
    with _lock:
        _write_json(SERVERS_FILE, {"servers": remaining})
    state = _read_json(STATE_FILE, {})
    state.get("seen", {}).pop(str(server_id), None)
    _write_json(STATE_FILE, state)
    return True


def _state():
    state = _read_json(STATE_FILE, {})
    state.setdefault("initialized", False)
    state.setdefault("seen", {})
    state.setdefault("last_sync", {})
    return state


def inbound_meta(server_id):
    state = _state()
    seen = state["seen"].get(str(server_id), {})
    return {
        "last_sync": int(state["last_sync"].get(str(server_id), 0) or 0),
        **seen,
    }


def sync_inbounds():
    """Poll all servers and return inbounds that were not seen before."""
    import xui_client  # lazy import avoids a module cycle

    state = _state()
    first_run = not state["initialized"]
    new_items = []
    now = int(time.time())

    for server in list_servers():
        sid = str(server.get("id"))
        if not server.get("enabled", True) or not server.get("token"):
            continue
        try:
            current = xui_client.inbounds(server=server)
        except Exception:
            continue

        state["last_sync"][sid] = now
        seen = state["seen"].setdefault(sid, {})
        for inbound in current:
            iid = str(inbound.get("id"))
            if not iid:
                continue
            if iid not in seen:
                seen[iid] = {
                    "first_seen": now,
                    "remark": inbound.get("remark", ""),
                    "port": inbound.get("port"),
                }
                if not first_run:
                    new_items.append({
                        "server_id": server.get("id"),
                        "server_name": server.get("name", ""),
                        "inbound_id": inbound.get("id"),
                        "remark": inbound.get("remark", ""),
                        "port": inbound.get("port"),
                    })
            else:
                seen[iid]["remark"] = inbound.get("remark", "")
                seen[iid]["port"] = inbound.get("port")

    state["initialized"] = True
    with _lock:
        _write_json(STATE_FILE, state)
    return new_items


def api_servers():
    """All servers with live inbound lists, safe for the admin API."""
    import xui_client  # lazy import avoids a module cycle

    items = []
    for server in list_servers():
        item = public_server(server)
        meta = inbound_meta(server.get("id"))
        item["last_sync"] = meta.pop("last_sync", 0)
        try:
            inbounds = xui_client.inbounds(server=server)
            item["ok"] = True
            item["error"] = ""
        except Exception as exc:
            inbounds = []
            item["ok"] = False
            item["error"] = str(exc)[:200]

        shaped = []
        for inbound in inbounds:
            iid = str(inbound.get("id"))
            info = meta.get(iid, {})
            stream = inbound.get("streamSettings") or {}
            shaped.append({
                "id": inbound.get("id"),
                "remark": inbound.get("remark", ""),
                "protocol": inbound.get("protocol", ""),
                "port": inbound.get("port"),
                "network": stream.get("network", ""),
                "security": stream.get("security", ""),
                "enable": bool(inbound.get("enable")),
                "first_seen": info.get("first_seen", 0),
            })
        item["inbounds"] = shaped
        items.append(item)
    return {"items": items}
