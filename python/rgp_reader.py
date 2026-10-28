"""Trusted, dependency-free RGP reader. Never imports or executes archived code."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

MAX_ARTIFACT = 64 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_FILE = 16 * 1024 * 1024
MAX_ENTRIES = 50_000
MAX_RATIO = 200
MAX_RESULT = 256 * 1024
HEX = re.compile(r"[0-9a-f]{64}\Z")


class RgpError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise RgpError(code, message)


def _safe_path(path: str) -> bool:
    return (
        isinstance(path, str)
        and bool(path)
        and not path.startswith("/")
        and "\\" not in path
        and not any(ord(c) < 32 or ord(c) == 127 for c in path)
        and all(segment not in ("", ".", "..") for segment in path.split("/"))
    )


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tree_digest(files: list[dict]) -> str:
    hasher = hashlib.sha256()
    for entry in files:
        hasher.update(entry["path"].encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(entry["mode"].encode("ascii"))
        hasher.update(b"\x00")
        hasher.update(bytes.fromhex(entry["sha256"]))
    return hasher.hexdigest()


def decode_transport(source: bytes) -> bytes:
    _require(len(source) <= 2 * MAX_ARTIFACT, "RESOURCE_LIMIT", "outer RGP too large")
    try:
        header, body = source.split(b"\n--\n", 1)
    except ValueError as exc:
        raise RgpError("TRUNCATED_ARTIFACT", "missing separator") from exc
    lines = header.split(b"\n")
    _require(len(lines) == 4 and lines[0] == b"RGP/1.0", "UNSUPPORTED_FORMAT", "header version")
    _require(lines[3] == b"base64_line_width=76", "UNSUPPORTED_FORMAT", "Base64 line width")
    _require(lines[1].startswith(b"payload_length=") and lines[2].startswith(b"payload_sha256="), "UNSUPPORTED_FORMAT", "header fields")
    declared_size = lines[1].split(b"=", 1)[1]
    declared_hash = lines[2].split(b"=", 1)[1]
    _require(bool(re.fullmatch(rb"[0-9]+", declared_size)), "UNSUPPORTED_FORMAT", "nondecimal size")
    _require(bool(re.fullmatch(rb"[0-9a-f]{64}", declared_hash)), "UNSUPPORTED_FORMAT", "invalid hash")
    _require(int(declared_size) <= MAX_ARTIFACT, "RESOURCE_LIMIT", "payload too large")
    _require(body.endswith(b"END RGP\n"), "TRUNCATED_ARTIFACT", "missing terminal marker")
    body = body[:-len(b"END RGP\n")]
    _require(body.endswith(b"\n"), "TRUNCATED_ARTIFACT", "missing Base64 newline")
    chunks = body[:-1].split(b"\n")
    _require(bool(chunks) and bool(chunks[0]), "TRUNCATED_ARTIFACT", "empty Base64")
    for n, chunk in enumerate(chunks):
        _require(0 < len(chunk) <= 76 and (n == len(chunks) - 1 or len(chunk) == 76), "TRUNCATED_ARTIFACT", "invalid Base64 line")
    try:
        payload = base64.b64decode(b"".join(chunks), validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise RgpError("TRUNCATED_ARTIFACT", "invalid Base64") from exc
    _require(len(payload) == int(declared_size), "TRUNCATED_ARTIFACT", "payload byte count mismatch")
    _require(_digest(payload).encode("ascii") == declared_hash, "HASH_MISMATCH", "ZIP hash mismatch")
    return payload


class Reader:
    """Verifies the complete RGP on construction. RGP content is *data*, never code."""

    def __init__(self, artifact: str | Path | bytes):
        if isinstance(artifact, bytes):
            source = artifact
        else:
            path = Path(artifact)
            _require(path.stat().st_size <= 2 * MAX_ARTIFACT, "RESOURCE_LIMIT", "artifact too large")
            source = path.read_bytes()
        payload = decode_transport(source)
        blobs: dict[str, bytes] = {}
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload))
            with archive:
                infos = archive.infolist()
                _require(0 < len(infos) <= MAX_ENTRIES, "RESOURCE_LIMIT", "ZIP entry count")
                names: set[str] = set()
                total = 0
                for index, info in enumerate(infos):
                    name = info.filename
                    _require(_safe_path(name), "UNSAFE_PATH", f"invalid ZIP path: {name!r}")
                    _require(name.casefold() not in names, "UNSAFE_PATH", "duplicate ZIP path")
                    names.add(name.casefold())
                    _require(name == "manifest.json" or name.startswith("files/"), "UNSAFE_PATH", "unexpected ZIP member")
                    _require(info.file_size <= (MAX_TOTAL if name == "manifest.json" else MAX_FILE), "RESOURCE_LIMIT", "oversized ZIP member")
                    _require(info.file_size <= max(4096, info.compress_size * MAX_RATIO), "RESOURCE_LIMIT", "compression ratio")
                    total += info.file_size
                    _require(total <= MAX_TOTAL, "RESOURCE_LIMIT", "expanded ZIP size")
                    contents = archive.read(info)
                    _require(len(contents) == info.file_size, "HASH_MISMATCH", "ZIP entry length")
                    if name == "manifest.json":
                        _require(index == 0, "UNSUPPORTED_FORMAT", "manifest must be first")
                        manifest_data = contents
                    else:
                        _require(name == f"files/{_digest(contents)}", "HASH_MISMATCH", "blob mismatch")
                        blobs[name] = contents
        except (zipfile.BadZipFile, RuntimeError, EOFError) as exc:
            raise RgpError("UNSUPPORTED_FORMAT", "invalid ZIP archive") from exc
        _require("manifest_data" in locals(), "UNSUPPORTED_FORMAT", "missing manifest")
        try:
            manifest = json.loads(manifest_data)
        except (ValueError, UnicodeError) as exc:
            raise RgpError("UNSUPPORTED_FORMAT", "malformed JSON manifest") from exc
        _require(isinstance(manifest, dict) and manifest.get("schema_version") == "1.0", "UNSUPPORTED_FORMAT", "manifest schema")
        files = manifest.get("files")
        excluded = manifest.get("excluded")
        _require(isinstance(files, list) and isinstance(excluded, list) and len(files) <= MAX_ENTRIES, "UNSUPPORTED_FORMAT", "manifest shape")
        records = {}
        used = set()
        previous = None
        for entry in files:
            _require(isinstance(entry, dict), "UNSUPPORTED_FORMAT", "file entry")
            path = entry.get("path")
            _require(_safe_path(path), "UNSAFE_PATH", "manifest file path")
            _require(path.casefold() not in used, "UNSAFE_PATH", "colliding paths")
            _require(previous is None or previous < path, "UNSUPPORTED_FORMAT", "file ordering")
            previous = path
            used.add(path.casefold())
            mode = entry.get("mode")
            _require(mode in ("100644", "100755", "120000"), "UNSUPPORTED_FORMAT", "file mode")
            _require(entry.get("kind") == ("symlink" if mode == "120000" else "file"), "UNSUPPORTED_FORMAT", "file kind")
            _require(entry.get("status") in ("included", "pointer_only"), "UNSUPPORTED_FORMAT", "file status")
            sha256 = entry.get("sha256")
            _require(isinstance(sha256, str) and bool(HEX.fullmatch(sha256)), "UNSUPPORTED_FORMAT", "file hash")
            blob = f"files/{sha256}"
            _require(entry.get("blob") == blob and blob in blobs, "HASH_MISMATCH", "missing blob")
            _require(type(entry.get("size")) is int and entry["size"] == len(blobs[blob]), "HASH_MISMATCH", "blob length")
            records[path] = entry
        _require(set(blobs) == {rec["blob"] for rec in records.values()}, "UNSUPPORTED_FORMAT", "unused ZIP blobs")
        for exclusion in excluded:
            _require(isinstance(exclusion, dict), "UNSUPPORTED_FORMAT", "exclusion entry")
            path = exclusion.get("path")
            _require(_safe_path(path) and path.casefold() not in used, "UNSAFE_PATH", "invalid exclusion path")
            used.add(path.casefold())
        _require(_tree_digest(files) == manifest.get("input_tree_digest"), "HASH_MISMATCH", "input tree digest")
        self.manifest = manifest
        self._blobs = blobs
        self._files = records

    def validate(self) -> dict:
        return {"valid": True, "input_tree_digest": self.manifest["input_tree_digest"], "file_count": len(self._files)}

    def overview(self) -> dict:
        return {
            "source_commit": self.manifest["source_commit"],
            "input_tree_digest": self.manifest["input_tree_digest"],
            "file_count": len(self._files),
            "excluded": self.manifest["excluded"],
            "snapshot_complete": not self.manifest["excluded"] and all(e["status"] == "included" for e in self._files.values()),
            "index_coverage": "not_indexed",
            "graph_coverage": "not_indexed",
        }

    def coverage(self, scope: str = "snapshot") -> dict:
        if scope == "snapshot":
            return self.overview()
        if scope in ("index", "graph", "investigation"):
            return {"scope": scope, "status": "not_indexed" if scope != "investigation" else "unknown"}
        raise RgpError("UNSUPPORTED_FORMAT", "unknown coverage scope")

    def tree(self, prefix: str = "", limit: int = 200, cursor: str | None = None) -> dict:
        _require(0 < limit <= 200, "RESOURCE_LIMIT", "tree limit")
        paths = [p for p in self._files if p.startswith(prefix) and (cursor is None or p > cursor)]
        page = paths[:limit]
        return {"paths": page, "next_cursor": page[-1] if len(paths) > limit else None}

    def read(self, path: str, start_line: int | None = None, end_line: int | None = None) -> dict:
        _require(path in self._files, "PARTIAL_SNAPSHOT", "file not present in snapshot")
        entry = self._files[path]
        data = self._blobs[entry["blob"]]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            _require(start_line is None and end_line is None, "UNSUPPORTED_FORMAT", "no line coordinates for binary")
            _require(len(data) <= MAX_RESULT, "RESOURCE_LIMIT", "binary output exceeds cap")
            return {"path": path, "encoding": "base64", "content": base64.b64encode(data).decode("ascii"), "status": entry["status"]}
        lines = text.splitlines(keepends=True)
        start = 1 if start_line is None else start_line
        end = len(lines) if end_line is None else end_line
        _require(1 <= start <= end + 1 and 0 <= end <= len(lines), "UNSUPPORTED_FORMAT", "invalid line range")
        content = "".join(lines[start - 1:end])
        _require(len(content.encode("utf-8")) <= MAX_RESULT, "RESOURCE_LIMIT", "result too large")
        return {"path": path, "encoding": "utf-8", "content": content, "start_line": start, "end_line": end, "status": entry["status"]}

    def search(self, query: str, paths: list[str] | None = None, limit: int = 50, cursor: int | None = None) -> dict:
        _require(0 < len(query) <= 1024 and 0 < limit <= 100, "RESOURCE_LIMIT", "search limits")
        _require(cursor is None or (type(cursor) is int and cursor >= 0), "UNSUPPORTED_FORMAT", "cursor")
        selected = sorted(self._files) if paths is None else sorted(set(paths) & self._files.keys())
        matches = []
        for path in selected:
            try:
                text = self._blobs[self._files[path]["blob"]].decode("utf-8")
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if query in line:
                    matches.append({"path": path, "line": number, "excerpt": line[:512]})
        offset = cursor or 0
        page = matches[offset:offset + limit]
        return {"matches": page, "next_cursor": offset + limit if offset + limit < len(matches) else None}


def main() -> None:
    parser = argparse.ArgumentParser(description="Trusted stdlib-only RGP reader")
    parser.add_argument("--artifact", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("overview")
    commands.add_parser("validate")
    tree = commands.add_parser("tree")
    tree.add_argument("--prefix", default="")
    read = commands.add_parser("read")
    read.add_argument("--path", required=True)
    read.add_argument("--start-line", type=int)
    read.add_argument("--end-line", type=int)
    search = commands.add_parser("search")
    search.add_argument("query")
    args = parser.parse_args()
    try:
        reader = Reader(args.artifact)
        if args.command == "overview":
            result = reader.overview()
        elif args.command == "validate":
            result = reader.validate()
        elif args.command == "tree":
            result = reader.tree(args.prefix)
        elif args.command == "read":
            result = reader.read(args.path, args.start_line, args.end_line)
        else:
            result = reader.search(args.query)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except RgpError as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
