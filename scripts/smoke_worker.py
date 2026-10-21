"""Readiness is not completion: exercise the external worker via the actual HTTP API."""

import time
from uuid import uuid4

import httpx


def main():
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=5) as client:
        assert client.get("/api/v1/capabilities").json()["execution_mode"] == "durable_sql_external"
        response = client.post(
            "/api/v1/runs",
            json={"query": "太空探索", "country": "CN"},
            headers={"Idempotency-Key": str(uuid4())},
        )
        response.raise_for_status()
        created = response.json()
        headers = {"X-Run-Token": created["access_token"]}
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            run = client.get(f"/api/v1/runs/{created['id']}", headers=headers).json()
            if run["status"] in {"completed", "partial", "failed", "cancelled"}:
                assert run["status"] == "completed", run["status"]
                assert run["results"] and run["attempt_count"] == 1
                print("Independent worker completed a persisted recommendation through HTTP")
                return
            time.sleep(0.2)
    raise SystemExit("No external worker completed the queued task")


if __name__ == "__main__":
    main()
