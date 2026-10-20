from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum


class Ticket(BaseModel):
    model_config = ConfigDict(
        # Removes the surrounding spaces bfeore checking the minimum length
        # That makes the description containing only spaces fail the validation
        str_strip_whitespace=True, 
        extra="forbid",
        strict=True
    )
    ticket_id: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    description: str = Field(min_length=1)

class Category(str, Enum):
    BUG = "BUG"
    BILLING="BILLING"
    ACCESS="ACCESS"
    INCIDENT="INCIDENT"
    FEATURE_REQUEST="FEATURE_REQUEST"

class Priority(str, Enum):
    # Highest to Lowest Priority
    P0="P0"
    P1="P1"
    P2="P2"
    P3="P3"

class RecommendedAction(str, Enum):
    REQUEST_INFORMATION = "REQUEST_INFORMATION"
    SEND_KNOWN_FIX = "SEND_KNOWN_FIX"
    ESCALATE_TO_ENGINEERING = "ESCALATE_TO_ENGINEERING"
    ESCALATE_TO_BILLING = "ESCALATE_TO_BILLING"
    CREATE_INCIDENT = "CREATE_INCIDENT"

class Evidence(BaseModel):
    """A supporting excerpt from a retrieved document"""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid"
    )

    document_id: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)

class TriageDecision(BaseModel):
    """The structured result produced for a support ticket"""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
        allow_inf_nan=False
    )

    ticket_id: str = Field(min_length=1)
    category: Optional[Category]
    priority: Priority
    confidence: float = Field(ge=0.0, le=1.0)
    customer_impact: str = Field(min_length=1)
    evidence: List[Evidence]
    recommended_action: RecommendedAction
    response_draft: str = Field(min_length=1)
    reasoning_summary: str = Field(min_length=1)
    missing_information: List[str]
    requires_human_review: bool

class PriorityFacts(BaseModel):
    """Reported or established impact used to evaluate priority."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    critical_service_unavailable: Optional[bool]
    multiple_organizations_affected: Optional[bool]
    critical_production_workflow_blocked: Optional[bool]
    workaround_available: Optional[bool]
    functionality_degraded: Optional[bool]
    routine_request: Optional[bool]

class PriorityAssessment(BaseModel):
    """Prioriy together with information needed for review"""

    model_config = ConfigDict(extra="forbid")

    priority: Priority
    missing_information: List[str]
    requires_human_review: bool


class TicketAnalysis(BaseModel):
    """Interpretation only; priority and action are always chosen by code."""

    model_config = ConfigDict(extra="forbid", strict=True)

    facts: PriorityFacts
    # API parsers may supply JSON enum values as strings; keep booleans strict.
    category: Optional[Category] = Field(strict=False)
    customer_impact: str = Field(min_length=1)
    missing_information: List[str]
    billing_details_present: Optional[bool]
