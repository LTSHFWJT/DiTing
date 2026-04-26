from __future__ import annotations

import json

from fastapi.testclient import TestClient

from diting_sandbox.core.config import Settings
from diting_sandbox.core.policy import HostExecutionBlocked
from diting_sandbox.node_agent.contracts import execute_on_host_is_forbidden
from diting_sandbox.server.app import create_app

from .helpers import make_work_dir, remove_work_dir


def make_client(work_dir) -> TestClient:
    settings = Settings(
        data_dir=work_dir,
        database_path=work_dir / "metadata.sqlite3",
        storage_dir=work_dir / "storage",
        max_file_size=10 * 1024 * 1024,
    )
    return TestClient(create_app(settings))


def test_submit_register_lease_vm_only_plan():
    work_dir = make_work_dir("api")
    try:
        client = make_client(work_dir)

        submit = client.post(
            "/api/v1/analyses",
            files={"file": ("sample.exe", b"MZ" + b"\x00" * 128, "application/octet-stream")},
            data={"options": json.dumps({"timeout": 90, "route": "drop"})},
        )
        assert submit.status_code == 200, submit.text
        body = submit.json()
        analysis_id = body["analysis"]["id"]
        assert body["analysis"]["identification"]["platforms"] == ["windows"]
        assert len(body["tasks"]) == 1
        assert body["tasks"][0]["platform"] == "windows"

        node = client.post(
            "/api/v1/nodes/register",
            json={
                "id": "node-1",
                "name": "node-1",
                "machines": [
                    {
                        "name": "win10-01",
                        "platform": "windows",
                        "os_version": "10",
                        "arch": "amd64",
                        "ip": "192.168.30.11",
                        "tags": ["office2019"],
                    }
                ],
            },
        )
        assert node.status_code == 200, node.text

        lease = client.post("/api/v1/tasks/lease", json={"node_id": "node-1"})
        assert lease.status_code == 200, lease.text
        lease_body = lease.json()
        assert lease_body["vm_only_execution"] is True
        assert lease_body["guest_plan"]["allowed_execution_context"] == "guest_vm"
        assert "node_agent_host" in lease_body["guest_plan"]["forbidden_execution_contexts"]
        assert lease_body["task"]["analysis_id"] == analysis_id

        sample = client.get(
            f"/api/v1/tasks/{lease_body['task']['id']}/sample",
            params={"lease_token": lease_body["lease_token"]},
        )
        assert sample.status_code == 200, sample.text
        assert sample.content.startswith(b"MZ")

        artifact = client.post(
            f"/api/v1/tasks/{lease_body['task']['id']}/artifacts",
            files={"file": ("events.jsonl", b'{"event":"process.create"}\n', "application/json")},
            data={"lease_token": lease_body["lease_token"], "type": "events", "name": "events.jsonl"},
        )
        assert artifact.status_code == 200, artifact.text
        artifact_body = artifact.json()
        assert artifact_body["type"] == "events"
        assert artifact_body["name"] == "events.jsonl"

        artifacts = client.get(f"/api/v1/analyses/{analysis_id}/artifacts")
        assert artifacts.status_code == 200, artifacts.text
        assert artifacts.json()[0]["id"] == artifact_body["id"]

        artifact_download = client.get(f"/api/v1/artifacts/{artifact_body['id']}")
        assert artifact_download.status_code == 200, artifact_download.text
        assert artifact_download.content == b'{"event":"process.create"}\n'

        events = client.post(
            f"/api/v1/tasks/{lease_body['task']['id']}/events",
            json={
                "lease_token": lease_body["lease_token"],
                "source": "guest_agent",
                "events": [
                    {
                        "event": "process.create",
                        "pid": 100,
                        "ppid": 50,
                        "image": "C:\\sample.exe",
                        "command_line": "C:\\sample.exe",
                    },
                    {
                        "event": "network.connect",
                        "pid": 100,
                        "protocol": "tcp",
                        "dst_ip": "203.0.113.10",
                        "dst_port": 443,
                    },
                ],
            },
        )
        assert events.status_code == 200, events.text
        assert events.json()["accepted"] == 2
        assert events.json()["artifact"]["type"] == "events"

        status = client.post(
            f"/api/v1/tasks/{lease_body['task']['id']}/result-status",
            json={"status": "complete", "lease_token": lease_body["lease_token"], "message": "guest finished"},
        )
        assert status.status_code == 200, status.text
        assert status.json()["artifact"]["type"] == "status"

        report = client.get(f"/api/v1/analyses/{analysis_id}/report")
        assert report.status_code == 200, report.text
        assert report.json()["info"]["vm_only_execution"] is True
        assert report.json()["artifacts"][0]["id"] == artifact_body["id"]
        assert report.json()["behavior"]["events_count"]["process.create"] == 1
        assert report.json()["behavior"]["events_count"]["network.connect"] == 1
        assert report.json()["behavior"]["process_tree"][0]["pid"] == 100
        assert report.json()["network"]["connections"][0]["dst_ip"] == "203.0.113.10"
    finally:
        remove_work_dir(work_dir)


