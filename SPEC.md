# BHP/1 — Binary HTTP Protocol, Version 1

*This document is written so that someone who has never seen the
implementation can, from these pages alone, build an independent
client or server that interoperates correctly with this one.*

⋆˚꩜｡ ---------------------------------------------------------------- .✦ ݁˖

## 1. Overview

BHP/1 is a compact binary request/response protocol for retrieving
files from a server over a single, long-lived TCP connection. It
tackles the same underlying problem as HTTP/1.1 — "request a resource,
receive a status, headers, and a body of bytes" — but instead of
relying on textual delimiters to mark where one message ends and the
next begins, every message is framed with an explicit, unambiguous
length. A receiver is therefore never left guessing where a message
stops.

⋆˚꩜｡ ---------------------------------------------------------------- .✦ ݁˖

## 2. Transport

BHP/1 is carried over TCP. A client establishes **exactly one** TCP
connection to a given server and reuses that same connection for every
request in the session; it must never open a second connection to the
same server while the first remains active. Once open, a connection
carries a stream of frames travelling in both directions and remains
open until either side closes the socket. There is no separate
handshake or teardown step per request.

⋆˚꩜｡ ---------------------------------------------------------------- .✦ ݁˖

## 3. Frame Format

Every message sent in either direction is a **frame**, consisting of a
fixed 8-byte header followed by a payload whose exact length the
header specifies. All multi-byte integers are encoded big-endian.

```
Offset | Size | Field
-------|------|--------------------------------------------
0      | 1    | Version          (must be 1)
1      | 1    | Type             (0x01 REQUEST, 0x02 RESPONSE)
2      | 1    | Flags            (reserved, must be 0)
3      | 1    | Reserved         (must be 0)
4      | 4    | Payload Length N (uint32, big-endian)
8      | N    | Payload
```

The fixed header is intentionally **type-agnostic**: determining how
many bytes a frame occupies depends solely on the Length field, never
on being able to parse what the payload actually contains. A receiver
always reads exactly `8 + N` bytes to consume one frame, regardless of
whether it recognises the `Type` value or understands the `Payload`.
This single property is what keeps the rest of the protocol robust — a
malformed payload can never desynchronise the byte stream, and a frame
type introduced by some future version 2 can be safely skipped by a
version 1 receiver without corrupting anything that follows.

The `Version`, `Flags`, and `Reserved` fields are fixed at 1, 0, and 0
respectively in this version. They exist solely to give a future
revision somewhere to place a capability bit, or a second version
number, without altering the size of the header itself.

⋆˚꩜｡ ---------------------------------------------------------------- .✦ ݁˖

## 4. Frame Types

- **0x01 REQUEST** — sent client → server; described in §5.
- **0x02 RESPONSE** — sent server → client; described in §6.
- **Any other value** — reserved for use by future protocol versions.
  A compliant receiver, regardless of its own version, MUST NOT reject
  or raise an error on encountering an unrecognised `Type`; see §9.

⋆˚꩜｡ ---------------------------------------------------------------- .✦ ݁˖

## 5. Request Format (payload of a REQUEST frame)

```
Offset | Size | Field
-------|------|--------------------------------------------
0      | 1    | Method id
1      | 2    | Path length P (uint16)
3      | P    | Path (UTF-8, e.g. "/index.html")
3+P    | 1    | Header count H
...    | ...  | H header entries (§7)
...    | rest | Body (0 bytes for GET/HEAD)
```

Method ids are assigned as follows: `1=GET, 2=HEAD, 3=POST, 4=PUT,
5=DELETE`. If a server does not support a given method for a given
resource, it responds with `405` (§8); if the method id itself is not
recognised at all, the request is treated as malformed and answered
with `400`.

⋆˚꩜｡ ---------------------------------------------------------------- .✦ ݁˖

## 6. Response Format (payload of a RESPONSE frame)

```
Offset | Size | Field
-------|------|--------------------------------------------
0      | 2    | Status code (uint16, e.g. 200, 404)
2      | 1    | Header count H
3      | ...  | H header entries (§7)
...    | rest | Body
```

⋆˚꩜｡ ---------------------------------------------------------------- .✦ ݁˖

## 7. Header Encoding

Ten header names that occur on almost every message are each assigned
a single-byte static id, so the connection doesn't pay for the full
name bytes on every message that uses them — the same underlying idea
HPACK relies on, scaled down to the ten names this protocol actually
needs:

```
id | name             id | name
---|------------------ --|------------------
1  | Host             6  | Date
2  | Content-Type      7  | Content-Encoding
3  | Content-Length    8  | Accept
4  | Connection        9  | User-Agent
5  | Server           10  | Location
```

Each header entry is structured as:

```
Size | Field
-----|-------------------------------------------------
1    | Name id (1-10 = static table; 0 = literal name follows)
     |   if 0: [1 byte name length][name bytes, UTF-8]
2    | Value length (uint16)
...  | Value bytes (UTF-8)
```

Any header whose name falls outside the static table is sent with id
`0`, followed by its explicit, length-prefixed name — so the protocol
is not restricted to only ten headers, it simply makes the ten most
common ones cheaper to send.

⋆˚꩜｡ ---------------------------------------------------------------- .✦ ݁˖

## 8. Status and Error Semantics

- **200** — the request was understood, the resource was located, and
  its body follows.
- **400 Bad Request** — the frame envelope itself was intact (the
  Length field was honoured), but its payload was structurally
  invalid: for example, a length field pointing past the end of the
  payload, an unrecognised static header id, a path attempting to
  escape the server root, an unrecognised method id, or a RESPONSE
  frame arriving where a REQUEST was expected.
- **404 Not Found** — a well-formed GET/HEAD request for a path that
  does not correspond to any file under the server's root.
- **405 Method Not Allowed** — a well-formed request using a method
  the server does not implement (this implementation supports only
  GET and HEAD).

In this implementation, a response body is left empty for any
non-200 status, though nothing in the frame format itself requires
that behaviour.

⋆˚꩜｡ ---------------------------------------------------------------- .✦ ݁˖

## 9. Unknown Frame Types

If a receiver reads a frame header whose `Type` it does not recognise,
it MUST still read exactly `Length` bytes of payload, discard them,
and continue on to read the next frame from the same connection —
exactly as though the unrecognised frame had never been sent. It MUST
NOT close the connection, MUST NOT send any error response for that
frame, and MUST NOT attempt to interpret its payload in any way. This
is precisely what allows a future version 2 of this protocol to
introduce new frame types — a PING, say, or a multi-frame streamed
body — that a version 1 peer can safely disregard without any risk to
the connection.

⋆˚꩜｡ ---------------------------------------------------------------- .✦ ݁˖

## 10. Connection Lifecycle

A connection begins with a TCP handshake and remains open across
however many frames pass in either direction. The server processes
frames from a given connection strictly in the order they arrive, and
sends exactly one RESPONSE frame for each REQUEST frame it accepts (it
sends nothing at all in reply to a skipped, unrecognised frame type,
per §9). The connection ends only when one side closes its socket —
or, in the case of `bserve`, after an idle timeout with no frames
received. Nothing in the protocol itself requires a client to wait for
a response before sending its next request, but this project's client
(`bcurl`) is deliberately designed to send one request and wait for
its response before exiting.

⋆˚꩜｡ ---------------------------------------------------------------- .✦ ݁˖

## 11. Worked Example

A complete, real, byte-by-byte annotated capture of one REQUEST frame
(`GET /index.html`) and its corresponding RESPONSE frame — generated
by running `bserve` and `bcurl` against each other — can be found in
`examples/hexdump.txt`.