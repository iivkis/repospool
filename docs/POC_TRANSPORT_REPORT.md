# Gate A — transport proof of concept

**Status: HOLD — connector end-to-end tests pending** (29 October 2026).

Repository: `iivkis/rgp`. The Rust generator and independent stdlib Python Reader passed prior GitHub CI round-trip checks. The test harness now generates **valid, incompressible, independently verified RGP fixtures**, rather than a non-RGP ZIP containing `probe.bin`.

| Test or property | Evidence | Status |
|---|---|---|
| Local Rust + Python end-to-end | Existing CI and independent Reader tests | PASS (local/CI only) |
| Synthetic 100 KiB/5 MiB/20 MiB/50 MiB RGP | Deterministic generator and standalone validation command | Implementation prepared; connector not yet tested |
| GitHub Actions workflow artifacts | `.github/workflows/transport-probe.yml`, manual matrix | Workflow added; needs remote run |
| Actual connector `fetch_file`, `fetch`, artifact download | Three independent retrieves per supported tier | NOT MEASURED |
| Range boundaries, silent truncation, order, duplicates, timeouts | Connector-specific experiments | NOT MEASURED |
| Calls, transfer bytes, tokens, latency, peak memory | Raw connector trial records | NOT MEASURED |
| `max_supported_artifact_bytes` / `max_supported_repo_bytes` | Must derive from measured mode-specific failures and passes | UNKNOWN |

A fixture is a synthetic `RGP/1.0`, not a real Git snapshot; its hashes are recorded in `expected.json`. The generator's 64 MiB *decoded ZIP* cap and 128 MiB *outer RGP text* cap are local safety policies, **not** connector guarantees. Source Actions artifact download and GitHub Contents API are different transport contracts.

See [protocol](TRANSPORT_PROBE_PROTOCOL.md) for reproducible steps and strict pass/fail requirements. Until actual connector reads verify the same bytes in the agent sandbox, **Gate A remains HOLD**.
