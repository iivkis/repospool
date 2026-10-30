## What changed

## Goal-level acceptance (agent-owned)
- [ ] Observable user goal and expected results were stated before the change
- [ ] Implementing agent generated a real RGP from a pinned commit, verified it and used the Reader to answer a predefined repository task (when RGP behavior changed)
- [ ] Agent compared results with source truth and recorded commit/SHA-256, exact commands, observed outcomes and failures
- [ ] If execution was blocked: clearly marked NOT RUN/BLOCKED, with no claim of completion
- [ ] Documentation-only change: links and consistency checked; no invented test executions

## Tests
- [ ] `just check` or equivalent tests
- [ ] Reproducible RGP output checked if serialization changed
- [ ] New negative cases for untrusted inputs

## Safety and product gates
- [ ] No secrets or private source snapshots committed/uploaded
- [ ] No semantic completeness or connector-transport claims without evidence
- [ ] Gate B remains HOLD unless its specific evidence requirements are fulfilled

See [AGENTS.md](../AGENTS.md) and [Product Roadmap](../docs/PRODUCT_ROADMAP.md).
