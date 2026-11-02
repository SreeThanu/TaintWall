# Draft issue for the MCP Python SDK (not filed)

Status: draft. Not searched for duplicates: `gh` was not authenticated when this was written, so search `modelcontextprotocol/python-sdk` before filing.

**Title:** Streamable HTTP client: one POST connection error closes the whole client, while an HTTP error fails only that request

**Version:** `mcp` 2.3.0, `httpx2` 2.13.1, Python 3.12.14, macOS. Both `mode="auto"` and `mode="legacy"`.

## What happens

If one POST for a JSON-RPC request raises a transport error such as `httpx2.ConnectError`, the exception is not resolved for that request. It leaves `_run_request_post` and reaches the task group in `streamable_http_client`. As a result:

1. The transport shuts down. The call gets `MCPError(CONNECTION_CLOSED)`, and so does every later call on that `Client`, even if the server is reachable again.
2. anyio cancels the task that entered the `Client` context, and `__aexit__` raises `ExceptionGroup(ConnectError)`. If that task holds other clients or services, such as a Starlette lifespan, they are cancelled too.

The cancellation in point 2 is normal anyio behaviour. The question for this issue is whether a per-request network failure should reach the task group at all.

## Why this looks unintended

`_handle_post_request` already turns other per-request failures into a `JSONRPCError` for the waiting caller: status >= 400, 202 for a request, an unfollowed redirect, an unparseable body, and an unexpected content type. The client keeps working after each of those. `post_writer` and the GET stream also catch their own exceptions. Request tasks are the unguarded path.

| Injected failure on one POST (server healthy) | That call | Next call | Task holding the client |
|---|---|---|---|
| HTTP 500 | `MCPError(INTERNAL_ERROR)` | succeeds | unaffected |
| `httpx2.ConnectError` | `MCPError(CONNECTION_CLOSED)` | `MCPError(CONNECTION_CLOSED)` | cancelled; `ExceptionGroup(ConnectError)` |

## Reproduction

Wrap a real `httpx2.AsyncHTTPTransport` so that it raises `httpx2.ConnectError` for one chosen POST, and pass it through `streamable_http_client(url, http_client=...)`. Taintwall's `tests/integration/test_upstream_supervision.py` has this as `FailNextPost`, with `test_one_connect_error_permanently_closes_the_client` and `test_one_http_500_fails_only_that_request`.

## Expected

The failed request gets an error, as it does for an HTTP error, and the client stays usable, so a brief connection failure doesn't require a new `Client`. Maintainers may prefer a close-on-connection-loss design. If so, it would help to document that a `Client` can cancel the task that entered it, and that long-lived applications should hold each client in its own task.

## Possibly related (observed, cause not confirmed)

In legacy mode, killing the server immediately after `Client` entry (no call made first) gave a different result. On all 4 runs, `post_writer` logged `Error in post_writer`, every call got `CONNECTION_CLOSED`, and the holding task was not cancelled. A failed notification POST inside `post_writer` would explain this, but that hasn't been confirmed. If it is right, the client ends up closed with no signal to the holder.

## Workaround

Enter each `Client` in its own long-lived task and treat the end of that task as loss of the connection.
