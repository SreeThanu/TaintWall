"""M1-5c: direct vs bridge for the workspace tools ground truth never executes."""

from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any

import pytest

pytest.importorskip("agentdojo")

from agentdojo.default_suites.v1.tools.types import EvenStatus

from eval.benchmark import load_suites
from eval.harness import connections
from eval.harness.connections import BridgeConnection
from eval.harness.env_bridge import EnvBridge
from eval.harness.frozen_clock import FIXED_NOW
from eval.harness.synthetic import (
    CANCEL_EVENT,
    INVALID_CASES,
    SYNTHETIC_TOOLS,
    VALID_CASES,
    Case,
    case_problems,
    differences,
    run_case,
)

PATHS = ["legacy", "http"]
ALL_CASES = VALID_CASES + INVALID_CASES
case_ids = [case.name for case in ALL_CASES]


@pytest.fixture(scope="module")
def suite() -> Any:
    return load_suites()["workspace"]


@pytest.fixture(scope="module")
def conns(suite: Any) -> Iterator[dict[str, BridgeConnection]]:
    bridge = EnvBridge(suite.tools)
    with ExitStack() as stack:
        yield {
            "legacy": stack.enter_context(connections.in_process(bridge)),
            "http": stack.enter_context(connections.over_http(bridge)),
        }


def test_cases_cover_exactly_the_tools_ground_truth_never_executes() -> None:
    assert {
        "cancel_calendar_event",
        "get_draft_emails",
        "get_file_by_id",
        "get_received_emails",
        "get_sent_emails",
        "search_contacts_by_email",
    } == SYNTHETIC_TOOLS
    assert {case.tool for case in VALID_CASES} == SYNTHETIC_TOOLS
    assert {case.tool for case in INVALID_CASES} == SYNTHETIC_TOOLS


@pytest.mark.parametrize("case", ALL_CASES, ids=case_ids)
def test_direct_run_meets_its_own_expectations(suite: Any, case: Case) -> None:
    assert case_problems(case, run_case(suite, case)) == []


@pytest.mark.parametrize("case", ALL_CASES, ids=case_ids)
def test_direct_run_is_deterministic(suite: Any, case: Case) -> None:
    assert differences(run_case(suite, case), run_case(suite, case)) == []


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("case", ALL_CASES, ids=case_ids)
def test_bridge_matches_direct(
    suite: Any, conns: dict[str, BridgeConnection], case: Case, path: str
) -> None:
    direct = run_case(suite, case)
    bridged = run_case(suite, case, conns[path])
    assert bridged.acted_on_env is True
    assert case_problems(case, bridged) == []
    assert bridged.text == direct.text  # byte-identical
    assert bridged.error == direct.error
    assert bridged.env_repr == direct.env_repr  # byte-identical


@pytest.mark.parametrize("path", ["direct", *PATHS])
def test_cancel_is_visible_in_post_env(
    suite: Any, conns: dict[str, BridgeConnection], path: str
) -> None:
    fresh = suite.load_and_inject_default_environment({})
    outcome = run_case(suite, CANCEL_EVENT, None if path == "direct" else conns[path])
    event_id = outcome.arguments["event_id"]
    before = fresh.calendar.events[event_id]
    after = outcome.env.calendar.events[event_id]
    assert before.status != EvenStatus.canceled
    assert after.status == EvenStatus.canceled
    assert before.participants

    # The notification is a real new email, stamped by the frozen clock.
    known = set(fresh.inbox.emails)
    new = [email for key, email in outcome.env.inbox.emails.items() if key not in known]
    assert len(new) == 1
    assert new[0].subject == f"Canceled: '{before.title}'"
    assert new[0].recipients == before.participants
    assert new[0].timestamp == FIXED_NOW

    # Nothing else in the calendar moved.
    others = {k: e for k, e in outcome.env.calendar.events.items() if k != event_id}
    assert others == {k: e for k, e in fresh.calendar.events.items() if k != event_id}


def test_each_run_starts_from_a_fresh_environment(
    suite: Any, conns: dict[str, BridgeConnection]
) -> None:
    """A write on one path must not leak into the next, or equivalence proves nothing."""
    first = run_case(suite, CANCEL_EVENT, conns["legacy"])
    second = run_case(suite, CANCEL_EVENT, conns["http"])
    assert second.before_repr == first.before_repr
    assert second.env is not first.env
    assert second.before_repr != second.env_repr


def test_valid_cases_count_as_executed_on_both_paths(suite: Any) -> None:
    bridge = EnvBridge(suite.tools)
    with connections.in_process(bridge) as b, connections.over_http(bridge) as c:
        for case in VALID_CASES:
            for conn in (b, c):
                run_case(suite, case, conn)
        for conn in (b, c):
            assert {tool for tool, n in conn.executed.items() if n} >= SYNTHETIC_TOOLS


def test_invalid_cases_with_required_arguments_do_not_count_as_executed(suite: Any) -> None:
    bridge = EnvBridge(suite.tools)
    with connections.in_process(bridge) as b:
        for case in INVALID_CASES:
            if case.expect_error is not None:
                assert run_case(suite, case, b).error is not None
                assert b.executed[case.tool] == 0
