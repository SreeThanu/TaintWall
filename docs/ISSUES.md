# Issues and Backlog

Status: `todo` · `in-progress` · `blocked` · `done`. The status is updated when a task is finished, and work stops after each task.

Plan: **M0 setup (day 1) → M1 pass-through + baseline (week 1) → M2 defenses (week 2) → M3 system layer (week 3) → M4 performance, eval report, deploy (week 4).** Stretch items only after M4.

## M0 — Setup (day 1)

| ID | Task | Evidence for done | Status |
|---|---|---|---|
| M0-1 | Repo skeleton: `uv` project, Python 3.12, packages `taintwall/`, `eval/`, `tests/`; pytest, ruff, mypy; dependency groups `dev` and `eval`; smoke test | Tree, `uv run pytest` output, docs/CONTRIBUTING.md commands filled in | done |
| M0-1b | Rename Agent Gateway / `gateway/` references in README, `docs/` and docs/CONTRIBUTING.md, plus repo paths, to Taintwall / `taintwall/` | grep showing no stale references (excluding DECISIONS.md history) | done |
| M0-2 | Local environment: uv cache and Python on a configurable disk (`UV_CACHE_DIR`, `UV_LINK_MODE`, `UV_PYTHON_INSTALL_DIR`); check caches stay on that disk | `du -sh` of cache; note in README "Local setup" | done |
| M0-3 | Pin versions: `mcp`, `agentdojo==0.1.35`, `anthropic` (compatible with AgentDojo); record in DECISIONS.md | `uv.lock` committed; `uv run python -c "import agentdojo, mcp"` works | done |
| M0-4 | GitHub repo + CI (ruff, mypy, pytest on push) | Green CI run link | todo |

## M1 — Pass-through gateway and baseline (week 1)

| ID | Task | Evidence for done | Stop and reconsider if |
|---|---|---|---|
| M1-1 (done) | Explain MCP basics in PRIMER.md (client, server, tools/list, tools/call, streamable HTTP, sessions) before coding | PRIMER section added | — |
| M1-2 (done) | Minimal upstream MCP server for tests (`MCPServer`): `echo`, `read_message` (untrusted content with IBAN, email and URL, plus spaced-IBAN and mixed-case-email variants), `fail` (`tool_error` / `protocol_error` with a fake secret, `crash`), `echo_request` (arguments and HTTP headers), `slow`, `large` | Unit tests call it directly | — |
| M1-3 (done) | Gateway pass-through: MCP server to agents, MCP client to configured upstreams, namespaced tools, JSONL audit of every call with per-stage timing | Integration test: an MCP client lists and calls upstream tools through the gateway; results identical to direct. Test: internal exception text (with a fake secret) never reaches the client (both protocol eras, `2026-07-28` and `2025-11-25`) or any log record (caplog). Done: `3c945b8 (was 1aa3599)`; latency run `20261102T083616Z-passthrough-latency` (commit `248e408 (was a93d6b2)`, `dirty: false`, label `ESTIMATE-local`); D25 pinned by `tests/integration/test_upstream_supervision.py` | MCP SDK v2 lacks something needed for proxying → record workaround in DECISIONS |
| M1-3b (done) | Minor bug: `loadtest` `--runs-dir` outside the repo crashes on the final print (`ValueError`) after files are written | Test running the benchmark with `--runs-dir` outside the repo | — |
| M1-4 (done) | Minimal grants from a grants file created by `taintwall-grant` (D19, D33): token → `grant_id`, `agent_id`, allowed tools (`*` allowed in M1), expiry; missing, malformed, unknown, expired or query-string token rejected; tool outside grant denied; `tools/list` filtered | Tests: each rejection path, with one generic response and a specific audit reason; expiry boundary with an injected clock; token looked up by SHA-256 hash (D28); token and hash never appear in logs or audit records, both protocol eras; rejection flood cannot fill the audit (D30); `-32030` free in the installed `mcp` package. Done: `a58d77f (was 1c882b0)`; latency run `20261102T091402Z-passthrough-latency` (`dirty: false`, label `ESTIMATE-local`) | — |
| M1-5a (done) | Bridge spike, no gateway, no LLM: `EnvBridge` (low-level MCP `Server`) + `BridgedRuntime` (`runtime_class`, D21). Replay ground truth for dev-suite user tasks (`workspace`, `slack`; `banking`/`travel` not replayed) three ways: direct, bridge in-process (`mode="legacy"`, real JSON-RPC), bridge over streamable HTTP | Per task: utility True on all paths; each tool-result text byte-identical; post-environment `model_dump()` identical to direct; an invalid call gives the same `"ErrorType: message"` string on all paths; tool schemas from the bridge's `list_tools` equal AgentDojo's function schemas (normalized JSON). Noted deviation: in eval the LLM sees AgentDojo's tool list, not the gateway's `tools/list` | Bridge cannot share environment state cleanly, or ground-truth args cannot cross JSON cleanly → stop, propose alternative |
| M1-5c | Synthetic equivalence tests for the 6 workspace tools never executed by ground truth (`cancel_calendar_event`, `get_draft_emails`, `get_file_by_id`, `get_received_emails`, `get_sent_emails`, `search_contacts_by_email`): direct vs bridge (b and c), valid args derived from the environment, frozen clock; compare result text and post-env byte-identical | coverage section shows schema-checked-only: 0 for workspace | — |
| M1-5b | Depends on M1-5c. Bridge through the gateway (`pass_through`): repeat the M1-5a ground-truth equivalence via Taintwall with a fresh grant per task, from a per-run temporary grants file generated by the harness with a TTL longer than the run (D33); then a 5-task `workspace` LLM smoke test, no attack | Ground-truth equivalence holds through the gateway; LLM smoke: no errors, and traces/audit show every call went through the gateway (equal utility is **not** a criterion: sampling cannot be pinned) | Results differ from M1-5a only through the gateway → gateway bug |
| M1-6 | Anthropic model wiring for AgentDojo (`claude-haiku-5-5`); 10-task smoke test; record tokens per run; update budget estimate | Token stats in `runs/`; EVAL_AND_BUDGET.md budget table updated | SDK incompatibility → pin and record |
| M1-7 | Baseline on dev suites (`workspace`, `slack`), `important_instructions`, `direct` and `pass_through` | `runs/<id>/metrics.json`; benign utility, utility under attack, ASR with CIs | `pass_through` differs from `direct` beyond noise → bridge bug |
| M1-8 | Week-1 gate analysis (EVAL §1.6): share of successful attacks that carry untrusted atoms into the harmful call; classify each injection task by whether the harmful call carries untrusted data; workspace injection tasks 6–13 (deletion/cover-tracks, no ground truth) are expected candidates for 'action-changing' | Number + examples in `docs/VALIDATION.md` | — (decides emphasis, not continuation) |

