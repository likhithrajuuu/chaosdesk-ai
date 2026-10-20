import json

import pytest

from chaosdesk.analysis import LocalAnalyzer
from chaosdesk.harness import (
    FailingAnalyzer, InvalidAnalyzer, faults, main, replay, run_ticket, validate_cases,
)
from chaosdesk.schemas import Ticket


@pytest.fixture
def case():
    return {
        "name": "reset", "ticket": {
            "ticket_id": "TEST-1", "subject": "Forgot password",
            "description": "I forgot my password.",
        },
        "expected": {"category": "ACCESS", "priority": "P3", "action": "SEND_KNOWN_FIX"},
    }


def test_replay_traces_tool_calls_and_checks_repeatability(case):
    report = replay([case], repeat=3)
    assert report["accepted"]
    assert report["executions"] == 3
    assert report["results"][0]["stable"]
    for run in report["results"][0]["runs"]:
        assert [event["tool"] for event in run["events"]] == ["analysis", "retrieval"]
        assert all(event["status"] == "ok" for event in run["events"])
    assert report["latency_ms"]["p95"] >= report["latency_ms"]["p50"] >= 0


def test_replay_detects_changes_outside_summary_fields(case):
    class ChangingAnalyzer:
        calls = 0

        def analyze(self, ticket):
            self.calls += 1
            analysis = LocalAnalyzer().analyze(ticket)
            analysis.customer_impact = f"Impact summary version {self.calls}"
            return analysis

    report = replay([case], repeat=2, analyzer=ChangingAnalyzer())
    assert not report["accepted"]
    assert not report["results"][0]["stable"]


def test_replay_fails_on_wrong_expected_decision(case):
    case["expected"]["priority"] = "P0"
    report = replay([case])
    assert not report["accepted"]
    assert report["results"][0]["stable"]


def test_invalid_tickets_are_not_silently_counted_as_success(case):
    case["ticket"] = None
    assert not replay([case])["accepted"]
    case["invalid"] = True
    report = replay([case])
    assert report["accepted"]
    assert report["executions"] == 0
    assert report["latency_ms"]["p50"] is None


def test_fault_matrix_verifies_graceful_degradation():
    report = faults()
    assert report["cases"] == 18
    assert report["accepted"], [row for row in report["results"] if not row["passed"]]
    assert {row["scenario"] for row in report["results"]} == {
        "analysis_timeout", "invalid_analysis", "retrieval_error",
        "empty_retrieval", "unsafe_document", "combined_failure",
    }


@pytest.mark.parametrize("analyzer", [FailingAnalyzer(), InvalidAnalyzer()])
def test_trace_reports_handled_analyzer_failure(analyzer, case):
    decision, trace = run_ticket(Ticket.model_validate(case["ticket"]), analyzer=analyzer)
    assert decision.requires_human_review
    assert trace["status"] == "ok"
    assert trace["events"][0]["status"] == "error"


def test_reports_exclude_raw_ticket_and_exception_details(case):
    secret = "private-token-123"
    case["ticket"]["description"] += " " + secret

    class PrivateErrorAnalyzer:
        def analyze(self, ticket):
            raise RuntimeError(secret)

    report = replay([case], analyzer=PrivateErrorAnalyzer())
    serialized = json.dumps(report)
    assert secret not in serialized
    assert case["ticket"]["ticket_id"] not in serialized
    assert "execution_or_validation_failed" not in serialized  # Fallback completed.


def test_unhandled_agent_failure_fails_replay(case, monkeypatch):
    def fail(*args):
        raise RuntimeError("sensitive failure")
    monkeypatch.setattr("chaosdesk.harness.TriageAgent.triage", fail)
    report = replay([case])
    assert not report["accepted"]
    assert report["results"][0]["runs"][0]["error"] == "execution_or_validation_failed"
    assert "sensitive failure" not in json.dumps(report)


def test_cli_saves_identical_json_report_and_returns_failure_for_bad_labels(case, tmp_path, capsys):
    case["expected"]["priority"] = "P0"
    dataset = tmp_path / "dataset.json"
    dataset.write_text(json.dumps([case]))
    output = tmp_path / "reports/result.json"
    assert main(["replay", "--dataset", str(dataset), "--output", str(output)]) == 1
    assert json.loads(output.read_text()) == json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("cases", [[], {}, [{"name": "missing ticket"}]])
def test_cli_rejects_bad_dataset_configuration(cases, tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(cases))
    assert main(["--dataset", str(path)]) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "invalid_dataset"}


def test_case_configuration_checks_expectation_types(case):
    case["expected"]["evidence"] = 42
    with pytest.raises(ValueError):
        validate_cases([case])


def test_repeat_cannot_be_zero(case):
    with pytest.raises(ValueError):
        replay([case], repeat=0)


def test_cli_fault_suite_does_not_need_dataset(tmp_path, capsys):
    assert main(["faults", "--dataset", str(tmp_path / "missing")]) == 0
    assert json.loads(capsys.readouterr().out)["suites"]["faults"]["accepted"]
