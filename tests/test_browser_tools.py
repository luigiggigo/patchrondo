"""The browser driver's protocol code and the browser check's behavior without a browser."""
import base64
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import socket
import struct
import sys
import threading
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def load(name):
    sys.path.insert(0, str(TOOLS))
    try:
        spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(TOOLS))


browser = load("browser")


def decode_client_frame(data: bytes) -> tuple[int, bytes, int]:
    """(opcode, payload, bytes used) of one masked frame, as a server reads it."""
    opcode, size, offset = data[0] & 0x0F, data[1] & 0x7F, 2
    assert data[1] & 0x80, "client frames must be masked"
    if size == 126:
        size, offset = struct.unpack(">H", data[2:4])[0], 4
    elif size == 127:
        size, offset = struct.unpack(">Q", data[2:10])[0], 10
    mask = data[offset:offset + 4]
    payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data[offset + 4:offset + 4 + size]))
    return opcode, payload, offset + 4 + size


def server_frame(payload: bytes, opcode: int = 0x1, final: bool = True) -> bytes:
    head = bytes([(0x80 if final else 0) | opcode])
    if len(payload) < 126:
        return head + bytes([len(payload)]) + payload
    if len(payload) < 65536:
        return head + bytes([126]) + struct.pack(">H", len(payload)) + payload
    return head + bytes([127]) + struct.pack(">Q", len(payload)) + payload


class WebSocketTests(unittest.TestCase):
    def test_frames_are_masked_and_sized_for_every_length_class(self):
        for size in (0, 5, 125, 126, 65535, 65536):
            payload = bytes(index % 251 for index in range(size))
            opcode, decoded, used = decode_client_frame(browser.encode_frame(payload))
            self.assertEqual((opcode, decoded, used), (1, payload, len(browser.encode_frame(payload))))

    def test_client_handshakes_reads_fragments_and_answers_pings(self):
        listener = socket.create_server(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        seen = {}

        def serve():
            conn, _ = listener.accept()
            with conn:
                request = b""
                while b"\r\n\r\n" not in request:
                    request += conn.recv(4096)
                seen["request"] = request.decode()
                key = next(line.split(": ")[1] for line in seen["request"].split("\r\n") if line.startswith("Sec-WebSocket-Key"))
                accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
                conn.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode())
                seen["sent"] = decode_client_frame(conn.recv(4096))[1]
                conn.sendall(server_frame(b"hel", final=False) + server_frame(b"ping-data", 0x9) + server_frame("lo ✓".encode(), 0x0))
                seen["pong"] = decode_client_frame(conn.recv(4096))[:2]
                conn.sendall(server_frame(b"x" * 70000))

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        client = browser.WebSocket(f"ws://127.0.0.1:{port}/devtools/page/1")
        try:
            client.send("question")
            self.assertEqual(client.receive(), "hello ✓")
            self.assertEqual(client.receive(), "x" * 70000)
        finally:
            client.close()
            thread.join(5)
            listener.close()
        self.assertEqual(seen["sent"], b"question")
        self.assertEqual(seen["pong"], (0xA, b"ping-data"))
        self.assertIn("GET /devtools/page/1 HTTP/1.1", seen["request"])
        self.assertNotIn("Origin:", seen["request"])


class BrowserCheckTests(unittest.TestCase):
    def test_browser_override_must_exist(self):
        with patch.dict(os.environ, {"PATCHRONDO_BROWSER": str(Path(__file__))}):
            self.assertEqual(browser.find_browser(), str(Path(__file__)))
        with patch.dict(os.environ, {"PATCHRONDO_BROWSER": str(Path(__file__).with_name("no-such-browser"))}):
            self.assertIsNone(browser.find_browser())

    def test_check_is_skipped_and_says_so_without_a_browser(self):
        tool = load("ui_e2e")
        self.assertEqual(set(tool.SCENARIO_FUNCTIONS), set(tool.SCENARIOS))
        with patch.object(tool.browser, "find_browser", return_value=None), patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(tool.main([]), 0)
        self.assertIn("SKIPPED", out.getvalue())
        self.assertNotIn("PASS", out.getvalue())

    def test_synthetic_runner_refuses_a_reachable_provider_cli(self):
        tool = load("ui_e2e")
        self.assertIn("refusing the synthetic run", tool.RUNNER)
        self.assertIn('patch("patchrondo.providers.execute"', tool.RUNNER)
        self.assertNotIn("--authorize-provider-calls", tool.RUNNER)


if __name__ == "__main__":
    unittest.main()
