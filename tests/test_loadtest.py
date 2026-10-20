import pytest

from chaosdesk.loadtest import run_load


def test_load_harness_exercises_bounded_concurrency_and_retries():
    result = run_load(tickets=12, workers=3, concurrency=4, latency_ms=20,
                      rate_limit_every=5, timeout=15)
    assert result["accepted"], result
    assert 1 < result["peak_model_concurrency"] <= 3
    assert result["model_attempts"] > result["tickets"]
    assert result["injected_rate_limits"] > 0
    assert result["completed"] == 12


def test_load_harness_does_not_hide_exhausted_retries():
    result = run_load(tickets=2, workers=1, concurrency=1, latency_ms=0,
                      rate_limit_every=1, timeout=10)
    assert not result["accepted"]
    assert result["failed"] == 2
    assert result["model_attempts"] == 6


def test_load_harness_enforces_latency_gate():
    result = run_load(tickets=2, workers=1, latency_ms=10, max_p95_ms=0.001)
    assert not result["accepted"]
    assert result["completed"] == 2


def test_load_harness_rejects_unbounded_input():
    with pytest.raises(ValueError):
        run_load(tickets=1000000)