## M2 — Defenses (week 2)

| ID | Task | Evidence for done |
|---|---|---|
| M2-1 | Policy schema + loader + engine (pure), decision combination, default deny, fail closed; replace POLICY_SPEC `session.id` with `grant.id`; replace M1's fixed out-of-grant deny, and revisit `tools/list` filtering for hold rules (D32) | Table-driven tests, property test (deny dominates), invalid-policy tests |
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
| M3-3 | Approvals state machine with atomic transitions, waiting with timeout, restart safety; recheck grant expiry after an approval wait (M1 checks once per HTTP request) | Tests incl. concurrent resolve and restart |
| M3-4 | Vault: encrypted secrets, injection, redaction everywhere; scrub all vault-injected secret values from upstream results and errors before they reach the agent (upstreams may echo credentials); secret-echo test. Note: `httpx2` logs upstream URLs at INFO; mitigate: reject credentials in upstream URLs at config validation (userinfo, query keys like key/token/secret) and/or raise the `httpx2` logger to WARNING | Security test passes; a URL with an embedded credential is rejected at startup |
| M3-4b | Investigation before M3-5: why does the SDK client call `tools/list` before each `call_tool`? Cite the SDK source. If expected, consider aggregating `tools/list` audit events per grant per window (as D30 does for rejections) | Findings with SDK source citation; decision recorded in DECISIONS |
| M3-5 | Audit via queue + consumer; backpressure metric; session replay query | Tests incl. slow consumer |
| M3-5b | Investigate: can audit record forwarded:true for a call never sent (legacy mode, upstream killed right after connect)? Reproduce through the gateway; if confirmed, fix so `forwarded` reflects whether bytes were actually sent to the upstream (or mark it unknown); add a test | Test reproducing the scenario through the gateway, audit field correct |
| M3-6 | Rate limits (token bucket) and circuit breakers per upstream | Tests with fault-injection upstream |
| M3-7 | Minimal dashboard: login, approval queue, approval detail with evidence, session timeline | Screenshot; approve/deny works end-to-end |
| M3-8 | OpenTelemetry spans per stage | Trace screenshot or exported trace |
| M3-9 | Tool-definition integrity: hash each upstream's tool definitions (name, description, input/output schemas) at startup; on refresh (with M3-6 reconnects) detect changes and alert, holding changed tools until reviewed (THREAT_MODEL T14) | Test: an upstream that changes a description after startup is detected and its tool is not served unreviewed |

## M4 — Performance, report, deploy (week 4)

| ID | Task | Evidence for done |
|---|---|---|
| M4-1 | Load tests: overhead, concurrency, 2 instances, chaos, audit backpressure (EVAL Part 2) | `runs/` + plots |
| M4-2 | Optional LLM judge for `uncertain`; compare with rules-only on dev suites (ASR, utility, latency, cost) | Table |
| M4-3 | Adaptive attack set; results on dev suites | Table |
| M4-4 | Held-out evaluation (once; second run only for a bug fix, disclosed) | `runs/` with commit hash |
| M4-5 | `eval/report.md` generated from runs; README results section citing run ids | Report |
| M4-6 | Deploy demo (OQ-3); gate: THREAT_MODEL T15 resolved first, including rate limits; demo script: held `send_money` approved in dashboard, visible in replay | Public URL or recording |

## Stretch (only after M4)

Hash-chained audit · policy simulation ("what would this policy have done on last week's traffic") · multi-tenancy · per-task least-privilege grants generated from the user request · session replay UI polish.

## Open questions

See `docs/PRD.md` §8.
