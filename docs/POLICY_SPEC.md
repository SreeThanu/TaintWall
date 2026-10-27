# Policy Specification (v0)

Policies are YAML files in `policies/`, validated against a schema at load and versioned by content hash. The engine is a pure function: `(call context, policy set) -> decision`.

## 1. Decision semantics

- Possible outcomes per rule: `allow`, `hold` (needs human approval), `deny`, `uncertain` (send to LLM judge if enabled, else treat as `hold`).
- **Combination:** `deny` beats `hold` beats `uncertain` beats `allow`.
- **Default:** if no rule matches, `deny`.
- **Errors:** any evaluation error is `deny` (fail closed) and is logged with the error type.
- Every decision records: `policy_version`, matching `rule_ids`, provenance `evidence`, and stage latencies.

## 2. Call context available to rules

| Field | Meaning |
|---|---|
| `agent.id`, `agent.tags` | Calling agent identity |
| `session.id`, `session.tainted` | Session; whether untrusted content has entered it |
| `grant.tools`, `grant.purpose`, `grant.active` | Current task grant |
| `tool.name`, `tool.namespace`, `tool.class` | Called tool and its class from tool labels (e.g., `read_external`, `send_external`, `money_movement`, `destructive`) |
| `args.<name>` | Call arguments |
| `prov.<arg>.labels` | Provenance labels found in that argument: `untrusted_source`, `sensitive`, `user_supplied` |
| `prov.any_untrusted`, `prov.any_sensitive` | Shortcuts across all arguments |

## 3. Tool labels (separate files)

Tool labels describe tools, not attacks. They are written from tool names and descriptions only.

```yaml
# policies/tool_labels/workspace.yaml
namespace: workspace
tools:
  get_unread_emails:     { class: read_external,  output_trust: untrusted }
  get_received_emails:   { class: read_external,  output_trust: untrusted }
  search_emails:         { class: read_external,  output_trust: untrusted }
  search_contacts_by_name: { class: read_internal, output_trust: trusted, sensitive_fields: [email] }
  send_email:            { class: send_external,  recipient_args: [recipients, cc, bcc] }
  delete_email:          { class: destructive }
```

`output_trust: untrusted` means atoms extracted from that tool's results get the `untrusted_source` label. `sensitive_fields` marks atoms as `sensitive`.

## 4. Rule syntax

```yaml
version: 0
name: default-dev
rules:
  - id: grant-required
    when: { grant.active: false }
    effect: deny
    reason: "No active task grant"

  - id: outside-grant
    when: { tool.in_grant: false }
    effect: hold
    reason: "Tool not in the declared task"

  - id: untrusted-into-outgoing
    when:
      tool.class: [send_external, money_movement]
      prov.any_untrusted: true
    effect: deny
    reason: "Data from untrusted content used in an outgoing action"

  - id: sensitive-to-new-recipient
    when:
      tool.class: send_external
      prov.any_sensitive: true
      recipient.known: false
    effect: hold
    reason: "Sensitive data to a recipient not seen in trusted sources"

  - id: tainted-session-consequential   # session-taint mode; enabled per config
    enabled_in_modes: [session_taint]
    when:
      session.tainted: true
      tool.class: [send_external, money_movement, destructive]
    effect: hold
    reason: "Session has read untrusted content"

  - id: reads-allowed
    when: { tool.class: [read_internal, read_external] }
    effect: allow
```

Matching rules:
- A list value means "any of".
- All keys inside `when` must match (AND). Use separate rules for OR.
- `recipient.known` is true if every recipient atom was seen in a trusted source or in the user's original request.

## 5. Modes (for evaluation)

| Mode | Rules active |
|---|---|
| `pass_through` | none; everything allowed (baseline, still audited) |
| `grants_only` | grant rules |
| `sequence_rules` | hand-written "tool A then tool B" patterns (Invariant-style baseline) |
| `provenance` | grant + provenance rules |
| `session_taint` | grant + coarse taint rule |
| `provenance+judge` | provenance, with `uncertain` sent to the LLM judge |

## 6. Testing requirements

- Schema tests: invalid policies fail to load with a clear error.
- Table-driven tests: (context, policy) → expected decision and rule ids.
- Property test: if any matching rule is `deny`, the decision is `deny`.
- Every new rule ships with at least one positive and one negative test.
