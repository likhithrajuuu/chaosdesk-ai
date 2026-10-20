from typing import Protocol

from chaosdesk.schemas import Ticket, PriorityFacts


class TicketInterpreter(Protocol):
    """The interface shared by mock and real AI interpreters."""

    def interpret(self, ticket: Ticket) -> PriorityFacts:
        ...


class MockInterpreter:
    """Return predefined facts for a specific example ticket."""

    def __init__(self, expected_ticket: Ticket, facts: PriorityFacts):
        # Store copies so external changes do not alter the mock's setup.
        self._expected_ticket = expected_ticket.model_copy(deep=True)
        self._facts = facts.model_copy(deep=True)

    def interpret(self, ticket: Ticket) -> PriorityFacts:
        if ticket != self._expected_ticket:
            raise ValueError(
                "No mock interpretation configured for this ticket."
            )

        # Callers can modify the result without changing stored facts.
        return self._facts.model_copy(deep=True)
