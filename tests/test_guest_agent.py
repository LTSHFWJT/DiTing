from __future__ import annotations

from fastapi.testclient import TestClient

from diting_sandbox.guest_agent.app import GuestSettings, create_app

from .helpers import make_work_dir, remove_work_dir


def test_guest_agent_refuses_store_when_not_in_guest_vm():
    work_dir = make_work_dir("guest-agent")
    try:
        settings = GuestSettings(
            inbox_dir=work_dir / "inbox",
            results_dir=work_dir / "results",
            execution_context="host",
        )
        client = TestClient(create_app(settings))

        health = client.get("/health")
        assert health.status_code == 200, health.text
        assert health.json()["is_guest_vm"] is False

        store = client.post("/store", params={"filename": "sample.exe"}, content=b"MZ")
        assert store.status_code == 403, store.text
        assert not (work_dir / "inbox" / "sample.exe").exists()
    finally:
        remove_work_dir(work_dir)
