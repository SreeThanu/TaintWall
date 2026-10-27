# Product Requirements — Agent Gateway

Version 0.1 · 27 Oct 2026 · Owner: Thanu

## 1. Summary

A drop-in gateway between MCP-speaking AI agents and their tool servers. It enforces task-scoped grants and declarative policies, tracks where data came from to block prompt-injection-driven calls, holds risky calls for human approval, injects credentials, and records an audit trail. Success is measured on AgentDojo and by latency and load tests.

## 2. Problem

- Agents are given broad tool access and raw credentials for every task.
- Agents read untrusted content; injected instructions can redirect their tool calls (indirect prompt injection).
- Existing options: commercial gateways (paid), agent redesigns from research (not drop-in), or manual approval of everything (does not scale).

## 3. Users

| User | Needs |
|---|---|
| **Platform / security engineer** at a company deploying agents | Define policies once; see every decision and why; prove controls to auditors |
| **Agent developer** | Point the agent at one URL; no SDK changes; clear errors when blocked |
| **Approver** (team lead, ops) | Short queue of held calls with enough context to decide quickly |

## 4. Goals and non-goals

**Goals (v1, 4 weeks)**
- G1. Works with any MCP client by changing one URL.
- G2. Every decision explainable (rule id, policy version, provenance labels).
- G3. Measured reduction in AgentDojo attack success, with the task-completion cost and approval burden reported alongside.
- G4. Gateway overhead measured under load (p50/p95 per call), with a stated budget.
- G5. Fails closed on any internal error.
- G6. Deployable demo with a minimal approval dashboard.

**Non-goals**
- Detecting injections by reading model reasoning (the gateway does not see it).
- Being a full identity provider or secrets manager (use simple built-ins; integrate real ones later).
- A polished UI.
- Multi-tenant SaaS (stretch only).

## 5. Functional requirements

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR1 | **MCP proxy**: gateway is an MCP server to agents and an MCP client to upstream servers; exposes upstream tools under namespaced names | An unmodified MCP client lists and calls tools from two upstreams through the gateway; results identical to direct calls in a test |
| FR2 | **Agent identity**: each agent authenticates; calls are attributed to an agent and session | Unauthenticated calls rejected; audit events carry agent id and session id |
| FR3 | **Grants**: a task grant lists allowed tools and an expiry; calls outside the grant are blocked (or held, per policy) | Tests: in-grant allowed, out-of-grant blocked, expired grant blocked, revoked grant blocked |
| FR4 | **Policy engine**: YAML policies (see POLICY_SPEC) evaluated on every call; deny > hold > allow; default deny | Schema validation at load; every decision records rule id and policy version; property test: no call is allowed when any matching rule denies |
| FR5 | **Provenance tracking**: tool results are labelled (trust, sensitivity) by tool config; atoms (emails, URLs, IBANs, phone numbers, long text shingles) are stored per session; outgoing arguments are checked for labelled atoms | Unit tests per atom type; an AgentDojo-style attack (attacker IBAN from an untrusted transaction note used in `send_money`) is blocked in a test |
| FR6 | **Session taint mode**: once a session has read untrusted content, configured "consequential" tools require approval | Test: after an untrusted read, `send_email` is held; before it, allowed |
| FR7 | **Approvals**: held calls enter a queue; approver approves/denies in dashboard; agent call waits up to a timeout; expiry denies | State machine tests: pending→approved, pending→denied, pending→expired; survives gateway restart (M3) |
| FR8 | **Credential vault**: upstream credentials stored encrypted; injected per call; never returned or logged | Secret-echo test passes (CLAUDE.md non-negotiable 2) |
| FR9 | **Audit log**: every request, decision and approval as an append-only event; queryable per session; session replay view | Audit write does not block the request path (queued); test: decisions still made if audit sink is slow, and events are not lost on normal shutdown |
| FR10 | **Rate limits and circuit breakers** per agent and per upstream tool | Load test shows limits enforced; a failing upstream trips the breaker and returns a clear error |
| FR11 | **Optional LLM risk judge** for calls that rules mark "uncertain" | Configurable; latency and token cost recorded per call; compared to rules-only in the eval |
| FR12 | **Dashboard** (minimal, server-rendered): approval queue, session timeline, decision details | Approver can decide a held call in under 3 clicks; shows rule id and provenance evidence |

## 6. Non-functional requirements

| Area | Requirement (targets, not results) |
|---|---|
| Latency | Gateway overhead p95 ≤ 15 ms per call on the policy path, excluding upstream time and approvals (measured on the dev laptop; revise with data) |
| Scalability | Gateway instances are stateless; all session state in the shared store; two instances behind a load balancer pass the same tests |
| Reliability | Fail closed; approvals survive restarts (M3); audit events durable once acknowledged |
| Security | Secrets encrypted at rest; never logged; dashboard behind authentication; least-privilege upstream credentials |
| Observability | OpenTelemetry traces per call with spans for auth, grant, policy, provenance, upstream; metrics for decisions by type |
| Honesty | README numbers only from run ids; held-out suites not tuned on |

## 7. Success metrics (reported, not promised)

- AgentDojo: attack success rate and utility under attack per configuration, with confidence intervals; benign utility; false blocks; approvals per task.
- Latency: p50/p95 overhead at 1, 10 and 50 concurrent sessions.
- One end-to-end demo: a held `send_money` call approved in the dashboard, visible in the session replay.

## 8. Open questions

| ID | Question | Needed by |
|---|---|---|
| OQ-1 | Week-1 gate: what share of successful attacks carry attacker-controlled atoms into the harmful call? Decides how much weight provenance vs session taint gets | M1 end |
| OQ-2 | Postgres/Redis locally: Homebrew-native or Colima with storage on the T7? | M3 start |
| OQ-3 | Demo deployment target and its free-tier terms | M4 |
| OQ-4 | Which LLM for the optional judge (cost vs accuracy) | M4 |
