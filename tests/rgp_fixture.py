"""RGP/0.1.0 synthetic UTF-8 artifacts for tests."""

def make_artifact(files=None, *, ignored=0, excluded=(), flags=None):
    files = {"src/a.py": b"needle from src A\nneedle again\n"} if files is None else files
    flags = flags or {}
    guide = b"Generic RGP snapshot instructions."
    index = "".join(f"F\t{path}\t{len(raw)}" + (f"\t{flags[path]}" if path in flags else "") + "\n" for path, raw in sorted(files.items()))
    if ignored:
        index += f"I\t{ignored}\n"
    index += "S\t1\n"
    index += "".join(f"X\t{path}\t{reason}\n" for path, reason in sorted(excluded))
    header = f"RGP/0.1.0\nguide_bytes={len(guide)}\nindex_bytes={len(index.encode())}\n--\n".encode()
    bodies = b"".join(f"FILE\t{path}\n".encode() + raw + b"\n" for path, raw in sorted(files.items()))
    return header + guide + b"\n" + index.encode() + b"\n" + bodies + b"END RGP\n"


def three_scope_artifact():
    return make_artifact({"src/a.py": b"needle from src A\nneedle again\n",
                          "src/sub/b.py": b"ignore\nneedle from sub B\n",
                          "src2/unrelated.py": b"needle from src2\n",
                          "tests/test_a.py": b"needle from tests\n"})
