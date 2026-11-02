# History rewrite

History rewritten 2026-11-02 to correct commit metadata and remove local tooling files, before the first push. No remote existed at the time.

What changed in every commit:

- Author and committer set to one identity.
- Local tooling files removed, and tool co-author trailers removed from messages.
- Hashes cited inside commit messages updated to the new hashes (two subjects, marked in the table with their new text).

File contents are otherwise unchanged, and older file versions were not edited.

## Evidence

`runs/*/config.yaml` keep their original commit hashes as immutable evidence. To check a run, find its recorded hash in the "Old" column below; the "New" column is the same code in today's history. Docs cite hashes as "new (was old)".

| Run | Recorded commit (old) | Same code now (new) |
|---|---|---|
| `20261102T083616Z-passthrough-latency` | `a93d6b24885981ae01efdfa6086bbbee3008774a` | `248e4084105df870f0677b613ca9a67a7b5f8700` |
| `20261102T091402Z-passthrough-latency` | `1c882b0afdb76c91634b33ee743a91cde76dfecc` | `a58d77f0733502da710a5077d797ff78c128ec15` |

## Wording changed before the rewrite

ISSUES rows M0-1 and M0-1b now name `docs/CONTRIBUTING.md`. That file was created just before the rewrite and took over the project rules from a local file, so at the time of those tasks the work was done in that earlier file.

## Old to new

Paired by position. Subjects matched line by line with hex hashes masked, and the pairing agrees with `git-filter-repo`'s commit map.

| Old | New | Subject (new) |
|---|---|---|
| `398687c62f5e96c4ac695f0dd1101bce92783f2d` | `ebfec9d30caa2a4458c1765726840b0369f334cc` | docs: baseline before M0-1 |
| `dff7445f28f224aad8d7dbc5a29f5dc95fbf5422` | `07b0b8e82d72605380b7f2639d34025e800a9102` | M0-1: repo skeleton (uv, py3.12.14, pytest/ruff/mypy, smoke test); name: Taintwall |
| `01530e03db77f9a5912858de5a9d1e6057726491` | `06574867807dade5f2bcfdb67fc569284ef642e5` | M0-1b: rename Agent Gateway/gateway/ references to Taintwall/taintwall/ |
| `8cd469d96a8d7ee179f10d19460f3610647644dd` | `9239392f2177a043ffe374d9a2745e1fd05cb3ca` | M0-2: generic local setup in README; uv cache/python/link-mode env documented |
| `1802717728d690275bd9bfab445239e0d8a7941e` | `526b751ace4242f88b2f06b5c74df1b5cfd03cb5` | M0-3: pin mcp/agentdojo/anthropic; benchmark v1.2.2 pinned + verified; task counts from package (949 cases) |
| `663c9f845d02de205e4aed71b6438a79dea4075d` | `04a5b7873f562cf1c8ca8c8a8a112b2faa012e0a` | M1-1: PRIMER (MCP v2, protocol eras, grant-scoped provenance, error mapping); D19–D21; docs aligned |
| `485fe6aed4d515e2702f1a66e6e13247db185a4f` | `0af22445b4d0d87ac3bc05e152e17b3dbe696801` | M1-5a: EnvBridge spike passes — ground-truth replay byte-identical on all dev-suite tasks via JSON-RPC and HTTP; frozen clock; redaction test |
| `3df0128be7211add750ecb119178169608f2cc50` | `c444d0ab42c9fd47b8fccc5eb53461f38e32b344` | ISSUES: add M1-5c (coverage gap before LLM runs) |
| `3f5d43b7face7dd34722256859ea2460da8ade7b` | `13b71042c25cc97e52043e0b4940d8e6e2bb6ee5` | M1-2: test upstream (echo, read_message, fail x3, echo_request, slow, large) |
| `1aa35991f1e8211e7e112beb518e5447df0b89d6` | `3c945b8452a458076385cd9d3a9c6d2309e9e5d1` | M1-3: pass-through gateway — namespaced multi-upstream proxy, redacted error mapping, fail-closed JSONL audit, per-upstream isolation, leak tests |
| `38c1de64bfc121c82f711bacc079679cd8ef1788` | `b577b02ac49243afa7fd6d63210a0d4be8a3af0e` | D25: characterize dead-upstream cancellation; reword and draft SDK issue |
| `a93d6b24885981ae01efdfa6086bbbee3008774a` | `248e4084105df870f0677b613ca9a67a7b5f8700` | D27: record runs/ tracking rule |
| `f6dbb535deffde7275ee94c55f3466a70810b0fc` | `4e51685d795b5066076ea174c251384f3e9a411d` | M1-3: pass-through latency run on clean commit 248e408 |
| `d6dbb1c0244c0c6d407f400d2ff2b48ec3921205` | `e9055c46447f741e67454cbde0a7fd8c60a5b7ca` | ISSUES: mark M1-3 done, cite latency run 20261102T083616Z |
| `b46f8a3f0c0e55243801274d4493d59b2639837b` | `9509f89df95ee52f6a52f3da95606fbb3251b6d5` | ISSUES: track possible forwarded:true audit inaccuracy |
| `1c882b0afdb76c91634b33ee743a91cde76dfecc` | `a58d77f0733502da710a5077d797ff78c128ec15` | M1-4: minimal grants — hashed bearer tokens, auth middleware, grant-scoped tools, aggregated rejection audit |
| `e20d937a770f36d022281fe7a46d796f5f6068d3` | `c426cb7fd3afeaa8418f326cd66c46a06ebc5247` | M1-4: latency run on clean commit a58d77f |
| `4f5ab5b3d74620220c6c0c137149c44fe3352669` | `b618440eed11f0bdf95759287e771947a9124ee1` | ISSUES: mark M1-4 done, cite latency run 20261102T091402Z |
| `9b58da15ecbcaba97cc4ebd441bfbc542cbbd688` | `c3e8106c0c9d0e3bd79cc960a1c693590ed37866` | M1-3b: fix benchmark crash with --runs-dir outside the repo |
| `2b00b6a5d1f0589899d70b403083dade8c303ffc` | `e3daeb74c10e6561e034b4784f423f68c43f03cc` | docs: add CONTRIBUTING with project rules; repoint references to it |
| `d90c5dfe677749214c28f3af043c341b50ddaa11` | `5b731e69fae4f3bb687f52635242d41288b1b3a7` | gitignore: ignore local tooling files |
| `d916ed400524f59539b70ea55b6786582f22f78a` | `492a317e013fc4cd8f1e3c7df203617a9d1a8b75` | docs: remove local tooling and machine references from ISSUES and PRIMER |
