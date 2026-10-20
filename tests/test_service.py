from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import APIStatusError
from pydantic import ValidationError
from unittest.mock import Mock

from chaosdesk.analysis import AIAnalyzer
from chaosdesk.api import create_app
from chaosdesk.interpreter import ProviderError
from chaosdesk.retrieval import SnapshotRetriever, retrieve_documents
from chaosdesk.schemas import Ticket
from chaosdesk.settings import Settings
from chaosdesk.store import AdmissionError, JobStore
from chaosdesk.triage import TriageAgent
from chaosdesk.worker import process_one


@pytest.fixture
def settings(tmp_path):
    return Settings(api_token="test-token-" + "x" * 32, database=tmp_path / "jobs.db")


@pytest.fixture
def ticket():
    return Ticket(ticket_id="T-1", subject="Forgot password", description="Please reset my password.")


@pytest.fixture
def store(settings):
    result = JobStore(settings, clock=lambda: 1000)
    result.initialize()
    return result


def headers(settings, key="test-key"):
    return {"Authorization": "Bearer " + settings.api_token.get_secret_value(), "Idempotency-Key": key}


def test_idempotency_is_atomic_under_concurrent_submissions(store, ticket):
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: store.submit(ticket, "same-key"), range(16)))
    assert len({result[0]["job_id"] for result in results}) == 1
    assert sum(created for _, created in results) == 1
    assert store.stats()["jobs"] == {"queued": 1}


def test_idempotency_conflict_is_not_an_overwrite(store, ticket):
    original, _ = store.submit(ticket, "key")
    changed = ticket.model_copy(update={"description": "Different ticket"})
    with pytest.raises(AdmissionError) as error:
        store.submit(changed, "key")
    assert error.value.status == 409
    assert store.get(original["job_id"])["status"] == "queued"


def test_concurrent_claims_are_distinct(store, ticket):
    for index in range(12):
        store.submit(ticket, str(index))
    with ThreadPoolExecutor(max_workers=8) as executor:
        claims = list(executor.map(lambda _: store.claim(), range(16)))
    claims = [job for job in claims if job]
    assert len(claims) == 12
    assert len({job["id"] for job in claims}) == 12


def test_expired_lease_is_reclaimed_and_stale_result_cannot_overwrite(store, ticket):
    store.submit(ticket, "key")
    first = store.claim()
    store.clock = lambda: 1000 + store.settings.lease_seconds + 1
    second = store.claim()
    decision = TriageAgent().triage(ticket)
    assert first["id"] == second["id"]
    assert first["lease_token"] != second["lease_token"]
    assert not store.complete(first, decision)
    assert not store.fail(first)
    assert store.complete(second, decision)
    assert store.get(first["id"])["status"] == "completed"


def test_expired_final_attempt_becomes_failed(store, ticket):
    store.settings.max_attempts = 1
    job, _ = store.submit(ticket, "key")
    store.claim()
    store.clock = lambda: 1000 + store.settings.lease_seconds + 1
    assert store.claim() is None
    assert store.get(job["job_id"])["error"] == "attempts_exhausted"


def test_retry_delay_and_attempt_budget_are_persistent(store, ticket):
    submitted, _ = store.submit(ticket, "key")
    for attempt in range(1, 4):
        job = store.claim()
        assert job["attempts"] == attempt
        assert store.fail(job, retryable=True, delay=10)
        assert store.claim() is None
        store.clock = lambda attempt=attempt: 1000 + attempt * 10
    assert store.get(submitted["job_id"])["status"] == "failed"


def test_pending_and_submission_rate_limits_preserve_idempotent_replays(store, ticket):
    store.settings.max_pending = 1
    store.submit(ticket, "first")
    assert not store.submit(ticket, "first")[1]
    with pytest.raises(AdmissionError) as caught:
        store.submit(ticket, "second")
    assert caught.value.code == "queue_full"
    store.settings.max_pending = 1000
    store.settings.submissions_per_minute = 1
    with pytest.raises(AdmissionError) as caught:
        store.submit(ticket, "second")
    assert caught.value.status == 429


def test_ai_start_pacing_and_daily_cap_are_shared_across_connections(store, ticket):
    store.settings.mode = "ai"
    store.settings.model_requests_per_minute = 60
    store.settings.model_requests_per_day = 2
    for key in ("a", "b", "c"):
        store.submit(ticket, key)
    assert store.claim()
    another = JobStore(store.settings, clock=store.clock)
    assert another.claim() is None
    store.clock = lambda: 1001
    assert store.claim()
    store.clock = lambda: 1002
    assert store.claim() is None
    assert store.stats()["model_requests_today"] == 2


def test_daily_budget_recovers_on_next_utc_day(store, ticket):
    store.settings.mode = "ai"
    store.settings.model_requests_per_day = 1
    store.submit(ticket, "a")
    first = store.claim()
    store.fail(first)
    store.submit(ticket, "b")
    assert store.claim() is None
    store.clock = lambda: 86401
    assert store.claim() is not None
    assert store.stats()["model_requests_today"] == 1


def test_stale_failure_cannot_throttle_new_worker(store, ticket):
    store.settings.mode = "ai"
    store.submit(ticket, "a")
    first = store.claim()
    store.clock = lambda: 1000 + store.settings.lease_seconds + 1
    assert store.claim()
    with store.connection() as db:
        before = store.counter(db, "model-next")
    assert not store.fail(first, retryable=True, delay=300)
    with store.connection() as db:
        assert store.counter(db, "model-next") == before


