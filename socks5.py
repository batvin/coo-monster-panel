"""Minimal SOCKS5 support for Telegram API calls (Python standard library only).

Only the small subset needed by this project is implemented: the CONNECT command
with optional username/password authentication, then TLS on top of the proxied
socket. No third-party package is required.
"""

import http.client
import socket
import ssl
import urllib.parse


class Socks5Error(Exception):
    pass


def parse_proxy(value):
    """Return a proxy dict, or None when the value is empty."""
    raw = (value or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "socks5://" + raw
    parts = urllib.parse.urlsplit(raw)
    scheme = (parts.scheme or "").lower()
    if scheme not in ("socks5", "socks5h"):
        raise Socks5Error("SOCKS5 آدرس باید با socks5:// شروع شود")
    host = parts.hostname
    if not host:
        raise Socks5Error("هاست SOCKS5 مشخص نشده است")
    try:
        port = parts.port or 1080
    except ValueError as exc:
        raise Socks5Error("پورت SOCKS5 نامعتبر است") from exc
    return {
        "host": host,
        "port": port,
        "username": urllib.parse.unquote(parts.username or ""),
        "password": urllib.parse.unquote(parts.password or ""),
    }


def mask_proxy(value):
    """Show a proxy without revealing its password."""
    try:
        proxy = parse_proxy(value)
    except Socks5Error:
        return "نامعتبر"
    if not proxy:
        return "غیرفعال"
    auth = ""
    if proxy["username"]:
        auth = proxy["username"] + ":***@"
    return "socks5://{}{}:{}".format(auth, proxy["host"], proxy["port"])


def _recv_exact(sock, size):
    chunks = []
    remaining = size
    while remaining:
        part = sock.recv(remaining)
        if not part:
            raise Socks5Error("اتصال SOCKS5 قطع شد")
        chunks.append(part)
        remaining -= len(part)
    return b"".join(chunks)


def _address_bytes(host):
    try:
        return b"\x01" + socket.inet_aton(host)
    except OSError:
        pass
    try:
        return b"\x04" + socket.inet_pton(socket.AF_INET6, host)
    except OSError:
        pass
    encoded = host.encode("idna")
    if len(encoded) > 255:
        raise Socks5Error("هاست SOCKS5 خیلی طولانی است")
    return b"\x03" + bytes([len(encoded)]) + encoded


def connect(address, proxy, timeout=15):
    """Open a TCP connection to ``address`` through a SOCKS5 proxy."""
    if not proxy:
        return socket.create_connection(address, timeout=timeout)
    host, port = address
    sock = socket.create_connection((proxy["host"], proxy["port"]), timeout=timeout)
    try:
        username = proxy.get("username") or ""
        password = proxy.get("password") or ""
        methods = b"\x02\x00" if username else b"\x00"
        sock.sendall(b"\x05" + bytes([len(methods)]) + methods)
        version, method = _recv_exact(sock, 2)
        if version != 5:
            raise Socks5Error("سرور SOCKS5 نسخه ۵ را پشتیبانی نمیکند")
        if method == 2:
            if not username:
                raise Socks5Error("سرور SOCKS5 احراز هویت خواست ولی نام کاربری تنظیم نشده است")
            auth = (
                b"\x01"
                + bytes([len(username.encode())]) + username.encode()
                + bytes([len(password.encode())]) + password.encode()
            )
            sock.sendall(auth)
            if _recv_exact(sock, 2) != b"\x01\x00":
                raise Socks5Error("احراز هویت SOCKS5 ناموفق بود")
        elif method != 0:
            raise Socks5Error("روش احراز هویت SOCKS5 پشتیبانی نمیشود")

        request = b"\x05\x01\x00" + _address_bytes(host) + int(port).to_bytes(2, "big")
        sock.sendall(request)
        reply = _recv_exact(sock, 4)
        if reply[0] != 5 or reply[1] != 0:
            raise Socks5Error("اتصال SOCKS5 برقرار نشد (کد {})".format(reply[1]))
        atyp = reply[3]
        if atyp == 1:
            _recv_exact(sock, 4)
        elif atyp == 4:
            _recv_exact(sock, 16)
        elif atyp == 3:
            length = _recv_exact(sock, 1)[0]
            _recv_exact(sock, length)
        else:
            raise Socks5Error("پاسخ نامعتبر از سرور SOCKS5")
        _recv_exact(sock, 2)
        return sock
    except Exception:
        sock.close()
        raise


class HTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection established through an optional SOCKS5 proxy."""

    def __init__(self, host, port=None, timeout=15, proxy=None, context=None):
        self._socks_proxy = proxy
        super().__init__(host, port, timeout=timeout, context=context)
        # Keep None so connect() can create a context at connection time.
        self._context = context

    def connect(self):
        self.sock = connect((self.host, self.port), self._socks_proxy, self.timeout)
        if self._tunnel_host:
            self._tunnel()
        context = self._context or ssl.create_default_context()
        self.sock = context.wrap_socket(self.sock, server_hostname=self.host)


def request(url, data=None, headers=None, timeout=18, proxy=None):
    """Issue a simple HTTP request. Returns ``(status_code, body_bytes)``."""
    proxy = parse_proxy(proxy) if isinstance(proxy, str) else proxy
    parts = urllib.parse.urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    connection = HTTPSConnection(
        parts.hostname, parts.port or 443, timeout=timeout, proxy=proxy
    )
    try:
        final_headers = dict(headers or {})
        if data is not None and "Content-Type" not in final_headers:
            final_headers["Content-Type"] = "application/json"
        connection.request(
            "POST" if data is not None else "GET",
            path,
            body=data,
            headers=final_headers,
        )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()
