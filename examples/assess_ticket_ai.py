"""Run from the project root: python -m examples.assess_ticket_ai."""

import os

from dotenv import load_dotenv

from chaosdesk.interpreter import AIInterpreter, InterpretationError
from chaosdesk.schemas import Ticket
from chaosdesk.triage import assess_ticket


def main():
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY in your environment or .env file.")

    ticket = Ticket(
        ticket_id="TKT-003",
        subject="Production uploads blocked",
        description=(
            "Our production workflow is blocked because uploads fail. "
            "We do not know whether other customers are affected "
            "or whether another browser works."
        ),
    )
    interpreter = AIInterpreter(model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
    try:
        assessment = assess_ticket(ticket, interpreter)
    except InterpretationError as error:
        raise SystemExit(str(error)) from None

    print(assessment.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
