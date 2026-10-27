# Decisions

Newest at the bottom. Status: `accepted`, `proposed` (Thanu to confirm), `superseded`.

| ID | Date | Decision | Why | Status |
|---|---|---|---|---|
| D1 | 2026-10-27 | Build an MCP gateway (control plane for agent tool calls) as the primary portfolio project | Real problem companies pay for (e.g., Clawvisor, YC Spring 2026); pure software; uses MCP experience; exercises system design; scored automatically by AgentDojo | accepted |
| D2 | 2026-10-27 | Python 3.12 + MCP Python SDK 2.x + Starlette/uvicorn | Thanu's strongest language; official SDK; measure latency before considering anything faster | accepted |
| D3 | 2026-10-27 | Gateway is both an MCP server (to agents) and an MCP client (to upstreams), with namespaced tools | Drop-in for any MCP client; one URL change | accepted |
| D4 | 2026-10-27 | Policy engine is a pure function over a call context; policies in versioned YAML | Testable, fast, explainable; no I/O on the decision path except one state lookup | accepted |
| D5 | 2026-10-27 | Combination deny > hold > uncertain > allow; default deny; errors deny | Fail closed by construction | accepted |
| D6 | 2026-10-27 | Provenance by atom extraction + normalised hash matching (+ shingles for long text), not model-internal tracking | A proxy only sees tool I/O; atoms cover AgentDojo-style attacks (addresses, IBANs, URLs); limits documented and measured | accepted |
| D7 | 2026-10-27 | SQLite + in-memory stores until M3; Postgres + Redis behind repository interfaces from M3 | 8 GB laptop, full internal disk; avoid Docker early; interfaces keep the swap cheap | accepted |
| D8 | 2026-10-27 | Eval harness separate from gateway; AgentDojo tools reached through an in-process EnvBridge MCP server behind the real gateway | Measures the real network path while keeping AgentDojo's environment checks valid | proposed (validate in M1-5) |
| D9 | 2026-10-27 | Build the AgentDojo pipeline in code with `AnthropicLLM(model="claude-haiku-5-5")` | Current Claude models are not in AgentDojo's CLI model list; Haiku 5.5 is cheap | accepted |
| D10 | 2026-10-27 | Dev suites `workspace`, `slack`; held-out `banking`, `travel`, labelled blind before any attack run | Prevents tuning on test | accepted |
| D11 | 2026-10-27 | Simulated approver in eval (`deny_all`, `approve_all`), both reported | Human approval behaviour cannot be measured here; bounds the outcome | accepted |
| D12 | 2026-10-27 | Dashboard server-rendered (Jinja), no frontend framework | Web is not the focus; keeps time on the backend | accepted |
| D13 | 2026-10-27 | Audit writes go through a queue, never block decisions; backpressure policy to be decided in M3-5 | Logging must not add latency; durability trade-off made explicit | proposed |
| D14 | 2026-10-27 | Project name Taintwall; import package `taintwall` | Names an existing project already uses were avoided (agentgateway, Gatehouse, Sluice, Portcullis, Chokepoint) | accepted |
