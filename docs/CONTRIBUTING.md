# Contributing to Taintwall

This is the contract for how work happens in this repo.

## What this project is

A self-hosted **control plane for AI agents' tool calls**. It sits between any MCP-speaking agent and the MCP tool servers it uses, and decides for every call: allow, block, or hold for a human.

It does five things:
1. **Scoped grants:** an agent works under a task grant that lists the tools it may use, with an expiry.
2. **Policy enforcement:** every call is checked against declarative policies (tool, arguments, recipients, data provenance).
3. **Provenance tracking:** the gateway remembers which values came from untrusted or sensitive tool results in this session, and blocks or holds calls that send them somewhere risky. This is the prompt-injection defense.
4. **Human approvals:** held calls wait in an approval queue; a person approves or denies them.
5. **Credential injection and audit:** API keys live in the gateway, never in the agent; every request and decision is logged.

It is a portfolio project for AI Engineer roles. It must show **system design** (stateless scaling, shared state, queues, async workflows, secrets, rate limiting, latency budgets) and **measured AI security** (AgentDojo results). It is inspired by commercial products such as Clawvisor (YC Spring 2026); it is not novel and does not claim to be.

## Where things are documented

| Need | File |
|---|---|
| What we build and acceptance criteria | `docs/PRD.md` |
| Components, data flow, scaling, failure modes, repo layout | `docs/ARCHITECTURE.md` |
| Policy language and decision semantics | `docs/POLICY_SPEC.md` |
| Threats, trust boundaries, known limits | `docs/THREAT_MODEL.md` |
| How results are measured, splits, budget | `docs/EVAL_AND_BUDGET.md` |
| Backlog, milestones, acceptance criteria | `docs/ISSUES.md` |
| Why each design choice was made | `docs/DECISIONS.md` |
| Plain-language primer for new concepts | `docs/PRIMER.md` |

If a doc and the code disagree, stop and ask which is right.

## How we work

- **One task at a time** from `docs/ISSUES.md`. When it is done, show the evidence listed for it, update its status, and stop. Do not start the next task without approval.
- **Tests first** for the policy engine, provenance tracker, grant checks and approval state machine. Every security-relevant change comes with a test that contains an attack case.
- **Never invent numbers.** Every number in README or docs must come from a saved run (`runs/<run_id>/`) or script output, cited by run id. Estimates are labelled ESTIMATE.
- **Ask before** adding a dependency, changing a public interface, deleting files, or anything irreversible.
- After a design choice, add a line to `docs/DECISIONS.md`.
- Before introducing a new concept, add a plain explanation to `docs/PRIMER.md`: what it is, why we need it here, what goes wrong without it.
- Keep usefulness, technical sophistication, novelty and differentiation as separate claims in the README.

## Non-negotiables (tests must enforce these)

1. **Fail closed.** If policy evaluation, provenance lookup or the state store errors or times out, the call is **denied**, never allowed.
2. **Secrets never leave the gateway.** Credentials are never returned to the agent, never written to logs or audit events, never included in error messages. A test sends a call that would echo a secret and asserts it is redacted everywhere.
3. **Every decision is explainable.** Each allow/block/hold records: policy version, rule id(s), matched provenance labels, latency. No decision without a reason.
4. **Policies are data.** Policies live in versioned YAML (`policies/`), validated by a schema at load. No policy logic hard-coded in request handlers.
5. **The gateway core never imports AgentDojo.** Evaluation lives in `eval/` and talks to the gateway over MCP like any other client.
6. **Hot-path latency is measured.** Any change to the request path reruns the latency benchmark and reports p50/p95 overhead.
7. **No tuning on the held-out suites.** `banking` and `travel` are held out (see `docs/EVAL_AND_BUDGET.md`). Their trust labels are written from tool descriptions only, before any attack run on them. Held-out runs are logged and happen at most twice.

## Stack

- Python 3.12, `uv` for environments and dependencies, `pytest`, `ruff`, `mypy` on `taintwall/`.
- MCP Python SDK (`mcp`, v2.x; 2.3.0 was current on 2026-10-27). **The v2 API differs from older tutorials.** `MCPServer` builds tool schemas from Python functions (test servers only); the gateway and EnvBridge use the low-level `Server(name, on_list_tools=..., on_call_tool=...)` with our own redacted error mapping (DECISIONS D20, PRIMER "MCP SDK v2"). Base MCP code on the installed package source, not tutorials; do not copy v1 examples.
- HTTP: Starlette/FastAPI + uvicorn; MCP over streamable HTTP between agent and gateway and between gateway and upstreams.
- State: SQLite + in-memory stores in week 1–2 (no Docker). Postgres (grants, approvals, audit) and Redis (session provenance, rate limits, approval notifications, audit stream) from milestone M3, behind repository interfaces.
- Eval: `agentdojo` (0.1.35 on 2026-10-27, MIT) in a separate dependency group. Its API is marked "still under development"; pin the exact version.
- LLM for eval: Anthropic `claude-haiku-5-5` via AgentDojo's `AnthropicLLM` class (the model is not in AgentDojo's CLI model list, so build the pipeline in code).

## Commands

```bash
uv sync --all-groups                             # install dev + eval groups (M0-1)
uv run pytest -q                                 # tests (M0-1)
uv run ruff check . && uv run ruff format --check .   # lint + format check (M0-1)
uv run mypy                                      # strict type check of taintwall/ (M0-1)
uv run taintwall-grant create --grants-file data/grants.yaml --agent-id dev --tools '*' --ttl-hours 24   # token printed once (M1-4)
uv run taintwall --config config/dev.yaml        # start the gateway (M1)
uv run python -m eval.run --suite workspace --config eval/configs/no_defense.yaml   # (M1)
uv run python -m loadtest.passthrough_latency --n 1000 --warmup 50   # pass-through overhead -> runs/ (M1-3)
```
