"""Repeatable evaluation: python -m chaosdesk.evaluate [--mode ai]."""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from chaosdesk.analysis import AIAnalyzer, LocalAnalyzer
from chaosdesk.retrieval import DEFAULT_KB_DIR, retrieve_documents
from chaosdesk.schemas import Ticket, TriageDecision
from chaosdesk.triage import TriageAgent
from chaosdesk.validation import validate_decision


ROOT = Path(__file__).resolve().parent.parent


def evaluate(analyzer, cases, thresholds):
    counts = {key: [0, 0] for key in thresholds}
    failures = []

    def record(metric, passed, name):
        counts[metric][1] += 1
        counts[metric][0] += int(passed)
        if not passed:
            failures.append({"case": name, "metric": metric})

    for case in cases:
        name = case["name"]
        try:
            ticket = Ticket.model_validate(case["ticket"])
        except ValidationError:
            record("invalid_input_rejection", case.get("invalid", False), name)
            continue
        if case.get("invalid"):
            record("invalid_input_rejection", False, name)
            continue
        kb = ROOT / case["kb"] if "kb" in case else DEFAULT_KB_DIR
        try:
            decision = TriageAgent(analyzer=analyzer, kb_dir=kb).triage(ticket)
            TriageDecision.model_validate_json(decision.model_dump_json())
        except Exception:
            for metric in ("schema_validity", "category_accuracy", "priority_accuracy", "action_accuracy"):
                record(metric, False, name)
            continue
        expected = case["expected"]
        record("schema_validity", True, name)
        record("category_accuracy", decision.category == expected["category"], name)
        record("priority_accuracy", decision.priority == expected["priority"], name)
        record("action_accuracy", decision.recommended_action == expected["action"], name)
        try:
            docs = retrieve_documents(f"{ticket.subject} {ticket.description}", kb_dir=kb)
        except Exception:
            docs = []
        try:
            validate_decision(decision, ticket, docs)
            valid = True
        except ValueError:
            valid = False
        record("citation_validity", valid, name)
        if expected.get("evidence"):
            record("evidence_recall", set(expected["evidence"]) <= {
                item.document_id for item in decision.evidence
            }, name)
        if expected["priority"] in ("P0", "P1"):
            record("urgent_escalation_accuracy", decision.recommended_action == expected["action"], name)
        if expected.get("review"):
            record("review_recall", decision.requires_human_review, name)

    scores = {key: passed / total if total else 0.0 for key, (passed, total) in counts.items()}
    return {
        "cases": len(cases), "scores": scores,
        "counts": {key: {"passed": passed, "total": total} for key, (passed, total) in counts.items()},
        "thresholds": thresholds,
        "accepted": all(scores[key] >= threshold for key, threshold in thresholds.items()),
        "failures": failures,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "ai"), default="mock")
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/evaluation.json")
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)
    load_dotenv()
    if args.mode == "ai" and not os.getenv("OPENAI_API_KEY"):
        parser.error("Set OPENAI_API_KEY for AI evaluation (makes paid requests).")
    model = args.model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    analyzer = AIAnalyzer(model=model) if args.mode == "ai" else LocalAnalyzer()
    report = evaluate(analyzer, json.loads(args.dataset.read_text()),
                      json.loads((ROOT / "data/acceptance.json").read_text()))
    report["mode"] = args.mode
    report["model"] = model if args.mode == "ai" else "local-keyword-baseline"
    print(json.dumps(report, indent=2))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
