#!/usr/bin/env python3
"""
Binary HTTP protocol (BHP/1) -- shared frame codec used by both bserve
(the server) and bcurl (the client).

See SPEC.md for the authoritative, byte-level description. This module
is the reference implementation of that spec.

Fixed frame header (8 bytes, all multi-byte fields big-endian):

    Offset | Size | Field
    -------|------|----------------------------------------------
    0      | 1    | Version            (must be 1)
    1      | 1    | Type                (0x01 REQUEST, 0x02 RESPONSE)
    2      | 1    | Flags               (reserved, must be 0)
    3      | 1    | Reserved            (must be 0)
    4      | 4    | Payload Length (N)  (uint32, bytes that follow)
    8      | N    | Payload

A receiver ALWAYS reads exactly `Payload Length` bytes for the payload,
regardless of whether it recognises `Type`. That is what lets an
unknown frame type be skipped cleanly without desynchronising the
stream: the fixed header is type-agnostic, so "how many bytes do I
consume" never depends on understanding the payload.
"""
import struct

VERSION = 1

TYPE_REQUEST = 0x01
TYPE_RESPONSE = 0x02
KNOWN_TYPES = (TYPE_REQUEST, TYPE_RESPONSE)

HEADER_STRUCT = struct.Struct(">BBBBI")  # version, type, flags, reserved, length
HEADER_SIZE = HEADER_STRUCT.size  # 8

METHOD_IDS = {"GET": 1, "HEAD": 2, "POST": 3, "PUT": 4, "DELETE": 5}
ID_TO_METHOD = {v: k for k, v in METHOD_IDS.items()}

# Static header table (HPACK-style): the ten names we expect to send
# most often get a 1-byte id instead of paying for their name bytes
# every frame. Anything else is sent as id 0 with an explicit,
# length-prefixed name.
STATIC_HEADER_TABLE = [
    None,               # id 0 is reserved for "literal name follows"
    "Host",             # 1
    "Content-Type",     # 2
    "Content-Length",   # 3
    "Connection",       # 4
    "Server",           # 5
    "Date",             # 6
    "Content-Encoding", # 7
    "Accept",           # 8
    "User-Agent",       # 9
    "Location",         # 10
]
NAME_TO_ID = {name.lower(): i for i, name in enumerate(STATIC_HEADER_TABLE) if name}


class FrameError(Exception):
    """Raised for a structurally invalid frame payload (maps to a 400)."""


class ConnectionClosed(Exception):
    """Raised when the peer closes the socket before delivering the
    requested number of bytes. `bytes_received` is 0 when the peer
    closed cleanly at a frame boundary (no partial frame in flight)."""

    def __init__(self, message: str, bytes_received: int = 0):
        super().__init__(message)
        self.bytes_received = bytes_received


# --------------------------------------------------------------------
# Reliable socket I/O helpers: TCP is a byte stream, never assume a
# single recv()/send() lines up with a single frame.
# --------------------------------------------------------------------

