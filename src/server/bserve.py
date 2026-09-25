#!/usr/bin/env python3
"""
bserve -- binary HTTP file server (BHP/1).

Usage:
    ./bserve <root-dir> <port>

Accepts TCP connections and, on each one, reads binary request frames
in a loop (the connection is never closed after a single request). For
each REQUEST frame it maps the path to a file under <root-dir> and
replies with a RESPONSE frame: 200 + bytes on success, 404 if the file
does not exist, 405 if the method isn't GET/HEAD, 400 if the frame
itself is malformed. Frame types this server does not recognise are
skipped without disturbing the connection.
"""
import mimetypes
import os
import socket
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "protocol"))
import frame  # noqa: E402

HOST = "0.0.0.0"
IDLE_TIMEOUT = 30.0

SERVER_HEADER = "bserve/1.0"


def safe_join(root: str, url_path: str):
    """Map a request path to a file under root. Returns the absolute
    path, or None if the path escapes root (path traversal)."""
    if not url_path.startswith("/"):
        return None
    if url_path == "/":
        url_path = "/index.html"
    rel = url_path.lstrip("/")
    candidate = os.path.normpath(os.path.join(root, rel))
    root_abs = os.path.abspath(root)
    candidate_abs = os.path.abspath(candidate)
    if candidate_abs != root_abs and not candidate_abs.startswith(root_abs + os.sep):
        return None
    return candidate_abs


def build_response_frame(status: int, extra_headers: dict, body: bytes = b"") -> bytes:
    headers = {"Server": SERVER_HEADER, "Content-Length": str(len(body))}
    headers.update(extra_headers)
    return frame.encode_response(status, headers, body)


def handle_request(req: frame.ParsedRequest, root: str) -> bytes:
    if req.method not in ("GET", "HEAD"):
        return build_response_frame(405, {"Allow": "GET, HEAD"})

    path = safe_join(root, req.path)
    if path is None:
        return build_response_frame(400, {})
    if not os.path.isfile(path):
        return build_response_frame(404, {})

    with open(path, "rb") as f:
        body = f.read()

    ctype, _ = mimetypes.guess_type(path)
    headers = {"Content-Type": ctype or "application/octet-stream"}
    if req.method == "HEAD":
        return build_response_frame(200, headers, b"")
    return build_response_frame(200, headers, body)


def handle_connection(conn: socket.socket, root: str):
    conn.settimeout(IDLE_TIMEOUT)
    try:
        while True:
            try:
                f = frame.read_frame(conn)
            except frame.ConnectionClosed:
                break
            except socket.timeout:
                break
            if f is None:
                break  # peer closed cleanly at a frame boundary

            if f.version != frame.VERSION:
                conn.sendall(build_response_frame(400, {}))
                continue

            if f.type not in frame.KNOWN_TYPES:
                # Forward-compatibility: skip frame types we don't
                # understand. The payload was already fully consumed
                # by read_frame() via the Length field, so the stream
                # stays in sync -- no response is sent for it.
                continue

            if f.type == frame.TYPE_RESPONSE:
                # A server should never receive a RESPONSE frame.
                conn.sendall(build_response_frame(400, {}))
                continue

            try:
                req = frame.parse_request_payload(f.payload)
            except frame.FrameError:
                conn.sendall(build_response_frame(400, {}))
                continue

            try:
                conn.sendall(handle_request(req, root))
            except frame.FrameError:
                conn.sendall(build_response_frame(400, {}))
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        conn.close()


def main():
    if len(sys.argv) != 3:
        print("usage: bserve <root-dir> <port>", file=sys.stderr)
        sys.exit(2)
    root = sys.argv[1]
    port = int(sys.argv[2])

    if not os.path.isdir(root):
        print(f"bserve: {root!r} is not a directory", file=sys.stderr)
        sys.exit(2)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, port))
    srv.listen(128)
    print(f"bserve listening on {HOST}:{port}, root={os.path.abspath(root)}", flush=True)
    try:
        while True:
            conn, _addr = srv.accept()
            t = threading.Thread(target=handle_connection, args=(conn, root), daemon=True)
            t.start()
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()


if __name__ == "__main__":
    main()