def test_cancel_rerun_and_recover_expired_lease():
    work_dir = make_work_dir("api-control")
    try:
        client = make_client(work_dir)

        submit = client.post(
            "/api/v1/analyses",
            files={"file": ("sample", b"\x7fELF" + b"\x02\x01\x01" + b"\x00" * 64, "application/octet-stream")},
            data={"options": json.dumps({"timeout": 60, "route": "drop"})},
        )
        assert submit.status_code == 200, submit.text
        analysis_id = submit.json()["analysis"]["id"]

        cancel = client.post(f"/api/v1/analyses/{analysis_id}/cancel")
        assert cancel.status_code == 200, cancel.text
        assert cancel.json()["status"] == "cancelled"
        cancelled_tasks = client.get(f"/api/v1/analyses/{analysis_id}/tasks")
        assert cancelled_tasks.status_code == 200, cancelled_tasks.text
        assert cancelled_tasks.json()[0]["status"] == "cancelled"

        rerun = client.post(f"/api/v1/analyses/{analysis_id}/rerun")
        assert rerun.status_code == 200, rerun.text
        rerun_body = rerun.json()
        rerun_analysis_id = rerun_body["analysis"]["id"]
        assert rerun_analysis_id != analysis_id
        assert rerun_body["tasks"][0]["status"] == "queued"

        node = client.post(
            "/api/v1/nodes/register",
            json={
                "id": "node-lease",
                "name": "node-lease",
                "machines": [
                    {
                        "name": "ubuntu-01",
                        "platform": "linux",
                        "os_version": "ubuntu22.04",
                        "arch": "amd64",
                    }
                ],
            },
        )
        assert node.status_code == 200, node.text

        lease = client.post("/api/v1/tasks/lease", json={"node_id": "node-lease", "lease_seconds": -1})
        assert lease.status_code == 200, lease.text
        leased_task_id = lease.json()["task"]["id"]

        recover = client.post("/api/v1/tasks/leases/recover")
        assert recover.status_code == 200, recover.text
        recovered = recover.json()
        assert recovered[0]["id"] == leased_task_id
        assert recovered[0]["status"] == "queued"
        assert recovered[0]["error_code"] == "LEASE_EXPIRED"

        lease_again = client.post("/api/v1/tasks/lease", json={"node_id": "node-lease"})
        assert lease_again.status_code == 200, lease_again.text
        assert lease_again.json()["task"]["id"] == leased_task_id
    finally:
        remove_work_dir(work_dir)


def test_host_execution_contract_is_blocked():
    try:
        execute_on_host_is_forbidden()
    except HostExecutionBlocked as exc:
        assert "must only be executed inside" in str(exc)
    else:
        raise AssertionError("host execution was not blocked")
