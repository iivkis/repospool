# RGP (Repository Graph Pack)

Reproducible, versioned **single-file repository snapshots** designed to be read by AI agents without cloning a repository or installing packages inside their Python sandbox.

> **Status: v0.1 / transport PoC.** 12 independent connector downloads of GitHub Actions artifacts (100 KiB–50 MiB outer RGP) passed byte-level verification in the Python sandbox. This is **conditional Gate A success for the Actions-artifact adapter only**, not proof that `fetch_file` delivers the canonical root file. Syntax/semantic indexing is intentionally not claimed.

## Получение свежего контекста для работы Киры

[Workflow `Kira sandbox RGP snapshot`](.github/workflows/kira-sandbox-rgp.yml)
собирает и проверяет RGP **при каждом обновлении `main`**,
публикует независимый от скачанного ZIP SHA-256 в Actions summary
и выгружает короткоживущий ZIP `kira-sandbox-rgp-<commit>`.
Теперь Кира может получить **снимок актуального кода**, а не
постоянно использовать старые замороженные benchmark-артефакты.
[Подробный порядок загрузки и проверки](docs/SANDBOX_SOURCE_WORKSPACE.md).
Перед изменением GitHub-кода сравнивать `--expected-commit` с
целевой ревизией GitHub и повторно проверять HEAD перед отправкой.

## Практический инструмент для песочницы Киры: локальные исходники

[Новый безопасный экспорт](docs/SANDBOX_SOURCE_WORKSPACE.md)
превращает независимо проверенный RGP в **обычную локальную папку
с исходниками**, доступную стандартным файловым инструментам Python.
Это позволяет Кире быстро изучать GitHub-репозиторий в своей
песочнице без `git clone`, без обязательного MCP и без
извлечения каждого файла отдельным вызовом Reader.

```sh
python3 python/rgp_sandbox.py \
  --artifact /path/to/REPO_CONTEXT.rgp \
  --trusted-sha256 <trusted-sha256-from-separate-source> \
  --expected-commit <GitHub-commit-SHA> \
  --output /tmp/rgp-source-workspace
```

Пределы по умолчанию: 1000 исходных файлов / 32 MiB.
Проверка внешнего SHA-256 сама по себе не доказывает, что исходники
актуальны для задачи. Если commit внутри RGP не совпадает с
`--expected-commit`, утилита возвращает `STALE_SNAPSHOT`
и не создаёт локальную папку. SHA целевой ревизии Кира должна
получить из GitHub отдельно; сама утилита не делает сетевых вызовов.
Данные неисполняемые, Git-симлинки создаются обычным текстом,
неполные снимки требуют явного согласия. Экспортированная папка
**не является Git-репозиторием**; реальные правки делаются через GitHub.
[Реальная проверка файлов на трёх Git-коммитах](scripts/stage1_sandbox_task_trial.py)
проверяет все файлы байт-в-байт, а не только цитаты.
Это инфраструктура для снижения ошибок Киры, не доказательство
снижения самих ошибок. Gate B = HOLD.

## Главная цель: Кира разрабатывает RGP для самой себя

**Основной пользователь RGP — Кира, ChatGPT coding-ассистент, работающий
с GitHub-проектами в собственной Python-песочнице.** RGP нужен, чтобы
Кира получала достоверный контекст проекта, лучше понимала структуру
кода и последствия изменений, **реже ошибалась и эффективнее
выполняла реальные задачи разработки**.

RGP — проверяемый *неизменяемый снимок для понимания*, а не редактор
GitHub и не LLM-runtime. Главный путь — использовать доверенный
Python stdlib Reader **прямо в песочнице Киры**, затем менять код
GitHub-инструментами и проверять результат. MCP — **необязательный
адаптер**, а не цель проекта и не автоматически доступный инструмент
чата. Основной масштаб применения — небольшие репозитории
(от десятков до примерно 1000 файлов), а не синтетические 50k.

