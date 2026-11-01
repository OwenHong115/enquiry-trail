"""Unit tests for scripts/perf_measure.py percentile and exit-code logic."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "perf_measure.py"
_spec = importlib.util.spec_from_file_location("perf_measure", _SCRIPT)
assert _spec is not None and _spec.loader is not None
perf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(perf)


def test_percentile_nearest_rank_p95_100() -> None:
    assert perf._percentile_nearest_rank(list(range(1, 101)), 95) == 95


def test_percentile_nearest_rank_p95_20() -> None:
    assert perf._percentile_nearest_rank(list(range(1, 21)), 95) == 19


def test_percentile_nearest_rank_single_and_unsorted() -> None:
    assert perf._percentile_nearest_rank([42.0], 95) == 42.0
    assert perf._percentile_nearest_rank([5, 1, 3], 95) == 5


def test_stats_p95_is_nearest_rank() -> None:
    _p50, p95, mx = perf._stats(list(range(1, 101)))
    assert p95 == 95
    assert mx == 100


def test_evaluate_met_targets_no_errors_is_zero() -> None:
    assert perf._evaluate(500, 10000, 0) == 0
    assert perf._evaluate(10, 10, 0) == 0


def test_evaluate_missed_intake_target_is_nonzero() -> None:
    assert perf._evaluate(501, 10, 0) == 2


def test_evaluate_missed_ready_target_is_nonzero() -> None:
    assert perf._evaluate(10, 10001, 0) == 2


def test_evaluate_errors_is_nonzero_even_when_targets_met() -> None:
    assert perf._evaluate(10, 10, 1) == 2


def test_evaluate_missing_series_is_nonzero() -> None:
    assert perf._evaluate(None, None, 0) == 2
