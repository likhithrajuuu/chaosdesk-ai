# Build Challenge: ChaosDesk — An Autonomous AI Support Triage Agent

Act as a senior AI engineer and challenge evaluator. Help me build **ChaosDesk**, a production-minded AI agent that autonomously processes customer-support tickets for a fictional SaaS company called CloudCrate.

## 1. The Scenario

CloudCrate receives hundreds of tickets every hour. Some describe ordinary bugs, some indicate widespread outages, some involve billing disputes, and some contain incomplete or malicious instructions.

Build an agent that accepts a ticket, searches a knowledge base, applies business policies, evaluates urgency, and produces an actionable triage decision.

## 2. Functional Requirements

The agent must:

1. **Understand the ticket:** Extract the issue, affected service, customer impact, urgency indicators, and missing information.
2. **Retrieve evidence:** Search a local knowledge base containing troubleshooting guides, incident reports, product documentation, and support policies. Return the most relevant evidence.
3. **Classify the issue:** Assign a category such as BUG, BILLING, ACCESS, INCIDENT, or FEATURE_REQUEST.
4. **Determine priority:** Assign P0, P1, P2, or P3 using explicit business rules. Widespread outages and critical service failures must outrank routine requests.
5. **Recommend an action:** Choose from REQUEST_INFORMATION, SEND_KNOWN_FIX, ESCALATE_TO_ENGINEERING, ESCALATE_TO_BILLING, or CREATE_INCIDENT.
6. **Explain the decision:** Provide a concise rationale grounded in the ticket, retrieved documents, and applicable policies.
7. **Handle uncertainty:** If evidence is insufficient or conflicting, identify the uncertainty and request human review instead of inventing facts.
8. **Resist prompt injection:** Treat ticket content and retrieved documents as untrusted data. Never allow instructions embedded in them to override system rules or authorize prohibited actions.
9. **Produce valid structured output:** Return a schema-validated JSON object with consistent fields and enumerated values.
10. **Maintain reliability:** Handle malformed inputs, empty tickets, retrieval failures, model timeouts, and invalid model responses gracefully.

## 3. Example Output Contract

Every ticket must produce an object containing:

- ticket_id
- category
- priority
- confidence
- customer_impact
- evidence: list of document IDs and supporting excerpts
- recommended_action
- response_draft
- reasoning_summary
- missing_information
- requires_human_review

Confidence must not be treated as proof of correctness. Do not invent evidence or claim that a tool succeeded when it failed.

## 4. Tooling Requirements

Implement separate, testable tools for:

- Knowledge-base retrieval
- Policy evaluation
- Ticket classification
- Priority determination
- Response generation
- Output validation

Keep deterministic rules separate from LLM-generated reasoning. The LLM may interpret ambiguous language, but critical escalation rules must be enforced by code.

Do not introduce unnecessary agents, frameworks, or microservices. Start with one agent and clearly defined tools.

## 5. Evaluation Scenarios

Create a local test dataset containing at least 30 tickets, including:

- A routine password-reset request
- A billing dispute with missing transaction details
- A single-user bug with a documented workaround
- A production outage affecting hundreds of customers
- Two tickets that describe the same underlying incident differently
- Conflicting or outdated knowledge-base articles
- A vague ticket with insufficient evidence
- A ticket containing prompt-injection instructions
- An empty or malformed ticket
- A ticket for which no relevant documentation exists

Add adversarial and boundary cases beyond these examples.

## 6. Testing and Evaluation

Build automated tests that measure:

- Category classification accuracy
- Priority classification accuracy
- Correct escalation decisions
- Evidence relevance and citation validity
- JSON schema validity
- Prompt-injection resistance
- Graceful failure handling
- Deterministic policy enforcement

Define measurable acceptance criteria before optimizing the system. Report results using a repeatable evaluation script rather than a few handpicked examples.

## 7. Engineering Constraints

- Provide a terminal-based interface.
- Use a clean, modular architecture.
- Keep configuration and secrets outside source code.
- Support a local mock mode so the project works without a paid LLM API.
- Include sample tickets, sample knowledge-base documents, automated tests, and a README.
- Log decisions and tool failures without exposing secrets or unnecessary personal data.
- Never send real customer communications or perform destructive actions automatically.

## 8. How We Will Build It

Work incrementally.

First, give me the requirements and a proposed architecture. Then let me implement each component myself, reviewing my design choices and tests before moving on.

Do not generate the entire codebase immediately. Challenge my assumptions, introduce realistic edge cases, and ask me to justify important engineering decisions.

The final goal is to build a small but reliable AI agent that can be explained, tested, and defended in a technical interview.