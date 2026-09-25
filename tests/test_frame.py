import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "protocol"))
import frame


class HeaderCodecTests(unittest.TestCase):
    def test_round_trip_static_and_literal_headers(self):
        headers = {"Host": "localhost:9000", "X-Custom": "hello world"}
        encoded = frame.encode_headers(headers)
        decoded, offset = frame.decode_headers(encoded, 0)
        self.assertEqual(offset, len(encoded))
        self.assertEqual(decoded, headers)

    def test_static_ids_used_for_known_names(self):
        encoded = frame.encode_headers({"Content-Type": "text/plain"})
        # byte 0 = count(1), byte 1 = id for Content-Type (2), no name bytes
        self.assertEqual(encoded[0], 1)
        self.assertEqual(encoded[1], frame.NAME_TO_ID["content-type"])

    def test_truncated_header_block_raises(self):
        encoded = frame.encode_headers({"Host": "x"})
        with self.assertRaises(frame.FrameError):
            frame.decode_headers(encoded[:-2], 0)

    def test_unknown_static_id_raises(self):
        bad = bytes([1, 250, 0, 0])  # count=1, id=250 (out of range), vlen=0
        with self.assertRaises(frame.FrameError):
            frame.decode_headers(bad, 0)


class RequestResponseCodecTests(unittest.TestCase):
    def test_request_round_trip(self):
        raw = frame.encode_request("GET", "/index.html", {"Host": "localhost:9000"})
        f = frame.HEADER_STRUCT
        version, ftype, flags, reserved, length = f.unpack(raw[:frame.HEADER_SIZE])
        self.assertEqual(version, frame.VERSION)
        self.assertEqual(ftype, frame.TYPE_REQUEST)
        self.assertEqual(length, len(raw) - frame.HEADER_SIZE)

        parsed = frame.parse_request_payload(raw[frame.HEADER_SIZE:])
        self.assertEqual(parsed.method, "GET")
        self.assertEqual(parsed.path, "/index.html")
        self.assertEqual(parsed.headers["Host"], "localhost:9000")
        self.assertEqual(parsed.body, b"")

    def test_response_round_trip_with_body(self):
        raw = frame.encode_response(200, {"Content-Type": "text/plain"}, b"hello")
        parsed = frame.parse_response_payload(raw[frame.HEADER_SIZE:])
        self.assertEqual(parsed.status, 200)
        self.assertEqual(parsed.body, b"hello")

    def test_unsupported_method_rejected_at_encode(self):
        with self.assertRaises(frame.FrameError):
            frame.encode_request("PATCH", "/x", {})

    def test_truncated_path_raises(self):
        # method=GET(1), path_len=100 but no path bytes follow
        payload = bytes([1]) + (100).to_bytes(2, "big")
        with self.assertRaises(frame.FrameError):
            frame.parse_request_payload(payload)


if __name__ == "__main__":
    unittest.main()
