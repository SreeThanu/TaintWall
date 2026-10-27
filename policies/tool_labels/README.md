# Tool labels

One YAML file per tool namespace. Format: `docs/POLICY_SPEC.md` §3.

Rules for writing labels (see `docs/THREAT_MODEL.md` §7):
- Label each tool from its **name and description only**. Never look at where AgentDojo places injections.
- Dev suites (`workspace`, `slack`): written in task M2-2, reviewed by Thanu.
- Held-out suites (`banking`, `travel`): written in task M2-8 and committed **before** any attack run on them. Record the commit hash in `docs/EVAL_AND_BUDGET.md`.
