import pytest

pytest.importorskip("agentdojo")

from eval import benchmark
from eval.benchmark import BenchmarkPinError


def test_pinned_benchmark_version_is_registered() -> None:
    assert benchmark.BENCHMARK_VERSION in benchmark.registered_versions()


def test_load_suites_returns_all_four_suites() -> None:
    assert set(benchmark.load_suites()) == benchmark.SUITE_NAMES


def test_unknown_benchmark_version_is_rejected() -> None:
    # AgentDojo's own get_suites() silently returns {} for an unknown version.
    with pytest.raises(BenchmarkPinError, match="not registered"):
        benchmark.load_suites("v9.9.9")


def test_package_version_mismatch_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(benchmark, "version", lambda _name: "0.0.0-tampered")
    with pytest.raises(BenchmarkPinError, match="installed"):
        benchmark.load_suites()
