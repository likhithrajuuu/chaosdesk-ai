# ChaosDesk

A support triage agent with a CLI and a durable internal API for fictional SaaS company CloudCrate.
It produces a validated JSON decision and a response draft. All escalations,
incident creation, and billing actions are recommendations; nothing is sent or
changed in a customer account.

For the authenticated service, persistent queue, worker processes, Docker Compose,
load testing, and deployment procedures, read [the operations guide](docs/operations.md).

## Quick start

Use Python 3.9+ and a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m chaosdesk --mode mock --ticket examples/ticket.json
```

Mock mode uses a deterministic keyword baseline, needs no API key, and makes no
network requests. It is a demonstration baseline with limited language coverage.
The earlier single-ticket `MockInterpreter` remains available for focused tests.

For AI mode, put your key in the ignored local `.env` file:

```text
OPENAI_API_KEY=your-key-here
OPENAI_MODEL=gpt-4.1-mini
```

```bash
python -m chaosdesk --mode ai --ticket examples/ticket.json
```

AI mode sends the ticket to OpenAI and uses your API account. The default model
matches the original project setup; `--model` overrides it. Each ticket uses one
structured extraction request, a 30-second request timeout, and no automatic
retries. Knowledge-base articles are processed locally, outside the model prompt.

The CLI also reads one JSON ticket from stdin:

```bash
cat examples/ticket.json | python -m chaosdesk
```

`python main.py` invokes the same CLI. Use `--kb-dir PATH` to select a knowledge
base and `--verbose` for logs on stderr. Logs contain event names, priorities,
actions, and review flags, without raw tickets or provider error details.

## Ticket and result contracts

Input is a JSON object with exactly three nonblank string fields:

```json
{
  "ticket_id": "TKT-001",
  "subject": "Forgot password",
  "description": "I need help resetting my password."
}
```

Results include `ticket_id`, nullable `category`, `priority`, `confidence`,
`customer_impact`, `evidence`, `recommended_action`, `response_draft`,
`reasoning_summary`, `missing_information`, and `requires_human_review`.
Evidence contains literal excerpts from retrieved documents. Unknown categories
remain null. Unknown impact facts stay null and trigger review.

Malformed or empty input produces a JSON error envelope instead of a fabricated
triage decision. Exit codes are 0 for a completed decision (including a decision
requiring review), 2 for input/configuration errors, and 1 for an unrecoverable
processing failure. Errors do not include raw input or provider exception text.

## How it works

```text
Ticket validation
  -> LocalAnalyzer or AIAnalyzer: category, reported impact, impact facts
  -> local retrieval: candidate documents
  -> document status, scope, and suspicious-content checks
  -> deterministic priority and action policies
  -> reviewed response templates
  -> output schema and citation validation
