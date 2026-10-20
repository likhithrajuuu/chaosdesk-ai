"""Offline API/worker process smoke test; needs permission to bind loopback."""

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parent.parent


def main():
    with tempfile.TemporaryDirectory(prefix="chaosdesk-smoke-") as folder:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, CHAOSDESK_API_TOKEN=secrets.token_urlsafe(32),
                   CHAOSDESK_DATABASE=str(Path(folder) / "jobs.db"),
                   CHAOSDESK_MODE="mock", CHAOSDESK_ENVIRONMENT="development",
                   CHAOSDESK_HOST="127.0.0.1", CHAOSDESK_PORT=str(port))
        processes = []
        with open(Path(folder) / "process.log", "w") as logs:
            try:
                api = subprocess.Popen([sys.executable, "-m", "chaosdesk.api"], env=env, cwd=ROOT,
                                       stdout=logs, stderr=logs)
                processes.append(api)
                worker = subprocess.Popen([sys.executable, "-m", "chaosdesk.worker"],
                                          env=env, cwd=ROOT, stdout=logs, stderr=logs)
                processes.append(worker)
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2) as client:
                    for _ in range(100):
                        try:
                            if client.get("/health/ready").status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(0.1)
                    else:
                        raise RuntimeError("Service readiness did not succeed")
                    assert client.get("/v1/metrics").status_code == 401
                    headers = {"Authorization": "Bearer " + env["CHAOSDESK_API_TOKEN"],
                               "Idempotency-Key": "process-smoke"}
                    ticket = json.loads((ROOT / "examples/ticket.json").read_text())
                    submitted = client.post("/v1/jobs", headers=headers, json=ticket)
                    assert submitted.status_code == 202
                    duplicate = client.post("/v1/jobs", headers=headers, json=ticket)
                    assert duplicate.status_code == 200
                    location = submitted.headers["location"]
                    for _ in range(100):
                        result = client.get(location, headers=headers).json()
                        if result["status"] == "completed":
                            break
                        time.sleep(0.05)
                    assert result["status"] == "completed" and result["decision"]["priority"] == "P0"
                    worker.terminate()
                    worker.wait(timeout=10)
                    assert client.get("/health/ready").status_code == 503
                    assert client.get(location, headers=headers).json()["status"] == "completed"
                    print(json.dumps({"accepted": True, "mode": "offline_process_smoke",
                                      "auth": "passed", "idempotency": "passed", "priority": "P0",
                                      "worker_shutdown_readiness": "passed", "persistence": "passed"}))
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()


if __name__ == "__main__":
    main()
