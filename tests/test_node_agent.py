from __future__ import annotations

from pathlib import Path
from typing import Any

from diting_sandbox.node_agent.config import MachineConfig, NodeAgentConfig
from diting_sandbox.node_agent.machinery import NoopMachinery
from diting_sandbox.node_agent.runner import NodeAgentRunner

from .helpers import make_work_dir, remove_work_dir


class FakeClient:
    server_url = "http://server.local"

    def __init__(self, lease: dict[str, Any]):
        self.lease = lease
        self.statuses: list[str] = []
        self.uploaded_events: list[dict[str, Any]] = []
        self.result_statuses: list[str] = []

    def update_task_status(
        self,
        task_id: int,
        status: str,
        lease_token: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        self.statuses.append(status)
        return {**self.lease["task"], "status": status}

    def download_task_sample(self, task_id: int, lease_token: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"MZ" + b"\0" * 64)
        return destination

    def upload_events(self, task_id: int, lease_token: str, source: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        self.uploaded_events.extend(events)
        return {"accepted": len(events), "artifact": {"id": "artifact-events"}}

    def upload_artifact(self, task_id: int, lease_token: str, path: Path, artifact_type: str, name: str | None = None) -> dict[str, Any]:
        return {"id": "artifact-pcap", "type": artifact_type, "name": name or path.name}

    def result_status(
        self,
        task_id: int,
        lease_token: str,
        status: str,
        message: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.result_statuses.append(status)
        return {"accepted": 1, "artifact": {"id": "artifact-status"}}


class FakeGuestAgent:
    def __init__(self, base_url: str):
        self.base_url = base_url

    def wait_healthy(self, timeout_seconds: int, interval_seconds: float = 2.0) -> dict[str, Any]:
        return {"status": "ok", "is_guest_vm": True, "base_url": self.base_url}

    def store_file(self, local_path: Path, filename: str | None = None) -> dict[str, Any]:
        return {"status": "stored", "path": f"/opt/diting/inbox/{filename or local_path.name}"}

    def execute(
        self,
        *,
        analysis_id: str,
        task_id: int,
        sample_path: str,
        timeout: int,
        resultserver_url: str | None,
        arguments: str | None = None,
    ) -> dict[str, Any]:
        return {"status": "complete", "sample_path": sample_path, "resultserver_url": resultserver_url}


def test_node_agent_runner_transfers_sample_to_guest_and_finishes(monkeypatch):
    import diting_sandbox.node_agent.runner as runner_module

    monkeypatch.setattr(runner_module, "GuestAgentClient", FakeGuestAgent)
    work_dir = make_work_dir("node-agent")
    try:
        lease = {
            "task": {
                "id": 7,
                "analysis_id": "analysis-1",
                "platform": "windows",
                "os_version": "10",
                "arch": "amd64",
                "status": "leasing",
                "node_id": "node-1",
                "machine_id": "node-1:win10-01",
                "timeout": 90,
                "route": "drop",
            },
            "lease_token": "lease-token",
            "lease_expires_at": "2026-04-26T12:00:00Z",
            "vm_only_execution": True,
            "guest_plan": {
                "analysis_id": "analysis-1",
                "task_id": 7,
                "platform": "windows",
                "sample": {
                    "filename": "sample.exe",
                    "sha256": "0" * 64,
                    "storage_key": "samples/sha256/00/00/sample",
                },
                "allowed_execution_context": "guest_vm",
                "forbidden_execution_contexts": ["server", "node_agent_host", "host_shell", "container", "ci"],
            },
        }
        client = FakeClient(lease)
        config = NodeAgentConfig(
            node_id="node-1",
            name="node-1",
            server_url="http://server.local",
            work_dir=work_dir / "work",
            plan_dir=work_dir / "plans",
            guest_poll_interval=0.01,
            machines=[
                MachineConfig(
                    name="win10-01",
                    platform="windows",
                    os_version="10",
                    arch="amd64",
                    ip="192.168.30.11",
                )
            ],
        )

        result = NodeAgentRunner(config, client, NoopMachinery()).run_lease(lease)

        assert result.status == "finished"
        assert client.statuses == ["starting_vm", "preparing_guest", "running", "collecting"]
        assert client.result_statuses == ["complete"]
        assert (work_dir / "work" / "task-7" / "samples" / "sample.exe").read_bytes().startswith(b"MZ")
        assert any(event["event"] == "guest_agent.sample.stored" for event in client.uploaded_events)
        assert any(event["event"] == "machinery.start_vm.skipped" for event in client.uploaded_events)
    finally:
        remove_work_dir(work_dir)
