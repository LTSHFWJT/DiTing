from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from diting_sandbox.core.policy import HostExecutionBlocked, VM_ONLY_EXECUTION_POLICY, assert_vm_execution_context


@dataclass(frozen=True)
class GuestSettings:
    inbox_dir: Path
    results_dir: Path
    execution_context: str

    @property
    def is_guest_vm(self) -> bool:
        return self.execution_context == "guest_vm"


class ExecuteRequest(BaseModel):
    analysis_id: str
    task_id: int
    sample_path: str
    timeout: int
    resultserver_url: str | None = None
    arguments: str | None = None


def load_guest_settings() -> GuestSettings:
    return GuestSettings(
        inbox_dir=Path(os.environ.get("DITING_GUEST_INBOX", "C:/diting/inbox" if os.name == "nt" else "/opt/diting/inbox")),
        results_dir=Path(os.environ.get("DITING_GUEST_RESULTS", "C:/diting/results" if os.name == "nt" else "/opt/diting/results")),
        execution_context=os.environ.get("DITING_EXECUTION_CONTEXT", "host"),
    )


def create_app(settings: GuestSettings | None = None) -> FastAPI:
    settings = settings or load_guest_settings()
    app = FastAPI(
        title="DiTing Guest Agent",
        version="0.1.0",
        description="Guest-side API intended to run inside Windows/Linux analysis VMs.",
    )
    app.state.settings = settings

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "execution_context": settings.execution_context,
            "is_guest_vm": settings.is_guest_vm,
            "vm_only_execution": True,
            "policy": VM_ONLY_EXECUTION_POLICY,
        }

    @app.post("/store")
    async def store(filename: str, request: Request) -> dict:
        _require_guest_vm(settings)
        safe_name = Path(filename).name or "sample.bin"
        settings.inbox_dir.mkdir(parents=True, exist_ok=True)
        path = settings.inbox_dir / safe_name
        data = await request.body()
        path.write_bytes(data)
        return {
            "status": "stored",
            "path": str(path),
            "size": len(data),
        }

    @app.post("/execute")
    def execute(task: ExecuteRequest) -> dict:
        _require_guest_vm(settings)
        raise HTTPException(
            status_code=501,
            detail=(
                "MVP guest agent exposes the VM-only execution boundary, but the "
                "platform-specific analyzer runner is not implemented yet."
            ),
        )

    return app


def _require_guest_vm(settings: GuestSettings) -> None:
    try:
        assert_vm_execution_context(settings.is_guest_vm)
    except HostExecutionBlocked as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
