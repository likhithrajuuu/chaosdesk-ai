from chaosdesk.schemas import Priority, PriorityFacts, PriorityAssessment

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