import logging
import re
from pathlib import Path

from chaosdesk.analysis import Analyzer, LocalAnalyzer, suspicious_text
from chaosdesk.interpreter import ProviderError, TicketInterpreter
from chaosdesk.policies import assess_priority, explain_priority, recommend_action
from chaosdesk.retrieval import DEFAULT_KB_DIR, retrieve_documents
from chaosdesk.responses import generate_response
from chaosdesk.schemas import (
    Category, Evidence, Priority, PriorityAssessment, PriorityFacts,
    RecommendedAction, Ticket, TicketAnalysis, TriageDecision,
)
from chaosdesk.validation import validate_decision


logger = logging.getLogger(__name__)


def document_applies(document, ticket, analysis):
    """Reviewed scope checks; lexical retrieval is candidate selection only."""
    text = f"{ticket.subject} {ticket.description}".lower()
    scopes = {
        "KB-001": analysis.category == Category.ACCESS and "password" in text,
        "KB-002": all(word in text for word in ("safari", "upload", "unresponsive")),
        "KB-003": (
            analysis.category == Category.INCIDENT
            or analysis.facts.critical_service_unavailable is True
            or analysis.facts.critical_production_workflow_blocked is True
            or (analysis.category == Category.BUG and any(word in text for word in ("storage", "upload", "download")))
        ),
        "KB-004": analysis.category == Category.BILLING,
        "KB-005": analysis.category == Category.FEATURE_REQUEST,
    }
    return scopes.get(document.document_id, False)


def supporting_excerpt(document):
    for paragraph in document.text.split("\n\n"):
        paragraph = paragraph.strip()
        if paragraph and not paragraph.startswith(("#", "Document ID:", "Status:")):
            return paragraph
    return document.text.strip()

def assess_ticket(
        ticket: Ticket,
        interpreter: TicketInterpreter,
) -> PriorityAssessment:
    facts = interpreter.interpret(ticket)
    assessment = assess_priority(facts)
    return assessment


def select_known_fix(ticket, documents):
    """Only reviewed, narrowly scoped procedures may enter a customer draft."""
    text = f"{ticket.subject} {ticket.description}".lower()
    candidates = []
    if "password" in text and any(word in text for word in ("forgot", "reset")):
        candidates.append((
            "KB-001", 'Select "Forgot password".',
            'Open the sign-in page, select "Forgot password", and follow the reset '
            'email. Check your spam folder if needed. Never share your password.',
        ))
    if "safari" in text and "upload" in text and "unresponsive" in text:
        if not any(word in text for word in ("chrome failed", "chrome fails", "workaround failed", "workaround fails")):
            candidates.append((
                "KB-002", "Try uploading the same file using Chrome or Firefox.",
                "Try uploading the same file using Chrome or Firefox. If it still "
                "fails, share your browser version, error message, and failure time.",
            ))
    for doc in documents:
        for doc_id, excerpt, response in candidates:
            if doc.document_id == doc_id and excerpt in doc.text:
                return response, Evidence(document_id=doc_id, excerpt=excerpt)
    return None, None


