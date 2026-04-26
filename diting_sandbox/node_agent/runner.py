from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from diting_sandbox.core.policy import VM_ONLY_EXECUTION_POLICY
from diting_sandbox.core.timeutil import iso_now

from .capture import PacketCaptureManager
from .client import APIError, SandboxApiClient
from .config import MachineConfig, NodeAgentConfig
from .guest import GuestAgentClient, GuestAgentError
from .machinery import Machinery, MachineryError, MachineRuntime, create_machinery_backend


@dataclass
class RunResult:
    status: str
    task_id: int | None = None
    analysis_id: str | None = None
    machine: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "task_id": self.task_id,
            "analysis_id": self.analysis_id,
            "machine": self.machine,
            "events": self.events,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


class NodeAgentRunError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class NodeAgentRunner:
    def __init__(
        self,
        config: NodeAgentConfig,
        client: SandboxApiClient | None = None,
        machinery: Machinery | None = None,
    ):
        self.config = config
        self.client = client or SandboxApiClient(config.server_url)
        self.machinery = machinery or create_machinery_backend(config.machinery_backend)
        self.capture = PacketCaptureManager(config.capture)

    def register(self) -> dict[str, Any]:
        return self.client.register_node(
            node_id=self.config.node_id,
            name=self.config.name,
            api_addr=self.config.api_addr,
            capabilities={
                "vm_only_execution": True,
                "executes_samples_on_host": False,
                "machinery_backend": self.machinery.name,
                "packet_capture": self.config.capture.enabled,
                "policy": VM_ONLY_EXECUTION_POLICY,
            },
            machines=self.config.machine_registration(),
        )

    def lease_once(self) -> dict[str, Any] | None:
        try:
            lease = self.client.lease_task(self.config.node_id, self.config.lease_seconds)
        except APIError as exc:
            if exc.status_code == 404:
                return None
            raise
        self.write_guest_plan(lease)
        return lease

    def run_once(self, *, register_first: bool = False) -> RunResult:
        if register_first:
            self.register()
        lease = self.lease_once()
        if lease is None:
            return RunResult(status="idle")
        return self.run_lease(lease)

    def run_loop(self, *, interval_seconds: float = 5.0, max_tasks: int | None = None, register_first: bool = False) -> list[RunResult]:
        if register_first:
            self.register()
        results: list[RunResult] = []
        while max_tasks is None or len(results) < max_tasks:
            lease = self.lease_once()
            if lease is None:
                time.sleep(interval_seconds)
                continue
            results.append(self.run_lease(lease))
        return results

    def write_guest_plan(self, lease: dict[str, Any]) -> Path:
        task = lease["task"]
        self.config.plan_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.plan_dir / f"task-{task['id']}-guest-plan.json"
        path.write_text(json.dumps(lease["guest_plan"], indent=2, sort_keys=True), encoding="utf-8")
        return path

    def run_lease(self, lease: dict[str, Any]) -> RunResult:
        self._verify_execution_policy(lease)
        task = lease["task"]
        task_id = int(task["id"])
        analysis_id = str(task["analysis_id"])
        lease_token = str(lease["lease_token"])
        machine = self.config.machine_for_task(task)
        task_work_dir = self.config.work_dir / f"task-{task_id}"
        task_work_dir.mkdir(parents=True, exist_ok=True)

        events: list[dict[str, Any]] = [
            self._event("node_agent.task.accepted", task, machine, {"lease_expires_at": lease.get("lease_expires_at")})
        ]
        runtime: MachineRuntime | None = None
        capture_handle = None
        terminal_status = "finished"
        error_code: str | None = None
        error_message: str | None = None

        try:
            self.client.update_task_status(task_id, "starting_vm", lease_token)
            runtime, new_events = self.machinery.prepare(machine, task, task_work_dir)
            events.extend(new_events)

            capture_handle, new_events = self.capture.start(machine, task, task_work_dir)
            events.extend(new_events)

            self.client.update_task_status(task_id, "preparing_guest", lease_token)
            guest_url = machine.resolved_guest_url()
            if not guest_url:
                raise NodeAgentRunError("GUEST_AGENT_NOT_CONFIGURED", f"machine {machine.name} has no guest_url or ip")
            guest = GuestAgentClient(guest_url)
            health = guest.wait_healthy(self.config.guest_health_timeout, self.config.guest_poll_interval)
            events.append(self._event("guest_agent.health.ok", task, machine, {"guest_url": guest_url, "health": health}))

            guest_config_path = self._write_guest_task_config(lease, task_work_dir)
            config_response = guest.store_file(guest_config_path, "diting-task-config.json")
            events.append(
                self._event(
                    "guest_agent.config.stored",
                    task,
                    machine,
                    {"guest_url": guest_url, "guest_path": config_response.get("path")},
                )
            )

            sample_path = self._download_sample(lease, task_work_dir)
            store_response = guest.store_file(sample_path, lease["guest_plan"]["sample"]["filename"])
            guest_sample_path = str(store_response.get("path") or sample_path.name)
            events.append(
                self._event(
                    "guest_agent.sample.stored",
                    task,
                    machine,
                    {"guest_url": guest_url, "guest_path": guest_sample_path, "size": sample_path.stat().st_size},
                )
            )

            self.client.update_task_status(task_id, "running", lease_token)
            execute_response = guest.execute(
                analysis_id=analysis_id,
                task_id=task_id,
                sample_path=guest_sample_path,
                timeout=int(task["timeout"]),
                resultserver_url=self._resultserver_url(task_id),
            )
            events.append(self._event("guest_agent.execute.accepted", task, machine, {"response": execute_response}))

            terminal_status, error_code, error_message = self._wait_execution_result(
                guest,
                task,
                machine,
                execute_response,
                events,
            )
        except GuestAgentError as exc:
            terminal_status = "failed"
            error_code = exc.code
            error_message = exc.message
            events.append(self._event("guest_agent.error", task, machine, {"code": exc.code, "message": exc.message, "status_code": exc.status_code}))
        except MachineryError as exc:
            terminal_status = "failed"
            error_code = exc.code
            error_message = exc.message
            events.append(self._event("machinery.error", task, machine, {"code": exc.code, "message": exc.message}))
        except NodeAgentRunError as exc:
            terminal_status = "failed"
            error_code = exc.code
            error_message = exc.message
            events.append(self._event("node_agent.error", task, machine, {"code": exc.code, "message": exc.message}))
        except Exception as exc:
            terminal_status = "failed"
            error_code = "NODE_AGENT_EXCEPTION"
            error_message = str(exc)
            events.append(self._event("node_agent.error", task, machine, {"code": error_code, "message": error_message}))
        finally:
            try:
                self.client.update_task_status(task_id, "collecting", lease_token)
            except APIError:
                pass
            pcap_path, new_events = self.capture.stop(capture_handle, machine, task)
            events.extend(new_events)
            if pcap_path:
                try:
                    self.client.upload_artifact(task_id, lease_token, pcap_path, "pcap", pcap_path.name)
                    events.append(self._event("capture.artifact.uploaded", task, machine, {"path": str(pcap_path)}))
                except APIError as exc:
                    events.append(self._event("capture.artifact.upload_failed", task, machine, {"message": exc.detail}))
            if runtime is not None:
                try:
                    events.extend(self.machinery.cleanup(runtime))
                except MachineryError as exc:
                    terminal_status = "failed"
                    error_code = error_code or exc.code
                    error_message = error_message or exc.message
                    events.append(self._event("machinery.cleanup.error", task, machine, {"code": exc.code, "message": exc.message}))
            self._upload_lifecycle_events(task_id, lease_token, events)
            self._finish_task(task_id, lease_token, terminal_status, error_code, error_message)

        return RunResult(
            status=terminal_status,
            task_id=task_id,
            analysis_id=analysis_id,
            machine=machine.name,
            events=events,
            error_code=error_code,
            error_message=error_message,
        )

    def _download_sample(self, lease: dict[str, Any], task_work_dir: Path) -> Path:
        task = lease["task"]
        sample = lease["guest_plan"]["sample"]
        filename = Path(sample["filename"]).name or "sample.bin"
        sample_dir = task_work_dir / "samples"
        destination = sample_dir / filename
        self.client.download_task_sample(int(task["id"]), str(lease["lease_token"]), destination)
        return destination

    def _write_guest_task_config(self, lease: dict[str, Any], task_work_dir: Path) -> Path:
        task = lease["task"]
        path = task_work_dir / "guest-task-config.json"
        document = {
            "task": task,
            "guest_plan": lease["guest_plan"],
            "lease_token": lease["lease_token"],
            "resultserver_url": self._resultserver_url(int(task["id"])),
            "execution_policy": VM_ONLY_EXECUTION_POLICY,
            "vm_only_execution": True,
        }
        path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def _wait_execution_result(
        self,
        guest: GuestAgentClient,
        task: dict[str, Any],
        machine: MachineConfig,
        execute_response: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> tuple[str, str | None, str | None]:
        initial = _normalize_guest_status(execute_response.get("status"))
        if initial:
            return initial

        deadline = time.monotonic() + int(task["timeout"]) + self.config.task_timeout_grace
        last_status: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            time.sleep(self.config.guest_poll_interval)
            try:
                last_status = guest.status()
            except GuestAgentError as exc:
                if exc.code == "GUEST_ENDPOINT_NOT_FOUND":
                    return "failed", "GUEST_STATUS_NOT_IMPLEMENTED", exc.message
                raise
            events.append(self._event("guest_agent.status", task, machine, {"status": last_status}))
            normalized = _normalize_guest_status(last_status.get("status"))
            if normalized:
                return normalized
        return "failed", "TASK_TIMEOUT", f"guest execution exceeded timeout={task['timeout']}s"

    def _finish_task(
        self,
        task_id: int,
        lease_token: str,
        terminal_status: str,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        if terminal_status == "finished":
            try:
                self.client.result_status(task_id, lease_token, "complete", "guest execution finished", {})
                return
            except APIError:
                self.client.update_task_status(task_id, "finished", lease_token)
                return

        message = error_message or "node agent task failed"
        try:
            self.client.result_status(
                task_id,
                lease_token,
                "exception",
                message,
                {"code": error_code or "NODE_AGENT_FAILED"},
            )
        except APIError:
            self.client.update_task_status(task_id, "failed", lease_token, error_code, message)

    def _upload_lifecycle_events(self, task_id: int, lease_token: str, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        try:
            self.client.upload_events(task_id, lease_token, "node_agent", events)
        except APIError:
            pass

    def _resultserver_url(self, task_id: int) -> str:
        if self.config.resultserver_url:
            return self.config.resultserver_url.rstrip("/")
        return f"{self.client.server_url}/api/v1/tasks/{task_id}"

    def _verify_execution_policy(self, lease: dict[str, Any]) -> None:
        guest_plan = lease.get("guest_plan", {})
        if lease.get("vm_only_execution") is not True:
            raise NodeAgentRunError("POLICY_VIOLATION", "lease is not marked as vm_only_execution")
        if guest_plan.get("allowed_execution_context") != "guest_vm":
            raise NodeAgentRunError("POLICY_VIOLATION", "guest plan does not require guest_vm execution")
        forbidden = set(guest_plan.get("forbidden_execution_contexts") or [])
        if "node_agent_host" not in forbidden:
            raise NodeAgentRunError("POLICY_VIOLATION", "guest plan does not forbid node_agent_host execution")

    def _event(self, event: str, task: dict[str, Any], machine: MachineConfig, detail: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "event": event,
            "source": "node_agent",
            "task_id": task.get("id"),
            "analysis_id": task.get("analysis_id"),
            "machine": machine.name,
            "platform": machine.platform,
            "detail": detail or {},
            "timestamp": iso_now(),
        }


def _normalize_guest_status(status: Any) -> tuple[str, str | None, str | None] | None:
    if status is None:
        return None
    normalized = str(status).lower()
    if normalized in {"complete", "completed", "finished", "success", "ok"}:
        return "finished", None, None
    if normalized in {"exception", "error", "failed", "failure"}:
        return "failed", "GUEST_EXECUTION_FAILED", f"guest reported status={status}"
    return None