**Критерий успеха:** меньше неверных решений и правок, лучше
обоснованный выбор файлов/тестов, успешная верификация изменения.
Затраты на вызовы GitHub, передачу исходников, токены и время
измеряются честно, включая доставку RGP. Если GitHub без RGP
оказывается лучше на конкретной задаче, так и фиксировать.

**[Каноническая продуктовая цель и обязательный dogfooding](docs/PRODUCT_NORTH_STAR.md).**
[Контракт самого агента](AGENTS.md) требует проверять пользу
RGP на настоящей задаче, не только открывать архив. **Gate B = HOLD.**

## Product direction and agent accountability

The canonical [three-stage product roadmap](docs/PRODUCT_ROADMAP.md)
tracks the path from the transport PoC through useful indexed context
to product v1.0. The detailed [v1.1 technical plan](docs/ROADMAP_v1.1.md)
remains background, not an alternative product release checklist.

All contributors and coding agents follow
[Pragmatic Engineering, KISS and DRY](docs/ENGINEERING_PRINCIPLES.md).
Under the [agent contract](AGENTS.md), the agent implementing an RGP
change must also generate, verify and **use the resulting RGP** to
complete and check predefined repository tasks, recording observable
evidence. Passing archive validation alone is not goal-level success;
independent reviews can audit claims but do not replace agent testing.
See the [31 Oct 2026 review](docs/REVIEW_2026-10-31.md) for findings,
limitations and priorities.

## Подключить RGP как настоящие инструменты coding-агента

Экспериментальная [интеграция MCP](docs/RGP_MCP_AGENT.md)
позволяет модели напрямую вызывать read-only `rgp_status`,
`rgp_tree`, `rgp_search`, `rgp_read` и `rgp_context`
через локальный stdio-сервер. Никаких импортов Python со стороны
модели или исполнения кода из снимка.

```sh
python3 python/rgp_mcp.py --artifact /path/to/verified/REPO_CONTEXT.rgp
```

Команда запускается **MCP-клиентом**, который говорит с ней через
JSON-RPC. Подключение и ограничения доверия в документации.
[Реальный MCP E2E на трёх репозиториях](scripts/stage1_mcp_agent_trial.py)
проверяет полный цикл запросов и сверяет каждую цитату с pinned Git.
[Фактический результат 12/12, 72/72 фрагмента](benchmarks/gate_b/MCP_AGENT_TRIAL_2026-10-31.md)
зафиксирован с отдельными локальными MCP-вызовами/байтами, без
выдуманного модельного A/B.
Это проверка **работоспособности инструментов**, а не независимый
LLM A/B или доказательство экономии токенов. Gate B пока HOLD.

## Try RGP as an agent, not a ZIP benchmark

The [small-repository agent-first pilot](docs/AGENT_FIRST_PILOT.md)
uses **three actual public repos with 46, 93 and 50 source files**:
an LLM located search-cursor guards, JSON serializer/tests and
timestamp expiry logic using the downloaded RGP source, then
cross-checked the exact coordinates with pinned GitHub source.
This shows that RGP can supply useful evidence for an answer;
**it does not yet prove the answer is better than direct GitHub**.

With a separately verified RGP in hand, let the model choose a few
search terms and retrieve bounded, citable source context in one
command, using the trusted project Reader:

```sh
python3 python/rgp_agent_context.py \
  --artifact /path/to/REPO_CONTEXT.rgp \
  --term '_read_cursor' --term 'test_search_keyset_cursor_scope_and_exhaustion' \
  --max-files 4 --max-bytes 12000
```

The returned JSON preserves source commit, paths and line ranges,
honestly reports lexical-only retrieval, partial snapshots and
missing semantic coverage. Archived code is never executed. The
[three-repository agent-context CI walkthrough](scripts/stage1_agent_task_pilot.py)
checks the answer-ready passages against original pinned Git.

**Product priority: real LLM use and independent A/B, not more
50,000-file optimization. Stage 1 Gate B stays HOLD.**

## 12 реальные задачи агента на небольшом коде

