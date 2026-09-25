#!/usr/bin/env python3
# bcurl -- ./bcurl [-v] <host>:<port>/<path>. one connection, one request,
# one response. -v dumps the frames to stderr if you don't believe it.
import os
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "protocol"))
import frame  # noqa: E402

USER_AGENT = "bcurl/1.0"


def parse_target(target: str):
    if "/" in target:
        hostport, path = target.split("/", 1)
        path = "/" + path
    else:
        hostport, path = target, "/"
    if ":" in hostport:
        host, port_s = hostport.rsplit(":", 1)
        port = int(port_s)
    else:
        host, port = hostport, 9000
    return host, port, path


def hexdump(data: bytes, label: str):
    print(f"-- {label} ({len(data)} bytes) --", file=sys.stderr)
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"{i:04x}  {hex_part:<47}  {ascii_part}", file=sys.stderr)


def describe_request_frame(raw: bytes, method: str, path: str, headers: dict):
    print("-- request frame fields --", file=sys.stderr)
    print(f"  [0]     version        = {raw[0]}", file=sys.stderr)
    print(f"  [1]     type           = 0x{raw[1]:02x} (REQUEST)", file=sys.stderr)
    print(f"  [2]     flags          = {raw[2]}", file=sys.stderr)
    print(f"  [3]     reserved       = {raw[3]}", file=sys.stderr)
    print(f"  [4:8]   payload length = {int.from_bytes(raw[4:8], 'big')}", file=sys.stderr)
    print(f"  method={method} path={path!r} headers={headers}", file=sys.stderr)


def describe_response_frame(raw: bytes, parsed: frame.ParsedResponse):
    print("-- response frame fields --", file=sys.stderr)
    print(f"  [0]     version        = {raw[0]}", file=sys.stderr)
    print(f"  [1]     type           = 0x{raw[1]:02x} (RESPONSE)", file=sys.stderr)
    print(f"  [2]     flags          = {raw[2]}", file=sys.stderr)
    print(f"  [3]     reserved       = {raw[3]}", file=sys.stderr)
    print(f"  [4:8]   payload length = {int.from_bytes(raw[4:8], 'big')}", file=sys.stderr)
    print(f"  status={parsed.status} headers={parsed.headers} body_len={len(parsed.body)}", file=sys.stderr)


def main():
    args = sys.argv[1:]
    verbose = False
    if "-v" in args:
        verbose = True
        args.remove("-v")
    if len(args) != 1:
        print("usage: bcurl [-v] <host>:<port>/<path>", file=sys.stderr)
        sys.exit(2)

    host, port, path = parse_target(args[0])

    headers = {
        "Host": f"{host}:{port}",
        "User-Agent": USER_AGENT,
        "Connection": "keep-alive",
    }
    req_frame = frame.encode_request("GET", path, headers)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # this is the only connection we get, so don't blow it
    try:
        sock.connect((host, port))
        sock.sendall(req_frame)

        if verbose:
            hexdump(req_frame, "REQUEST")
            describe_request_frame(req_frame, "GET", path, headers)

        resp = frame.read_frame(sock)
        if resp is None:
            print("bcurl: server closed connection without a response", file=sys.stderr)
            sys.exit(1)

        raw_resp = frame.HEADER_STRUCT.pack(resp.version, resp.type, resp.flags, 0, resp.length) + resp.payload
        # rebuilt the header just for the hexdump, read_frame() already ate the real bytes

        if resp.type not in frame.KNOWN_TYPES:
            print(f"bcurl: server sent an unrecognised frame type 0x{resp.type:02x}", file=sys.stderr)
            sys.exit(1)
        if resp.type != frame.TYPE_RESPONSE:
            print("bcurl: expected a RESPONSE frame", file=sys.stderr)
            sys.exit(1)

        try:
            parsed = frame.parse_response_payload(resp.payload)
        except frame.FrameError as e:
            print(f"bcurl: malformed response frame: {e}", file=sys.stderr)
            sys.exit(1)

        if verbose:
            hexdump(raw_resp, "RESPONSE")
            describe_response_frame(raw_resp, parsed)

        sys.stdout.buffer.write(parsed.body)
        sys.stdout.buffer.flush()

        if parsed.status >= 400:
            sys.exit(1)
        sys.exit(0)
    except (ConnectionRefusedError, frame.ConnectionClosed) as e:
        print(f"bcurl: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        sock.close()


if __name__ == "__main__":
    main()
