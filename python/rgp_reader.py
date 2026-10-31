"""Trusted, dependency-free RGP reader. Never imports or executes archived code."""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import io
import json
import re
import stat
import unicodedata
import zipfile
from pathlib import Path

MAX_ARTIFACT = 64 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_FILE = 16 * 1024 * 1024
MAX_ENTRIES = 50_000
MAX_RATIO = 200
MAX_RESULT = 256 * 1024
MAX_MANIFEST = 16 * 1024 * 1024
MAX_CURSOR = 8192
MAX_PATH_BYTES = 4096
MAX_OVERVIEW_EXCLUSIONS = 100
MAX_HEADER = 4096
# Only legacy RGP/1.1 embeds the old guide. RGP/2.1 uses length-framed inline guidance.
# Pin its digest here so the trusted Reader stays standalone in a sandbox.
AGENT_GUIDE_SHA256 = "9585596e0f091096bac8c67163a50fb40ab789ee72f49089f80557acbec4740d"
HEX = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
EXCLUSION_REASONS = frozenset({"self_artifact", "sensitive_path", "submodule_unavailable", "size_limit", "entry_limit", "non_text", "rgpignore"})


class RgpError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise RgpError(code, message)


def _safe_path(path: str) -> bool:
    if not isinstance(path, str) or not path:
        return False
    # JSON can represent lone UTF-16 surrogates even though UTF-8 Git paths
    # cannot. Reject them as unsafe data, never as an uncaught Python error.
    try:
        size = len(path.encode("utf-8"))
    except UnicodeEncodeError:
        return False
    return (
        size <= MAX_PATH_BYTES
        and not path.startswith("/")
        and "\\" not in path
        and not any(unicodedata.category(c) == "Cc" for c in path)
        and all(segment not in ("", ".", "..") for segment in path.split("/"))
    )


def _safe_prefix(prefix: str) -> bool:
    """Validate a lexical path prefix, including an optional trailing slash."""
    if prefix == "":
        return True
    if not isinstance(prefix, str):
        return False
    try:
        encoded = prefix.encode("utf-8")
    except UnicodeEncodeError:
        return False
    if len(encoded) > MAX_PATH_BYTES:
        return False
    if prefix.startswith("/") or "\\" in prefix or any(
        unicodedata.category(c) == "Cc" for c in prefix
    ):
        return False
    segments = prefix.split("/")
    return (all(segment not in ("", ".", "..") for segment in segments[:-1])
            and segments[-1] not in (".", ".."))


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


MAX_SEARCH_EXCERPT = 512


