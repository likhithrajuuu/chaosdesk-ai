"""Reviewed response templates: retrieved text is never executed or sent verbatim."""

from chaosdesk.schemas import RecommendedAction


def generate_response(action, fix=None):
    templates = {
        RecommendedAction.REQUEST_INFORMATION: (
            "Please share the affected service, scope, approximate start time, "
            "and any error message. For billing, include the invoice or transaction "
            "ID, amount, and date. Do not share passwords or payment-card details."
        ),
        RecommendedAction.CREATE_INCIDENT: (
            "Your report indicates a potentially widespread service disruption. "
            "We recommend urgent incident investigation by the support team. "
            "Please share the start time and affected organizations."
        ),
        RecommendedAction.ESCALATE_TO_ENGINEERING: (
            "We recommend engineering investigation. Please share the affected "
            "service, error message, start time, and any workaround already tried."
        ),
        RecommendedAction.ESCALATE_TO_BILLING: (
            "We recommend billing-team review of the disputed transaction. "
            "No refund or account change has been performed."
        ),
    }
    if action == RecommendedAction.SEND_KNOWN_FIX:
        if not fix:
            raise ValueError("A validated fix is required")
        return fix
    return templates[action]
