"""Single-host durable queue. Short IMMEDIATE transactions serialize claims."""

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from chaosdesk.schemas import TriageDecision


class AdmissionError(Exception):
    def __init__(self, code, status):
        self.code, self.status = code, status


class JobStore:
    def __init__(self, settings, clock=time.time):
        self.settings, self.clock = settings, clock
        self.path = settings.database

    @contextmanager
    def connection(self, write=False):
        connection = sqlite3.connect(str(self.path), timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("Unsupported database schema")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                    fingerprint TEXT NOT NULL, ticket TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('queued','running','completed','failed')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    available_at REAL NOT NULL, lease_until REAL,
                    lease_token TEXT, result TEXT, error TEXT,
                    mode TEXT NOT NULL, model TEXT NOT NULL,
                    kb_revision TEXT, pipeline_version TEXT NOT NULL DEFAULT '1'
                );
                CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(state, available_at, created_at);
                CREATE TABLE IF NOT EXISTS counters (key TEXT PRIMARY KEY, value REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, heartbeat REAL NOT NULL,
                    mode TEXT NOT NULL, model TEXT NOT NULL);
                PRAGMA user_version=1;
            """)
        self.path.chmod(0o600)

    def submit(self, ticket, key):
        payload = ticket.model_dump_json()
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()
        now = self.clock()
        with self.connection(write=True) as db:
            previous = db.execute("SELECT * FROM jobs WHERE idempotency_key=?", (key,)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise AdmissionError("idempotency_conflict", 409)
                return self.public(previous), False
            pending = db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
            stored = db.execute("SELECT count(*) FROM jobs").fetchone()[0]
            if pending >= self.settings.max_pending or stored >= self.settings.max_stored_jobs:
                raise AdmissionError("queue_full", 503)
            bucket = "submissions:" + str(int(now // 60))
            count = self.counter(db, bucket)
            if count >= self.settings.submissions_per_minute:
                raise AdmissionError("submission_rate_limited", 429)
            self.set_counter(db, bucket, count + 1)
            job_id = uuid.uuid4().hex
            db.execute("""INSERT INTO jobs
                (id,idempotency_key,fingerprint,ticket,state,created_at,updated_at,available_at,mode,model)
                VALUES (?,?,?,?,'queued',?,?,?,?,?)""",
                (job_id, key, fingerprint, payload, now, now, now,
                 self.settings.mode, self.settings.model))
            return self.public(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()), True

    @staticmethod
    def counter(db, key):
        row = db.execute("SELECT value FROM counters WHERE key=?", (key,)).fetchone()
        return row[0] if row else 0

    @staticmethod
    def set_counter(db, key, value):
        db.execute("INSERT INTO counters(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, value))

    def claim(self):
        now = self.clock()
        with self.connection(write=True) as db:
            db.execute("""UPDATE jobs SET state='failed',error='attempts_exhausted',updated_at=?,
                lease_token=NULL,lease_until=NULL WHERE state='running' AND lease_until<=? AND attempts>=?""",
                (now, now, self.settings.max_attempts))
            row = db.execute("""SELECT * FROM jobs WHERE mode=? AND model=? AND attempts<? AND
                ((state='queued' AND available_at<=?) OR (state='running' AND lease_until<=?))
                ORDER BY created_at,id LIMIT 1""",
                (self.settings.mode, self.settings.model, self.settings.max_attempts, now, now)).fetchone()
            if row is None:
                return None
            if self.settings.mode == "ai":
                day = "model-day:" + str(int(now // 86400))
                if self.counter(db, "model-next") > now or self.counter(db, day) >= self.settings.model_requests_per_day:
                    return None
                self.set_counter(db, "model-next", now + 60 / self.settings.model_requests_per_minute)
                self.set_counter(db, day, self.counter(db, day) + 1)
            token = uuid.uuid4().hex
            db.execute("""UPDATE jobs SET state='running',attempts=attempts+1,lease_token=?,
                lease_until=?,updated_at=?,error=NULL WHERE id=?""",
                (token, now + self.settings.lease_seconds, now, row["id"]))
            return dict(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def complete(self, job, result, kb_revision=None):
        result = TriageDecision.model_validate(result.model_dump())
        now = self.clock()
        with self.connection(write=True) as db:
            if result.ticket_id != json.loads(job["ticket"])["ticket_id"]:
                raise ValueError("Result ticket identity mismatch")
            return db.execute("""UPDATE jobs SET state='completed',result=?,updated_at=?,kb_revision=?,
                lease_token=NULL,lease_until=NULL,error=NULL WHERE id=? AND state='running'
                AND lease_token=? AND lease_until>?""",
                (result.model_dump_json(), now, kb_revision, job["id"], job["lease_token"], now)).rowcount == 1

    def fail(self, job, retryable=False, delay=0):
        now = self.clock()
        retry = retryable and job["attempts"] < self.settings.max_attempts
        with self.connection(write=True) as db:
            owns_lease = db.execute("""SELECT 1 FROM jobs WHERE id=? AND state='running'
                AND lease_token=? AND lease_until>?""", (job["id"], job["lease_token"], now)).fetchone()
            if not owns_lease:
                return False
            if retryable and self.settings.mode == "ai":
                self.set_counter(db, "model-next", max(self.counter(db, "model-next"), now + delay))
            return db.execute("""UPDATE jobs SET state=?,error=?,available_at=?,updated_at=?,
                lease_token=NULL,lease_until=NULL WHERE id=? AND state='running'
                AND lease_token=? AND lease_until>?""",
                ("queued" if retry else "failed", "provider_retry" if retry else "processing_failed",
                 now + delay, now, job["id"], job["lease_token"], now)).rowcount == 1

    @staticmethod
    def public(row):
        if row is None:
            return None
        return {"job_id": row["id"], "status": row["state"], "attempts": row["attempts"],
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "error": row["error"], "mode": row["mode"], "model": row["model"],
                "kb_revision": row["kb_revision"], "pipeline_version": row["pipeline_version"],
                "decision": json.loads(row["result"]) if row["result"] else None}

    def get(self, job_id):
        with self.connection() as db:
            return self.public(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def heartbeat(self, worker_id):
        with self.connection(write=True) as db:
            db.execute("INSERT INTO workers(id,heartbeat,mode,model) VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat",
                       (worker_id, self.clock(), self.settings.mode, self.settings.model))

    def unregister_worker(self, worker_id):
        with self.connection(write=True) as db:
            db.execute("DELETE FROM workers WHERE id=?", (worker_id,))

    def stats(self):
        now = self.clock()
        with self.connection() as db:
            states = dict(db.execute("SELECT state,count(*) FROM jobs GROUP BY state").fetchall())
            live = db.execute("SELECT count(*) FROM workers WHERE heartbeat>? AND mode=? AND model=?",
                              (now - 20, self.settings.mode, self.settings.model)).fetchone()[0]
            expired = db.execute("SELECT count(*) FROM jobs WHERE state='running' AND lease_until<=?", (now,)).fetchone()[0]
            oldest = db.execute("SELECT min(created_at) FROM jobs WHERE state='queued'").fetchone()[0]
            return {"jobs": states, "live_worker_processes": live, "expired_leases": expired,
                    "oldest_queued_seconds": max(0, now - oldest) if oldest is not None else 0,
                    "model_requests_today": self.counter(db, "model-day:" + str(int(now // 86400)))}

    def purge(self):
        now = self.clock()
        with self.connection(write=True) as db:
            removed = db.execute("DELETE FROM jobs WHERE state IN ('completed','failed') AND updated_at<?",
                                 (now - self.settings.retention_days * 86400,)).rowcount
            db.execute("DELETE FROM workers WHERE heartbeat<?", (now - 86400,))
            db.execute("DELETE FROM counters WHERE (key LIKE 'submissions:%' AND key!=?) OR (key LIKE 'model-day:%' AND key!=?)",
                       ("submissions:" + str(int(now // 60)), "model-day:" + str(int(now // 86400))))
            return removed
