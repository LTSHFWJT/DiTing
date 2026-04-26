from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import HTTPException

from diting_sandbox.core.config import Settings
from diting_sandbox.core.db import Database
from diting_sandbox.core.identification import identify_file
from diting_sandbox.core.ids import new_analysis_id, new_object_id, new_token
from diting_sandbox.core.policy import VM_ONLY_EXECUTION_POLICY
from diting_sandbox.core.storage import LocalObjectStorage
from diting_sandbox.core.timeutil import iso_after, iso_now

from .schemas import NodeRegistration, SubmissionOptions


DEFAULT_OS_VERSION = {
    "windows": "10",
    "linux": "ubuntu22.04",
}

TERMINAL_TASK_STATUSES = {"finished", "failed", "cancelled"}
ALLOWED_TASK_STATUSES = {
    "queued",
    "leasing",
    "starting_vm",
    "preparing_guest",
    "running",
    "collecting",
    "postprocessing",
    *TERMINAL_TASK_STATUSES,
}


class SandboxService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.database_path)
        self.storage = LocalObjectStorage(settings.storage_dir, settings.max_file_size)

    def initialize(self) -> None:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.storage.initialize()
        self.db.initialize()

    def submit_file(
        self,
        stream: BinaryIO,
        filename: str,
        content_type: str | None,
        options: SubmissionOptions,
    ) -> dict[str, Any]:
        try:
            stored = self.storage.ingest_sample(stream, filename)
        except ValueError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        identification = identify_file(stored.path, filename).to_dict()
        sample_id = self.db.upsert_sample(stored, content_type)

        analysis_id = self._unique_analysis_id()
        settings = {
            "timeout": options.timeout or self.settings.default_timeout,
            "priority": options.priority or self.settings.default_priority,
            "route": options.route or self.settings.default_route,
            "platforms": [_model_to_dict(p) for p in options.platforms] if options.platforms else None,
            "vm_only_execution": True,
        }
        self.db.create_analysis(
            analysis_id=analysis_id,
            sample_id=sample_id,
            settings=settings,
            identification=identification,
            submitter=options.submitter,
        )

        task_specs = self._task_specs(settings, identification)
        task_ids = self.db.create_tasks(analysis_id, task_specs)

        analysis_doc = {
            "id": analysis_id,
            "sample": {
                "filename": stored.filename,
                "size": stored.size,
                "md5": stored.md5,
                "sha1": stored.sha1,
                "sha256": stored.sha256,
                "sha512": stored.sha512,
                "storage_key": stored.storage_key,
            },
            "settings": settings,
            "identification": identification,
            "execution_policy": VM_ONLY_EXECUTION_POLICY,
        }
        self.storage.write_analysis_json(analysis_id, "analysis.json", analysis_doc)

        tasks = self.db.list_tasks(analysis_id)
        for task in tasks:
            self.storage.write_task_json(
                analysis_id,
                task["id"],
                "task.json",
                {
                    "task": task,
                    "sample_storage_key": stored.storage_key,
                    "execution_policy": VM_ONLY_EXECUTION_POLICY,
                    "vm_only_execution": True,
                },
            )

        return {
            "analysis": self.get_analysis_or_404(analysis_id),
            "tasks": tasks,
        }

    def get_analysis_or_404(self, analysis_id: str) -> dict[str, Any]:
        analysis = self.db.get_analysis(analysis_id)
        if analysis is None:
            raise HTTPException(status_code=404, detail="analysis not found")
        return analysis

    def get_tasks_or_404(self, analysis_id: str) -> list[dict[str, Any]]:
        self.get_analysis_or_404(analysis_id)
        return self.db.list_tasks(analysis_id)

    def get_report_or_404(self, analysis_id: str) -> dict[str, Any]:
        analysis = self.get_analysis_or_404(analysis_id)
        tasks = self.db.list_tasks(analysis_id)
        artifacts = self.db.list_artifacts(analysis_id)
        report = self.storage.read_analysis_json(analysis_id, "report.json")
        if report is None:
            report = {
                "info": {
                    "analysis_id": analysis_id,
                    "status": analysis["status"],
                    "vm_only_execution": True,
                    "execution_policy": VM_ONLY_EXECUTION_POLICY,
                },
                "target": {
                    "filename": analysis["filename"],
                    "sha256": analysis["sha256"],
                    "size": analysis["size"],
                },
                "score": 0,
                "verdict": "unknown",
                "static": {
                    "identification": analysis["identification"],
                },
                "behavior": {
                    "process_tree": [],
                    "summary": {},
                    "events_count": {},
                },
                "network": {
                    "connections": [],
                },
                "dropped": [],
                "memory": {},
                "detections": [],
                "tasks": tasks,
                "artifacts": artifacts,
                "errors": [
                    {
                        "component": "runtime",
                        "message": "Dynamic execution has not run yet. A node must lease the task and execute it inside a VM.",
                    }
                ],
            }
        return report

    def get_task_sample_file_or_404(self, task_id: int, lease_token: str) -> tuple[Path, str, str]:
        task = self._require_task_lease(task_id, lease_token)
        analysis = self.get_analysis_or_404(task["analysis_id"])
        path = self.storage.sample_path(analysis["sample_storage_key"])
        if not path.exists():
            raise HTTPException(status_code=404, detail="sample object not found")
        return path, analysis["filename"], analysis["mime"] or "application/octet-stream"

    def create_task_artifact(
        self,
        task_id: int,
        lease_token: str,
        artifact_type: str,
        name: str,
        data: bytes,
    ) -> dict[str, Any]:
        task = self._require_task_lease(task_id, lease_token)
        artifact_id = new_object_id("artifact")
        try:
            stored = self.storage.store_artifact(
                analysis_id=task["analysis_id"],
                task_id=task_id,
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                name=name,
                data=data,
            )
        except ValueError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        artifact = self.db.add_artifact(
            artifact_id=artifact_id,
            analysis_id=task["analysis_id"],
            task_id=task_id,
            artifact_type=stored.artifact_type,
            name=stored.name,
            storage_key=stored.storage_key,
            size=stored.size,
            sha256=stored.sha256,
        )
        self._write_report(task["analysis_id"])
        return artifact

    def ingest_task_events(
        self,
        task_id: int,
        lease_token: str,
        source: str,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not events:
            raise HTTPException(status_code=400, detail="events must not be empty")
        task = self._require_task_lease(task_id, lease_token)
        now = iso_now()
        records = [
            {
                **event,
                "task_id": task_id,
                "source": event.get("source") or source,
                "received_at": event.get("received_at") or now,
            }
            for event in events
        ]
        artifact = self._append_task_log_artifact(task, "events", "events.jsonl", records)
        self._write_report(task["analysis_id"])
        return {
            "accepted": len(records),
            "artifact": artifact,
        }

    def ingest_task_result_status(
        self,
        task_id: int,
        lease_token: str,
        status: str,
        message: str | None,
        detail: dict[str, Any],
    ) -> dict[str, Any]:
        task = self._require_task_lease(task_id, lease_token)
        record = {
            "event": "task.status",
            "task_id": task_id,
            "status": status,
            "message": message,
            "detail": detail,
            "received_at": iso_now(),
        }
        artifact = self._append_task_log_artifact(task, "status", "status.jsonl", [record])

        task_status = _normalize_result_status(status)
        if task_status:
            self.update_task_status(
                task_id=task_id,
                status=task_status,
                lease_token=lease_token,
                error_code="GUEST_EXCEPTION" if task_status == "failed" else None,
                error_message=message if task_status == "failed" else None,
            )
        else:
            self._write_report(task["analysis_id"])

        return {
            "accepted": 1,
            "artifact": artifact,
        }

    def get_artifact_file_or_404(self, artifact_id: str) -> tuple[Path, str, str]:
        artifact = self.db.get_artifact(artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        path = self.storage.artifact_path(artifact["storage_key"])
        if not path.exists():
            raise HTTPException(status_code=404, detail="artifact object not found")
        return path, artifact["name"], "application/octet-stream"

    def register_node(self, registration: NodeRegistration) -> dict[str, Any]:
        node_id = registration.id or new_object_id("node")
        self.db.register_node(
            node_id=node_id,
            name=registration.name,
            api_addr=registration.api_addr,
            capabilities=registration.capabilities,
        )
        for machine in registration.machines:
            self.db.upsert_machine(
                {
                    "id": f"{node_id}:{machine.name}",
                    "node_id": node_id,
                    "name": machine.name,
                    "platform": machine.platform,
                    "os_version": machine.os_version,
                    "arch": machine.arch,
                    "ip": machine.ip,
                    "tags": machine.tags,
                    "state": machine.state,
                }
            )
        return {
            "node_id": node_id,
            "nodes": self.db.list_nodes(),
            "machines": self.db.list_machines(),
        }

    def lease_task(self, node_id: str, lease_seconds: int) -> dict[str, Any]:
        self.recover_expired_leases()
        lease_token = new_token()
        lease_expires_at = iso_after(lease_seconds)
        task = self.db.lease_task(node_id, lease_token, lease_expires_at)
        if task is None:
            raise HTTPException(status_code=404, detail="no queued task for this node")
        analysis = self.get_analysis_or_404(task["analysis_id"])
        return {
            "task": task,
            "lease_token": lease_token,
            "lease_expires_at": lease_expires_at,
            "execution_policy": VM_ONLY_EXECUTION_POLICY,
            "vm_only_execution": True,
            "guest_plan": {
                "analysis_id": task["analysis_id"],
                "task_id": task["id"],
                "platform": task["platform"],
                "os_version": task["os_version"],
                "arch": task["arch"],
                "timeout": task["timeout"],
                "route": task["route"],
                "sample": {
                    "filename": analysis["filename"],
                    "sha256": analysis["sha256"],
                    "storage_key": analysis["sample_storage_key"],
                },
                "allowed_execution_context": "guest_vm",
                "forbidden_execution_contexts": [
                    "server",
                    "processing_worker",
                    "node_agent_host",
                    "host_shell",
                    "container",
                    "ci",
                ],
            },
        }

    def cancel_analysis(self, analysis_id: str) -> dict[str, Any]:
        self.get_analysis_or_404(analysis_id)
        cancelled = self.db.cancel_analysis(analysis_id)
        for task in cancelled:
            current = self.db.get_task(task["id"])
            if current:
                self._write_task_record(current)
        self._write_report(analysis_id)
        return self.get_analysis_or_404(analysis_id)

    def rerun_analysis(self, analysis_id: str) -> dict[str, Any]:
        source = self.get_analysis_or_404(analysis_id)
        new_analysis_id = self._unique_analysis_id()
        settings = source["settings"]
        identification = source["identification"]
        self.db.create_analysis(
            analysis_id=new_analysis_id,
            sample_id=source["sample_id"],
            settings=settings,
            identification=identification,
            submitter=source.get("submitter"),
        )
        task_specs = self._task_specs(settings, identification)
        self.db.create_tasks(new_analysis_id, task_specs)
        analysis_doc = {
            "id": new_analysis_id,
            "source_analysis_id": analysis_id,
            "sample": {
                "filename": source["filename"],
                "size": source["size"],
                "sha256": source["sha256"],
                "storage_key": source["sample_storage_key"],
            },
            "settings": settings,
            "identification": identification,
            "execution_policy": VM_ONLY_EXECUTION_POLICY,
        }
        self.storage.write_analysis_json(new_analysis_id, "analysis.json", analysis_doc)
        tasks = self.db.list_tasks(new_analysis_id)
        for task in tasks:
            self._write_task_record(task, source["sample_storage_key"])
        return {
            "analysis": self.get_analysis_or_404(new_analysis_id),
            "tasks": tasks,
        }

    def recover_expired_leases(self) -> list[dict[str, Any]]:
        expired = self.db.requeue_expired_leases(iso_now())
        recovered: list[dict[str, Any]] = []
        for task in expired:
            current = self.db.get_task(task["id"])
            if current:
                self._write_task_record(current)
                self._write_report(current["analysis_id"])
                recovered.append(current)
        return recovered

    def update_task_status(
        self,
        task_id: int,
        status: str,
        lease_token: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        if status not in ALLOWED_TASK_STATUSES:
            raise HTTPException(status_code=400, detail=f"unsupported status: {status}")
        if lease_token is not None:
            self._require_task_lease(task_id, lease_token)
        task = self.db.update_task_status(task_id, status, error_code, error_message)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        self.storage.write_task_json(
            task["analysis_id"],
            task["id"],
            "task.json",
            self._task_document(task),
        )
        if status in TERMINAL_TASK_STATUSES:
            self._write_report(task["analysis_id"])
        return task

    def _task_specs(self, settings: dict[str, Any], identification: dict[str, Any]) -> list[dict[str, Any]]:
        platforms = settings.get("platforms")
        if platforms:
            requested = [
                {
                    "platform": p["os"].lower(),
                    "os_version": p.get("version"),
                    "arch": p.get("arch"),
                }
                for p in platforms
            ]
        else:
            requested = [
                {"platform": platform, "os_version": None, "arch": identification.get("arch")}
                for platform in identification.get("platforms", ["windows", "linux"])
            ]

        specs: list[dict[str, Any]] = []
        seen: set[tuple[str, str | None, str | None]] = set()
        for item in requested:
            platform = item["platform"]
            if platform not in {"windows", "linux"}:
                raise HTTPException(status_code=400, detail=f"unsupported platform: {platform}")
            spec = {
                "platform": platform,
                "os_version": item.get("os_version") or DEFAULT_OS_VERSION[platform],
                "arch": item.get("arch") or "amd64",
                "timeout": settings["timeout"],
                "route": settings["route"],
            }
            key = (spec["platform"], spec["os_version"], spec["arch"])
            if key not in seen:
                specs.append(spec)
                seen.add(key)
        return specs

    def _unique_analysis_id(self) -> str:
        for _ in range(10):
            analysis_id = new_analysis_id()
            if self.db.get_analysis(analysis_id) is None:
                return analysis_id
        raise RuntimeError("failed to allocate unique analysis id")

    def _require_task_lease(self, task_id: int, lease_token: str) -> dict[str, Any]:
        task = self.db.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        if not task.get("lease_token") or task["lease_token"] != lease_token:
            raise HTTPException(status_code=403, detail="invalid task lease token")
        return task

    def _write_task_record(self, task: dict[str, Any], sample_storage_key: str | None = None) -> None:
        self.storage.write_task_json(
            task["analysis_id"],
            task["id"],
            "task.json",
            self._task_document(task, sample_storage_key),
        )

    def _append_task_log_artifact(
        self,
        task: dict[str, Any],
        artifact_type: str,
        name: str,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            storage_key, size, sha256 = self.storage.append_task_jsonl(
                task["analysis_id"],
                task["id"],
                name,
                records,
            )
        except ValueError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        return self.db.upsert_task_artifact(
            artifact_id=new_object_id(f"artifact_{artifact_type}"),
            analysis_id=task["analysis_id"],
            task_id=task["id"],
            artifact_type=artifact_type,
            name=name,
            storage_key=storage_key,
            size=size,
            sha256=sha256,
        )

    def _task_document(self, task: dict[str, Any], sample_storage_key: str | None = None) -> dict[str, Any]:
        document = {
            "task": task,
            "execution_policy": VM_ONLY_EXECUTION_POLICY,
            "vm_only_execution": True,
        }
        if sample_storage_key:
            document["sample_storage_key"] = sample_storage_key
        return document

    def _write_report(self, analysis_id: str) -> dict[str, Any]:
        analysis = self.get_analysis_or_404(analysis_id)
        tasks = self.db.list_tasks(analysis_id)
        artifacts = self.db.list_artifacts(analysis_id)
        behavior = self._build_behavior_summary(analysis_id, tasks)
        failed_tasks = [task for task in tasks if task["status"] == "failed"]
        errors = [
            {
                "component": "task",
                "task_id": task["id"],
                "platform": task["platform"],
                "code": task.get("error_code") or "TASK_FAILED",
                "message": task.get("error_message") or "task failed",
            }
            for task in failed_tasks
        ]
        report = {
            "info": {
                "analysis_id": analysis_id,
                "status": analysis["status"],
                "vm_only_execution": True,
                "execution_policy": VM_ONLY_EXECUTION_POLICY,
            },
            "target": {
                "filename": analysis["filename"],
                "sha256": analysis["sha256"],
                "size": analysis["size"],
            },
            "score": 0,
            "verdict": "error" if failed_tasks else "unknown",
            "signatures": [],
            "mitre_attack": [],
            "static": {
                "identification": analysis["identification"],
            },
            "behavior": behavior["behavior"],
            "network": behavior["network"],
            "dropped": [],
            "memory": {},
            "detections": [],
            "tasks": tasks,
            "artifacts": artifacts,
            "errors": errors,
        }
        report_key = self.storage.write_analysis_json(analysis_id, "report.json", report)
        self.db.upsert_report(analysis_id, report_key, report["score"])
        return report

    def _build_behavior_summary(self, analysis_id: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        for task in tasks:
            events.extend(self.storage.read_task_jsonl(analysis_id, task["id"], "events.jsonl"))

        counts: Counter[str] = Counter()
        process_tree: list[dict[str, Any]] = []
        network_connections: list[dict[str, Any]] = []
        file_events: list[dict[str, Any]] = []
        registry_events: list[dict[str, Any]] = []

        for event in events:
            event_name = str(event.get("event") or event.get("type") or "unknown")
            counts[event_name] += 1
            if event_name in {"process.create", "process.start", "execve"}:
                process_tree.append(
                    {
                        "pid": event.get("pid") or event.get("process_id"),
                        "ppid": event.get("ppid") or event.get("parent_process_id"),
                        "image": event.get("image") or event.get("path") or event.get("exe"),
                        "command_line": event.get("command_line") or event.get("cmdline"),
                        "task_id": event.get("task_id"),
                    }
                )
            elif event_name.startswith("network."):
                network_connections.append(
                    {
                        "event": event_name,
                        "protocol": event.get("protocol"),
                        "src_ip": event.get("src_ip"),
                        "src_port": event.get("src_port"),
                        "dst_ip": event.get("dst_ip") or event.get("host"),
                        "dst_port": event.get("dst_port") or event.get("port"),
                        "domain": event.get("domain"),
                        "process_id": event.get("pid") or event.get("process_id"),
                        "task_id": event.get("task_id"),
                    }
                )
            elif event_name.startswith("file."):
                file_events.append(event)
            elif event_name.startswith("registry."):
                registry_events.append(event)

        return {
            "behavior": {
                "process_tree": process_tree,
                "summary": {
                    "total_events": len(events),
                    "process_events": sum(count for name, count in counts.items() if name.startswith("process.") or name == "execve"),
                    "file_events": sum(count for name, count in counts.items() if name.startswith("file.")),
                    "registry_events": sum(count for name, count in counts.items() if name.startswith("registry.")),
                    "network_events": sum(count for name, count in counts.items() if name.startswith("network.")),
                },
                "events_count": dict(sorted(counts.items())),
                "files": file_events[:200],
                "registry": registry_events[:200],
            },
            "network": {
                "connections": network_connections[:200],
            },
        }


def parse_submission_options(raw: str | None) -> SubmissionOptions:
    if not raw:
        return SubmissionOptions()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid options JSON: {exc}") from exc
    return SubmissionOptions(**data)


def _model_to_dict(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _normalize_result_status(status: str) -> str | None:
    normalized = status.lower()
    if normalized == "heartbeat":
        return None
    if normalized == "complete":
        return "finished"
    if normalized in {"exception", "error"}:
        return "failed"
    if normalized in ALLOWED_TASK_STATUSES:
        return normalized
    raise HTTPException(status_code=400, detail=f"unsupported result status: {status}")
