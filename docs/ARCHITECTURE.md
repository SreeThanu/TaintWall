# Architecture

Draft v1, 27 Oct 2026. Reasons for choices are in `DECISIONS.md`.

## 1. Design goals, in priority order

1. **Safe by default:** fail closed; secrets never leak; every decision explainable.
2. **Drop-in:** any MCP client works by changing one URL.
3. **Low, measured overhead** on the request path.
4. **Horizontally scalable:** gateway processes hold no session state.
5. **Learnable and buildable by one person in 4 weeks.**

## 2. System overview

```
                         ┌────────────────────────────── Taintwall ───────────────────────────────┐
Agent (MCP client) ─────►│ MCP server endpoint (streamable HTTP)                                  │
 + grant token           │   1 Auth            grant token → grant_id, agent_id                   │
                         │   2 Rate limit      per agent, per tool (token bucket in Redis)        │
                         │   3 Grant check     tool ∈ grant? grant unexpired/unrevoked?           │
                         │   4 Provenance      args ∩ session atoms → labels                      │
                         │   5 Policy engine   rules(tool, args, labels, grant) → allow/hold/deny │
                         │   6 (optional) LLM judge for "uncertain"                               │
                         │   7 Approval wait   hold → approval record → wait (timeout)            │
                         │   8 Vault           inject credentials for the upstream                │
                         │   9 Upstream call   MCP client → upstream server (circuit breaker)     │
                         │  10 Label result    tool config → trust/sensitivity → extract atoms    │
                         │  11 Audit emit      event → queue (non-blocking)                       │
                         └───────┬───────────────────────┬──────────────────────┬────────────────┘
                                 │                       │                      │
                         Redis: session atoms,     Postgres: grants,     Audit consumer ─► Postgres
                         rate limits, approval     policies (versions),  (append-only events)
                         pub/sub, audit stream     approvals, vault
                                                                          Dashboard (server-rendered):
                                                                          approvals · session replay
```

Steps 3–5 never call the network except the state store. Step 6 only runs when a rule returns `uncertain` and the judge is enabled.

## 3. Request lifecycle (tools/call)

1. Agent sends `tools/call` for `bank__send_money` with arguments.
2. **Auth:** ASGI middleware reads the grant token from the `Authorization: Bearer` header the MCP client is configured with → SHA-256 hash → `grant_id` → `agent_id`. Missing, malformed, unknown, expired or revoked → HTTP 401 before MCP, with the same response for every reason (D28–D31). Provenance and taint state are keyed by `grant_id`; MCP sessions are not used and the endpoint runs `stateless_http=True` (DECISIONS D19, PRIMER "Session (in this project)").
3. **Rate limit:** token bucket keyed by agent and by tool. Exceeded → error with retry-after.
4. **Grant:** load the grant named by the token (step 2). Tool not in grant → per policy `deny` or `hold` (M1: fixed deny, and `tools/list` is filtered to the grant; D32).
5. **Provenance:** extract atoms from arguments; look them up in the session's atom store; attach labels such as `untrusted_source`, `sensitive`, with the source tool call ids as evidence.
6. **Policy:** evaluate rules in order; combine with deny > hold > allow; default deny. Record `rule_ids`, `policy_version`.
7. **Hold:** create approval record (status `pending`, expires_at). The request waits on a notification (Redis pub/sub) up to `approval_timeout`. Approved → continue. Denied or expired → return an MCP error result explaining it was not executed.
8. **Vault:** fetch the upstream credential, inject into the upstream request. Never place it in arguments the agent can see.
9. **Upstream:** call through one long-lived MCP `Client` per upstream over a shared, pooled HTTP client (`httpx2.AsyncClient`); there is no MCP session to pool on the `2026-07-28` protocol. Circuit breaker per upstream; timeout. Upstream errors are mapped to fixed, redacted messages (DECISIONS D20).
10. **Label result:** per-tool config says whether output is `untrusted` (external content) and which fields are `sensitive`; extract atoms and store them in the session with TTL.
11. **Return** result to agent; **emit audit events** (request, decision, approval, result metadata, latency per stage) to the queue.

