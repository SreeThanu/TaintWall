# Threat Model

Draft v1, 27 Oct 2026. Review whenever the request path changes.

## 1. Assets

- Upstream credentials (API keys, OAuth tokens) held in the vault.
- Data reachable through tools: emails, files, messages, money movements.
- Integrity of actions taken on the user's behalf.
- Audit log integrity.
- Policies and grants.

## 2. Actors

| Actor | Trust | Capability |
|---|---|---|
| User / agent operator | Trusted for intent | Sets the task |
| Agent (LLM) | **Not trusted** to follow policy; may be manipulated | Issues tool calls |
| Content author (email sender, web page, document, other Slack users) | **Untrusted** | Controls text the agent reads; this is the prompt-injection attacker |
| Approver | Trusted, can make mistakes | Approves/denies held calls |
| Gateway operator | Trusted | Writes policies, manages secrets |
| Network attacker | Untrusted | Out of scope beyond TLS (see §5) |

## 3. Trust boundaries

1. Agent ↔ gateway (authenticated agent key; everything the agent sends is untrusted input).
2. Gateway ↔ upstream tool servers (gateway holds credentials).
3. Tool results ↔ agent context (untrusted content enters the model here; the gateway labels it on the way through).
4. Dashboard ↔ approver (authenticated).

## 4. Threats and mitigations

| # | Threat | Mitigation | Verified by |
|---|---|---|---|
| T1 | Indirect prompt injection makes the agent send data or money to an attacker, using an address/IBAN/URL from the injected text | Provenance: atoms from untrusted results labelled; outgoing args containing them denied/held | AgentDojo runs; unit tests per atom type |
| T2 | Injection makes the agent call a harmful tool with **clean** arguments (no tracked data), e.g., "delete all files" | Grants (tool outside task → hold); session-taint mode holds consequential calls after untrusted reads | AgentDojo runs in `session_taint` mode; approvals-per-task metric |
| T3 | Data exfiltration of sensitive data to a new recipient | `sensitive-to-new-recipient` rule | Tests + AgentDojo exfiltration tasks |
| T4 | Agent tries tools outside its task | Grants, default deny | Unit tests |
| T5 | Credential leakage via results, errors or logs | Vault injection server-side; redaction in logs/audit; scrub all vault-injected secret values from upstream results and errors before they reach the agent (upstreams may echo credentials); gateway handlers catch every exception and return a fixed, redacted error (DECISIONS D20); secret-echo test | Security test suite |
| T6 | Gateway bypass (agent calls upstream directly) | Deployment: upstreams reachable only from gateway network; agents never receive upstream credentials | Documented deployment check |
| T7 | Approval fatigue: approvers rubber-stamp | Report approvals-per-task; show evidence (source call, matched atom) in the approval view | Eval metric |
| T8 | Race: two approvers or two instances resolve one approval | Atomic conditional update | State machine test |
| T9 | State store outage used to bypass checks | Fail closed | Chaos tests |
| T10 | Policy misconfiguration allows too much | Schema validation, default deny, policy tests, version hash in every decision | Tests |
| T11 | Evasion of provenance matching: attacker asks the agent to rewrite the address ("attacker at example dot com"), split it, or encode it | Normalisation catches simple cases only; **documented limit**; measured with adaptive attacks | Adaptive attack set in eval |
| T12 | Denial of service via many calls | Rate limits, circuit breakers | Load test |

## 5. Out of scope (v1)

- Model-internal defenses (fine-tuning, system prompts) — the gateway is agent-agnostic.
- Compromised gateway host or operator.
- Network attackers beyond using TLS in deployment.
- Side channels (timing).
- Multi-tenant isolation (stretch goal only).

## 6. The core limitation, stated plainly

The gateway sees tool calls, their arguments and their results. It does not see which inputs influenced the model's decision. Provenance tracking can only stop attacks that move tracked data into an action. Attacks that change *which* action happens without moving data are handled only by coarse controls (grants, session taint, approvals), which cost task completion or human time. The evaluation reports both sides of this trade-off rather than a single "blocked X%" figure.

## 7. Fairness notes for the evaluation

- Tool labels encode deployer knowledge ("email bodies are external content"). They are written per tool from names and descriptions, never from knowledge of where AgentDojo places injections.
- Held-out suites (`banking`, `travel`) get their labels before any attack run on them.
- Approvals in eval are simulated (`deny_all` and `approve_all`); real approver behaviour is not measured.
