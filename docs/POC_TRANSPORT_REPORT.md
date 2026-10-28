# Gate A — transport proof of concept

**Status: NOT VERIFIED / Gate A open** (29 October 2026).

Repository: `iivkis/rgp`. The Rust producer and independent Python stdlib Reader implement byte-level transport self-checks and CI local round-trip tests. This does **not** prove the GitHub connector delivers a large text artifact intact.

| Question | Observation | Status |
|---|---|---|
| Local Rust ZIP + text encoding | Implemented; CI round-trip is configured | Pending CI execution |
| Python stdlib independent decode and SHA/byte checks | Implemented; tests configured | Pending CI execution |
| Connector 100 KiB / 5 MiB / 20 MiB / 50 MiB | No measured runs | Unverified |
| Repeated connector retrieval, truncation and byte/range accuracy | No measured runs | Unverified |
| Calls, transfer bytes, tokens, latency and memory | No measured runs | Unverified |
| `max_supported_artifact_bytes` | Not established | Unverified |
| `max_supported_repo_bytes` | Not established | Unverified |

**Gate A decision: HOLD.** Do not claim a supported connector size or implement semantic graph indexing until the chosen connector's actual retrieval path has been measured on repeated samples. The generator's 64 MiB compressed-payload cap is a local resource policy, not a connector promise.
