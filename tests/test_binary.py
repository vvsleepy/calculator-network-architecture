import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src", "protocol"))
import frame  # noqa: E402

BSERVE = os.path.join(ROOT, "src", "server", "bserve.py")
BCURL = os.path.join(ROOT, "src", "client", "bcurl.py")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for_port(port, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.2)
            s.close()
            return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("bserve never started listening")


class BinaryProtocolTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.www = tempfile.mkdtemp()
        with open(os.path.join(cls.www, "index.html"), "w") as f:
            f.write("<html>hi</html>")
        with open(os.path.join(cls.www, "big.bin"), "wb") as f:
            f.write(os.urandom(5000))

        cls.port = free_port()
        cls.proc = subprocess.Popen(
            [sys.executable, BSERVE, cls.www, str(cls.port)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        wait_for_port(cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=5)

    def connect(self):
        return socket.create_connection(("127.0.0.1", self.port), timeout=5)

    def read_response(self, sock):
        f = frame.read_frame(sock)
        self.assertIsNotNone(f)
        self.assertEqual(f.type, frame.TYPE_RESPONSE)
        return frame.parse_response_payload(f.payload)

    # -- basic file serving -------------------------------------------

    def test_serve_known_file(self):
        sock = self.connect()
        sock.sendall(frame.encode_request("GET", "/index.html", {"Host": "x"}))
        resp = self.read_response(sock)
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.body, b"<html>hi</html>")
        sock.close()

    def test_missing_file_is_404(self):
        sock = self.connect()
        sock.sendall(frame.encode_request("GET", "/nope.html", {"Host": "x"}))
        resp = self.read_response(sock)
        self.assertEqual(resp.status, 404)
        sock.close()

    def test_post_method_is_405(self):
        sock = self.connect()
        sock.sendall(frame.encode_request("POST", "/index.html", {"Host": "x"}))
        resp = self.read_response(sock)
        self.assertEqual(resp.status, 405)
        sock.close()

    def test_malformed_frame_is_400(self):
        sock = self.connect()
        # A REQUEST frame whose payload claims a path length far beyond
        # what actually follows.
        bad_payload = bytes([1]) + struct.pack(">H", 9000) + b"/x"
        raw = frame.build_frame(frame.TYPE_REQUEST, bad_payload)
        sock.sendall(raw)
        resp = self.read_response(sock)
        self.assertEqual(resp.status, 400)
        sock.close()

    # -- the framing requirements that are the actual point ------------

    def test_multiple_requests_one_connection(self):
        sock = self.connect()
        for path in ("/index.html", "/nope.html", "/index.html"):
            sock.sendall(frame.encode_request("GET", path, {"Host": "x"}))
        statuses = [self.read_response(sock).status for _ in range(3)]
        self.assertEqual(statuses, [200, 404, 200])
        sock.close()

    def test_multiple_frames_in_a_single_write(self):
        sock = self.connect()
        combined = (
            frame.encode_request("GET", "/index.html", {"Host": "x"})
            + frame.encode_request("GET", "/big.bin", {"Host": "x"})
            + frame.encode_request("GET", "/nope.html", {"Host": "x"})
        )
        sock.sendall(combined)  # one write, three frames
        statuses = [self.read_response(sock).status for _ in range(3)]
        self.assertEqual(statuses, [200, 200, 404])
        sock.close()

    def test_frame_split_across_many_reads(self):
        sock = self.connect()
        raw = frame.encode_request("GET", "/big.bin", {"Host": "x"})
        for i in range(0, len(raw), 7):
            sock.sendall(raw[i:i + 7])
            time.sleep(0.001)
        resp = self.read_response(sock)
        self.assertEqual(resp.status, 200)
        self.assertEqual(len(resp.body), 5000)
        sock.close()

    def test_unknown_frame_type_is_skipped_cleanly(self):
        sock = self.connect()
        junk_payload = b"whatever bytes, doesn't matter" * 3
        junk_frame = frame.build_frame(0x7F, junk_payload)  # unknown type
        real_frame = frame.encode_request("GET", "/index.html", {"Host": "x"})
        sock.sendall(junk_frame + real_frame)
        # only ONE response should arrive, for the real request, and
        # the connection must not desync or close.
        resp = self.read_response(sock)
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.body, b"<html>hi</html>")
        # connection still alive: send another real request after
        sock.sendall(frame.encode_request("GET", "/nope.html", {"Host": "x"}))
        resp2 = self.read_response(sock)
        self.assertEqual(resp2.status, 404)
        sock.close()

    # -- client (bcurl) end to end -------------------------------------

    def run_bcurl(self, *extra_args):
        return subprocess.run(
            [sys.executable, BCURL, *extra_args],
            capture_output=True, timeout=5,
        )

    def test_bcurl_exit_zero_on_200(self):
        result = self.run_bcurl(f"localhost:{self.port}/index.html")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"<html>hi</html>")

    def test_bcurl_exit_nonzero_on_404(self):
        result = self.run_bcurl(f"localhost:{self.port}/nope.html")
        self.assertNotEqual(result.returncode, 0)

    def test_bcurl_verbose_hexdumps(self):
        result = self.run_bcurl("-v", f"localhost:{self.port}/index.html")
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"REQUEST", result.stderr)
        self.assertIn(b"RESPONSE", result.stderr)


if __name__ == "__main__":
    unittest.main()