[Реальный опыт модели](docs/AGENT_VALUE_TRIAL_2026-10-31.md)
выполнил **12 зафиксированных задач** в публичных репозиториях
с 46, 93 и 50 файлами. Через скачанный RGP модель нашла и
объяснила проверяемые участки исходников; [CI проверил
72 исходных фрагмента против закреплённого Git](scripts/stage1_agent_task_pilot.py).

Честный итог: в исследованном сценарии **6 целевых GitHub-операций
подготовки/получения RGP против 13 запросов исходных файлов**,
но **396 854 байт трёх ZIP против 336 734 исходных байт 13 файлов**.
Это *не* доказательство снижения токенов или улучшения ответов
модели. Независимый слепой A/B не завершён, поэтому Gate B остаётся
**HOLD**. Дальнейший приоритет: реальные задачи модели, качество
ответов и полная стоимость, а не файлы на 50 тысяч.

## Stage 1 implementation and functional acceptance

[Stage 1 implementation notes](docs/STAGE1_IMPLEMENTATION.md) describe
the production-hardening work in progress: batched Git blob retrieval,
collision-free staging for concurrent generation, a lower-memory Reader,
and a repeatable **real-RGP, goal-level acceptance** against pinned Git
source truths. Run `task stage1:acceptance` (or
`python3 scripts/stage1_acceptance.py --rust-cli target/debug/rgp`
after building). CI uploads sanitized metadata for this acceptance.

This verifies functional source tasks, **not** a Gate B decision or
connector transport claim. Gate B remains **HOLD**.

The [Stage 1 stress suite](docs/STAGE1_STRESS.md) also exercises 2,000
small source files, 12 MiB compressible/incompressible files and a
16 MiB-per-file boundary using temporary pinned Git commits.
When a valid source blob compresses beyond the Reader's anti-zip-bomb
ratio, the generator now uses deterministic ZIP_STORED for that member,
without weakening validation. See the separate CI stress matrix for
actual evidence and resource measurements.

For the next large Stage 1 diagnostic, the
[three-repository frozen corpus protocol](docs/STAGE1_CORPUS_DIAGNOSTIC.md)
exercises all 12 preregistered task reference sets across pinned
Rust/Python repositories, checking actual Reader search/read answers
against Git source and collecting observed runtime/RSS metrics. Run
`task stage1:corpus REPOSITORY=rgp` (or `serde_json` /
`itsdangerous`). The corresponding Actions matrix retains
**metadata-only evidence**. It does not claim Gate B success.

## Development

The primary implementation is **Rust**. The trusted Reader and bootstrap interface use **Python stdlib only**. `uv` is for developer tooling, never a runtime dependency of the agent.

```sh
nix develop            # Rust, Cargo, Clippy, rustfmt, Python, uv, just
just check             # fmt, clippy, Rust and Python tests
cargo run -- generate --repo . --output REPO_CONTEXT.rgp
cargo run -- verify --artifact REPO_CONTEXT.rgp --repo .
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp overview
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp tree
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp read --path README.md
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp search TODO --prefix src/
```

The Git source is a **fixed commit** (default `HEAD` resolved before enumeration); modified and untracked working-tree files are never included. `REPO_CONTEXT.rgp` is excluded from input hashing, and potentially sensitive paths are conservatively excluded and reported. Raw blob bytes are not normalized. Symlinks are preserved as link text rather than followed; submodules are listed as exclusions. Git LFS pointers remain marked `pointer_only`. A partial snapshot **must not** be called complete.

`cargo run -- inspect --artifact REPO_CONTEXT.rgp` prints validated metadata. Use `--rev <commit>` with `generate` to pin another source revision.

## CI and workflow

`.github/workflows/ci.yml` runs Rust format, Clippy, tests, Python stdlib tests and a deterministic cross-language snapshot smoke test for pull requests and main. `.github/workflows/security.yml` runs a scheduled dependency audit. Both have read-only repository permissions. Dependabot maintains Cargo and Actions updates. `nix flake check` verifies the flake evaluates; CI exercises `nix develop` as a separate environment smoke test.

