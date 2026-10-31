# Product contract: RepoSpool moves bytes, the agent reasons

RepoSpool exists to make **any Git repository** efficient to inspect for a coding
agent. It is not a model, classifier, analyzer, search engine, knowledge
graph, language server or tool adapter. Its intelligence is deliberately
zero. The agent decides what source means.

## Required mechanical behavior

1. Read an explicitly selected **committed Git revision**.
2. Apply explicit `.repospoolignore`, Git metadata, text and safety limits.
3. Preserve the exact selected UTF-8 source bytes without interpretation.
4. Provide a compact, stable file index with exact byte lengths, source line
   coordinates, flags and transparent exclusion coverage.
5. Embed a fixed, repository-independent guide so one `.repospool` is enough to
   read any included source without extra software.
6. Produce deterministic output on the same Git tree and chosen revision.
7. Optionally include a sanitized GitHub locator and **one** Git commit ID;
   never include per-file hashes or generated prose explaining file purpose.
8. Make structural validity verifiable without executing archived code.

## Explicitly out of scope

- Source language and framework detection, even when filenames look familiar
- Automatic file roles such as ENTRY, FORMAT, BUILD, FILTER, TEST, INDEX
- Symbols, ASTs, imports, call graphs, dependency graphs or module semantics
- Relevance scoring, task-specific ordering, inferred coverage importance
- AI summaries, embeddings, databases, Python readers, MCP or sandbox runtimes

RepoSpool may record **bounded, lexical previews** of ignored paths; their ordering
is deterministic and is not a ranking of importance. A file not selected by
the user is missing context, not an irrelevant file.

## Trust and quality

The source revision and optional GitHub locator are **hints**, not proof.
Agents must independently confirm repository identity, revision and live HEAD
before pushing changes. RepoSpool provides no authenticity guarantee.

Use measurable outcomes, not metadata quantity, to evaluate the format:
source-finding accuracy, connector calls, tokens, and successful edits.
Research archives are available in Git history, not the active working tree.
