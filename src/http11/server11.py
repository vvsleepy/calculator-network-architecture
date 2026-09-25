#!/usr/bin/env python3
"""
server11 -- an HTTP/1.1 calculator that stays on the line.

Usage:
    python3 server11.py [port]        (default port 8080)

Supports:
    GET /add?a=<int>&b=<int>   -> 200, body = a+b
    GET /sub?a=<int>&b=<int>   -> 200, body = a-b
    GET /mul?a=<int>&b=<int>   -> 200, body = a*b
    GET /div?a=<int>&b=<int>   -> 200, body = a/b (integer division), 400 if b == 0

Error behaviour (see SPEC.md / README.md for the full table):
    unknown operation                  -> 404
    method other than GET              -> 405
    missing Host header (HTTP/1.1)     -> 400
    missing/non-integer a or b         -> 400
    malformed request line             -> 400

The socket is never closed after a single request: many requests may be
pipelined over the same TCP connection, one after another, and the
connection stays open until the client closes it, sends
"Connection: close", or an idle timeout elapses.
"""
import socket
import sys
import threading

HOST = "0.0.0.0"
DEFAULT_PORT = 8080
IDLE_TIMEOUT = 30.0          # seconds a connection may sit with no data
MAX_HEADER_BYTES = 8192      # guard against unbounded header growth
MAX_BODY_BYTES = 1 << 20     # 1 MiB cap on request bodies we will buffer

OPS = {
    "add": lambda a, b: a + b,
    "sub": lambda a, b: a - b,
    "mul": lambda a, b: a * b,
}


def parse_query(qs: str) -> dict:
    params = {}
    if not qs:
        return params
    for part in qs.split("&"):
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
        else:
            k, v = part, ""
        params[k] = v
    return params


def make_response(status: int, reason: str, body: str, keep_alive: bool) -> bytes:
    body_bytes = body.encode("utf-8")
    headers = [
        f"HTTP/1.1 {status} {reason}",
        f"Content-Length: {len(body_bytes)}",
        "Content-Type: text/plain; charset=utf-8",
        "Connection: keep-alive" if keep_alive else "Connection: close",
        "",
        "",
    ]
    return "\r\n".join(headers).encode("utf-8") + body_bytes


class BadRequest(Exception):
    pass


def parse_request(head: bytes):
    """Parse the request line + headers (not including the trailing
    blank line). Raises BadRequest on anything malformed."""
    lines = head.split(b"\r\n")
    if not lines or lines[0] == b"":
        raise BadRequest("empty request line")
    request_line = lines[0].decode("iso-8859-1", errors="replace")
    parts = request_line.split(" ")
    if len(parts) != 3:
        raise BadRequest("malformed request line")
    method, target, version = parts
    if not version.startswith("HTTP/1."):
        raise BadRequest("unsupported HTTP version")

    headers = {}
    for line in lines[1:]:
        if not line:
            continue
        if b":" not in line:
            raise BadRequest("malformed header line")
        name, value = line.split(b":", 1)
        headers[name.strip().lower().decode("iso-8859-1")] = value.strip().decode("iso-8859-1")

    return method, target, version, headers


def handle_calculator(method: str, target: str, headers: dict):
    """Returns (status, reason, body, keep_alive)."""
    connection_header = headers.get("connection", "").lower()
    keep_alive = connection_header != "close"

    if "host" not in headers:
        return 400, "Bad Request", "missing Host header", keep_alive

    if method != "GET":
        return 405, "Method Not Allowed", f"unsupported method {method}", keep_alive

    if "?" in target:
        path, qs = target.split("?", 1)
    else:
        path, qs = target, ""

    op = path.lstrip("/")
    if op not in OPS and op != "div":
        return 404, "Not Found", f"unknown operation {op!r}", keep_alive

    params = parse_query(qs)
    if "a" not in params or "b" not in params:
        return 400, "Bad Request", "missing operand a or b", keep_alive

    try:
        a = int(params["a"])
        b = int(params["b"])
    except ValueError:
        return 400, "Bad Request", "operands must be integers", keep_alive

    if op == "div":
        if b == 0:
            return 400, "Bad Request", "division by zero", keep_alive
        result = a // b
    else:
        result = OPS[op](a, b)

    return 200, "OK", str(result), keep_alive


def recv_until_headers_done(sock: socket.socket, buf: bytearray):
    """Ensure buf contains a full '\\r\\n\\r\\n'-terminated header block,
    reading more from the socket as needed. Returns the header block end
    index, or None if the peer closed before sending a full header."""
    while True:
        idx = buf.find(b"\r\n\r\n")
        if idx != -1:
            return idx + 4
        if len(buf) > MAX_HEADER_BYTES:
            raise BadRequest("headers too large")
        chunk = sock.recv(4096)
        if not chunk:
            return None
        buf += chunk


def recv_body(sock: socket.socket, buf: bytearray, body_start: int, content_length: int):
    """Ensure buf has body_start + content_length bytes available."""
    needed = body_start + content_length
    while len(buf) < needed:
        chunk = sock.recv(4096)
        if not chunk:
            raise BadRequest("connection closed mid-body")
        buf += chunk
    return bytes(buf[body_start:needed])


def handle_connection(conn: socket.socket):
    conn.settimeout(IDLE_TIMEOUT)
    buf = bytearray()
    try:
        while True:
            try:
                header_end = recv_until_headers_done(conn, buf)
            except socket.timeout:
                break
            except BadRequest:
                conn.sendall(make_response(400, "Bad Request", "headers too large", False))
                break
            if header_end is None:
                break  # peer closed cleanly between requests

            head = bytes(buf[:header_end - 4])
            try:
                method, target, version, headers = parse_request(head)
            except BadRequest as e:
                conn.sendall(make_response(400, "Bad Request", str(e), False))
                break

            content_length = 0
            if "content-length" in headers:
                try:
                    content_length = int(headers["content-length"])
                    if content_length < 0 or content_length > MAX_BODY_BYTES:
                        raise ValueError
                except ValueError:
                    conn.sendall(make_response(400, "Bad Request", "bad Content-Length", False))
                    break

            try:
                recv_body(conn, buf, header_end, content_length)
            except (BadRequest, socket.timeout):
                break

            consumed = header_end + content_length
            del buf[:consumed]  # exactly the bytes of this request, no more

            status, reason, body, keep_alive = handle_calculator(method, target, headers)
            conn.sendall(make_response(status, reason, body, keep_alive))

            if not keep_alive:
                break
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        conn.close()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, port))
    srv.listen(128)
    print(f"server11 listening on {HOST}:{port}", flush=True)
    try:
        while True:
            conn, _addr = srv.accept()
            t = threading.Thread(target=handle_connection, args=(conn,), daemon=True)
            t.start()
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()


if __name__ == "__main__":
    main()
