"""M1-5c: synthetic calls for the workspace tools ground truth never executes.

Arguments are read from a freshly loaded environment, so a case can't name an id that doesn't exist.
Direct and bridged runs go through the same `run_function` call under the frozen clock, and each
run gets its own environment. Environments are compared as `repr(model_dump())`, which unlike `==`
also tells `1` from `1.0`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from agentdojo.agent_pipeline.tool_execution import tool_result_to_str
from agentdojo.default_suites.v1.tools.types import EmailStatus, EvenStatus
from agentdojo.functions_runtime import FunctionsRuntime

from eval.harness.bridged_runtime import bridged_runtime_class
from eval.harness.connections import BridgeConnection
from eval.harness.frozen_clock import frozen_clock

SEEDED_DRAFT_SUBJECT = "Synthetic draft"
# Passed to the tools that take no arguments. AgentDojo's input model ignores it, so they have no
# invalid-argument error to compare; the cases assert every path ignores it the same way.
UNEXPECTED_ARGUMENT = {"unexpected": "value"}


def _no_arguments(env: Any) -> dict[str, Any]:
    return {}


def _no_setup(env: Any) -> None:
    return None


def _unexpected_argument(env: Any) -> dict[str, Any]:
    return dict(UNEXPECTED_ARGUMENT)


def _absent_from(values: Iterable[str]) -> str:
    """A value that is no substring of any of `values`, so it can't match any lookup."""
    existing = list(values)
    n = 0
    while any(f"missing-{n}" in value for value in existing):
        n += 1
    return f"missing-{n}"


def _cancellable_event(env: Any) -> Any:
    return next(
        event
        for _, event in sorted(env.calendar.events.items())
        if event.status == EvenStatus.confirmed and event.participants
    )


def _first_file_id(env: Any) -> str:
    return str(min(env.cloud_drive.files))


def _email_marker(email: Any) -> str:
    return f"id_: '{email.id_}'"


def _seed_draft(env: Any) -> None:
    """The default environment has no drafts; add one, built from a real email, on every path."""
    draft_id = env.inbox._get_next_id()
    env.inbox.emails[draft_id] = env.inbox.received[0].model_copy(
        update={"id_": draft_id, "status": EmailStatus.draft, "subject": SEEDED_DRAFT_SUBJECT}
    )


@dataclass(frozen=True)
class Case:
    name: str
    tool: str
    arguments: Callable[[Any], dict[str, Any]]
    must_contain: Callable[[Any, dict[str, Any]], str]  # expected in the text of a successful call
    prepare: Callable[[Any], None] = _no_setup
    expect_error: str | None = None  # the error's "ErrorType: " prefix, if the call must fail
    writes: bool = False


def _no_text(env: Any, arguments: dict[str, Any]) -> str:
    return ""


CANCEL_EVENT = Case(
    "cancel_existing_event",
    "cancel_calendar_event",
    lambda env: {"event_id": _cancellable_event(env).id_},
    lambda env, args: f"Event with ID {args['event_id']} has been canceled",
    writes=True,
)

VALID_CASES = [
    CANCEL_EVENT,
    Case("drafts_default_empty", "get_draft_emails", _no_arguments, lambda env, args: "[]"),
    Case(
        "drafts_seeded",
        "get_draft_emails",
        _no_arguments,
        lambda env, args: SEEDED_DRAFT_SUBJECT,
        prepare=_seed_draft,
    ),
    Case(
        "file_existing",
        "get_file_by_id",
        lambda env: {"file_id": _first_file_id(env)},
        lambda env, args: env.cloud_drive.files[args["file_id"]].filename,
    ),
    Case(
        "received_all",
        "get_received_emails",
        _no_arguments,
        lambda env, args: _email_marker(env.inbox.received[0]),
    ),
    Case(
        "sent_all",
        "get_sent_emails",
        _no_arguments,
        lambda env, args: _email_marker(env.inbox.sent[0]),
    ),
    Case(
        "contact_existing",
        "search_contacts_by_email",
        lambda env: {"query": env.inbox.contact_list[0].email},
        lambda env, args: args["query"],
    ),
]

INVALID_CASES = [
    Case(
        "cancel_absent_event",
        "cancel_calendar_event",
        lambda env: {"event_id": _absent_from(env.calendar.events)},
        _no_text,
        expect_error="ValueError: ",
    ),
    Case(
        "file_absent",
        "get_file_by_id",
        lambda env: {"file_id": _absent_from(env.cloud_drive.files)},
        _no_text,
        expect_error="ValueError: ",
    ),
    Case(
        "contact_absent",
        "search_contacts_by_email",
        lambda env: {"query": _absent_from(c.email for c in env.inbox.contact_list)},
        _no_text,
        expect_error="ValueError: ",
    ),
    Case("drafts_unexpected_argument", "get_draft_emails", _unexpected_argument, _no_text),
    Case(
        "received_unexpected_argument",
        "get_received_emails",
        _unexpected_argument,
        lambda env, args: _email_marker(env.inbox.received[0]),
    ),
    Case(
        "sent_unexpected_argument",
        "get_sent_emails",
        _unexpected_argument,
        lambda env, args: _email_marker(env.inbox.sent[0]),
    ),
]

SYNTHETIC_TOOLS = {case.tool for case in VALID_CASES}


@dataclass(frozen=True)
class Outcome:
    arguments: dict[str, Any]
    text: str
    error: str | None
    before_repr: str
    env_repr: str
    env: Any
    must_contain: str
    acted_on_env: bool | None  # None on the direct path


def run_case(suite: Any, case: Case, connection: BridgeConnection | None = None) -> Outcome:
    """Run one call on a fresh environment, directly or over MCP, with the clock frozen."""
    env = suite.load_and_inject_default_environment({})
    case.prepare(env)
    arguments = case.arguments(env)
    before_repr = repr(env.model_dump())
    runtime: Any = (
        FunctionsRuntime(suite.tools)
        if connection is None
        else bridged_runtime_class(connection)(suite.tools)
    )
    try:
        with frozen_clock():
            result, error = runtime.run_function(env, case.tool, arguments)
        acted = None if connection is None else connection.holder.is_bound_to(env)
    finally:
        if connection is not None:
            connection.holder.clear()
    return Outcome(
        arguments,
        tool_result_to_str(result),
        error,
        before_repr,
        repr(env.model_dump()),
        env,
        case.must_contain(env, arguments),
        acted,
    )


def case_problems(case: Case, outcome: Outcome) -> list[str]:
    """Reasons a run did not do what its case expects, so equal results can't be empty results."""
    problems = []
    if case.expect_error is None:
        if outcome.error is not None:
            problems.append(f"unexpected error: {outcome.error}")
        if outcome.must_contain not in outcome.text:
            problems.append(f"text lacks {outcome.must_contain!r}")
    elif outcome.error is None or not outcome.error.startswith(case.expect_error):
        problems.append(f"expected {case.expect_error!r}, got {outcome.error!r}")
    if case.writes and outcome.env_repr == outcome.before_repr:
        problems.append("post-env unchanged by a writing tool")
    if not case.writes and outcome.env_repr != outcome.before_repr:
        problems.append("post-env changed by a read or failed call")
    return problems


def differences(direct: Outcome, other: Outcome) -> list[str]:
    fields = ("arguments", "text", "error", "before_repr", "env_repr")
    return [field for field in fields if getattr(direct, field) != getattr(other, field)]
