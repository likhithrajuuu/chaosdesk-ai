from chaosdesk.policies import determine_priority, assess_priority
from chaosdesk.schemas import Priority, PriorityFacts
import pytest


def test_widespread_critical_failure_is_p0():
    facts = PriorityFacts(
        critical_service_unavailable=True,
        multiple_organizations_affected=True,
        critical_production_workflow_blocked=True,
        workaround_available=None,
        functionality_degraded=True,
        routine_request=False,
    )

    assert determine_priority(facts) == Priority.P0

@pytest.mark.parametrize(
    "blocked,workaround,degraded,routine,expected",
    [
        # Production blocked, no workaround.
        (True, False, True, False, Priority.P1),

        # Unknown workaround must not downgrade a production blockage.
        (True, None, True, False, Priority.P1),

        # A usable workaround reduces severity.
        (True, True, True, False, Priority.P2),

        # Degraded functionality without a complete blockage.
        (False, None, True, False, Priority.P2),

        # Routine request with explicitly no disruption.
        (False, None, False, True, Priority.P3),

        # Unknown impact uses provisional P2.
        (None, None, None, None, Priority.P2),

        # A routine label cannot override a production blockage.
        (True, False, True, True, Priority.P1),
    ],
)
def test_priority_rules(
    blocked, workaround, degraded, routine, expected
):
    facts = PriorityFacts(
        critical_service_unavailable=False,
        multiple_organizations_affected=None,
        critical_production_workflow_blocked=blocked,
        workaround_available=workaround,
        functionality_degraded=degraded,
        routine_request=routine,
    )

    assert determine_priority(facts) == expected

def test_completely_unknown_impact_defaults_to_p2():
    facts = PriorityFacts(
        critical_service_unavailable=None,
        multiple_organizations_affected=None,
        critical_production_workflow_blocked=None,
        workaround_available=None,
        functionality_degraded=None,
        routine_request=None,
    )

    assert determine_priority(facts) == Priority.P2

def test_unknown_workaround_preserves_p1_and_requires_review():
    facts = PriorityFacts(
        critical_service_unavailable=False,
        multiple_organizations_affected=False,
        critical_production_workflow_blocked=True,
        workaround_available=None,
        functionality_degraded=True,
        routine_request=False,
    )

    result = assess_priority(facts)

    assert result.priority == Priority.P1
    assert result.requires_human_review is True
    assert result.missing_information == ["workaround_available"]