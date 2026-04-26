from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PlatformRequest(BaseModel):
    os: str = Field(..., description="windows or linux")
    version: str | None = None
    arch: str | None = None


class SubmissionOptions(BaseModel):
    timeout: int | None = None
    priority: int | None = None
    route: str | None = None
    platforms: list[PlatformRequest] | None = None
    submitter: str | None = None


class TaskView(BaseModel):
    id: int
    analysis_id: str
    platform: str
    os_version: str | None = None
    arch: str | None = None
    status: str
    node_id: str | None = None
    machine_id: str | None = None
    timeout: int
    route: str
    error_code: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str


class ArtifactView(BaseModel):
    id: str
    analysis_id: str
    task_id: int | None = None
    type: str
    name: str
    storage_key: str
    size: int
    sha256: str | None = None
    created_at: str


class AnalysisView(BaseModel):
    id: str
    status: str
    submitter: str | None = None
    created_at: str
    updated_at: str
    sha256: str
    filename: str
    size: int
    mime: str | None = None
    sample_storage_key: str
    settings: dict[str, Any]
    identification: dict[str, Any]


class SubmissionResponse(BaseModel):
    analysis: AnalysisView
    tasks: list[TaskView]


class MachineRegistration(BaseModel):
    name: str
    platform: str
    os_version: str | None = None
    arch: str | None = None
    ip: str | None = None
    tags: list[str] = Field(default_factory=list)
    state: str = "available"


class NodeRegistration(BaseModel):
    id: str | None = None
    name: str
    api_addr: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    machines: list[MachineRegistration] = Field(default_factory=list)


class NodeLeaseRequest(BaseModel):
    node_id: str
    lease_seconds: int = 300


class TaskLeaseResponse(BaseModel):
    task: TaskView
    lease_token: str
    lease_expires_at: str
    execution_policy: str
    vm_only_execution: bool = True
    guest_plan: dict[str, Any]


class TaskStatusUpdate(BaseModel):
    status: str
    lease_token: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class TaskEventBatch(BaseModel):
    lease_token: str
    source: str = "guest_agent"
    events: list[dict[str, Any]]


class TaskResultStatusMessage(BaseModel):
    lease_token: str
    status: str
    message: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class TaskIngestResponse(BaseModel):
    accepted: int
    artifact: ArtifactView
