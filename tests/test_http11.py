import os
import socket
import subprocess
import sys
import time
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
SERVER_PATH = os.path.join(ROOT, "src", "http11", "server11.py")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Server11TestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.proc = subprocess.Popen(
            [sys.executable, SERVER_PATH, str(cls.port)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        cls._wait_for_port(cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=5)

    @staticmethod
    def _wait_for_port(port, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=0.2)
                s.close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("server11 never started listening")

    def connect(self):
        return socket.create_connection(("127.0.0.1", self.port), timeout=5)

    def send_request(self, sock, method, target, headers=None, close_conn=False):
        hdrs = {"Host": "localhost"}
        if headers:
            hdrs.update(headers)
        if close_conn:
            hdrs["Connection"] = "close"
        lines = [f"{method} {target} HTTP/1.1"]
        for k, v in hdrs.items():
            lines.append(f"{k}: {v}")
        lines.append("")
        lines.append("")
        sock.sendall("\r\n".join(lines).encode())

    def read_one_response(self, buf: bytearray, sock):
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        header_end = buf.find(b"\r\n\r\n") + 4
        head = bytes(buf[:header_end])
        status = int(head.split(b" ")[1])
        content_length = 0
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                content_length = int(line.split(b":")[1].strip())
        needed = header_end + content_length
        while len(buf) < needed:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        body = bytes(buf[header_end:needed])
        del buf[:needed]
        return status, body

    # -- basic arithmetic --------------------------------------------

    def test_add_sub_mul_div(self):
        sock = self.connect()
        buf = bytearray()
        cases = [
            ("/add?a=2&b=3", 200, b"5"),
            ("/sub?a=10&b=4", 200, b"6"),
            ("/mul?a=6&b=7", 200, b"42"),
            ("/div?a=9&b=3", 200, b"3"),
        ]
        for target, exp_status, exp_body in cases:
            self.send_request(sock, "GET", target)
            status, body = self.read_one_response(buf, sock)
            self.assertEqual(status, exp_status)
            self.assertEqual(body, exp_body)
        sock.close()

    def test_div_by_zero_is_400(self):
        sock = self.connect()
        buf = bytearray()
        self.send_request(sock, "GET", "/div?a=1&b=0")
        status, _ = self.read_one_response(buf, sock)
        self.assertEqual(status, 400)
        sock.close()

    def test_malformed_operand_is_400(self):
        sock = self.connect()
        buf = bytearray()
        self.send_request(sock, "GET", "/add?a=x&b=3")
        status, _ = self.read_one_response(buf, sock)
        self.assertEqual(status, 400)
        sock.close()

    def test_unknown_operation_is_404(self):
        sock = self.connect()
        buf = bytearray()
        self.send_request(sock, "GET", "/pow?a=2&b=8")
        status, _ = self.read_one_response(buf, sock)
        self.assertEqual(status, 404)
        sock.close()

    def test_unsupported_method_is_405(self):
        sock = self.connect()
        buf = bytearray()
        self.send_request(sock, "POST", "/add?a=1&b=2")
        status, _ = self.read_one_response(buf, sock)
        self.assertEqual(status, 405)
        sock.close()

    def test_missing_host_is_400(self):
        sock = self.connect()
        sock.sendall(b"GET /add?a=1&b=2 HTTP/1.1\r\n\r\n")
        buf = bytearray()
        status, _ = self.read_one_response(buf, sock)
        self.assertEqual(status, 400)
        sock.close()

    # -- the actual point of the assignment ---------------------------

    def test_one_connection_many_requests(self):
        """One TCP handshake, six requests, six responses, socket stays open."""
        sock = self.connect()
        buf = bytearray()
        expected = [
            ("/add?a=2&b=3", 200),
            ("/sub?a=10&b=4", 200),
            ("/mul?a=6&b=7", 200),
            ("/div?a=1&b=0", 400),
            ("/pow?a=2&b=8", 404),
        ]
        for target, exp_status in expected:
            self.send_request(sock, "GET", target)
        for target, exp_status in expected:
            status, _ = self.read_one_response(buf, sock)
            self.assertEqual(status, exp_status)
        # a POST on the same still-open connection
        self.send_request(sock, "POST", "/add")
        status, _ = self.read_one_response(buf, sock)
        self.assertEqual(status, 405)
        # prove the socket is still usable
        self.send_request(sock, "GET", "/add?a=100&b=1")
        status, body = self.read_one_response(buf, sock)
        self.assertEqual(status, 200)
        self.assertEqual(body, b"101")
        sock.close()

    def test_pipelined_requests_arrive_in_one_write(self):
        """All requests sent in a single sendall() -- the server must
        still parse them as separate messages."""
        sock = self.connect()
        reqs = b""
        for target in ("/add?a=1&b=1", "/add?a=2&b=2", "/add?a=3&b=3"):
            reqs += f"GET {target} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()
        sock.sendall(reqs)
        buf = bytearray()
        results = []
        for _ in range(3):
            status, body = self.read_one_response(buf, sock)
            results.append((status, body))
        self.assertEqual(results, [(200, b"2"), (200, b"4"), (200, b"6")])
        sock.close()

    def test_fragmented_request_reassembled(self):
        """Send one request one byte at a time -- framing must still work."""
        sock = self.connect()
        request = b"GET /mul?a=3&b=4 HTTP/1.1\r\nHost: localhost\r\n\r\n"
        for b in request:
            sock.sendall(bytes([b]))
            time.sleep(0.001)
        buf = bytearray()
        status, body = self.read_one_response(buf, sock)
        self.assertEqual(status, 200)
        self.assertEqual(body, b"12")
        sock.close()

    def test_connection_close_header_closes_socket(self):
        sock = self.connect()
        buf = bytearray()
        self.send_request(sock, "GET", "/add?a=1&b=1", close_conn=True)
        status, _ = self.read_one_response(buf, sock)
        self.assertEqual(status, 200)
        # server should now close its end
        remaining = sock.recv(16)
        self.assertEqual(remaining, b"")
        sock.close()


if __name__ == "__main__":
    unittest.main()