See [format specification](docs/RGP_FORMAT_SPEC.md), [security model](docs/RGP_SECURITY_MODEL.md), and [Gate A report](docs/POC_TRANSPORT_REPORT.md). CI round-trip alone is insufficient: the report separately documents 12 verified connector retrievals and the limited channel they validate.

## Gate A connector transport experiments

Use `nix develop` and `uv run --no-project python scripts/transport_probe.py generate --output-dir /tmp/rgp-gate-a` to make valid RGP fixtures at ~100 KiB, 5 MiB, 20 MiB and 50 MiB **outer text size**. Validate independently with `scripts/transport_probe.py verify --input-dir /tmp/rgp-gate-a --expected /tmp/rgp-gate-a/expected.json`. The manually triggered [Gate A transport fixtures](.github/workflows/transport-probe.yml) workflow uploads three-day synthetic test artifacts; it never commits large fixtures or repository source code. See the [experimental protocol](docs/TRANSPORT_PROBE_PROTOCOL.md) and [measurements report](docs/POC_TRANSPORT_REPORT.md). Local success is not connector proof.

## Trusted bootstrap from a GitHub Actions download

After the connected GitHub app supplies an actual ZIP file, run the trusted stdlib adapter in the Python sandbox (no `pip`, `uv`, or `git clone` required):

```sh
python3 python/rgp_bootstrap.py \
  --actions-zip /path/to/downloaded-actions-artifact.zip \
  --output /tmp/REPO_CONTEXT.rgp \
  --trusted-sha256 <out-of-band-verified-RGP-SHA256>
python3 python/rgp_reader.py --artifact /tmp/REPO_CONTEXT.rgp overview
```

Bootstrap refuses path traversal, duplicate/extra ZIP members, malformed manifests, resource-limit violations and hash mismatches; it does not execute ZIP content. The optional `expected.json` *inside* the ZIP is not an authenticity anchor. A SHA-256 supplied from the same untrusted download does not independently prove provenance.

## Stage 1 authenticated delivery checks

The [Stage 1 delivery protocol](docs/STAGE1_DELIVERY.md) replays
the supported GitHub Actions ZIP adapter using an RGP SHA-256 pinned in
the Git repository **outside the downloaded ZIP**. The three-repo
`Gate B pinned corpus preparation` workflow now runs the ZIP bootstrap,
checks actual Reader source-navigation answers against independent
pinned Git data and publishes a metadata-only test report. The agent
still must retrieve and test the actual connected-app artifact separately.
Source-byte integrity is not proof of independent answer grading.

The [observed connected-delivery audit](benchmarks/stage1/CONNECTED_DELIVERY_2026-10-31.md)
records three real GitHub app ZIP downloads, independent RGP checksums
and 13 pinned Git blob-identity comparisons; the operation history
belongs to this Stage 1 delivery check, **not** the earlier Gate B pilot.

