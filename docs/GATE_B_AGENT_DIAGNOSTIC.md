# Gate B: executable agent diagnostic (not a gate-scoring trial)

The production Gate B preregistration compares the **connected ChatGPT GitHub app**
with an **Actions RGP artifact**. This pilot is intentionally different: it runs
the same OpenAI Responses model against **GitHub REST** or an already-downloaded,
verified **local RGP**. These are diagnostic adapters, not the preregistered
arms. **Its traces MUST NOT be imported into `gate_b_capture.py` or counted
among the 72 required Gate B trials.**

## Why this exists

The model receives identical tool names (`list_files`, `read_file`), prompt,
maximum output tokens and source commit in either diagnostic arm. Real model
usage is taken from the provider's response receipts, including cumulative
input and output tokens over every function-calling iteration. GitHub REST
network byte totals and timings are recorded separately; local RGP reads are
not represented as network calls. Tool responses have a 12 KB cap and reads
are limited to 100 lines. Both modes only read public, pinned source code.

The diagnostic intentionally **does not calculate percent savings**: cached
GitHub tree reads and a locally present archive have different preparation
costs, and neither transport matches the production connected-app contract.
Even perfect diagnostic results cannot give Gate B `GO`.

## Run a real model diagnostic

Requires an OpenAI API key with separate API billing; ChatGPT subscription
alone does not supply an API key. The script uses Python's standard library,
no `pip`/`uv` runtime packages, and does not save credentials. Only explicitly
requested runs make model API calls (and incur API charges).

```sh
nix develop
export OPENAI_API_KEY='...'
# Optional, for authenticated GitHub REST queries (never written to trace):
export GH_TOKEN='...'
python3 scripts/gate_b_agent_pilot.py \
  --task rgp-transport-hash \
  --arm github_rest_diagnostic \
  --model YOUR_EXACT_API_MODEL_ID \
  --token-budget 16000 \
  --output /tmp/rgp-rest-diagnostic.json

# Download a verified RGP artifact via the connected app first; use SHA-256
# from a separate trusted source, not provenance inside the ZIP.
python3 scripts/gate_b_agent_pilot.py \
  --task rgp-transport-hash \
  --arm rgp_local_diagnostic \
  --model YOUR_EXACT_API_MODEL_ID \
  --rgp /tmp/REPO_CONTEXT.rgp \
  --trusted-rgp-sha256 THE_PINNED_64_CHARACTER_SHA256 \
  --token-budget 16000 \
  --output /tmp/rgp-local-diagnostic.json
```

Outputs contain the final answer, answer SHA, provider token usage receipts,
individual tool requests and transport telemetry. These are **ungraded
traces**; an independent assessor still needs to review source citations
against the frozen rubric. Do not publish raw traces before inspecting them
for sensitive text. Unavailable provider usage or broken/incomplete model
responses abort without writing a misleading success report.

## Verification and limits

```sh
task benchmark:pilot:test
```

This command runs offline synthetic tests of the Responses function-call loop,
error handling, pinned-commit enforcement, hash failure, token receipts and
non-eligibility for Gate B. Synthetic tests do not call a provider or count as
agent measurements. A diagnostic run with a real API key is required before
claiming that the live model adapter is validated.

The production Gate B still requires an actual connected-app agent runtime,
full telemetry, independent grading and 72 properly randomized paired runs.
