"""Bounded workers over the durable queue. No in-memory task backlog."""

import argparse
import json
import logging
import os
import random
import signal
import threading
import time
import uuid

from chaosdesk.analysis import AIAnalyzer, LocalAnalyzer
from chaosdesk.interpreter import ProviderError
from chaosdesk.retrieval import SnapshotRetriever
from chaosdesk.schemas import Ticket
from chaosdesk.settings import Settings
from chaosdesk.store import JobStore
from chaosdesk.triage import TriageAgent


logger = logging.getLogger(__name__)


def make_agent(settings, retriever):
    analyzer = LocalAnalyzer() if settings.mode == "mock" else AIAnalyzer(
        model=settings.model, timeout=settings.model_timeout,
        max_output_tokens=settings.model_output_tokens,
        api_key=settings.openai_api_key.get_secret_value(),
    )
    return TriageAgent(analyzer=analyzer, retriever=retriever,
                       kb_dir=settings.kb_dir, retry_provider_errors=True)


def process_one(store, agent):
    job = store.claim()
    if job is None:
        return False
    started = time.monotonic()
    try:
        decision = agent.triage(Ticket.model_validate_json(job["ticket"]))
        committed = store.complete(job, decision, getattr(agent.retriever, "revision", None))
        event = "job_completed" if committed else "stale_job_discarded"
    except ProviderError as error:
        delay = max(error.retry_after, min(60, store.settings.retry_base_seconds *
                    2 ** (job["attempts"] - 1)) * random.uniform(0.75, 1.25))
        store.fail(job, retryable=error.retryable, delay=delay)
        event = "provider_failure"
    except Exception:
        store.fail(job)
        event = "job_failed"
    logger.info(json.dumps({"event": event, "job_id": job["id"], "attempt": job["attempts"],
                            "duration_ms": round((time.monotonic() - started) * 1000, 2)}))
    return True


class WorkerPool:
    def __init__(self, settings, store=None, agent_factory=None):
        self.settings = settings
        self.store = store if store is not None else JobStore(settings)
        self.agent_factory = agent_factory
        self.stop_event = threading.Event()
        self.threads = []
        self.worker_id = uuid.uuid4().hex

    def start(self):
        self.store.initialize()
        retriever = SnapshotRetriever(self.settings.kb_dir)
        # Construct clients before starting threads so configuration errors fail startup.
        agents = [(self.agent_factory() if self.agent_factory else make_agent(self.settings, retriever))
                  for _ in range(self.settings.workers)]
        for agent in agents:
            thread = threading.Thread(target=self._run, args=(agent,), daemon=True)
            self.threads.append(thread)
            thread.start()

    def _run(self, agent):
        while not self.stop_event.is_set():
            try:
                if process_one(self.store, agent):
                    continue
            except Exception:
                logger.error('{"event":"worker_store_error"}')
            self.stop_event.wait(self.settings.poll_seconds)

    def heartbeat(self):
        # A dead thread must not be advertised as available capacity.
        if self.threads and all(thread.is_alive() for thread in self.threads):
            self.store.heartbeat(self.worker_id)

    def stop(self):
        self.stop_event.set()
        self.store.unregister_worker(self.worker_id)
        deadline = time.monotonic() + self.settings.shutdown_seconds
        for thread in self.threads:
            thread.join(max(0, deadline - time.monotonic()))
        # Jobs interrupted by process termination remain leased and are recovered later.


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--purge", action="store_true", help="Delete expired terminal jobs and exit")
    args = parser.parse_args(argv)
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # SDK/http debug logs may contain request metadata; never enable them here.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = Settings.from_env()
        store = JobStore(settings)
        store.initialize()
        if args.purge:
            print(json.dumps({"purged": store.purge()}))
            return 0
        pool = WorkerPool(settings, store=store)
        signal.signal(signal.SIGTERM, lambda *_: pool.stop_event.set())
        signal.signal(signal.SIGINT, lambda *_: pool.stop_event.set())
        pool.start()
        try:
            last_purge = 0
            while not pool.stop_event.is_set():
                pool.heartbeat()
                if time.monotonic() - last_purge > 3600:
                    store.purge()
                    last_purge = time.monotonic()
                pool.stop_event.wait(5)
        finally:
            pool.stop()
    except Exception:
        logger.error('{"event":"worker_startup_or_runtime_failure"}')
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
