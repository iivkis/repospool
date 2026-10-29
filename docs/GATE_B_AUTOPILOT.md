# Gate B autonomous diagnostic launch (one shot)

This workflow starts automatically when `.github/workflows/gate-b-one-shot.yml` is
committed to `main`. It needs no manual workflow dispatch. It checks one pinned
source-code task for each of three preregistered repositories using the existing
Actions corpus (`40767794459`) and independent committed RGP SHA-256 anchors.

## Safety and billing limits

- The download, RGP validation, pinned-commit check and paired-run `--dry-run`
  execute without calling a language model.
- Only if the repository already has an `OPENAI_API_KEY` Actions secret will the
  workflow run the **non-scoring** paired diagnostic (GitHub REST vs local RGP).
- Every matrix job uses the same pinned `gpt-4.1-mini-2025-04-14` API model.
- Each arm is limited to **four** Responses calls and **8,000 total input/output
  tokens**; the entire three-repository run has an upper limit of **24 model
  calls**, subject to the runner's timeout and API policies.
- Raw model answers, tool traces and secrets are not uploaded. The GitHub Actions
  step summary exposes only the diagnostic status, tool counts, provider-reported
  token counts and elapsed time. Partial results may remain only on the runner.
- The `push` path filter applies **only to the workflow file itself**. Other
  repository changes do not rerun or bill the diagnostic.

## Scientific limitation

The two diagnostic arms are GitHub REST and an already-downloaded local RGP.
They **are not** the preregistered connected-app GitHub connector vs Actions
artifact retrieval arms. These results must never be imported into the Gate B
scorer or added to the 72 official agent trials. If the OpenAI API secret is
missing or the model API is unavailable, no model measurements are claimed.

**Gate B remains HOLD until separately verified real paired trials exist.**
