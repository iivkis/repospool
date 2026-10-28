"""Generate deterministic synthetic transport fixtures; no network operations.

Use connector-specific operations externally to retrieve the resulting bytes and
compare their length + SHA-256 over at least three independent trials per size.
"""
import argparse
import hashlib
import io
import json
import zipfile
from pathlib import Path


SIZES = (100 * 1024, 5 * 1024 * 1024, 20 * 1024 * 1024, 50 * 1024 * 1024)


def make_fixture(size: int) -> bytes:
    # Deterministic pseudorandom content prevents trivial compression.
    payload = bytearray()
    block = 0
    while len(payload) < size:
        payload.extend(hashlib.sha256(f"rgp-transport-{size}-{block}".encode()).digest())
        block += 1
    content = bytes(payload[:size])
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("probe.bin", content)
    zipped = stream.getvalue()
    import base64
    encoded = base64.b64encode(zipped).decode("ascii")
    header = f"RGP/1.0\npayload_length={len(zipped)}\npayload_sha256={hashlib.sha256(zipped).hexdigest()}\nbase64_line_width=76\n--\n"
    return (header + "".join(encoded[n:n + 76] + "\n" for n in range(0, len(encoded), 76)) + "END RGP\n").encode()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    metrics = []
    for size in SIZES:
        result = make_fixture(size)
        path = args.output_dir / f"probe-{size}.rgp"
        path.write_bytes(result)
        metrics.append({"file": path.name, "input_bytes": size, "transport_bytes": len(result), "sha256": hashlib.sha256(result).hexdigest()})
    (args.output_dir / "expected.json").write_text(json.dumps(metrics, indent=2) + "\n")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
