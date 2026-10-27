# Issues and Backlog

Status: `todo` · `in-progress` · `blocked` · `done`. Claude Code updates the status when a task is finished and stops after each task.

Plan: **M0 setup (day 1) → M1 pass-through + baseline (week 1) → M2 defenses (week 2) → M3 system layer (week 3) → M4 performance, eval report, deploy (week 4).** Stretch items only after M4.

## M0 — Setup (day 1)

| ID | Task | Evidence for done | Status |
|---|---|---|---|
| M0-1 | Repo skeleton: `uv` project, Python 3.12, packages `taintwall/`, `eval/`, `tests/`; pytest, ruff, mypy; dependency groups `dev` and `eval`; smoke test | Tree, `uv run pytest` output, CLAUDE.md commands filled in | done |
| M0-1b | Rename Agent Gateway / `gateway/` references in README, `docs/` and CLAUDE.md, plus repo paths, to Taintwall / `taintwall/` | grep showing no stale references (excluding DECISIONS.md history) | done |
| M0-2 | Local environment on the T7: `UV_CACHE_DIR` and `UV_LINK_MODE` set; check internal disk is not used for caches | `du -sh` of cache on T7; note in README "Local setup" | todo |
| M0-3 | Pin versions: `mcp`, `agentdojo==0.1.35`, `anthropic` (compatible with AgentDojo); record in DECISIONS.md | `uv.lock` committed; `uv run python -c "import agentdojo, mcp"` works | todo |
| M0-4 | GitHub repo + CI (ruff, mypy, pytest on push) | Green CI run link | todo |

## M1 — Pass-through gateway and baseline (week 1)

| ID | Task | Evidence for done | Stop and reconsider if |
|---|---|---|---|
| M1-1 | Explain MCP basics in PRIMER.md (client, server, tools/list, tools/call, streamable HTTP, sessions) before coding | PRIMER section added | — |
| M1-2 | Minimal upstream MCP server for tests (`echo`, `slow`, `fail` tools) | Unit tests call it directly | — |
| M1-3 | Gateway pass-through: MCP server to agents, MCP client to configured upstreams, namespaced tools, JSONL audit of every call with per-stage timing | Integration test: an MCP client lists and calls upstream tools through the gateway; results identical to direct | MCP SDK v2 lacks something needed for proxying → record workaround in DECISIONS |
| M1-4 | Agent API keys and session ids; unauthenticated calls rejected | Tests | — |
| M1-5 | Eval spike: `EnvBridge` MCP server + `GatewayToolsExecutor`; run 5 `workspace` user tasks with no attack, `direct` vs `pass_through`; results must match | Both runs' utility identical on the 5 tasks; traces show calls through gateway | Bridge cannot share environment state cleanly → stop, propose alternative |
| M1-6 | Anthropic model wiring for AgentDojo (`claude-haiku-5-5`); 10-task smoke test; record tokens per run; update budget estimate | Token stats in `runs/`; EVAL_AND_BUDGET.md budget table updated | SDK incompatibility → pin and record |
| M1-7 | Baseline on dev suites (`workspace`, `slack`), `important_instructions`, `direct` and `pass_through` | `runs/<id>/metrics.json`; benign utility, utility under attack, ASR with CIs | `pass_through` differs from `direct` beyond noise → bridge bug |
| M1-8 | Week-1 gate analysis (EVAL §1.6): share of successful attacks that carry untrusted atoms into the harmful call | Number + examples in `docs/VALIDATION.md` | — (decides emphasis, not continuation) |

## M2 — Defenses (week 2)

| ID | Task | Evidence for done |
|---|---|---|
| M2-1 | Policy schema + loader + engine (pure), decision combination, default deny, fail closed | Table-driven tests, property test (deny dominates), invalid-policy tests |
| M2-2 | Tool labels for dev suites from tool names/descriptions | `policies/tool_labels/workspace.yaml`, `slack.yaml`; reviewed by Thanu |
| M2-3 | Provenance: atom extractors (email, URL/domain, IBAN/account, phone, path, shingles), normalisation, session store (in-memory), labelling of results | Unit tests per atom type incl. negatives; benchmark of extraction time |
| M2-4 | Grants: create via admin API and `declare_task` meta-tool; expiry, revocation | Tests |
| M2-5 | Modes: `grants_only`, `sequence_rules`, `provenance`, `session_taint` | Config files in `eval/configs/` |
| M2-6 | Simulated approver (`deny_all`, `approve_all`) in harness | Test |
| M2-7 | Dev-suite eval for all modes × approver modes | `runs/` + short table in VALIDATION.md; no held-out runs yet |
| M2-8 | Write held-out labels (`banking`, `travel`) from tool descriptions only; commit **before** any held-out run | Commit hash recorded in EVAL doc |

## M3 — System layer (week 3)

| ID | Task | Evidence for done |
|---|---|---|
| M3-1 | Decide local Postgres/Redis (OQ-2); explain choice | DECISIONS entry; services running |
| M3-2 | Repository interfaces; Postgres for grants, policies, approvals, vault, audit; Redis for atoms, taint, rate limits, pub/sub, audit stream | Same test suite passes on both backends |
| M3-3 | Approvals state machine with atomic transitions, waiting with timeout, restart safety | Tests incl. concurrent resolve and restart |
| M3-4 | Vault: encrypted secrets, injection, redaction everywhere; secret-echo test | Security test passes |
| M3-5 | Audit via queue + consumer; backpressure metric; session replay query | Tests incl. slow consumer |
| M3-6 | Rate limits (token bucket) and circuit breakers per upstream | Tests with fault-injection upstream |
| M3-7 | Minimal dashboard: login, approval queue, approval detail with evidence, session timeline | Screenshot; approve/deny works end-to-end |
| M3-8 | OpenTelemetry spans per stage | Trace screenshot or exported trace |

## M4 — Performance, report, deploy (week 4)

| ID | Task | Evidence for done |
|---|---|---|
| M4-1 | Load tests: overhead, concurrency, 2 instances, chaos, audit backpressure (EVAL Part 2) | `runs/` + plots |
| M4-2 | Optional LLM judge for `uncertain`; compare with rules-only on dev suites (ASR, utility, latency, cost) | Table |
| M4-3 | Adaptive attack set; results on dev suites | Table |
| M4-4 | Held-out evaluation (once; second run only for a bug fix, disclosed) | `runs/` with commit hash |
| M4-5 | `eval/report.md` generated from runs; README results section citing run ids | Report |
| M4-6 | Deploy demo (OQ-3); demo script: held `send_money` approved in dashboard, visible in replay | Public URL or recording |

## Stretch (only after M4)

Hash-chained audit · policy simulation ("what would this policy have done on last week's traffic") · multi-tenancy · per-task least-privilege grants generated from the user request · session replay UI polish.

## Open questions

See `docs/PRD.md` §8.
