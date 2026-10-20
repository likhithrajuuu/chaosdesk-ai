"""Single-ticket JSON CLI. Diagnostics go to stderr; results go to stdout."""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from chaosdesk.analysis import AIAnalyzer, LocalAnalyzer
from chaosdesk.retrieval import DEFAULT_KB_DIR
from chaosdesk.schemas import Ticket
from chaosdesk.triage import TriageAgent


def main(argv=None):
    parser = argparse.ArgumentParser(description="CloudCrate support triage; produces drafts only")
    parser.add_argument("--ticket", type=Path, help="Ticket JSON file; defaults to stdin")
    parser.add_argument("--mode", choices=("mock", "ai"), default="mock")
    parser.add_argument("--kb-dir", type=Path, default=DEFAULT_KB_DIR)
    parser.add_argument("--model", help="OpenAI model; defaults to OPENAI_MODEL or gpt-4.1-mini")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR,
                        format="%(levelname)s %(message)s")
    try:
        raw = args.ticket.read_text(encoding="utf-8") if args.ticket else sys.stdin.read()
        ticket = Ticket.model_validate(json.loads(raw))
    except (ValueError, OSError, UnicodeError):
        print(json.dumps({"error": "invalid_ticket", "message":
              "Provide a JSON object with nonblank ticket_id, subject, and description."}))
        return 2

    load_dotenv()
    if args.mode == "ai" and not os.getenv("OPENAI_API_KEY"):
        print(json.dumps({"error": "configuration_error", "message": "Set OPENAI_API_KEY to use AI mode."}))
        return 2
    try:
        analyzer = (AIAnalyzer(model=args.model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
                    if args.mode == "ai" else LocalAnalyzer())
        result = TriageAgent(analyzer=analyzer, kb_dir=args.kb_dir).triage(ticket)
    except Exception:
        print(json.dumps({"error": "triage_failed", "message": "Triage could not complete; request human review."}))
        return 1
    print(result.model_dump_json(indent=2))
    return 0
