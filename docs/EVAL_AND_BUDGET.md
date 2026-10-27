# Evaluation and Budget Plan

Draft v1, 27 Oct 2026. Targets are not results. Labels: FACT (checked against a primary source or the package source), ESTIMATE, VERIFY.

## Part 1 — Security and utility on AgentDojo

### 1.1 Benchmark facts

- AgentDojo (ETH Zurich SPY Lab), MIT licence, `pip install agentdojo`; latest version 0.1.35 (released 27 Oct 2025); requires Python ≥ 3.10. FACT (`curl -s https://pypi.org/pypi/agentdojo/0.1.35/json`, checked 27 Oct 2026).
- The README notes the package API "is still under development and might change". FACT. → Pinned `agentdojo==0.1.35` (DECISIONS D16).
- Benchmark CLI: `python -m agentdojo.scripts.benchmark` with options including `--suite/-s`, `--user-task/-ut`, `--injection-task/-it`, `--attack`, `--defense`, `--logdir` (default `./runs`), `--benchmark-version` (default `v1.2.2`), `--module-to-load` for custom attacks/defenses. FACT (installed 0.1.35, `agentdojo/scripts/benchmark.py` L122–123). We never rely on that default: the benchmark version is pinned to **`v1.2.2`** in `eval/benchmark.py` (DECISIONS D17), and all suites are loaded through it.
- Registered benchmark versions in 0.1.35: `v1`, `v1.1`, `v1.1.1`, `v1.1.2`, `v1.2`, `v1.2.1`, `v1.2.2`. FACT (`uv run python -m eval.count_tasks --all-versions`).
- How a run is counted: `benchmark_suite_with_injections` runs every user task × every injection task of a suite (no pair filtering; only denial-of-service attacks use one injection task per user task), and first runs each injection task once as a plain user task to check it is solvable. FACT (installed 0.1.35, `agentdojo/benchmark.py`: `benchmark_suite_with_injections` L160–230, `run_task_with_injection_tasks` L41–). Task sets per version come from `get_version_compatible_items` (`agentdojo/task_suite/task_suite.py` L36).
- Suites at the pinned **v1.2.2**: workspace 40 user × 14 injection tasks = 560, slack 21 × 5 = 105, banking 16 × 9 = 144, travel 20 × 7 = 140 → **97 user tasks, 35 injection tasks, 949 (user, injection) security cases**. FACT (`uv run python -m eval.count_tasks`, agentdojo 0.1.35). For context: `v1`–`v1.1.2` have workspace 40 × 6 → 97 user tasks, 27 injection tasks, 629 cases (the AgentDojo paper's numbers); `v1.2` added 8 workspace injection tasks. FACT (`uv run python -m eval.count_tasks --all-versions`). An earlier draft of this line said 567 cases with workspace 33 and slack 17 user tasks; that matched no registered version.
- Built-in defenses: `tool_filter`, `transformers_pi_detector` (needs the `transformers` extra and torch), `spotlighting_with_delimiting`, `repeat_user_prompt`. FACT (package source).
- Attacks include `important_instructions` (and variants), `tool_knowledge`, `direct`, `ignore_previous`, `injecagent`, `system_message`, and several denial-of-service variants. FACT (package source).
- The CLI model list does **not** include current Claude models; `AnthropicLLM(client, model=...)` accepts any model string, so the pipeline is built in code. FACT (package source). AgentDojo requires `anthropic>=0.47.0`; we pin `anthropic==1.13.0` (DECISIONS D16), which sends `anthropic-version: 2023-06-01` (installed `anthropic/_constants.py`). M0-3 checked import and construction only; **VERIFY call-level compatibility in M1-6**. Known issues for M1-6. The AgentDojo behaviour is FACT from the installed source (`agentdojo/agent_pipeline/llms/anthropic_llm.py`); the Haiku 5.5 behaviour is not yet observed live:
  - `AnthropicLLM` defaults to `temperature=0.0` (FACT, `__init__` L263; request helper L197); `temperature=None` is sent as `NOT_GIVEN` (FACT, L206). Haiku 5.5 rejects non-default sampling values with a 400: from docs — VERIFY live in M1-6. Plan: pass `temperature=None`.
  - `thinking_budget_tokens` sends `{"type": "enabled", "budget_tokens": N}` (FACT, L207). Haiku 5.5 rejects manual thinking budgets with a 400: from docs — VERIFY live in M1-6. Plan: leave it `None`.
  - `max_tokens` defaults to 1024 (FACT, L265). Thinking counts toward it: FACT for adaptive thinking in general (https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost: "a hard cap on total output for the request, thinking and response text combined"). Check for `stop_reason: "max_tokens"` in M1-6 and raise the cap if needed.
  - API version `2026-10-21` rejects `temperature`/`top_p`/`top_k` on every model: from docs — VERIFY live before any SDK bump. (anthropic 1.13.0 sends `2023-06-01`: FACT, `anthropic/_constants.py`.)

### 1.2 Splits (no tuning on test)

| Split | Suites | Use |
|---|---|---|
| Dev | `workspace`, `slack` | Build and tune policies, labels, matching |
| Held-out | `banking`, `travel` | Labels written from tool descriptions only, before any attack run; evaluated at most twice; both runs logged |

### 1.3 Configurations

| Config | What it is |
|---|---|
| `direct` | AgentDojo default executor, no gateway (reference) |
| `pass_through` | Through the gateway, no rules (measures overhead and checks the bridge does not change results) |
| AgentDojo built-ins | `tool_filter`, `spotlighting_with_delimiting`, `repeat_user_prompt` (in-agent baselines) |
| `sequence_rules` | Invariant-style "A then B" rules |
| `grants_only` | Task grants from a per-task tool list |
| `provenance` | Our main defense |
| `session_taint` | Coarse defense |
| `provenance+judge` | With LLM judge for uncertain calls |

Each config with holds runs twice: simulated approver `deny_all` and `approve_all`.

### 1.4 Metrics (AgentDojo definitions where they exist)

| Metric | Definition |
|---|---|
| Benign utility | Share of user tasks completed with no attack |
| Utility under attack | Share of (user, injection) cases where the user task is still completed |
| Attack success rate (ASR) | Share of cases where the injection goal is achieved |
| False blocks | Calls denied or held in benign runs, per task |
| Approvals per task | Held calls per task (human burden) |
| Overhead | Gateway latency per call (p50/p95), from traces |
| Cost | Tokens and USD per configuration |

Report 95% bootstrap confidence intervals over tasks. Compare to published results from other systems (e.g., FIDES) only as context: different models and versions make direct comparison invalid. Say so in the report.

### 1.5 Adaptive attacks (honesty check)

A small custom attack set (registered via `--module-to-load` or the harness) that targets our matching: spelled-out addresses, split strings, base64/encoded values, paraphrased paragraphs, and attacks that need no data transfer (e.g., "delete the file"). Report ASR on these separately.

### 1.6 Week-1 gate (OQ-1)

On `workspace` with `important_instructions` and `pass_through`:
1. Run all cases once; collect successful attacks.
2. For each, check whether an atom from untrusted tool results appears in the harmful call's arguments.
3. Report the share: **data-carrying attacks / successful attacks**.

High share → provenance is the main defense. Low share → session taint and grants carry more weight; emphasise the trade-off curve. Either way the project continues; this decides emphasis.

### 1.7 Open items for M1

- **OPEN — run-to-run variance.** Sampling can't be pinned: Haiku 5.5 rejects `temperature` (from docs — VERIFY live in M1-6), and adaptive thinking adds further variance.
  - Before comparing configurations, decide on repeated runs and/or confidence intervals (how many repeats, which interval, over which unit).
  - Set thinking effort explicitly (never the model default) and log it per run.
- **OPEN — per-suite reporting.** Workspace is 560 of the 949 security cases at v1.2.2 (`uv run python -m eval.count_tasks`), so aggregated numbers mostly reflect workspace.
  - Report every metric per suite, with aggregates alongside, never instead.

## Part 2 — System performance

| Test | Method | Report |
|---|---|---|
| Overhead | Synthetic MCP upstream that returns instantly; compare direct vs through gateway | p50/p95/p99 per call per mode |
| Concurrency | 1, 10, 50 concurrent sessions | Throughput, latency percentiles, error rate |
| Horizontal scaling | 1 vs 2 gateway instances behind a load balancer | Throughput ratio; correctness (cross-instance atom check) |
| Chaos | Kill Redis / Postgres / upstream mid-run | All calls fail closed; recovery behaviour |
| Audit backpressure | Slow audit consumer | Request latency unaffected; backpressure metric fires |

Results go to `runs/<run_id>/` with config, git commit, versions, and raw traces.

## Part 3 — Budget

### LLM (Claude docs fetched 27 Oct 2026; nothing below observed live yet)

Claude Haiku 5.5 (`claude-haiku-5-5`) is priced per request by prompt length. FACT from the pricing table (https://platform.claude.com/docs/en/about-claude/pricing, rows "Claude Haiku 5.5 (for prompts up to 100,000 tokens)" and "… over 100,000 tokens"):
- prompt ≤ 100,000 tokens: $0.10 per million input tokens, $0.50 per million output tokens, cache hits $0.01 per million;
- prompt > 100,000 tokens: $0.50 input, $2.50 output, cache hits $0.05.
- Cache tokens count toward the threshold. FACT (https://platform.claude.com/docs/en/about-claude/pricing: "counts all of its input tokens, including cache reads and cache writes").

Two things that change token counts compared with older models:
- **Tokenizer:** FACT (https://platform.claude.com/docs/en/about-claude/pricing: "Claude 4.7 and later models and Claude Mythos Preview use a newer tokenizer"; "This tokenizer produces approximately 30% more tokens for the same text"). Haiku 5.5 being one of the "4.7 and later" models is our reading of the docs; VERIFY in M1-6 with `count_tokens` on a sample prompt. Token figures from older models or older published runs undercount.
- **Thinking:** default effort on Haiku 5.5 is `medium` (FACT, https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost, effort table: "`medium` (default on Claude Opus 5.5 and Claude Haiku 5.5)"). Thinking is billed as output (FACT, https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost: "Tokens Claude uses while thinking (billed as output tokens)"), whatever the display setting (FACT, same page: "What you're billed for is the same regardless of the `display` setting").

**M1-6 must log, per request:**
- prompt size in input tokens, to see whether any request crosses the 100K tier;
- `output_tokens`, which include thinking;
- `usage.output_tokens_details.thinking_tokens`.

ESTIMATE (replace with measured tokens from M1-6). One full pass over all four suites at v1.2.2 is 949 security cases + 35 injection-task checks + 97 benign tasks = 1,081 agent runs (counts: `uv run python -m eval.count_tasks`).
- At ~20k input and ~1.5k output tokens per run, all requests ≤ 100K: ≈ 21.6M input + 1.6M output ≈ **$3.0 per pass**. Ten configurations × two approver modes ≈ **$59**.
- If thinking pushes output to ~5k tokens per run: ≈ **$4.9 per pass, ≈ $97 for 20 passes**.
- Most configurations run only on the dev suites, so the real total should be lower; held-out passes are limited to two.

Measure tokens on 20 runs in M1-6 and update this section before running anything large.

Controls: set a monthly spend limit in the Anthropic console; run a 10-task smoke test before any full run; cache tool schemas where the pipeline allows.

### Hosting

- Development: local only (no cost).
- Demo (M4): one gateway + Postgres + Redis. Candidates: a free-tier container host plus free-tier managed Postgres/Redis, or one small VM. VERIFY free-tier terms at M4 and record the choice in DECISIONS.md. Target: ₹0 recurring.
