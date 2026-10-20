"""Offline load harness exercising HTTP admission, SQLite, workers, and retries."""

import argparse
import json
import math
import secrets
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from chaosdesk.analysis import LocalAnalyzer
from chaosdesk.api import create_app
from chaosdesk.interpreter import ProviderError
from chaosdesk.retrieval import SnapshotRetriever
from chaosdesk.settings import Settings
from chaosdesk.triage import TriageAgent
from chaosdesk.worker import WorkerPool


class SimulatedModel:
    """Shared concurrency instrumentation; zero external requests."""

    def __init__(self, latency_ms, rate_limit_every):
        self.latency_ms, self.rate_limit_every = latency_ms, rate_limit_every
        self.lock = threading.Lock()
        self.active = self.peak = self.calls = self.rate_limits = 0

    def analyze(self, ticket):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls += 1
            throttled = self.rate_limit_every > 0 and self.calls % self.rate_limit_every == 0
            self.rate_limits += int(throttled)
        try:
            time.sleep(self.latency_ms / 1000)
            if throttled:
                raise ProviderError(retryable=True, retry_after=0.05)
            return LocalAnalyzer().analyze(ticket)
        finally:
            with self.lock:
                self.active -= 1


def run_load(tickets=100, workers=4, concurrency=8, latency_ms=50,
             rate_limit_every=0, timeout=60, max_p95_ms=None):
    if not (1 <= tickets <= 5000 and 1 <= workers <= 32 and 1 <= concurrency <= 100
            and 0 <= latency_ms <= 10000 and rate_limit_every >= 0 and 0 < timeout <= 300):
        raise ValueError("Load parameters exceed supported bounds")
    simulator = SimulatedModel(latency_ms, rate_limit_every)
    with tempfile.TemporaryDirectory(prefix="chaosdesk-load-") as directory:
        settings = Settings(
            api_token=secrets.token_urlsafe(32), database=Path(directory) / "jobs.db",
            workers=workers, max_pending=tickets, max_stored_jobs=max(1000, tickets),
            submissions_per_minute=tickets + 1, retry_base_seconds=0.1,
            poll_seconds=0.01, shutdown_seconds=15,
        )
        retriever = SnapshotRetriever(settings.kb_dir)
        pool = WorkerPool(settings, agent_factory=lambda: TriageAgent(
            analyzer=simulator, retriever=retriever, retry_provider_errors=True))
        with TestClient(create_app(settings)) as client:
            pool.start()
            pool.heartbeat()
            headers = {"Authorization": "Bearer " + settings.api_token.get_secret_value()}
            started = time.monotonic()

            def submit(index):
                begin = time.monotonic()
                response = client.post("/v1/jobs", headers={**headers, "Idempotency-Key": f"load-{index}"},
                                       json={"ticket_id": f"LOAD-{index}", "subject": "Forgot password",
                                             "description": "Please reset my password."})
                return response.status_code, response.json().get("job_id"), begin

            pending, latencies = {}, []
            completed = failed = rejected = 0
            try:
                with ThreadPoolExecutor(max_workers=concurrency) as submitters:
                    for status, job_id, begin in submitters.map(submit, range(tickets)):
                        if status == 202:
                            pending[job_id] = begin
                        else:
                            rejected += 1
                while pending and time.monotonic() - started < timeout:
                    pool.heartbeat()
                    for job_id in list(pending):
                        response = client.get("/v1/jobs/" + job_id, headers=headers)
                        if response.status_code != 200:
                            continue
                        state = response.json()["status"]
                        if state in ("completed", "failed"):
                            completed += int(state == "completed")
                            failed += int(state == "failed")
                            latencies.append((time.monotonic() - pending.pop(job_id)) * 1000)
                    time.sleep(0.01)
                elapsed = time.monotonic() - started
            finally:
                pool.stop()
    ordered = sorted(latencies)
    def percentile(fraction):
        return round(ordered[math.ceil(len(ordered) * fraction) - 1], 2) if ordered else None
    p95 = percentile(0.95)
    accepted = (completed == tickets and failed == rejected == len(pending) == 0
                and simulator.peak <= workers
                and (max_p95_ms is None or (p95 is not None and p95 <= max_p95_ms)))
    return {
        "accepted": accepted, "mode": "simulated_offline", "tickets": tickets,
        "workers": workers, "submission_concurrency": concurrency,
        "simulated_model_latency_ms": latency_ms, "model_attempts": simulator.calls,
        "injected_rate_limits": simulator.rate_limits, "peak_model_concurrency": simulator.peak,
        "completed": completed, "failed": failed, "rejected": rejected, "unfinished": len(pending),
        "elapsed_seconds": round(elapsed, 3), "tickets_per_second": round(completed / elapsed, 2),
        "end_to_end_latency_ms": {"p50": percentile(0.5), "p95": p95},
        "max_p95_ms": max_p95_ms,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickets", type=int, default=100)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--latency-ms", type=float, default=50)
    parser.add_argument("--rate-limit-every", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--max-p95-ms", type=float)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = run_load(tickets=args.tickets, workers=args.workers, concurrency=args.concurrency,
                          latency_ms=args.latency_ms, rate_limit_every=args.rate_limit_every,
                          timeout=args.timeout, max_p95_ms=args.max_p95_ms)
    except ValueError:
        parser.error("Invalid load-test bounds")
    payload = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n")
    print(payload)
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