class TriageAgent:
    def __init__(self, analyzer: Analyzer = None, kb_dir: Path = DEFAULT_KB_DIR,
                 retriever=retrieve_documents, retry_provider_errors=False):
        self.analyzer = analyzer if analyzer is not None else LocalAnalyzer()
        self.kb_dir = Path(kb_dir)
        self.retriever = retriever
        self.retry_provider_errors = retry_provider_errors

    def triage(self, ticket: Ticket) -> TriageDecision:
        problems = []
        try:
            analysis = self.analyzer.analyze(ticket)
            analysis = TicketAnalysis.model_validate(analysis.model_dump())
        except Exception as error:
            if self.retry_provider_errors and isinstance(error, ProviderError):
                raise
            logger.warning("tool_failure tool=analysis")
            problems.append("Ticket interpretation failed; human investigation is required.")
            # Preserve only recognized urgent symptoms as a provisional safety floor.
            analysis = LocalAnalyzer().analyze(ticket)
            if assess_priority(analysis.facts).priority not in (Priority.P0, Priority.P1):
                analysis = TicketAnalysis(
                    facts=PriorityFacts(**{key: None for key in PriorityFacts.model_fields}),
                    category=None, customer_impact="Impact could not be determined.",
                    missing_information=[], billing_details_present=None,
                )

        assessment = assess_priority(analysis.facts)
        query = f"{ticket.subject} {ticket.description}"
        documents = []
        try:
            retrieved = self.retriever(query, kb_dir=self.kb_dir)
            for doc in retrieved:
                if suspicious_text(doc.text):
                    problems.append("Potentially unsafe knowledge-base content was excluded.")
                elif not re.search(r"^Status:\s*current\s*$", doc.text, re.M | re.I):
                    problems.append("Outdated or unverified relevant documentation needs review.")
                elif doc.document_id not in {"KB-001", "KB-002", "KB-003", "KB-004", "KB-005"}:
                    problems.append("Unreviewed relevant documentation needs review.")
                elif document_applies(doc, ticket, analysis):
                    documents.append(doc)
        except Exception:
            logger.warning("tool_failure tool=retrieval")
            documents = []
            problems.append("Knowledge-base retrieval failed.")

        injection = suspicious_text(query)
        if injection:
            problems.append("Potential instructions in ticket content require human review.")
        if not documents:
            problems.append("No usable supporting documentation was retrieved.")
        fix, citation = select_known_fix(ticket, documents)
        # A lexical match alone is insufficient to establish a usable known fix.
        uncertain = bool(problems or analysis.missing_information) or analysis.category is None
        if analysis.facts.routine_request is not True and analysis.facts.functionality_degraded is None:
            uncertain = True
        action = recommend_action(
            assessment.priority, analysis.category, analysis.billing_details_present,
            known_fix=bool(fix), uncertain=uncertain,
        )
        evidence = []
        if citation and not problems:
            evidence.append(citation)
        elif documents:
            # Cite literal excerpts; retrieval relevance remains provisional.
            for doc in documents:
                excerpt = supporting_excerpt(doc)
                if excerpt:
                    evidence.append(Evidence(document_id=doc.document_id, excerpt=excerpt))
        missing = list(analysis.missing_information) + assessment.missing_information
        if analysis.category == Category.BILLING and analysis.billing_details_present is not True:
            missing.append("Invoice or transaction ID, amount, and date")
        if action == RecommendedAction.REQUEST_INFORMATION and not missing:
            missing.append("Details needed to confirm the issue and next step")
        review = bool(assessment.requires_human_review or problems or missing or action in (
            RecommendedAction.CREATE_INCIDENT, RecommendedAction.ESCALATE_TO_ENGINEERING,
            RecommendedAction.ESCALATE_TO_BILLING, RecommendedAction.REQUEST_INFORMATION,
        ))
        reasoning = (
            f"Deterministic impact policy assigned provisional {assessment.priority.value}; "
            f"{explain_priority(analysis.facts, assessment.priority)} "
            f"Routing policy selected {action.value}. Customer impact is reported, not verified. "
            "No external action has been performed."
        )
        if evidence:
            reasoning += " Supporting documents: " + ", ".join(item.document_id for item in evidence) + "."
        if problems:
            reasoning += " " + " ".join(dict.fromkeys(problems))
        decision = TriageDecision(
            ticket_id=ticket.ticket_id, category=analysis.category,
            priority=assessment.priority,
            confidence=0.2 if problems else 0.6,
            customer_impact=analysis.customer_impact, evidence=evidence,
            recommended_action=action, response_draft=generate_response(action, fix),
            reasoning_summary=reasoning, missing_information=list(dict.fromkeys(missing)),
            requires_human_review=review,
        )
        result = validate_decision(decision, ticket, documents)
        logger.info("triage_complete priority=%s action=%s review=%s",
                    result.priority.value, action.value, review)
        return result
