"""MCP protocol and source safety on a real, independently verified Reader."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tests"))
from test_reader_api import three_scope_artifact  # noqa: E402
from rgp_mcp import RgpMcp, TOOLS, MAX_RESPONSE  # noqa: E402
from rgp_reader import Reader  # noqa: E402


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.server = RgpMcp(Reader(three_scope_artifact()))
        self.seq = 0

    def ask(self, method, params=None):
        self.seq += 1
        request = {"jsonrpc": "2.0", "id": self.seq, "method": method}
        if params is not None:
            request["params"] = params
        return self.server.dispatch(request)

    def tool(self, name, args=None):
        reply = self.ask("tools/call", {"name": name, "arguments": args or {}})
        self.assertEqual(reply["id"], self.seq)
        result = reply["result"]
        if not result["isError"]:
            return json.loads(result["content"][0]["text"])
        return result

    def initialize(self):
        message = self.ask("initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(message["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", message["result"]["capabilities"])

    def test_initialize_before_tools_and_legacy_client(self):
        self.assertEqual(self.ask("tools/list")["error"]["code"], -32000)
        for version in ("2024-11-05", "2025-03-26", "2025-06-18"):
            response = self.ask("initialize", {"protocolVersion": version})
            self.assertEqual(response["result"]["protocolVersion"], version)
        self.assertIsNone(self.server.dispatch(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(self.ask("ping")["result"], {})

    def test_tools_are_real_and_bounded(self):
        self.initialize()
        listed = self.ask("tools/list")["result"]["tools"]
        self.assertEqual({t["name"] for t in listed},
                         {"rgp_status", "rgp_tree", "rgp_search",
                          "rgp_read", "rgp_context"})
        self.assertEqual(len(TOOLS), 5)
        status = self.tool("rgp_status")
        self.assertEqual(status["overview"]["source_commit"], "a" * 40)
        self.assertTrue(status["overview"]["snapshot_complete"])
        self.assertEqual(status["index"]["status"], "not_indexed")
        self.assertTrue(status["repository_source_untrusted"])
        tree = self.tool("rgp_tree", {"prefix": "src/", "limit": 1})
        self.assertEqual(tree["paths"], ["src/a.py"])
        last = self.tool("rgp_tree", {"prefix": "src/", "limit": 1,
                                     "cursor": tree["next_cursor"]})
        self.assertEqual(last["paths"], ["src/sub/b.py"])
        search = self.tool("rgp_search", {"query": "needle", "prefix": "src/"})
        self.assertEqual(len(search["matches"]), 3)
        read = self.tool("rgp_read", {"path": "src/a.py", "start_line": 1,
                                     "end_line": 1})
        self.assertEqual(read["content"], "needle from src A\n")
        ctx = self.tool("rgp_context", {"terms": ["needle", "again"],
                                        "prefix": "src/", "max_bytes": 2048})
        self.assertEqual(ctx["terms_without_source"], [])
        self.assertLessEqual(len(json.dumps(ctx, ensure_ascii=False).encode()),
                             2048 + 300)  # printed JSON uses canonical compact separators
        self.assertTrue(all(x["path"].startswith("src/") for x in ctx["sources"]))

    def test_bad_arguments_are_recoverable_not_fake_source(self):
        self.initialize()
        for name, args in (
            ("rgp_read", {"path": "../secret"}),
            ("rgp_read", {"path": "src/a.py", "start_line": True}),
            ("rgp_search", {"query": "needle", "paths": ["../unknown"]}),
            ("rgp_context", {"terms": ["needle"], "max_bytes": 100}),
            ("rgp_search", {"query": "needle", "extra": "fake"}),
            ("unknown_tool", {}),
        ):
            with self.subTest(name=name, args=args):
                error = self.tool(name, args)
                self.assertTrue(error["isError"])
                self.assertLessEqual(len(error["content"][0]["text"]), 240)
        self.assertEqual(self.tool("rgp_read", {"path": "src/a.py",
                                               "start_line": 1,
                                               "end_line": 1})["content"],
                         "needle from src A\n")

    def test_protocol_errors_and_unknown_notifications(self):
        self.initialize()
        self.assertIsNone(self.server.dispatch(
            {"jsonrpc": "2.0", "method": "notifications/unknown"}))
        self.assertEqual(self.ask("unknown/method")["error"]["code"], -32601)
        self.assertEqual(self.ask("tools/call", [])["error"]["code"], -32602)
        self.assertEqual(self.server.dispatch(
            {"jsonrpc": "2.0", "id": True, "method": "ping"})["error"]["code"], -32600)

    def test_stdio_real_process_keeps_one_reader_and_does_not_log_to_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "pinned.rgp"
            artifact.write_bytes(three_scope_artifact())
            with subprocess.Popen(
                [sys.executable, "-u", str(ROOT / "python/rgp_mcp.py"),
                 "--artifact", str(artifact)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ) as process:
                assert process.stdin and process.stdout
                def send(request):
                    process.stdin.write(
                        (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
                    process.stdin.flush()
                    return json.loads(process.stdout.readline())
                init = send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-06-18"}})
                self.assertEqual(init["result"]["serverInfo"]["name"], "rgp-pinned-source")
                process.stdin.write(
                    b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
                process.stdin.flush()
                result = send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": "rgp_search",
                                          "arguments": {"query": "again"}}})
                self.assertEqual(result["id"], 2)
                self.assertEqual(json.loads(result["result"]["content"][0]["text"])
                                 ["matches"][0]["line"], 2)
                process.stdin.close()
                self.assertEqual(process.wait(timeout=10), 0)
                self.assertEqual(process.stderr.read(), b"")

    def test_bad_model_unicode_is_recoverable_via_mcp(self):
        self.initialize()
        for name, arguments in (
            ("rgp_tree", {"prefix": chr(0xD800)}),
            ("rgp_search", {"query": chr(0xD800)}),
            ("rgp_context", {"terms": [chr(0xD800)]}),
        ):
            with self.subTest(name=name):
                result = self.tool(name, arguments)
                self.assertTrue(result["isError"])
        self.assertEqual(self.tool("rgp_search", {"query": "needle"})
                         ["matches"][0]["path"], "src/a.py")

    def test_escaped_surrogate_rpc_id_does_not_terminate_stdio(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "pinned.rgp"
            artifact.write_bytes(three_scope_artifact())
            with subprocess.Popen(
                [sys.executable, "-u", str(ROOT / "python/rgp_mcp.py"),
                 "--artifact", str(artifact)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ) as process:
                assert process.stdin and process.stdout
                def send_bytes(raw):
                    process.stdin.write(raw + b"\\n")
                    process.stdin.flush()
                    return json.loads(process.stdout.readline())
                send_bytes(b'{"jsonrpc":"2.0","id":1,"method":"initialize"}')
                reply = send_bytes(b'{"jsonrpc":"2.0","id":"\\\\ud800","method":"ping"}')
                self.assertEqual(reply["id"], chr(0xD800))
                self.assertEqual(reply["result"], {})
                valid = send_bytes(b'{"jsonrpc":"2.0","id":3,"method":"tools/list"}')
                self.assertEqual(len(valid["result"]["tools"]), 5)
                process.stdin.close()
                self.assertEqual(process.wait(timeout=10), 0)
                self.assertEqual(process.stderr.read(), b"")

    def test_invalid_archive_cannot_start_mcp(self):
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "broken.rgp"
            invalid.write_bytes(b"not an RGP")
            process = subprocess.run(
                [sys.executable, str(ROOT / "python/rgp_mcp.py"),
                 "--artifact", str(invalid)],
                capture_output=True, timeout=10,
            )
            self.assertEqual(process.returncode, 2)
            self.assertIn(b"RGP MCP startup failed", process.stderr)
            self.assertEqual(process.stdout, b"")


if __name__ == "__main__":
    unittest.main()
