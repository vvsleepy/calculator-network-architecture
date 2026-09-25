⋆˚꩜｡ HTTP/1.1 & Binary HTTP — Network Architecture Assignment ⋆˚꩜｡

**Ankita Tripathi**
Roll Number: 24bcs10062

---

This repository holds my submission for both parts of the assignment
(`Network Architecture Assignment_ Build a Calculator that stays on the line.pdf`):

1. **Part A** — `server11`, an HTTP/1.1 calculator server built to hold a single
   TCP connection open across multiple requests instead of reconnecting each time.
2. **Part B** — `bserve` / `bcurl`, a custom binary protocol I designed
   (**BHP/1**), along with a matching server and client that speak it to serve
   files rather than do arithmetic.

Everything here is written in plain Python 3 using only the standard library —
no web framework, no external dependencies, just raw sockets.

⋆˚꩜｡ ---------------------------------------------------------------- .✦ ݁˖

## Repository Layout

```
src/http11/server11.py   Part A: HTTP/1.1 calculator server
src/protocol/frame.py    Part B: BHP/1 frame codec (shared by server & client)
src/server/bserve.py     Part B: file server
src/client/bcurl.py      Part B: client
bserve, bcurl            executable wrapper scripts for the two programs above
www/                     sample files served by bserve
tests/                   unittest suite covering protocol, server11, bserve+bcurl
examples/hexdump.txt     annotated hexdump of one real request/response pair
SPEC.md                  the BHP/1 protocol specification I wrote
```

⋆˚꩜｡ ---------------------------------------------------------------- .✦ ݁˖

## Part A — Running the Calculator Server

Start the server:

```bash
python3 src/http11/server11.py 8080
```

In a separate terminal, open a single connection and send several requests
down it — this is really the whole point of the exercise: one socket carrying
many requests, one after another.

```python
import socket
s = socket.create_connection(("localhost", 8080))
s.sendall(b"GET /add?a=2&b=3 HTTP/1.1\r\nHost: localhost\r\n\r\n")
s.sendall(b"GET /div?a=1&b=0 HTTP/1.1\r\nHost: localhost\r\n\r\n")
print(s.recv(4096))
print(s.recv(4096))
```

Alternatively, this works just as well with `curl --http1.1` or a browser.
`server11` keeps the connection alive by default, and only closes it when it
receives an explicit `Connection: close` header or after 30 seconds of
inactivity.

### Expected Behaviour

| Request                          | Status | Body                     |
|-----------------------------------|--------|--------------------------|
| `GET /add?a=2&b=3`                | 200    | `5`                      |
| `GET /sub?a=10&b=4`               | 200    | `6`                      |
| `GET /mul?a=6&b=7`                | 200    | `42`                     |
| `GET /div?a=9&b=3`                | 200    | `3`                      |
| `GET /div?a=1&b=0`                | 400    | division by zero         |
| `GET /add?a=x&b=3`                | 400    | non-integer operand      |
| `GET /pow?a=2&b=8`                | 404    | unknown operation        |
| `POST /add`                       | 405    | unsupported method       |
| `GET /add` with no `Host` header  | 400    | missing Host             |

⋆˚꩜｡ ---------------------------------------------------------------- .✦ ݁˖

## Part B — Running the File Server and Client

```bash
./bserve ./www 9000
./bcurl -v localhost:9000/index.html
```

`bcurl` prints the response body to stdout, and when run with `-v` also
dumps both frames to stderr in hex with a field-by-field breakdown. It opens
exactly one TCP connection per run, and exits with a non-zero status on any
4xx or 5xx response:

```bash
./bcurl localhost:9000/does-not-exist.html; echo "exit=$?"   # exit=1
./bcurl localhost:9000/index.html;           echo "exit=$?"  # exit=0
```

The full wire format is documented in [SPEC.md](SPEC.md), and a real
annotated capture of one request/response exchange is available in
[examples/hexdump.txt](examples/hexdump.txt).

⋆˚꩜｡ ---------------------------------------------------------------- .✦ ݁˖

## Running the Tests

```bash
python3 -m unittest discover -s tests -v
```

There are 29 tests in total, with no external dependencies required (pytest
is not needed). Together they cover, for both parts of the assignment: basic
request handling, the one-connection/many-requests behaviour, frames that
arrive split across multiple TCP reads, multiple frames arriving within a
single read, malformed input (expected to return 400), missing resources
(expected to return 404), unsupported methods (expected to return 405),
unknown BHP/1 frame types being skipped cleanly without breaking the
connection, and the client correctly reporting exit codes on error responses.

⋆˚꩜｡ ---------------------------------------------------------------- .✦ ݁˖

## A Note on Scope

The assignment PDF presents the HTTP/1.1 calculator (page 1) and the binary
protocol course project (page 2) as two related exercises that share a single
underlying lesson — framing over a persistent connection — rather than one
combined specification. I have therefore implemented them here as two
independent programs that share nothing beyond that lesson, and occasionally
a small helper such as the hexdump printer where it made sense to reuse code.
`server11` and `bserve`/`bcurl` do not depend on one another in any way.

---

*This project, including the protocol design, implementation, and tests, was
written and submitted by me, Ankita Tripathi (Roll Number 24bcs10062), for
the Network Architecture course assignment.*