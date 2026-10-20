from chaosdesk.schemas import Category, Priority, PriorityFacts, PriorityAssessment, RecommendedAction

def determine_priority(facts: PriorityFacts) -> Priority:
    """Choose a provisional priority from the available impact information"""

    if(
        facts.critical_service_unavailable is True
        and facts.multiple_organizations_affected is True
    ):
        return Priority.P0

    if(
        facts.critical_production_workflow_blocked is True
        and facts.workaround_available is not True
    ):
        return Priority.P1

    if(
        facts.functionality_degraded is True
        or facts.critical_production_workflow_blocked is True
        or facts.critical_service_unavailable is True
    ):
        return Priority.P2

    if (
        facts.routine_request is True
        and facts.critical_service_unavailable is False
        and facts.critical_production_workflow_blocked is False
        and facts.functionality_degraded is False
    ):
        return Priority.P3

    return Priority.P2

def assess_priority(facts: PriorityFacts) -> PriorityAssessment:
    missing_information = [
        field_name
        for field_name, value in facts.model_dump().items()
        if value is None
    ]

    return PriorityAssessment(
        priority=determine_priority(facts),
        missing_information=missing_information,
        requires_human_review=bool(missing_information),
    )


def recommend_action(priority, category, billing_details_present, known_fix, uncertain):
    """Severity takes precedence over uncertainty and routine workflow routing."""
    if priority == Priority.P0:
        return RecommendedAction.CREATE_INCIDENT
    if priority == Priority.P1:
        return RecommendedAction.ESCALATE_TO_ENGINEERING
    if category == Category.BILLING:
        return (
            RecommendedAction.ESCALATE_TO_BILLING
            if billing_details_present is True
            else RecommendedAction.REQUEST_INFORMATION
        )
    if uncertain:
        return RecommendedAction.REQUEST_INFORMATION
    if known_fix:
        return RecommendedAction.SEND_KNOWN_FIX
    if category == Category.BUG:
        return RecommendedAction.ESCALATE_TO_ENGINEERING
    return RecommendedAction.REQUEST_INFORMATION


def explain_priority(facts, priority):
    """Describe the specific deterministic rule that selected the priority."""
    if priority == Priority.P0:
        return "Critical service unavailability and multiple affected organizations were reported."
    if priority == Priority.P1:
        return "A critical production workflow is blocked with no confirmed usable workaround."
    if priority == Priority.P3:
        return "A routine request was identified with no reported service disruption."
    if any(value is True for value in (
        facts.functionality_degraded, facts.critical_service_unavailable,
        facts.critical_production_workflow_blocked,
    )):
        return "Reported functionality is impaired; higher-severity rule conditions are not established."
    return "Impact is insufficiently established, so the provisional priority is P2."
