# Evaluation and Budget Plan

Draft v1, 27 Oct 2026. Targets are not results. Labels: FACT (checked against a primary source or the package source), ESTIMATE, VERIFY.

## Part 1 — Security and utility on AgentDojo

### 1.1 Benchmark facts

- AgentDojo (ETH Zurich SPY Lab), MIT licence, `pip install agentdojo`; latest version 0.1.35 (released 27 Oct 2025); requires Python ≥ 3.10. FACT (PyPI).
- The README notes the package API "is still under development and might change". FACT. → Pin `agentdojo==0.1.35`.
- Benchmark CLI: `python -m agentdojo.scripts.benchmark` with options including `--suite/-s`, `--user-task/-ut`, `--injection-task/-it`, `--attack`, `--defense`, `--logdir` (default `./runs`), `--benchmark-version` (default `v1.2.2`), `--module-to-load` for custom attacks/defenses. FACT (benchmark.py).
- Suites: `workspace`, `slack`, `banking`, `travel`. In the v1 suite files: workspace 33 user × 6 injection tasks, slack 17 × 5, banking 16 × 9, travel 20 × 7 → 97 user tasks, 27 injection tasks, 567 (user, injection) security cases. FACT (counted in package source). Later versions modify some tasks: **recount at the pinned benchmark version** before reporting.
- Built-in defenses: `tool_filter`, `transformers_pi_detector` (needs the `transformers` extra and torch), `spotlighting_with_delimiting`, `repeat_user_prompt`. FACT (package source).
- Attacks include `important_instructions` (and variants), `tool_knowledge`, `direct`, `ignore_previous`, `injecagent`, `system_message`, and several denial-of-service variants. FACT (package source).
- The CLI model list does **not** include current Claude models; `AnthropicLLM(client, model=...)` accepts any model string, so the pipeline is built in code. FACT (package source). AgentDojo's Anthropic code was written against older `anthropic` SDK versions (requires ≥ 0.47; current SDK is 2.x): **VERIFY compatibility in M1**, pin if needed.

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

### LLM (FACT, Anthropic pricing page, checked 19 Oct 2026)

Claude Haiku 5.5 (`claude-haiku-5-5`): $0.10 per million input tokens and $0.50 per million output tokens for prompts up to 100,000 tokens (higher above that). Prompt-cache hits $0.01 per million.

ESTIMATE (replace with measured tokens from M1): if one agent run averages ~20k input and ~1.5k output tokens, one full pass over the 567 security cases plus 97 benign tasks ≈ 13M input + 1M output tokens ≈ **$1.8**. Ten configurations × two approver modes ≈ **$40** total. Measure tokens on 20 runs in M1 and update this table before running anything large.

Controls: set a monthly spend limit in the Anthropic console; run a 10-task smoke test before any full run; cache tool schemas where the pipeline allows.

### Hosting

- Development: local only (no cost).
- Demo (M4): one gateway + Postgres + Redis. Candidates: a free-tier container host plus free-tier managed Postgres/Redis, or one small VM. VERIFY free-tier terms at M4 and record the choice in DECISIONS.md. Target: ₹0 recurring.