def _matching_excerpt(line: str, query: str) -> str:
    """Bound UTF-8 output while keeping the first matched term in view.

    A query longer than the 512-byte excerpt cannot be shown in full;
    include its beginning instead. The search result still names the full
    file and line for a scoped Reader.read() follow-up.
    """
    data = line.encode("utf-8")
    if len(data) <= MAX_SEARCH_EXCERPT:
        return line
    match_at = line.find(query)  # callers only pass lines containing query
    match_byte = len(line[:match_at].encode("utf-8"))
    match_size = len(query.encode("utf-8"))
    # Reserve some left context, never at the expense of the full match
    # when the query fits within the byte limit.
    before = min(128, max(0, MAX_SEARCH_EXCERPT - match_size) // 2)
    offset = max(0, match_byte - before)
    # The slice may start/end inside a UTF-8 character. Dropping just the
    # partial character is safe and keeps the matching text intact.
    return data[offset:offset + MAX_SEARCH_EXCERPT].decode("utf-8", errors="ignore")


def decode_transport(source: bytes) -> bytes:
    _require(len(source) <= 2 * MAX_ARTIFACT, "RESOURCE_LIMIT", "outer RGP too large")
    try:
        header, body = source.split(b"\n--\n", 1)
    except ValueError as exc:
        raise RgpError("TRUNCATED_ARTIFACT", "missing separator") from exc
    _require(len(header) <= MAX_HEADER, "RESOURCE_LIMIT", "header too large")
    lines = header.split(b"\n")
    if lines[0] in (b"RGP/1.0", b"RGP/1.2"):
        _require(len(lines) == 4, "UNSUPPORTED_FORMAT", "plain header fields")
        fields = lines[1:]
    elif lines[0] == b"RGP/1.1":
        _require(len(lines) >= 7, "UNSUPPORTED_FORMAT", "missing agent guide")
        # Reject instructions modified in transit, including extra header
        # fields. A ZIP digest alone would not protect the plaintext guide.
        guide = b"\n".join(lines[1:-3]) + b"\n"
        _require(_digest(guide) == AGENT_GUIDE_SHA256,
                 "UNSUPPORTED_FORMAT", "unexpected agent guide")
        fields = lines[-3:]
    else:
        raise RgpError("UNSUPPORTED_FORMAT", "header version")
    _require(fields[2] == b"base64_line_width=76", "UNSUPPORTED_FORMAT", "Base64 line width")
    _require(fields[0].startswith(b"payload_length=") and fields[1].startswith(b"payload_sha256="),
             "UNSUPPORTED_FORMAT", "header fields")
    declared_size = fields[0].split(b"=", 1)[1]
    declared_hash = fields[1].split(b"=", 1)[1]
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


def decode_plain(source: bytes) -> tuple[bytes, dict[str, int], dict[str, tuple[int, int]], dict | None]:
    """Read exact UTF-8 source spans from RGP/2.0 or self-contained 2.1."""
    _require(len(source) <= 192 * 1024 * 1024, "RESOURCE_LIMIT", "RGP/2.0 too large")
    try:
        source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RgpError("UNSUPPORTED_FORMAT", "non-UTF8 plain RGP") from exc
    _require(b"\x00" not in source, "UNSUPPORTED_FORMAT", "binary RGP")
    pos = 0

    def line() -> bytes:
        nonlocal pos
        end = source.find(b"\n", pos)
        _require(end >= 0, "TRUNCATED_ARTIFACT", "plain line")
        part = source[pos:end]
        pos = end + 1
        return part

    version = line()
    _require(version in (b"RGP/2.0", b"RGP/2.1"),
             "UNSUPPORTED_FORMAT", "plain version")
    embedded = None
    if version == b"RGP/2.1":
        commit_line = line()
        complete_line = line()
        length_line = line()
        guide_hash_line = line()
        _require(commit_line.startswith(b"source_commit=") and
                 complete_line in (b"snapshot_complete=true", b"snapshot_complete=false") and
                 length_line.startswith(b"guide_length=") and
                 guide_hash_line.startswith(b"guide_sha256="),
                 "UNSUPPORTED_FORMAT", "embedded metadata fields")
        commit = commit_line[len(b"source_commit="):]
        raw_length = length_line[len(b"guide_length="):]
        guide_hash = guide_hash_line[len(b"guide_sha256="):]
        _require(bool(re.fullmatch(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit)) and
                 bool(re.fullmatch(rb"[0-9]+", raw_length)) and
                 bool(re.fullmatch(rb"[0-9a-f]{64}", guide_hash)),
                 "UNSUPPORTED_FORMAT", "embedded metadata values")
        guide_length = int(raw_length)
        _require(0 < guide_length <= 8192, "RESOURCE_LIMIT", "embedded guide size")
        embedded = {
            "source_commit": commit.decode("ascii"),
            "snapshot_complete": complete_line == b"snapshot_complete=true",
            "guide_length": guide_length,
            "guide_sha256": guide_hash.decode("ascii"),
        }
    field = line()
    digest_field = line()
    _require(field.startswith(b"manifest_length=") and
             digest_field.startswith(b"manifest_sha256="), "UNSUPPORTED_FORMAT", "plain header")
    number = field[len(b"manifest_length="):]
    digest = digest_field[len(b"manifest_sha256="):]
    _require(bool(re.fullmatch(rb"[0-9]+", number)) and
             bool(re.fullmatch(rb"[0-9a-f]{64}", digest)), "UNSUPPORTED_FORMAT", "plain header fields")
    count = int(number)
    _require(count <= MAX_MANIFEST, "RESOURCE_LIMIT", "manifest too large")
    _require(line() == b"--", "UNSUPPORTED_FORMAT", "plain separator")
    if embedded is not None:
        raw_guide = source[pos:pos + embedded["guide_length"]]
        _require(len(raw_guide) == embedded["guide_length"],
                 "TRUNCATED_ARTIFACT", "embedded guide")
        _require(_digest(raw_guide) == embedded["guide_sha256"],
                 "HASH_MISMATCH", "embedded guide")
        _require(b"\x00" not in raw_guide, "UNSUPPORTED_FORMAT", "guide NUL")
        embedded["instructions"] = raw_guide.decode("utf-8")
        pos += embedded["guide_length"]
        _require(source[pos:pos + 1] == b"\n", "TRUNCATED_ARTIFACT", "guide delimiter")
        pos += 1
    manifest_bytes = source[pos:pos + count]
    _require(len(manifest_bytes) == count, "TRUNCATED_ARTIFACT", "plain manifest")
    _require(_digest(manifest_bytes).encode() == digest, "HASH_MISMATCH", "plain manifest hash")
    pos += count
    _require(source[pos:pos + 1] == b"\n", "TRUNCATED_ARTIFACT", "manifest delimiter")
    pos += 1
    try:
        manifest = json.loads(manifest_bytes)
    except (ValueError, UnicodeError) as exc:
        raise RgpError("UNSUPPORTED_FORMAT", "plain manifest JSON") from exc
    _require(isinstance(manifest, dict) and isinstance(manifest.get("files"), list) and
             len(manifest["files"]) < MAX_ENTRIES, "UNSUPPORTED_FORMAT", "plain file list")
    sizes = {}
    offsets = {}
    total = 0
    for record in manifest["files"]:
        _require(isinstance(record, dict), "UNSUPPORTED_FORMAT", "plain file record")
        path, size, digest = (record.get("path"), record.get("size"), record.get("sha256"))
        _require(_safe_path(path) and type(size) is int and 0 <= size <= MAX_FILE and
                 isinstance(digest, str) and bool(HEX.fullmatch(digest)),
                 "UNSUPPORTED_FORMAT", "plain source description")
        total += size
        _require(total <= MAX_TOTAL, "RESOURCE_LIMIT", "source bytes")
        path_json = json.dumps(path, ensure_ascii=False, separators=(",", ":"))
        expected = f"FILE\t{path_json}\t{size}\t{digest}".encode("utf-8")
        _require(line() == expected, "UNSUPPORTED_FORMAT", "plain file ordering/header")
        raw = source[pos:pos + size]
        _require(len(raw) == size, "TRUNCATED_ARTIFACT", "plain source")
        _require(_digest(raw) == digest, "HASH_MISMATCH", "plain source SHA")
        sizes[f"files/{digest}"] = size
        offsets[f"files/{digest}"] = (pos, size)
        pos += size
        _require(source[pos:pos + 1] == b"\n", "TRUNCATED_ARTIFACT", "plain file separator")
        pos += 1
    _require(line() == b"END RGP" and pos == len(source),
             "TRUNCATED_ARTIFACT", "plain terminal marker/trailing data")
    return manifest_bytes, sizes, offsets, embedded


def decode_lean(source: bytes):
    """RGP/3.0: zero hashes/commit IDs in text; derive fingerprints only in RAM."""
    _require(len(source) <= 192 * 1024 * 1024, "RESOURCE_LIMIT", "RGP/3.0 size")
    try:
        source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RgpError("UNSUPPORTED_FORMAT", "RGP/3.0 requires UTF-8") from exc
    _require(b"\x00" not in source, "UNSUPPORTED_FORMAT", "RGP/3.0 contains NUL")
    pos = 0

    def line():
        nonlocal pos
        end = source.find(b"\n", pos)
        _require(end >= 0, "TRUNCATED_ARTIFACT", "RGP/3.0 line")
        part = source[pos:end]
        pos = end + 1
        return part

    _require(line() == b"RGP/3.0", "UNSUPPORTED_FORMAT", "lean version")
    sizes = []
    for label, cap in ((b"guide_bytes=", 8192), (b"index_bytes=", MAX_MANIFEST)):
        field = line()
        _require(field.startswith(label) and bool(re.fullmatch(rb"[0-9]+", field[len(label):])),
                 "UNSUPPORTED_FORMAT", "lean length")
        size = int(field[len(label):])
        _require(size <= cap, "RESOURCE_LIMIT", "lean section size")
        sizes.append(size)
    _require(line() == b"--", "UNSUPPORTED_FORMAT", "lean boundary")
    guide_size, index_size = sizes
    guide = source[pos:pos + guide_size]
    _require(len(guide) == guide_size and guide_size > 0,
             "TRUNCATED_ARTIFACT", "lean guide")
    pos += guide_size
    _require(source[pos:pos + 1] == b"\n", "TRUNCATED_ARTIFACT", "lean guide separator")
    pos += 1
    index = source[pos:pos + index_size]
    _require(len(index) == index_size, "TRUNCATED_ARTIFACT", "lean index")
    pos += index_size
    _require(source[pos:pos + 1] == b"\n", "TRUNCATED_ARTIFACT", "lean index separator")
    pos += 1
    entries = []
    exclusions = []
    counts = {"rgpignore": 0, "self_artifact": 0}
    offsets = {}
    blobs = {}
    previous_path = None
    previous_exclusion = None
    names = set()
    exclusions_started = False
    total = 0
    for item in index.split(b"\n"):
        if not item:
            continue
        try:
            fields = item.decode("utf-8").split("\t")
        except UnicodeDecodeError as exc:
            raise RgpError("UNSUPPORTED_FORMAT", "lean index UTF-8") from exc
        if fields[0] == "F":
            _require(not exclusions_started and len(fields) in (3, 4),
                     "UNSUPPORTED_FORMAT", "file index entry")
            name, number = fields[1:3]
            flag = fields[3] if len(fields) == 4 else ""
            modes = {"": ("100644", "included"), "x": ("100755", "included"),
                     "l": ("120000", "included"), "p": ("100644", "pointer_only"),
                     "xp": ("100755", "pointer_only"), "lp": ("120000", "pointer_only")}
            _require(_safe_path(name) and bool(re.fullmatch(r"[0-9]+", number)) and
                     flag in modes and (previous_path is None or previous_path < name),
                     "UNSUPPORTED_FORMAT", "file path/size/mode/order")
            length = int(number)
            total += length
            _require(length <= MAX_FILE and total <= MAX_TOTAL,
                     "RESOURCE_LIMIT", "lean file size")
            _require(name.lower() not in names, "UNSAFE_PATH", "duplicate path")
            names.add(name.lower())
            _require(line() == ("FILE\t" + name).encode("utf-8"),
                     "UNSUPPORTED_FORMAT", "lean FILE header")
            raw = source[pos:pos + length]
            _require(len(raw) == length, "TRUNCATED_ARTIFACT", "lean source")
            _require(source[pos + length:pos + length + 1] == b"\n",
                     "TRUNCATED_ARTIFACT", "lean source boundary")
            digest = _digest(raw)  # derived ONLY in RAM, never written to RGP
            blob = f"files/{digest}"
            blobs[blob] = length
            offsets[blob] = (pos, length)
            pos += length + 1
            mode, status = modes[flag]
            entries.append({"path": name, "mode": mode,
                            "kind": "symlink" if mode == "120000" else "file",
                            "size": length, "sha256": digest, "blob": blob,
                            "status": status})
            previous_path = name
        elif fields[0] in ("I", "S"):
            exclusions_started = True
            key = "rgpignore" if fields[0] == "I" else "self_artifact"
            _require(len(fields) == 2 and bool(re.fullmatch(r"[0-9]+", fields[1])) and
                     counts[key] == 0, "UNSUPPORTED_FORMAT", "lean omission count")
            counts[key] = int(fields[1])
            _require(counts[key] <= MAX_ENTRIES, "RESOURCE_LIMIT", "omission count")
        elif fields[0] == "X":
            exclusions_started = True
            _require(len(fields) == 3, "UNSUPPORTED_FORMAT", "lean excluded entry")
            path, reason = fields[1:]
            _require(_safe_path(path) and (previous_exclusion is None or previous_exclusion < path)
                     and path.lower() not in names and reason in EXCLUSION_REASONS
                     and reason not in counts, "UNSUPPORTED_FORMAT", "excluded path/reason")
            exclusions.append({"path": path, "reason": reason})
            names.add(path.lower())
            previous_exclusion = path
        else:
            raise RgpError("UNSUPPORTED_FORMAT", "lean index marker")
    _require(len(entries) < MAX_ENTRIES and len(exclusions) <= MAX_ENTRIES,
             "RESOURCE_LIMIT", "lean entries")
    _require(line() == b"END RGP" and pos == len(source),
             "TRUNCATED_ARTIFACT", "lean end marker")
    manifest = {"schema_version": "1.0", "source_commit": None,
                "input_tree_digest": _tree_digest(entries), "files": entries,
                "excluded": exclusions, "index_coverage": "not_indexed",
                "graph_coverage": "not_indexed"}
    return (json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            blobs, offsets, counts, guide.decode("utf-8"))


class Reader:
    """Verifies the complete RGP on construction. RGP content is *data*, never code."""

    def __init__(self, artifact: str | Path | bytes):
        if isinstance(artifact, bytes):
            source = artifact
        else:
            path = Path(artifact)
            _require(path.stat().st_size <= 192 * 1024 * 1024, "RESOURCE_LIMIT", "artifact too large")
            source = path.read_bytes()
        self._plain = None
        self._plain_offsets = {}
        self._embedded_metadata = None
        self._lean = False
        self._lean_counts = {"rgpignore": 0, "self_artifact": 0}
        self._lean_guide = None
        if source.startswith(b"RGP/3.0\n"):
            manifest_data, blobs, self._plain_offsets, self._lean_counts, self._lean_guide = decode_lean(source)
            self._plain = source
            self._lean = True
            payload = None
        elif source.startswith((b"RGP/2.0\n", b"RGP/2.1\n")):
            manifest_data, blobs, self._plain_offsets, self._embedded_metadata = decode_plain(source)
            self._plain = source
            payload = None
        else:
            # Explicitly reject unknown transport versions before legacy
            # separator parsing, which otherwise misreports truncation.
            if source.startswith(b"RGP/") and not source.startswith((
                b"RGP/1.0\n", b"RGP/1.1\n", b"RGP/1.2\n",
            )):
                raise RgpError("UNSUPPORTED_FORMAT", "unsupported RGP transport")
            payload = decode_transport(source)
            # Validate every source blob eagerly, but retain only its length and
            # the compressed ZIP payload. Reads and searches decompress on demand.
            blobs: dict[str, int] = {}
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
                        _require(not stat.S_ISLNK(info.external_attr >> 16), "UNSAFE_PATH", "ZIP symlink entry")
                        _require(name.lower() not in names, "UNSAFE_PATH", "duplicate ZIP path")
                        names.add(name.lower())
                        _require(name == "manifest.json" or name.startswith("files/"), "UNSAFE_PATH", "unexpected ZIP member")
                        _require(info.file_size <= (MAX_MANIFEST if name == "manifest.json" else MAX_FILE), "RESOURCE_LIMIT", "oversized ZIP member")
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
                            blobs[name] = len(contents)
            except (zipfile.BadZipFile, RuntimeError, EOFError) as exc:
                raise RgpError("UNSUPPORTED_FORMAT", "invalid ZIP archive") from exc
        _require("manifest_data" in locals(), "UNSUPPORTED_FORMAT", "missing manifest")
        try:
            def no_duplicate_keys(pairs):
                result = {}
                for key, value in pairs:
                    _require(key not in result, "UNSUPPORTED_FORMAT", "duplicate JSON key")
                    result[key] = value
                return result

            manifest = json.loads(manifest_data, object_pairs_hook=no_duplicate_keys)
        except (ValueError, UnicodeError) as exc:
            raise RgpError("UNSUPPORTED_FORMAT", "malformed JSON manifest") from exc
        _require(isinstance(manifest, dict) and manifest.get("schema_version") == "1.0", "UNSUPPORTED_FORMAT", "manifest schema")
        commit = manifest.get("source_commit")
        tree_hash = manifest.get("input_tree_digest")
        _require((commit is None if self._lean else isinstance(commit, str) and bool(COMMIT.fullmatch(commit))),
                 "UNSUPPORTED_FORMAT", "source commit")
        _require(isinstance(tree_hash, str) and bool(HEX.fullmatch(tree_hash)), "UNSUPPORTED_FORMAT", "tree digest")
        _require(manifest.get("index_coverage") == "not_indexed" and manifest.get("graph_coverage") == "not_indexed", "UNSUPPORTED_FORMAT", "coverage fields")
        files = manifest.get("files")
        excluded = manifest.get("excluded")
        _require(isinstance(files, list) and isinstance(excluded, list) and len(files) <= MAX_ENTRIES and len(excluded) <= MAX_ENTRIES, "UNSUPPORTED_FORMAT", "manifest shape")
        records = {}
        used = set()
        previous = None
        for entry in files:
            _require(isinstance(entry, dict), "UNSUPPORTED_FORMAT", "file entry")
            path = entry.get("path")
            _require(_safe_path(path), "UNSAFE_PATH", "manifest file path")
            _require(path.lower() not in used, "UNSAFE_PATH", "colliding paths")
            _require(previous is None or previous < path, "UNSUPPORTED_FORMAT", "file ordering")
            previous = path
            used.add(path.lower())
            mode = entry.get("mode")
            _require(mode in ("100644", "100755", "120000"), "UNSUPPORTED_FORMAT", "file mode")
            _require(entry.get("kind") == ("symlink" if mode == "120000" else "file"), "UNSUPPORTED_FORMAT", "file kind")
            _require(entry.get("status") in ("included", "pointer_only"), "UNSUPPORTED_FORMAT", "file status")
            sha256 = entry.get("sha256")
            _require(isinstance(sha256, str) and bool(HEX.fullmatch(sha256)), "UNSUPPORTED_FORMAT", "file hash")
            blob = f"files/{sha256}"
            _require(entry.get("blob") == blob and blob in blobs, "HASH_MISMATCH", "missing blob")
            _require(type(entry.get("size")) is int and entry["size"] == blobs[blob], "HASH_MISMATCH", "blob length")
            records[path] = entry
        _require(set(blobs) == {rec["blob"] for rec in records.values()}, "UNSUPPORTED_FORMAT", "unused ZIP blobs")
        last_exclusion = None
        for exclusion in excluded:
            _require(isinstance(exclusion, dict), "UNSUPPORTED_FORMAT", "exclusion entry")
            path = exclusion.get("path")
            _require(_safe_path(path) and path.lower() not in used, "UNSAFE_PATH", "invalid exclusion path")
            _require(last_exclusion is None or last_exclusion < path, "UNSUPPORTED_FORMAT", "exclusion ordering")
            last_exclusion = path
            _require(exclusion.get("reason") in EXCLUSION_REASONS, "UNSUPPORTED_FORMAT", "exclusion reason")
            used.add(path.lower())
        _require(_tree_digest(files) == manifest.get("input_tree_digest"), "HASH_MISMATCH", "input tree digest")
        self.manifest = manifest
        self._blobs = frozenset(blobs)
        self._payload = payload
        self._files = records
        if self._embedded_metadata is not None:
            _require(self._embedded_metadata["source_commit"] == manifest["source_commit"],
                     "HASH_MISMATCH", "source commit header")
            _require(self._embedded_metadata["snapshot_complete"] ==
                     self._snapshot_summary()["snapshot_complete"],
                     "HASH_MISMATCH", "snapshot completeness header")

    def _blob(self, path: str) -> bytes:
        """Read a verified source blob on demand, without caching all raw files."""
        if self._plain is not None:
            start, size = self._plain_offsets[self._files[path]["blob"]]
            return self._plain[start:start + size]
        with zipfile.ZipFile(io.BytesIO(self._payload)) as archive:
            return archive.read(self._files[path]["blob"])

    def read_bytes(self, path: str) -> bytes:
        """Return independently verified raw Git source bytes for a known path.

        Unlike read(), this preserves binary data, encodings and line endings
        exactly. It never executes or follows symlinks in the source snapshot.
        """
        _require(isinstance(path, str) and path in self._files,
                 "PARTIAL_SNAPSHOT", "file not present in snapshot")
        return self._blob(path)

    def validate(self) -> dict:
        """All transport, manifest and content checks completed during construction."""
        return {"valid": True, "input_tree_digest": self.manifest["input_tree_digest"], "file_count": len(self._files)}

    def _snapshot_summary(self) -> dict:
        pointer_only = sum(entry["status"] == "pointer_only" for entry in self._files.values())
        by_reason = {reason: 0 for reason in sorted(EXCLUSION_REASONS)}
        for excluded in self.manifest["excluded"]:
            by_reason[excluded["reason"]] += 1
        if self._lean:
            by_reason["rgpignore"] += self._lean_counts["rgpignore"]
            by_reason["self_artifact"] += self._lean_counts["self_artifact"]
        # Self-referential output is deliberately outside the snapshot's input scope.
        # Deliberate RGP ignore exclusions are outside the selected source scope.
        unavailable = sum(
            count for reason, count in by_reason.items()
            if reason not in ("self_artifact", "rgpignore")
        )
        complete = pointer_only == 0 and unavailable == 0
        return {
            "status": "available" if complete else "partial",
            "snapshot_complete": complete,
            "included_files": len(self._files) - pointer_only,
            "pointer_only_files": pointer_only,
            "excluded_files": unavailable,
            "self_artifact_excluded": by_reason["self_artifact"],
            "intentionally_ignored_files": by_reason["rgpignore"],
            "excluded_by_reason": {reason: count for reason, count in by_reason.items() if count},
            "included_bytes": sum(entry["size"] for entry in self._files.values() if entry["status"] == "included"),
        }

    def overview(self) -> dict:
        return {
            "source_commit": self.manifest["source_commit"],
            "transport": "RGP/3.0" if self._lean else ("RGP/2.1" if self._embedded_metadata is not None
                         else ("RGP/2.0" if self._plain is not None else "RGP/1.x")),
            "embedded_instructions": self._lean_guide if self._lean
                                     else (self._embedded_metadata["instructions"]
                                           if self._embedded_metadata is not None else None),
            "input_tree_digest": self.manifest["input_tree_digest"],
            "file_count": len(self._files),
            "excluded": self.manifest["excluded"][:MAX_OVERVIEW_EXCLUSIONS],
            "excluded_truncated": len(self.manifest["excluded"]) > MAX_OVERVIEW_EXCLUSIONS,
            **self._snapshot_summary(),
            "index_coverage": "not_indexed",
            "graph_coverage": "not_indexed",
        }

    def coverage(self, scope: str = "snapshot") -> dict:
        if scope == "snapshot":
            return {"scope": "snapshot", **self.overview()}
        if scope in ("index", "graph"):
            return {"scope": scope, "status": "not_indexed", "reason": "no analyzer output in RGP 1.0"}
        if scope == "investigation":
            return {"scope": "investigation", "status": "unknown", "reason": "Reader cannot observe agent comprehension"}
        raise RgpError("UNSUPPORTED_FORMAT", "unknown coverage scope")

    def _cursor_context(self, operation: str, parameters: object) -> str:
        raw = json.dumps([self.manifest["input_tree_digest"], operation, parameters], ensure_ascii=False, separators=(",", ":"))
        return _digest(raw.encode("utf-8"))[:32]

    def _make_cursor(self, operation: str, context: str, path: str, line: int) -> str:
        state = json.dumps([1, operation, context, path, line], ensure_ascii=False, separators=(",", ":"))
        return base64.urlsafe_b64encode(state.encode("utf-8")).decode("ascii").rstrip("=")

    def _read_cursor(self, token: str | None, operation: str, context: str) -> tuple[str, int] | None:
        if token is None:
            return None
        _require(isinstance(token, str) and 0 < len(token) <= MAX_CURSOR and bool(re.fullmatch(r"[A-Za-z0-9_-]+", token)), "UNSUPPORTED_FORMAT", "invalid cursor")
        try:
            raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
            state = json.loads(raw)
        except (ValueError, UnicodeError, base64.binascii.Error) as exc:
            raise RgpError("UNSUPPORTED_FORMAT", "malformed cursor") from exc
        _require(isinstance(state, list) and len(state) == 5 and state[:3] == [1, operation, context], "UNSUPPORTED_FORMAT", "cursor does not match query or snapshot")
        _, _, _, path, line = state
        _require(isinstance(path, str) and path in self._files and type(line) is int and line >= 0, "UNSUPPORTED_FORMAT", "invalid cursor position")
        _require(self._make_cursor(operation, context, path, line) == token, "UNSUPPORTED_FORMAT", "noncanonical cursor")
        return path, line

    def tree(self, prefix: str = "", limit: int = 200, cursor: str | None = None) -> dict:
        _require(_safe_prefix(prefix), "UNSUPPORTED_FORMAT", "tree prefix")
        _require(type(limit) is int and 0 < limit <= 200, "RESOURCE_LIMIT", "tree limit")
        context = self._cursor_context("tree", prefix)
        position = self._read_cursor(cursor, "tree", context)
        _require(position is None or (position[1] == 0 and position[0].startswith(prefix)), "UNSUPPORTED_FORMAT", "tree cursor position")
        paths = [p for p in self._files if p.startswith(prefix) and (position is None or p > position[0])]
        page = paths[:limit]
        next_cursor = self._make_cursor("tree", context, page[-1], 0) if len(paths) > limit else None
        return {"paths": page, "next_cursor": next_cursor, "snapshot_status": self._snapshot_summary()["status"]}

    def read(self, path: str, start_line: int | None = None, end_line: int | None = None) -> dict:
        _require(isinstance(path, str) and path in self._files, "PARTIAL_SNAPSHOT", "file not present in snapshot")
        _require(start_line is None or (type(start_line) is int and start_line >= 1), "UNSUPPORTED_FORMAT", "start line")
        _require(end_line is None or (type(end_line) is int and end_line >= 0), "UNSUPPORTED_FORMAT", "end line")
        entry = self._files[path]
        data = self._blob(path)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            _require(start_line is None and end_line is None, "UNSUPPORTED_FORMAT", "no line coordinates for binary")
            _require(((len(data) + 2) // 3) * 4 <= MAX_RESULT, "RESOURCE_LIMIT", "binary output exceeds cap")
            return {"path": path, "encoding": "base64", "content": base64.b64encode(data).decode("ascii"), "status": entry["status"]}
        lines = text.splitlines(keepends=True)
        start = 1 if start_line is None else start_line
        end = len(lines) if end_line is None else end_line
        _require(1 <= start <= end + 1 and 0 <= end <= len(lines), "UNSUPPORTED_FORMAT", "invalid line range")
        content = "".join(lines[start - 1:end])
        _require(len(content.encode("utf-8")) <= MAX_RESULT, "RESOURCE_LIMIT", "result too large")
        return {"path": path, "encoding": "utf-8", "content": content, "start_line": start, "end_line": end, "status": entry["status"]}

    def search(self, query: str, paths: list[str] | None = None, limit: int = 50,
               cursor: str | None = None, *, prefix: str = "") -> dict:
        """Find source lines, optionally restricted to a lexical path prefix."""
        _require(isinstance(query, str), "RESOURCE_LIMIT", "search query")
        try:
            query_bytes = len(query.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise RgpError("UNSUPPORTED_FORMAT", "search query is not valid UTF-8") from exc
        _require(0 < query_bytes <= 1024, "RESOURCE_LIMIT", "search query")
        _require(type(limit) is int and 0 < limit <= 100, "RESOURCE_LIMIT", "search limit")
        _require(_safe_prefix(prefix), "UNSUPPORTED_FORMAT", "invalid search prefix")
        _require(paths is None or (isinstance(paths, list) and len(paths) <= MAX_ENTRIES and all(isinstance(p, str) for p in paths)), "UNSUPPORTED_FORMAT", "paths")
        if paths is not None:
            _require(all(p in self._files for p in paths), "PARTIAL_SNAPSHOT", "requested search path is not in snapshot")
            _require(all(p.startswith(prefix) for p in paths), "UNSUPPORTED_FORMAT", "search paths outside prefix")
        selected = (sorted(p for p in self._files if p.startswith(prefix))
                    if paths is None else sorted(set(paths)))
        # Preserve existing no-prefix cursor contracts. A scoped cursor also
        # binds the literal prefix, even when two prefixes select the same set.
        parameters = [query, selected if paths is not None else None]
        if prefix:
            parameters.append(prefix)
        context = self._cursor_context("search", parameters)
        position = self._read_cursor(cursor, "search", context)
        _require(position is None or (position[0] in selected and position[1] >= 1), "UNSUPPORTED_FORMAT", "search cursor position")
        page = []
        last = None
        # Reuse one ZIP handle for legacy transports; RGP/2.0 reads the
        # verified source spans directly from the original plaintext buffer.
        source = (zipfile.ZipFile(io.BytesIO(self._payload)) if self._plain is None
                  else contextlib.nullcontext())
        with source as archive:
            for path in selected:
                if position is not None and path < position[0]:
                    continue
                try:
                    raw = (archive.read(self._files[path]["blob"]) if archive is not None
                           else self._blob(path))
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                for number, line in enumerate(text.splitlines(), start=1):
                    if position is not None and path == position[0] and number <= position[1]:
                        continue
                    if query not in line:
                        continue
                    if len(page) >= limit:
                        return {"matches": page, "next_cursor": self._make_cursor("search", context, last[0], last[1]), "snapshot_status": self._snapshot_summary()["status"]}
                    # Use a bounded match-aware window, not the first bytes of
                    # a potentially long minified/source line.
                    excerpt = _matching_excerpt(line, query)
                    page.append({"path": path, "line": number, "excerpt": excerpt})
                    last = (path, number)
        return {"matches": page, "next_cursor": None, "snapshot_status": self._snapshot_summary()["status"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Trusted stdlib-only RGP reader")
    parser.add_argument("--artifact", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("overview")
    commands.add_parser("validate")
    coverage = commands.add_parser("coverage")
    coverage.add_argument("--scope", choices=("snapshot", "index", "graph", "investigation"), default="snapshot")
    tree = commands.add_parser("tree")
    tree.add_argument("--prefix", default="")
    tree.add_argument("--limit", type=int, default=200)
    tree.add_argument("--cursor")
    read = commands.add_parser("read")
    read.add_argument("--path", required=True)
    read.add_argument("--start-line", type=int)
    read.add_argument("--end-line", type=int)
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--path", action="append", dest="paths")
    search.add_argument("--prefix", default="")
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--cursor")
    args = parser.parse_args()
    try:
        reader = Reader(args.artifact)
        if args.command == "overview":
            result = reader.overview()
        elif args.command == "validate":
            result = reader.validate()
        elif args.command == "coverage":
            result = reader.coverage(args.scope)
        elif args.command == "tree":
            result = reader.tree(args.prefix, args.limit, args.cursor)
        elif args.command == "read":
            result = reader.read(args.path, args.start_line, args.end_line)
        else:
            result = reader.search(args.query, args.paths, args.limit, args.cursor, prefix=args.prefix)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except RgpError as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