def recv_exact(sock, n: int) -> bytes:
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            received = n - remaining
            raise ConnectionClosed(
                f"peer closed with {remaining} of {n} bytes still expected",
                bytes_received=received,
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


# --------------------------------------------------------------------
# Header block encode/decode
# --------------------------------------------------------------------

def encode_headers(headers: dict) -> bytes:
    if len(headers) > 255:
        raise FrameError("too many headers")
    out = bytearray()
    out.append(len(headers))
    for name, value in headers.items():
        value_bytes = str(value).encode("utf-8")
        if len(value_bytes) > 0xFFFF:
            raise FrameError("header value too long")
        hid = NAME_TO_ID.get(name.lower(), 0)
        if hid:
            out.append(hid)
        else:
            name_bytes = name.encode("utf-8")
            if len(name_bytes) > 0xFF:
                raise FrameError("header name too long")
            out.append(0)
            out.append(len(name_bytes))
            out += name_bytes
        out += struct.pack(">H", len(value_bytes))
        out += value_bytes
    return bytes(out)


def decode_headers(buf: bytes, offset: int):
    """Returns (headers_dict, new_offset)."""
    if offset >= len(buf):
        raise FrameError("truncated header block (missing count)")
    count = buf[offset]
    offset += 1
    headers = {}
    for _ in range(count):
        if offset >= len(buf):
            raise FrameError("truncated header entry")
        hid = buf[offset]
        offset += 1
        if hid == 0:
            if offset >= len(buf):
                raise FrameError("truncated literal header name length")
            nlen = buf[offset]
            offset += 1
            if offset + nlen > len(buf):
                raise FrameError("truncated literal header name")
            name = buf[offset:offset + nlen].decode("utf-8", errors="replace")
            offset += nlen
        else:
            if hid >= len(STATIC_HEADER_TABLE):
                raise FrameError(f"unknown static header id {hid}")
            name = STATIC_HEADER_TABLE[hid]
        if offset + 2 > len(buf):
            raise FrameError("truncated header value length")
        vlen = struct.unpack_from(">H", buf, offset)[0]
        offset += 2
        if offset + vlen > len(buf):
            raise FrameError("truncated header value")
        value = buf[offset:offset + vlen].decode("utf-8", errors="replace")
        offset += vlen
        headers[name] = value
    return headers, offset


# --------------------------------------------------------------------
# Whole-frame encode
# --------------------------------------------------------------------

def build_frame(ftype: int, payload: bytes) -> bytes:
    if len(payload) > 0xFFFFFFFF:
        raise FrameError("payload too large")
    header = HEADER_STRUCT.pack(VERSION, ftype, 0, 0, len(payload))
    return header + payload


def encode_request(method: str, path: str, headers: dict, body: bytes = b"") -> bytes:
    if method not in METHOD_IDS:
        raise FrameError(f"unsupported method {method!r}")
    path_bytes = path.encode("utf-8")
    if len(path_bytes) > 0xFFFF:
        raise FrameError("path too long")
    payload = bytearray()
    payload.append(METHOD_IDS[method])
    payload += struct.pack(">H", len(path_bytes))
    payload += path_bytes
    payload += encode_headers(headers)
    payload += body
    return build_frame(TYPE_REQUEST, bytes(payload))


def encode_response(status: int, headers: dict, body: bytes = b"") -> bytes:
    if not (0 <= status <= 0xFFFF):
        raise FrameError("status out of range")
    payload = bytearray()
    payload += struct.pack(">H", status)
    payload += encode_headers(headers)
    payload += body
    return build_frame(TYPE_RESPONSE, bytes(payload))


# --------------------------------------------------------------------
# Whole-frame decode
# --------------------------------------------------------------------

class Frame:
    __slots__ = ("version", "type", "flags", "length", "payload")

    def __init__(self, version, ftype, flags, length, payload):
        self.version = version
        self.type = ftype
        self.flags = flags
        self.length = length
        self.payload = payload


def read_frame(sock):
    """Read exactly one frame from sock. Returns a Frame, or None if the
    peer closed cleanly right at a frame boundary (no bytes read yet).
    Raises ConnectionClosed if it closes mid-frame."""
    try:
        header = recv_exact(sock, HEADER_SIZE)
    except ConnectionClosed as e:
        if e.bytes_received > 0:
            raise
        return None
    version, ftype, flags, reserved, length = HEADER_STRUCT.unpack(header)
    payload = recv_exact(sock, length) if length else b""
    return Frame(version, ftype, flags, length, payload)


class ParsedRequest:
    __slots__ = ("method", "path", "headers", "body")

    def __init__(self, method, path, headers, body):
        self.method = method
        self.path = path
        self.headers = headers
        self.body = body


class ParsedResponse:
    __slots__ = ("status", "headers", "body")

    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body


def parse_request_payload(payload: bytes) -> ParsedRequest:
    if len(payload) < 3:
        raise FrameError("request payload too short")
    method_id = payload[0]
    if method_id not in ID_TO_METHOD:
        raise FrameError(f"unknown method id {method_id}")
    method = ID_TO_METHOD[method_id]
    path_len = struct.unpack_from(">H", payload, 1)[0]
    offset = 3
    if offset + path_len > len(payload):
        raise FrameError("truncated path")
    path = payload[offset:offset + path_len].decode("utf-8", errors="replace")
    offset += path_len
    headers, offset = decode_headers(payload, offset)
    body = payload[offset:]
    return ParsedRequest(method, path, headers, body)


def parse_response_payload(payload: bytes) -> ParsedResponse:
    if len(payload) < 2:
        raise FrameError("response payload too short")
    status = struct.unpack_from(">H", payload, 0)[0]
    offset = 2
    headers, offset = decode_headers(payload, offset)
    body = payload[offset:]
    return ParsedResponse(status, headers, body)
