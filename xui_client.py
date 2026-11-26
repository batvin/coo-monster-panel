"""3x-ui API client with support for multiple servers."""

import base64
import hashlib
import json
import re
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
import time

import servers

ROOT = servers.ROOT

CONF = servers.CONF
HOST = CONF.get("XUI_HOST", "http://127.0.0.1:2053").rstrip("/")
BASE_PATH = CONF.get("XUI_BASE_PATH", "/panel").rstrip("/")
PUBLIC_HOST = CONF.get("PUBLIC_HOST", "").strip()
XRAY_BIN = CONF.get("XRAY_BIN", "/usr/local/x-ui/bin/xray-linux-amd64")

_pub_cache = {}
_health_cache = {}
_health_lock = threading.Lock()
HEALTH_TTL = 15


class XuiError(RuntimeError):
    pass


def _server(server=None):
    if server is None:
        return servers.default_server()
    if isinstance(server, dict):
        return server
    resolved = servers.get_server(server)
    if not resolved:
        raise XuiError(f"server {server} not found")
    return resolved


def _api_base(server=None):
    item = _server(server)
    host = str(item.get("host") or HOST).rstrip("/")
    base = str(item.get("base_path") or BASE_PATH).rstrip("/")
    return host + base


def _request(method, path, body=None, server=None, timeout=12):
    item = _server(server)
    token = str(item.get("token") or "")
    if not token:
        raise XuiError(f"XUI token not configured for server {item.get('id')}")

    data = None
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        # Some panels sit behind Cloudflare, which rejects urllib's default UA (error 1010).
        "User-Agent": "CooMonster-Admin/1.0",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(_api_base(item) + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise XuiError(f"HTTP {exc.code} from x-ui") from None
    except Exception as exc:
        raise XuiError(f"x-ui unreachable: {type(exc).__name__}") from None

    try:
        obj = json.loads(raw)
    except Exception:
        raise XuiError("invalid JSON from x-ui") from None
    if isinstance(obj, dict) and obj.get("success") is False:
        raise XuiError(str(obj.get("msg") or "x-ui returned failure")[:300])
    return obj


def clear_health_cache():
    with _health_lock:
        _health_cache.clear()


def health(server=None):
    key = str(server or "default")
    now = time.time()
    with _health_lock:
        cached = _health_cache.get(key)
        if cached and now - cached[0] < HEALTH_TTL:
            return dict(cached[1])
    try:
        data = _request("GET", "/panel/api/inbounds/list", server=server, timeout=6)
        result = {"ok": True, "inbounds": len(data.get("obj") or [])}
    except XuiError as exc:
        result = {"ok": False, "error": str(exc)}
    with _health_lock:
        _health_cache[key] = (now, result)
    return dict(result)


def inbounds(server=None):
    data = _request("GET", "/panel/api/inbounds/list", server=server)
    return data.get("obj") or []


def get_inbound(inbound_id, server=None):
    data = _request("GET", f"/panel/api/inbounds/get/{int(inbound_id)}", server=server)
    return data.get("obj")


def get_client(email, server=None):
    safe = urllib.parse.quote(str(email), safe="")
    data = _request("GET", f"/panel/api/clients/get/{safe}", server=server)
    obj = data.get("obj")
    if isinstance(obj, list):
        obj = obj[0] if obj else None
    if isinstance(obj, dict) and isinstance(obj.get("client"), dict):
        return obj["client"]
    return obj


def public_key_from_private(private_key, server=None):
    if not private_key:
        return ""
    cache_key = hashlib.sha256(private_key.encode()).hexdigest()
    if cache_key in _pub_cache:
        return _pub_cache[cache_key]
    xray_bin = str(_server(server).get("xray_bin") or XRAY_BIN)
    try:
        out = subprocess.run(
            [xray_bin, "x25519", "-i", private_key],
            capture_output=True,
            text=True,
            timeout=6,
        ).stdout
        match = re.search(r"PublicKey\)\s*:\s*(\S+)", out)
        value = match.group(1) if match else ""
    except Exception:
        value = ""
    _pub_cache[cache_key] = value
    return value


def retarget_links(links, public_host):
    """Rewrite the host inside existing share links after a server host change."""
    host = str(public_host or "").strip()
    if not host:
        return [str(link) for link in (links or []) if link]
    result = []
    for link in links or []:
        link = str(link)
        try:
            parts = urllib.parse.urlsplit(link)
            if not parts.netloc or "@" not in parts.netloc:
                result.append(link)
                continue
            userinfo, hostport = parts.netloc.rsplit("@", 1)
            if hostport.startswith("["):
                new_hostport = host + hostport[hostport.find("]"):]
            elif ":" in hostport:
                new_hostport = f"{host}:{hostport.rsplit(':', 1)[1]}"
            else:
                new_hostport = host
            result.append(urllib.parse.urlunsplit((
                parts.scheme, f"{userinfo}@{new_hostport}", parts.path,
                parts.query, parts.fragment,
            )))
        except Exception:
            result.append(link)
    return result


def replace_link_identity(links, old_uuid, new_uuid):
    """Rewrite the credential inside share links after a UUID change."""
    old = str(old_uuid or "")
    new = str(new_uuid or "")
    if not old or not new or old == new:
        return [str(link) for link in (links or []) if link]
    result = []
    for link in links or []:
        link = str(link)
        try:
            parts = urllib.parse.urlsplit(link)
            if "@" not in parts.netloc:
                result.append(link)
                continue
            userinfo, hostport = parts.netloc.rsplit("@", 1)
            if parts.scheme == "ss":
                padded = userinfo + "=" * (-len(userinfo) % 4)
                decoded = base64.urlsafe_b64decode(padded.encode()).decode("utf-8", "replace")
                method, sep, password = decoded.partition(":")
                if sep and password == old:
                    userinfo = base64.urlsafe_b64encode(
                        f"{method}:{new}".encode()
                    ).decode().rstrip("=")
            elif userinfo == old:
                userinfo = new
            result.append(urllib.parse.urlunsplit((
                parts.scheme, f"{userinfo}@{hostport}", parts.path,
                parts.query, parts.fragment,
            )))
        except Exception:
            result.append(link)
    return result


def build_link(inbound, client, host=None, server=None):
    """Build a VLESS share link from an inbound and its client."""
    if not inbound or not client:
        return ""
    item = _server(server)
    host = (host or item.get("public_host") or PUBLIC_HOST).strip()
    protocol = (inbound.get("protocol") or "vless").lower()
    if protocol not in ("vless", "shadowsocks"):
        return ""

    uuid = client.get("uuid") or client.get("id") or client.get("password") or ""
    if not uuid:
        return ""

    port = inbound.get("port")
    if protocol == "shadowsocks":
        settings = inbound.get("settings") or {}
        if isinstance(settings, str):
            try:
                settings = json.loads(settings)
            except Exception:
                settings = {}
        method = str(settings.get("method") or "")
        password = str(client.get("password") or uuid)
        if not method:
            return ""
        creds = base64.urlsafe_b64encode(f"{method}:{password}".encode()).decode().rstrip("=")
        name = str(inbound.get("remark") or "COO-MONSTER")
        if client.get("email"):
            name += " - " + str(client["email"])
        return f"ss://{creds}@{host}:{port}#{urllib.parse.quote(name)}"

    stream = inbound.get("streamSettings") or {}
    network = stream.get("network") or "tcp"
    security = stream.get("security") or "none"
    reality = stream.get("realitySettings") or {}
    tls = stream.get("tlsSettings") or {}

    params = {"type": network}
    if security == "reality":
        params["security"] = "reality"
        params["pbk"] = public_key_from_private(reality.get("privateKey", ""), server=item)
        names = reality.get("serverNames") or []
        params["sni"] = names[0] if names else "www.yahoo.com"
        shorts = reality.get("shortIds") or []
        params["sid"] = shorts[0] if shorts else ""
        params["fp"] = "firefox"
    elif security == "tls":
        params["security"] = "tls"
        names = tls.get("serverNames") or tls.get("serverName") or []
        if isinstance(names, list):
            params["sni"] = names[0] if names else host
        elif isinstance(names, str):
            params["sni"] = names or host
        params["fp"] = "firefox"

    flow = client.get("flow") or ""
    if network in ("tcp", "raw") and security in ("reality", "tls"):
        if not inbound.get("disableFlow"):
            params["flow"] = flow or "xtls-rprx-vision"

    if network == "ws":
        ws = stream.get("wsSettings") or {}
        if ws.get("path"):
            params["path"] = ws["path"]
        params["host"] = ((ws.get("headers") or {}).get("Host")) or host
    elif network == "grpc":
        grpc = stream.get("grpcSettings") or {}
        if grpc.get("serviceName"):
            params["serviceName"] = grpc["serviceName"]
        params["mode"] = "multi"
    elif network == "xhttp":
        xhttp = stream.get("xhttpSettings") or {}
        if xhttp.get("path"):
            params["path"] = xhttp["path"]
        params["host"] = xhttp.get("host") or host
        if xhttp.get("mode"):
            params["mode"] = xhttp["mode"]
    elif network in ("h2", "http"):
        hs = stream.get("httpSettings") or {}
        if hs.get("path"):
            params["path"] = hs["path"]
        params["host"] = ",".join(hs.get("host") or [host])

    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    name = str(inbound.get("remark") or "COO-MONSTER")
    if client.get("email"):
        name += " - " + str(client["email"])
    return f"vless://{uuid}@{host}:{port}?{query}#{urllib.parse.quote(name)}"


def add_client(email, days, total_gb, inbound_ids, tg_id=0, comment="", server=None):
    item = _server(server)
    ids = [int(i) for i in inbound_ids]
    if not ids:
        raise XuiError("no inbound selected")

    now = time.time()
    body = {
        "client": {
            "email": email,
            "totalGB": int(float(total_gb) * 1024 ** 3),
            "expiryTime": int(now * 1000) + int(days) * 86400 * 1000,
            "tgId": int(tg_id or 0),
            "limitIp": 0,
            "enable": True,
            "comment": comment or "",
        },
        "inboundIds": ids,
    }
    _request("POST", "/panel/api/clients/add", body, server=item)
    client = get_client(email, server=item)
    links = []
    inbound_names = []
    for inbound_id in ids:
        inbound = get_inbound(inbound_id, server=item)
        link = build_link(inbound, client, host=item.get("public_host"), server=item)
        if link:
            links.append(link)
        if inbound:
            inbound_names.append(str(inbound.get("remark") or inbound_id))

    return {
        "email": email,
        "client": client,
        "link": links[0] if links else "",
        "links": links,
        "expiry_ts": int(now) + int(days) * 86400,
        "total_gb": float(total_gb),
        "inbound_id": ids[0],
        "inbound_ids": ids,
        "inbound_name": "، ".join(inbound_names),
        "server_id": item.get("id"),
        "server_name": item.get("name", ""),
    }


def delete_client(email, server=None):
    safe = urllib.parse.quote(str(email), safe="")
    return _request("POST", f"/panel/api/clients/del/{safe}", server=server)


def renew_client(email, days, total_gb=0, server=None):
    existing = get_client(email, server=server)
    if not existing:
        raise XuiError("client not found in x-ui")
    current_ms = int(existing.get("expiryTime") or 0)
    now_ms = int(time.time() * 1000)
    patch = {
        "expiryTime": max(current_ms, now_ms) + int(days) * 86400 * 1000,
    }
    if total_gb:
        patch["totalGB"] = int(existing.get("totalGB") or 0) + int(float(total_gb) * 1024 ** 3)
    # Use the full typed payload: a minimal patch resets enable/tgId in x-ui.
    return update_client(email, patch, server=server)


def update_client(email, patch, server=None):
    """Merge fields into the full client object, then push it back to x-ui.

    x-ui's update endpoint expects ``id`` as the UUID string (the GET response
    uses an integer database id) and ``allowedIPs`` as a list, so normalize both.
    """
    existing = get_client(email, server=server)
    if not existing:
        raise XuiError("client not found in x-ui")
    merged = dict(existing)
    merged.pop("reverse", None)
    merged["id"] = str(existing.get("uuid") or existing.get("id") or "")
    merged["allowedIPs"] = [
        part.strip() for part in str(existing.get("allowedIPs") or "").split(",") if part.strip()
    ]
    merged.update(patch or {})
    safe = urllib.parse.quote(str(email), safe="")
    return _request("POST", f"/panel/api/clients/update/{safe}", merged, server=server)


def set_client_enabled(email, enabled, server=None):
    return update_client(email, {"enable": bool(enabled)}, server=server)


def change_client_uuid(email, new_uuid, server=None):
    existing = get_client(email, server=server)
    if not existing:
        raise XuiError("client not found in x-ui")
    patch = {"id": str(new_uuid)}
    if existing.get("password"):
        patch["password"] = str(new_uuid)
    return update_client(email, patch, server=server)
