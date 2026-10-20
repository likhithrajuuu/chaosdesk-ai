"""Authenticated internal, single-organization JSON API over the durable queue."""

import hmac
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from chaosdesk.retrieval import SnapshotRetriever
from chaosdesk.schemas import Ticket
from chaosdesk.settings import Settings
from chaosdesk.store import AdmissionError, JobStore


logger = logging.getLogger(__name__)


def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def create_app(settings=None):
    if settings is None:
        try:
            settings = Settings.from_env()
        except Exception:
            raise RuntimeError("Invalid service configuration") from None
    store = JobStore(settings)

    @asynccontextmanager
    async def lifespan(app):
        try:
            store.initialize()
            SnapshotRetriever(settings.kb_dir)
        except Exception:
            raise RuntimeError("Service storage or knowledge-base initialization failed") from None
        app.state.store = store
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def boundary(request, call_next):
        request_id = uuid.uuid4().hex
        start = time.monotonic()
        if request.url.path not in ("/health/live", "/health/ready"):
            auth = request.headers.get("authorization", "")
            supplied = auth[7:] if auth.startswith("Bearer ") else ""
            if not hmac.compare_digest(supplied.encode(), settings.api_token.get_secret_value().encode()):
                response = JSONResponse({"error": "unauthorized"}, status_code=401,
                                        headers={"WWW-Authenticate": "Bearer"})
            else:
                response = None
        else:
            response = None
        if response is None:
            try:
                response = await call_next(request)
            except Exception:
                logger.error('{"event":"api_processing_failure"}')
                response = JSONResponse({"error": "service_unavailable"}, status_code=503)
        response.headers.update({"X-Request-ID": request_id, "Cache-Control": "no-store",
                                 "X-Content-Type-Options": "nosniff"})
        # Paths/headers/bodies are omitted because they can contain customer data.
        logger.info(json.dumps({"event": "http_request", "request_id": request_id,
                               "status": response.status_code,
                               "duration_ms": round((time.monotonic() - start) * 1000, 2)}))
        return response

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready():
        try:
            stats = store.stats()
            healthy = stats["live_worker_processes"] > 0 and stats["expired_leases"] == 0
            if settings.mode == "ai" and stats["model_requests_today"] >= settings.model_requests_per_day:
                healthy = False
            if sum(stats["jobs"].get(state, 0) for state in ("queued", "running")) >= settings.max_pending:
                healthy = False
        except Exception:
            healthy = False
        return JSONResponse({"status": "ready" if healthy else "not_ready"},
                            status_code=200 if healthy else 503)

    @app.get("/v1/metrics")
    def metrics():
        return store.stats()

    @app.post("/v1/jobs")
    async def submit(request: Request):
        key = request.headers.get("idempotency-key", "")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", key):
            return JSONResponse({"error": "invalid_idempotency_key"}, status_code=400)
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return JSONResponse({"error": "json_content_type_required"}, status_code=415)
        length = request.headers.get("content-length")
        if length is not None:
            if not length.isdigit():
                return JSONResponse({"error": "invalid_content_length"}, status_code=400)
            if len(length) > 12 or int(length) > settings.max_body_bytes:
                return JSONResponse({"error": "request_too_large"}, status_code=413)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > settings.max_body_bytes:
                return JSONResponse({"error": "request_too_large"}, status_code=413)
            body.extend(chunk)
        try:
            ticket = Ticket.model_validate(json.loads(body, object_pairs_hook=reject_duplicates))
        except (ValueError, UnicodeError, TypeError, RecursionError):
            return JSONResponse({"error": "invalid_ticket"}, status_code=422)
        try:
            job, created = await run_in_threadpool(store.submit, ticket, key)
        except AdmissionError as error:
            return JSONResponse({"error": error.code}, status_code=error.status,
                                headers={"Retry-After": "60"} if error.status in (429, 503) else {})
        return JSONResponse(job, status_code=202 if created else 200,
                            headers={"Location": "/v1/jobs/" + job["job_id"]})

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            return JSONResponse({"error": "job_not_found"}, status_code=404)
        job = store.get(job_id)
        return JSONResponse(job) if job else JSONResponse({"error": "job_not_found"}, status_code=404)

    return app


def main():
    import uvicorn
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        settings = Settings.from_env()
        app = create_app(settings)
    except Exception:
        logger.error('{"event":"invalid_service_configuration"}')
        return 1
    uvicorn.run(app, host=settings.host, port=settings.port, workers=1, access_log=False,
                limit_concurrency=100, limit_max_requests=100000,
                timeout_keep_alive=5, timeout_graceful_shutdown=45)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
