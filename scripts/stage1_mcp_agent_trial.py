#!/usr/bin/env python3
"""Full real Git -> RGP -> MCP stdio tool -> pinned Git evidence exercise.

Four previously frozen coding tasks per repo, with the exact same
source anchors as the existing agent-context protocol. This validates
that an *external MCP client* can navigate real source using just
tool calls, not Python imports of Reader. It is not LLM inference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from gate_b import strict_json, validate_plan  # noqa: E402
from stage1_agent_task_pilot import TERMS  # noqa: E402


class Client:
    def __init__(self, artifact: Path):
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "python/rgp_mcp.py"),
             "--artifact", str(artifact)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.next_id = 0
        self.requests = 0
        self.response_bytes = 0
        self.tool_calls = 0

    def send(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            message["params"] = params
        assert self.process.stdin and self.process.stdout
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise AssertionError("MCP process terminated without response")
        self.requests += 1
        self.response_bytes += len(line)
        response = json.loads(line)
        assert response.get("jsonrpc") == "2.0" and response.get("id") == self.next_id
        assert "error" not in response, f"unexpected MCP protocol error: {response}"
        return response["result"]

    def tool(self, name: str, args: dict | None = None) -> dict:
        self.tool_calls += 1
        result = self.send("tools/call", {"name": name, "arguments": args or {}})
        assert not result.get("isError"), f"unexpected MCP tool error: {result}"
        assert len(result["content"]) == 1
        return json.loads(result["content"][0]["text"])

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        try:
            code = self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            raise AssertionError("MCP process failed to shut down on EOF")
        assert code == 0, f"MCP exited {code}, {self.process.stderr.read()[-400:]!r}"


def run(artifact: Path, source_repo: Path, repository: str, plan: Path) -> dict:
    frozen = strict_json(plan)
    validate_plan(frozen)
    selected = [t for t in frozen["tasks"] if t["repository"] == repository]
    repo = next(r for r in frozen["repositories"] if r["id"] == repository)
    assert len(selected) == 4, "expected four frozen coding tasks per repo"
    start = time.perf_counter_ns()
    client = Client(artifact)
    read_paths = set()
    count = 0
    total_context_bytes = 0
    rows = []
    try:
        handshake = client.send("initialize", {"protocolVersion": "2025-06-18",
                                                "clientInfo": {"name": "rgp-agent-acceptance",
                                                               "version": "1.0"},
                                                "capabilities": {}})
        assert handshake["protocolVersion"] == "2025-06-18"
        tools = client.send("tools/list")["tools"]
        assert {t["name"] for t in tools} == {
            "rgp_status", "rgp_tree", "rgp_search", "rgp_read", "rgp_context"}
        status = client.tool("rgp_status")["overview"]
        assert status["source_commit"] == repo["commit"]
        assert status["snapshot_complete"]
        assert status["file_count"] > 0
        assert status["index_coverage"] == "not_indexed"
        first = client.tool("rgp_tree", {"limit": 200})
        assert first["paths"]
        for task in selected:
            terms = list(TERMS[task["id"]])
            context = client.tool("rgp_context", {
                "terms": terms, "max_bytes": 24_000, "max_files": 4
            })
            assert context["source_commit"] == repo["commit"]
            assert not context["terms_without_source"]
            assert not context["search_capped_terms"]
            actual_paths = {x["path"] for x in context["sources"]}
            assert set(task["reference_paths"]) <= actual_paths, (
                task["id"], set(task["reference_paths"]) - actual_paths)
            total_context_bytes += len(json.dumps(
                context, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8"))
            for item in context["sources"]:
                path = item["path"]
                original = subprocess.check_output([
                    "git", "-C", str(source_repo), "show",
                    f"{repo['commit']}:{path}"
                ])
                lines = original.decode("utf-8").splitlines(keepends=True)
                start_line, end_line = item["start_line"], item["end_line"]
                assert 1 <= start_line <= item["match_line"] <= end_line <= len(lines)
                assert "".join(lines[start_line - 1:end_line]) == item["content"]
                assert item["match_term"] in lines[item["match_line"] - 1]
                # Independently follow one actual MCP rgp_read citation for
                # each task, not just inspect its preselected context JSON.
                count += 1
                read_paths.add(path)
            source = context["sources"][0]
            reread = client.tool("rgp_read", {
                "path": source["path"], "start_line": source["start_line"],
                "end_line": source["end_line"],
            })
            assert reread["content"] == source["content"]
            # The search tool is a real independent step in the agent
            # protocol; the model can use it to refine its next query.
            matches = client.tool("rgp_search", {
                "query": source["match_term"], "paths": [source["path"]], "limit": 20
            })
            assert any(m["line"] == source["match_line"] for m in matches["matches"])
            rows.append({"task_id": task["id"], "passages_verified": len(context["sources"]),
                         "source_paths": sorted(actual_paths),
                         "context_bytes": len(json.dumps(
                            context, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":")).encode("utf-8"))})
        negative = client.send("tools/call", {
            "name": "rgp_read", "arguments": {"path": "../not-in-repo"}
        })
        assert negative["isError"] and "PARTIAL_SNAPSHOT" in negative["content"][0]["text"]
        assert client.send("ping") == {}
    finally:
        client.close()
    elapsed_ms = round((time.perf_counter_ns() - start) / 1e6, 3)
    return {
        "kind": "rgp_real_mcp_agent_source_navigation",
        "status": "PASS", "repository": repository, "source_commit": repo["commit"],
        "task_count": len(rows), "verified_source_passages": count,
        "unique_source_paths": len(read_paths), "context_bytes": total_context_bytes,
        "mcp_json_rpc_requests": client.requests,
        "mcp_tool_calls": client.tool_calls,
        "mcp_wire_response_bytes": client.response_bytes,
        "elapsed_ms_including_local_git_truth_checks": elapsed_ms,
        "tasks": rows, "negative_read_rejected": True,
        "model_inference": "NOT_RUN",
        "model_tokens": "NOT_MEASURED",
        "github_provider_calls": "NOT_MEASURED_BY_LOCAL_GIT",
        "independent_answer_grading": "NOT_RUN",
        "gate_b": "HOLD",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--source-repo", required=True, type=Path)
    parser.add_argument("--repository", choices=("rgp", "serde_json", "itsdangerous"),
                        required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = run(args.artifact, args.source_repo, args.repository,
                     ROOT / "benchmarks/gate_b/plan.json")
    except Exception as exc:
        result = {"kind": "rgp_real_mcp_agent_source_navigation", "status": "FAIL",
                  "repository": args.repository, "reason": str(exc),
                  "gate_b": "HOLD"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False,
                                      indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
