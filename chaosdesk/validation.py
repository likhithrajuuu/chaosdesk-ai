"""Validate the output contract and citations against actual retrieval results."""

from chaosdesk.schemas import RecommendedAction, TriageDecision


def validate_decision(decision, ticket, documents):
    result = TriageDecision.model_validate(decision.model_dump())
    if result.ticket_id != ticket.ticket_id:
        raise ValueError("Decision ticket ID mismatch")
    sources = {doc.document_id: doc.text for doc in documents}
    for item in result.evidence:
        if item.document_id not in sources or item.excerpt not in sources[item.document_id]:
            raise ValueError("Evidence is not supported by retrieved documents")
    if result.recommended_action == RecommendedAction.SEND_KNOWN_FIX and not result.evidence:
        raise ValueError("A known fix needs supporting evidence")
    return result
