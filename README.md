# Agent Gateway

A self-hosted control plane for AI agents' tool calls: scoped task grants, declarative policies, prompt-injection defense by data provenance, human approvals, credential injection and a full audit trail. It works with any MCP-speaking agent without changing the agent.

Status: **planning complete, code not started** (27 Oct 2026). No results yet; this README quotes measured numbers only from `eval/report.md`, with run ids.

## The problem

Companies want AI agents that act: send email, move money, edit files, call internal APIs. Two things stop them:
1. **Over-broad access.** Agents get raw API keys and every tool, for every task.
2. **Prompt injection.** Agents read untrusted content (emails, web pages, documents). Text hidden in that content can steer the agent into harmful tool calls, such as forwarding data to an attacker.

Teams end up either blocking agents from acting or approving everything by hand. Several YC companies sell control layers for this (for example Clawvisor, Spring 2026). This project is an open, measured version of that idea.

## What it does

```
Agent (MCP client) ──► Agent Gateway ──► MCP tool servers
                         grants · policies · provenance · approvals · vault · audit
```

- **Grants:** each task gets a short-lived grant naming the tools it may use.
- **Policies:** YAML rules on tools, arguments, recipients and data provenance; every decision names the rule that made it.
- **Provenance tracking:** values from untrusted or sensitive tool results are remembered per session; calls that send them to risky destinations are blocked or held.
- **Approvals:** held calls wait for a person in a small dashboard.
- **Vault:** API keys are injected by the gateway at call time; the agent never sees them.
- **Audit:** every request and decision is recorded and replayable.

## How it is evaluated

On **AgentDojo** (ETH Zurich, MIT licence), a public benchmark that simulates email, Slack, banking and travel tools with prompt-injection attacks. It scores runs automatically, so no hand-labelled data is needed. We report task completion, task completion under attack, attack success rate, false blocks, human approvals per task and the latency the gateway adds. Two suites are held out and configured blind. See `docs/EVAL_AND_BUDGET.md`.

## Claims, kept separate

- **Useful:** addresses a real deployment blocker that companies currently pay for.
- **Technically substantial:** a stateless, horizontally scalable proxy with shared session state, async approvals, a queue-fed audit log, secrets handling, rate limiting and measured latency.
- **Not novel:** commercial gateways and research defenses (Invariant Guardrails, Microsoft FIDES, CaMeL, Progent) exist. The contribution is an open, drop-in implementation with honest measurements, including where a proxy cannot help.
- **Known limit:** a proxy sees tool calls and their data, not the model's reasoning. An injection that steers the agent into a harmful call without passing any tracked data through is invisible to provenance tracking. The coarse "session taint" mode exists for that case, at a cost in human approvals. Both are measured.

## Documentation

`CLAUDE.md` (rules for building), `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/POLICY_SPEC.md`, `docs/THREAT_MODEL.md`, `docs/EVAL_AND_BUDGET.md`, `docs/ISSUES.md`, `docs/DECISIONS.md`, `docs/PRIMER.md`.

## Local setup

Filled in by issue M0-1.