A [Gate B review handoff](docs/GATE_B_REVIEW.md) now contains a
**19-operation provider receipt ledger** (13 pinned GitHub fetches plus
6 RGP setup/acquisition calls) and a single fail-closed decision dossier
alongside the 24-answer masked reviewer packet. The original pilot's
reported 53.85% call reduction is still an unauthenticated claim,
not a licensed GO decision; see [Issue #25](https://github.com/iivkis/rgp/issues/25).

## Stage 1 high-cardinality source trees

The [50k-member capacity contract](docs/STAGE1_SCALE.md) now ensures
the generator never produces RGPs that its own 50,000-ZIP-member
verifier refuses: **49,999 tracked files plus the manifest** are the
maximum included paths, with later paths explicitly recorded as
`entry_limit` exclusions in a **partial** snapshot. A reproducible
real Git→RGP→Reader stress test commits 50,005 unique blobs and checks
full pagination, exact source reads, negative cases, determinism and
observed resource cost. This is not Gate B GO.

## Stage 1 memory-bounded generation

The [bounded-memory producer](docs/STAGE1_MEMORY.md) now stages
deterministic ZIP files and streams the Base64 transport, with
no change to RGP/1.0 output bytes. [Actual paired same-runner
measurements](benchmarks/stage1/MEMORY_AB_2026-10-31.md) show
about **52% lower peak generator RSS** for 12MiB blobs and the
50k-member workload, but **large-tree generation is slower**
(5.46s vs 2.49s in the paired test). The gain is in memory,
not proven universal runtime speed. Gate B remains HOLD.

## Stage 1 adaptive producer throughput

The [adaptive ZIP writer](docs/STAGE1_ADAPTIVE_ZIP.md) avoids the
high-cardinality seek overhead of always-disk-backed ZIP production
while keeping streamed Base64 output and strict resource validation.
[Same-runner A/B evidence](benchmarks/stage1/ADAPTIVE_ZIP_2026-10-31.md)
on 50,005 Git files observed **4.37 → 2.13 s** with peak Rust
memory **88.5 → 103.2 MiB**. This is a documented, bounded-memory
tradeoff rather than a claim of universal acceleration. The five
synthetic stress cases include a 36 MiB disk-fallback source test;
RGP output stays byte-identical.

## Gate B: assistant-only source comparison (protocol v2)

The **assistant in the active conversation** writes the code, checks the source
and evaluates the results. The repository has no model runtime, inference API,
API key, automated agent, or model-related CI dependency. This replaces an
earlier, incorrect benchmark harness; see [correction](docs/GATE_B_CORRECTION.md).

The [12-task plan](benchmarks/gate_b/plan.json) covers three pinned Git
repositories. The assistant compares reading pinned code with the connected
GitHub tools and reading identical source through the trusted RGP Reader.
The [methodology](docs/RGP_BENCHMARK_BASELINE.md) counts actual connector
operations, **including acquisition of the RGP**, and guards correctness.
Unknown token counts are not reported as zero. No external models are started.

```sh
task benchmark:plan
task benchmark:report     # no observations => HOLD
task benchmark:test
# after downloading the three already prepared RGP snapshots:
python3 scripts/gate_b_rgp_audit.py --rgp-dir /path/to/pinned-rgp
```

[Source integrity audit](benchmarks/gate_b/source_audit_2026-10-29.json):
3/3 complete RGP snapshots, 12/12 task reference sets present.

A [single-session Gate B diagnostic](benchmarks/gate_b/PILOT_2026-10-29.md)
now records 24 source-answer observations, anchored to 13 independently
GitHub-verified source blobs. In this **unblinded, assistant-scored** run,
18 GitHub source fetches were compared with 6 RGP acquisition/preparation
calls including independent Reader setup (66.67% fewer connector calls); per-task local Reader operations
required no GitHub calls. No token, timing or independent-quality claim is
made. Reuse of shared files was not enabled in the GitHub baseline.

A [cache-fair acquisition replay](benchmarks/gate_b/CACHE_FAIR_2026-10-29.md)
has now measured **13 unique GitHub source fetches versus 6 RGP acquisition/
Reader setup calls**, a 53.85% reduction.
A separate [task-volume sensitivity analysis](benchmarks/gate_b/SENSITIVITY_2026-10-30.md)
shows why this cannot be extrapolated to a single question: it enumerates
4095 conditional workload subsets, with **zero additional experiments**. It reuses the earlier pilot's
answers and correctness judgments; it is **not** a new independently graded
run. A typed first-use source-cache ledger makes zero-call GitHub tasks
explicit and rejects missing/mismatched Git blob anchors.

An [arm-masked independent review handoff](docs/GATE_B_REVIEW.md) is
now available. Source-line citations are checked against a 13-file index
recomputed from pinned RGP snapshots and Git blob identifiers. It prepares randomly ordered A/B answers and refuses to
treat absent independent judgments as success. Neither the masked-review
utility nor a reviewer self-declaration alone can authenticate actual
grading or turn the current diagnostic into GO.

The evaluator returns **GO_CANDIDATE**, not an automatic GO. **Gate B
decision remains HOLD** pending independent correctness review and
authenticated call-history inspection.
