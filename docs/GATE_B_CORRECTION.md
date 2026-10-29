# Gate B correction: no model runtime

Project direction: **The assistant in the active conversation performs the
engineering and source-analysis work.** RGP is a static snapshot format and
Reader, not an agent orchestration service. Neither the package nor GitHub
Actions needs a model, an inference API, a key, billing, or a substitute model.

Removed from HEAD: OpenAI Responses client, paired-model test harness,
automatic model-evaluation workflows, model-token telemetry, and the
associated tests/docs. CI remains responsible for deterministic correctness
checks. `Gate B pinned corpus preparation` generates fixed RGP snapshots; it
never calls any model.

The benchmark was redesigned as a human-reviewable record of the *assistant's*
paired GitHub/RGP source-reading exercises, versioned as plan v2. Old
plan/evaluator artifacts are retained in Git history, not used for decisions.
Token savings cannot be confirmed from unreported counts, and the benchmark
remains HOLD until actual paired source tasks are performed.
