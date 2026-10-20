import pytest
from pydantic import ValidationError

from chaosdesk.schemas import Ticket
from chaosdesk.schemas import TriageDecision

def test_valid_ticket():
    ticket = Ticket(
        ticket_id="TKT-001",
        subject="Cannot Log In",
        description="I forgot my password",
    )

    assert ticket.ticket_id == "TKT-001"
    assert ticket.description == "I forgot my password"

@pytest.mark.parametrize("description", ["", " ", "\n\t"])
def test_rejects_blank_description(description):
    with pytest.raises(ValidationError):
        Ticket(
            ticket_id="TKT-001",
            subject="Cannot Log In",
            description=description
        )

@pytest.fixture
def decision_data():
    return {
        "ticket_id": "TKT-001",
        "category": "ACCESS",
        "priority": "P3",
        "confidence": 0.8,
        "customer_impact": "One user cannot access their account.",
        "evidence": [],
        "recommended_action": "REQUEST_INFORMATION",
        "response_draft": (
            "Have you tried resetting your password, "
            "and did you receive the reset email?"
        ),
        "reasoning_summary": (
            "The user reports a login problem. No supporting "
            "documentation has been retrieved."
        ),
        "missing_information": ["Whether password reset was attempted"],
        "requires_human_review": True,
    }

def test_valid_triage_decisino(decision_data):
    decision = TriageDecision(**decision_data)
    assert decision.category.value == "ACCESS"
    assert decision.priority.value == "P3"
    assert decision.evidence == []

@pytest.mark.parametrize(
    "field, value",
    [
        ("category", "SOMETHING_ELSE"),
        ("priority", "P9"),
        ("recommended_action", "DELETE_ACCOUNT"),
        ("confidence", -0.1),
        ("confidence", 1.1),
        ("confidence", float("nan")),
    ],
)
def test_rejects_invalid_decision_values(decision_data, field, value):
    decision_data[field] = value

    with pytest.raises(ValidationError):
        TriageDecision(**decision_data)

def test_rejects_incomplete_evidence(decision_data):
    decision_data["evidence"] = [
        {
            "document_id": "KB-001"
        }
    ]

    with pytest.raises(ValidationError):
        TriageDecision(**decision_data)