```

| File | Responsibility |
|---|---|
| `chaosdesk/schemas.py` | Input, analysis, and final decision contracts |
| `chaosdesk/analysis.py` | Offline baseline and structured AI extraction |
| `chaosdesk/interpreter.py` | Earlier priority-only interpreter interface and implementations |
| `chaosdesk/retrieval.py` | Deterministic local article ranking |
| `chaosdesk/policies.py` | Priority, action routing, and rule explanations |
| `chaosdesk/triage.py` | End-to-end coordination and document scope checks |
| `chaosdesk/responses.py` | Reviewed response draft templates |
| `chaosdesk/validation.py` | Schema, ticket identity, and citation checks |
| `chaosdesk/cli.py` | JSON terminal interface |
| `chaosdesk/evaluate.py` | Repeatable labeled evaluation |

P0 requires reported critical-service unavailability across multiple independent
organizations. P1 requires a blocked critical production workflow without a
confirmed usable workaround. Disruption otherwise produces P2. Routine requests
with no disruption produce P3. Unknown impact defaults to provisional P2.

P0 recommends incident investigation, and P1 recommends engineering review even
when evidence is unavailable. These priorities always require human review.
Billing cases need a transaction/invoice ID, amount, and date before billing
routing. Otherwise they request information. Unknown categories, unavailable
supporting documentation, and unsafe or outdated documents trigger review.

Known fixes are restricted to reviewed password-reset and Safari-button
procedures with exact supporting excerpts and matching ticket scope. Retrieval
score alone cannot authorize a fix. A failed Safari workaround is not sent again.
New document IDs require a scope rule in `document_applies` before they can
support a decision; new fixes also require an entry in `select_known_fix`.
Unreviewed matching documents are conservatively flagged for review. This also
prevents conflicting additional articles from silently authorizing a fix.

AI failures yield a review-required decision with unknown impact. A narrow local
symptom check preserves clearly recognized P0/P1 reports as provisional urgent
escalations. Retrieval failures preserve severity, remove evidence, and flag
review. No model response directly sets priority or authorizes external actions.

## Tests and evaluation

```bash
python -m pytest -q
python -m chaosdesk.evaluate
```

`data/evaluation.json` contains labeled routine, urgent, ambiguous, billing,
malformed, injection, workaround, outdated/conflicting-document, and retrieval
failure cases. `data/acceptance.json` defines acceptance thresholds. Evaluation
prints scores, denominators, thresholds, and failing case names, and exits with
status 1 when a threshold is missed. Tests also inject timeouts, invalid model
responses, unsafe documents, fabricated citations, and tool failures.

To evaluate the real model, explicitly run:

```bash
python -m chaosdesk.evaluate --mode ai
```

This makes paid API requests for valid tickets. Offline results measure the local
keyword baseline and deterministic pipeline, not real-model quality. The small
fixture set is a regression suite, not a representative production benchmark.
Evidence recall is measured only on cases with explicitly labeled source IDs;
citation validity checks provenance, not semantic truth.

## Harnesses

Run all three offline harnesses and save a JSON report:

```bash
python -m chaosdesk.harness all --output reports/harness.json
```

| Harness | What it checks |
|---|---|
| `regression` | Existing labeled evaluation against acceptance thresholds |
| `replay` | Expected outcomes, schema/citation validity, and identical full decisions across repeated runs |
| `faults` | Safe degradation under analyzer timeouts, invalid analysis, retrieval errors, empty retrieval, unsafe documents, and combined failures |

Run individual harnesses:

```bash
python -m chaosdesk.harness regression
python -m chaosdesk.harness replay --repeat 3
python -m chaosdesk.harness faults
```

Replay accepts `--dataset PATH` using the same case format as
`data/evaluation.json`. It checks every provided expectation, including review
flags and required evidence IDs, rather than using aggregate accuracy thresholds.
Invalid tickets must explicitly have `"invalid": true`. Duplicate case names,
unknown expectations, and invalid expectation types are configuration errors.
An empty dataset cannot pass. Regression still requires coverage of all metrics
in the acceptance file, so a small custom dataset may fail its coverage gates.

The fault harness runs six failure modes against three ticket types: widespread
outage, production blockage, and routine assistance. It checks that urgent cases
retain their escalation, all degraded decisions require review, and unavailable
or unsafe sources do not produce evidence. Analyzer failure on routine tickets
must produce unknown-impact P2 rather than an invented routine classification.

Reports show per-case outcomes, tool calls, handled tool errors, elapsed
milliseconds, and replay p50/p95 latency (nearest-rank). Repeated decisions are
compared in full, excluding timing data. Timing is diagnostic and has no pass
threshold. Traces omit ticket IDs, ticket text, response drafts, retrieved text,
and exception details; case names should be non-sensitive labels.

Exit codes: 0 means all selected harnesses passed, 1 means checks failed, and 2
means dataset configuration or report writing failed. Reports are printed as JSON
on stdout and optionally saved with `--output`; tool diagnostics use stderr.
The harness CLI is entirely offline. Use the existing
`python -m chaosdesk.evaluate --mode ai` separately for paid real-model evaluation.

## Service load testing

The load harness exercises HTTP admission, SQLite persistence, bounded workers,
and retry handling with a simulated model. It makes no external API calls:

```bash
python -m chaosdesk.loadtest --tickets 100 --workers 4 --latency-ms 50 --rate-limit-every 10 --output reports/load.json
```

Reports include throughput, end-to-end p50/p95 latency, peak model concurrency,
attempts, failures, and unfinished jobs. `--max-p95-ms` adds an explicit latency
gate. These are local pipeline measurements; actual model latency, quotas, and
costs require a separate controlled evaluation.

## Current limits

The offline baseline uses keyword rules and can misread negation, paraphrases,
and complex scope statements. It is not suitable for unattended production use.
The model can also misinterpret symptoms even when its JSON is valid. The fixed
confidence values (0.6 normally, 0.2 when tool/evidence concerns arise) are
uncalibrated conservative indicators, not measured probabilities.

Ticket instructions stay in a separate untrusted message. Retrieved documents
never enter the model prompt or directly supply response text. Suspicious-content
checks and reviewed templates add containment, but the regex checks do not prove
prompt-injection resistance. Real-model adversarial evaluation, a broader held-out
dataset, stronger retrieval, and operational monitoring remain necessary before
production deployment. Articles are checked for explicit current status; there is
no automatic date-based expiration or arbitrary semantic contradiction detector.

The original requirements are retained in [docs/build-challenge.md](docs/build-challenge.md).
