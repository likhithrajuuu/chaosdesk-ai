from chaosdesk.schemas import Ticket, PriorityFacts
from chaosdesk.policies import assess_priority
from chaosdesk.interpreter import MockInterpreter

def main():
    ticket = Ticket.model_validate({
        "ticket_id": "TKT-003",
        "subject": "Production uploads blocked",
        "description": (
            "Our production workflow is blocked because uploads fail. "
            "We do not know whether other customers are affected "
            "or whether another browser works."
        )
    })

    facts = PriorityFacts(
        critical_service_unavailable=None,
        multiple_organizations_affected=None,
        critical_production_workflow_blocked=True,
        workaround_available=None,
        functionality_degraded=True,
        routine_request=False,
    )

    interpreter = MockInterpreter(expected_ticket=ticket, facts=facts)
    interpreted_facts = interpreter.interpret(ticket)
    assessment = assess_priority(interpreted_facts)

    print(f"Ticket: {ticket.ticket_id}")
    print(assessment.model_dump_json(indent=2))

if __name__ == "__main__":
    main()