Every stage is an OpenTelemetry span.

## 4. Components

### 4.1 Gateway process (`taintwall/`)
- Starlette app hosting the MCP endpoint (MCP Python SDK v2) plus admin/dashboard routes.
- **Upstream registry:** config lists upstream MCP servers (URL, auth, namespace). On start, connects and caches tool lists (startup fails if any upstream is unreachable, D24); tools exposed as `<namespace>__<tool>`. Each upstream client is held by its own supervisor task, so one upstream failing cannot cancel the gateway or other upstreams; a broken upstream fails closed until reconnects arrive in M3-6 (D25).
- Stateless: any instance can serve any request.

### 4.2 Policy engine (`taintwall/policy/`)
- Pure functions: `(CallContext, PolicySet) -> Decision`. No I/O. Easy to test and fast.
- Policies loaded from YAML, schema-validated, versioned by content hash. See `POLICY_SPEC.md`.

### 4.3 Provenance tracker (`taintwall/provenance/`)
- **Atom extractors:** email addresses, URLs/domains, IBANs and account numbers, phone numbers, file paths, and word shingles (e.g., 8-word windows) for long free text.
- **Atom store (per grant):** `grant_id → {atom_hash → labels, source_call_ids, first_seen}` with TTL. Redis hash in M3; in-memory dict before (DECISIONS D19).
- **Matching:** normalise (case, whitespace, punctuation), hash, look up. Shingle matching catches copied paragraphs; it will miss paraphrases (documented limit).
- **Session taint flag:** set when any untrusted result enters the session; stored per `grant_id`.

### 4.4 Grants (`taintwall/grants/`)
- M1: created by the `taintwall-grant` CLI, which prints the token once; the gateway reads the grants file at startup (D33).
- From M2-4: created by an admin API call or by a gateway meta-tool `declare_task(purpose, tools)` that the agent can call; policy decides whether self-declared grants need approval.
- Fields: `grant_id, agent_id, token_hash, tools[], expires_at, revoked_at, created_by`. The grant is also the session key (D19).

### 4.5 Approvals (`taintwall/approvals/`)
- State machine: `pending → approved | denied | expired`. Transitions are atomic (conditional update in Postgres) so two approvers or two gateway instances cannot both resolve it.
- Waiting request subscribes to `approval:<id>`; the approver's action publishes the result. Restart safety: on restart, pending approvals stay pending; waiting requests time out cleanly.

### 4.6 Vault (`taintwall/vault/`)
- Secrets encrypted with a key from the environment (Fernet or equivalent) in Postgres (M3) or an encrypted local file before that. Decrypted only in memory at injection time.

### 4.7 Audit (`taintwall/audit/`)
- Request path puts events on a queue (in-process queue → Redis Stream in M3). A consumer writes them to an append-only Postgres table. If the queue is full, the request still completes but a `audit_backpressure` metric fires; the policy for this (drop vs block) is a recorded decision.
- Stretch: hash-chain events for tamper evidence.

### 4.8 Dashboard (`taintwall/dashboard/`)
- Server-rendered HTML (Jinja templates), no frontend framework. Pages: approval queue, approval detail (tool, args with secrets redacted, provenance evidence, rule ids), session timeline/replay.
- Protected by a simple login for approvers.

### 4.9 Eval harness (`eval/`) — separate from the gateway
AgentDojo normally executes tools in-process. To send them through the real network path:

```
run_task_with_pipeline(..., runtime_class=BridgedRuntime)   pipeline: claude-haiku-5-5 or ground-truth replay
  └─ BridgedRuntime ──MCP────────► Taintwall ──MCP──────► EnvBridge MCP server
                                                         (runs inside the harness process,
                                                          holds the live task environment,
                                                          calls runtime.run_function(env, ...))
```