def test_worker_heartbeat_expiration_and_shutdown_remove_readiness(store):
    store.heartbeat("worker")
    assert store.stats()["live_worker_processes"] == 1
    store.clock = lambda: 1021
    assert store.stats()["live_worker_processes"] == 0
    store.heartbeat("worker")
    store.unregister_worker("worker")
    assert store.stats()["live_worker_processes"] == 0


def test_wrong_ticket_result_cannot_be_committed(store, ticket):
    store.submit(ticket, "key")
    job = store.claim()
    decision = TriageAgent().triage(ticket.model_copy(update={"ticket_id": "wrong"}))
    with pytest.raises(ValueError):
        store.complete(job, decision)
    assert store.get(job["id"])["status"] == "running"


def test_persistence_survives_new_store_instance(store, ticket):
    submitted, _ = store.submit(ticket, "key")
    assert process_one(store, TriageAgent())
    reopened = JobStore(store.settings)
    assert reopened.get(submitted["job_id"])["decision"]["ticket_id"] == ticket.ticket_id


def test_retention_does_not_delete_pending_work(store, ticket):
    done, _ = store.submit(ticket, "done")
    process_one(store, TriageAgent())
    queued, _ = store.submit(ticket, "queued")
    store.clock = lambda: 1000 + 8 * 86400
    assert store.purge() == 1
    assert store.get(done["job_id"]) is None
    assert store.get(queued["job_id"])["status"] == "queued"


@pytest.mark.parametrize("retryable", [True, False])
def test_worker_retries_only_retryable_provider_errors(store, ticket, retryable):
    analyzer = Mock()
    analyzer.analyze.side_effect = ProviderError(retryable=retryable, retry_after=10)
    agent = TriageAgent(analyzer=analyzer, retry_provider_errors=True)
    job, _ = store.submit(ticket, "key")
    process_one(store, agent)
    result = store.get(job["job_id"])
    assert result["status"] == ("queued" if retryable else "failed")
    assert result["decision"] is None


def test_api_authentication_and_input_limits(settings, ticket):
    with TestClient(create_app(settings)) as client:
        assert client.post("/v1/jobs", json=ticket.model_dump()).status_code == 401
        assert client.get("/v1/metrics").status_code == 401
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        assert client.post("/v1/jobs", headers=headers(settings), content="{" + "x" * 70000).status_code == 415
        h = {**headers(settings), "Content-Type": "application/json"}
        assert client.post("/v1/jobs", headers=h, content="x" * 70000).status_code == 413
        result = client.post("/v1/jobs", headers=h, content='{"ticket_id":"secret"}')
        assert result.status_code == 422
        assert "secret" not in result.text
        assert result.headers["cache-control"] == "no-store"


def test_api_stream_limit_without_content_length(settings):
    with TestClient(create_app(settings)) as client:
        def chunks():
            yield b"x" * 40000
            yield b"x" * 40000
        response = client.post("/v1/jobs", headers={**headers(settings), "Content-Type": "application/json"}, content=chunks())
        assert response.status_code == 413


def test_api_duplicate_json_keys_are_rejected(settings):
    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/jobs", headers={**headers(settings), "Content-Type": "application/json"},
                               content='{"ticket_id":"a","ticket_id":"b","subject":"x","description":"x"}')
        assert response.status_code == 422


def test_api_submission_polling_and_worker_health(settings, ticket):
    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/jobs", headers=headers(settings), json=ticket.model_dump())
        assert response.status_code == 202
        location = response.headers["location"]
        repeat = client.post("/v1/jobs", headers=headers(settings), json=ticket.model_dump())
        assert repeat.status_code == 200
        assert repeat.json()["job_id"] == response.json()["job_id"]
        store = client.app.state.store
        store.heartbeat("test-worker")
        assert client.get("/health/ready").status_code == 200
        agent = TriageAgent(retriever=SnapshotRetriever(settings.kb_dir))
        process_one(store, agent)
        completed = client.get(location, headers=headers(settings)).json()
        assert completed["status"] == "completed"
        assert completed["kb_revision"] == agent.retriever.revision
        assert completed["decision"]["ticket_id"] == ticket.ticket_id


def test_snapshot_does_not_reread_files_and_matches_retrieval(settings, tmp_path):
    source = tmp_path / "article.md"
    source.write_text("Document ID: KB-001\nStatus: current\nPassword reset help.")
    snapshot = SnapshotRetriever(tmp_path)
    expected = retrieve_documents("password reset", tmp_path)
    source.unlink()
    assert snapshot("password reset") == expected


def test_production_requires_ai_and_valid_secret(settings):
    with pytest.raises(ValidationError):
        Settings(api_token="short")
    with pytest.raises(ValidationError):
        Settings(api_token="x" * 32, environment="production", mode="mock")
    with pytest.raises(ValidationError):
        Settings(api_token="x" * 32, environment="production", mode="ai")


@pytest.mark.parametrize("status,retryable", [(401, False), (400, False), (429, True), (503, True)])
def test_provider_status_errors_are_classified_without_leaking_details(ticket, status, retryable):
    llm = Mock()
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.openai.com"))
    llm.with_structured_output.return_value.invoke.side_effect = APIStatusError(
        "private provider content", response=response, body=None)
    with pytest.raises(ProviderError) as caught:
        AIAnalyzer(llm=llm).analyze(ticket)
    assert caught.value.retryable is retryable
    assert "private" not in str(caught.value)


def test_insufficient_quota_is_not_retried(ticket):
    llm = Mock()
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com"))
    llm.with_structured_output.return_value.invoke.side_effect = APIStatusError(
        "quota", response=response, body={"code": "insufficient_quota"})
    with pytest.raises(ProviderError) as caught:
        AIAnalyzer(llm=llm).analyze(ticket)
    assert not caught.value.retryable