- `EnvBridge` is a low-level MCP `Server` (DECISIONS D20) that exposes the current task's AgentDojo functions as MCP tools and mutates the same environment object AgentDojo later inspects for utility and security checks.
- `BridgedRuntime` is a `FunctionsRuntime` subclass passed as `runtime_class` (DECISIONS D21); AgentDojo's own `ToolsExecutor` stays. Its `run_function` sends each call to the gateway with the task's grant token (a fresh grant per task, D19) and turns gateway errors (blocked, denied, expired) into the `"ErrorType: message"` strings AgentDojo already shows the agent.
- Deviation: the LLM sees AgentDojo's tool list (`runtime.functions`), not the gateway's `tools/list`. M1-5a checks that the bridge's `list_tools` schemas equal AgentDojo's function schemas (normalized JSON).
- Tasks run sequentially (one environment at a time). This is a week-1 spike; if it does not work cleanly, record the alternative in DECISIONS.md.
- Simulated approver for holds: configurable `deny_all` (security-maximal) or `approve_all` (shows what approvals alone cannot stop); both reported.

## 5. Data model (M3, Postgres)

```
agents(agent_id, name, api_key_hash, created_at, disabled_at)
grants(grant_id, agent_id, token_hash unique, tools jsonb, purpose, expires_at, revoked_at, created_by)
policy_versions(version_hash, yaml, loaded_at, loaded_by)
approvals(approval_id, grant_id, agent_id, tool, args_redacted jsonb, evidence jsonb,
          status, created_at, expires_at, decided_by, decided_at)
secrets(secret_id, upstream, ciphertext, created_at, rotated_at)
audit_events(event_id, ts, grant_id, agent_id, type, payload jsonb, policy_version, rule_ids text[])
```

Redis keys: `atoms:<grant_id>` (hash, TTL), `taint:<grant_id>`, `rl:<agent>:<tool>`, channel `approval:<id>`, stream `audit`.

## 6. Scaling and failure modes

| Concern | Design | Test |
|---|---|---|
| Many gateway instances | Stateless processes; shared Redis/Postgres | Two instances behind a simple load balancer pass the integration suite; a grant's atoms recorded on one instance block a call on the other |
| Redis down | Provenance and rate limits unavailable → **deny** (fail closed) | Chaos test: stop Redis mid-run |
| Postgres down | Cannot create approvals or read grants → deny; audit queue buffers | Chaos test |
| Upstream slow/down | Timeout + circuit breaker; clear error to agent | Fault-injection upstream |
| Approver absent | Expiry → deny; metric of expired approvals | State machine test |
| Audit backlog | Bounded queue; backpressure metric; documented drop/block policy | Load test with slow consumer |
| Hot path latency | Policy engine pure and in-memory; one Redis round trip for atoms; pooled upstream HTTP connections | Latency benchmark per change |

## 7. Observability

OpenTelemetry traces (spans per stage), metrics (decisions by type, approvals pending/expired, overhead histograms, breaker state), structured logs with secrets redacted. Local: console exporter; later Jaeger or similar if it fits in memory.

## 8. Repository layout

```
taintwall/
  CLAUDE.md  README.md  pyproject.toml  .env.example  .gitignore
  config/            dev.yaml (upstreams, timeouts, store backends)
  policies/          *.yaml + tool_labels/*.yaml
  taintwall/
    app.py  mcp_proxy.py  upstreams.py  auth.py  ratelimit.py
    grants/  policy/  provenance/  approvals/  vault/  audit/  dashboard/  stores/
  eval/
    harness/           env_bridge.py  bridged_runtime.py  connections.py  replay.py  frozen_clock.py
                       spike_ground_truth.py
    configs/  run.py  report.py
  loadtest/
  tests/
    unit/  integration/  security/  chaos/
  runs/              (git-ignored except reports)
  docs/
```

## 9. Technology choices (summary)

Python 3.12; MCP Python SDK 2.x; Starlette/uvicorn; Pydantic models; SQLite → Postgres; in-memory → Redis; Jinja for dashboard; OpenTelemetry; pytest + hypothesis; a Python load tool (e.g., Locust) or `k6` for load tests (decide in M4).
