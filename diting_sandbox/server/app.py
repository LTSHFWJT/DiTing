from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, File, Form, Query, UploadFile
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.responses import FileResponse, RedirectResponse
from starlette.staticfiles import StaticFiles

from diting_sandbox.core.config import Settings, load_settings

from .schemas import (
    AnalysisView,
    ArtifactView,
    NodeLeaseRequest,
    NodeRegistration,
    SubmissionResponse,
    TaskEventBatch,
    TaskIngestResponse,
    TaskLeaseResponse,
    TaskResultStatusMessage,
    TaskStatusUpdate,
    TaskView,
)
from .service import SandboxService, parse_submission_options


PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    service = SandboxService(settings)
    service.initialize()

    app = FastAPI(
        title="DiTing Sandbox API",
        version="0.1.0",
        description="MVP sandbox control plane. Submitted files are never executed on the host.",
    )
    app.state.service = service
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def web_dashboard(request: Request) -> object:
        analyses = service.db.list_analyses(limit=20)
        nodes = service.db.list_nodes()
        machines = service.db.list_machines()
        tasks = []
        for analysis in analyses:
            tasks.extend(service.db.list_tasks(analysis["id"]))
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "active": "dashboard",
                "analyses": analyses,
                "nodes": nodes,
                "machines": machines,
                "stats": {
                    "analyses": len(analyses),
                    "queued": sum(1 for task in tasks if task["status"] == "queued"),
                    "running": sum(1 for task in tasks if task["status"] in {"leasing", "starting_vm", "preparing_guest", "running", "collecting"}),
                    "finished": sum(1 for task in tasks if task["status"] == "finished"),
                    "machines": len(machines),
                },
            },
        )

    @app.get("/submit")
    def web_submit_form(request: Request) -> object:
        return templates.TemplateResponse(
            request,
            "submit.html",
            {
                "active": "submit",
                "default_timeout": settings.default_timeout,
                "default_route": settings.default_route,
                "error": None,
            },
        )

    @app.post("/submit")
    async def web_submit_analysis(
        request: Request,
        file: UploadFile = File(...),
        timeout: int | None = Form(default=None),
        route: str | None = Form(default=None),
        platforms: list[str] | None = Form(default=None),
    ) -> object:
        options = {
            "timeout": timeout,
            "route": route,
            "platforms": [{"os": platform} for platform in platforms or []],
        }
        options = {key: value for key, value in options.items() if value not in (None, [], "")}
        parsed = parse_submission_options(json.dumps(options))
        try:
            file.file.seek(0)
            result = service.submit_file(file.file, file.filename or "sample.bin", file.content_type, parsed)
        except Exception as exc:
            return templates.TemplateResponse(
                request,
                "submit.html",
                {
                    "active": "submit",
                    "default_timeout": settings.default_timeout,
                    "default_route": settings.default_route,
                    "error": str(exc),
                },
                status_code=400,
            )
        finally:
            await file.close()
        analysis_id = result["analysis"]["id"]
        return RedirectResponse(url=f"/analyses/{analysis_id}", status_code=303)

    @app.get("/analyses")
    def web_analyses(request: Request, limit: int = 50, offset: int = 0) -> object:
        return templates.TemplateResponse(
            request,
            "analyses.html",
            {
                "active": "analyses",
                "analyses": service.db.list_analyses(limit=limit, offset=offset),
                "limit": limit,
                "offset": offset,
            },
        )

    @app.get("/analyses/{analysis_id}")
    def web_analysis_detail(request: Request, analysis_id: str) -> object:
        analysis = service.get_analysis_or_404(analysis_id)
        tasks = service.get_tasks_or_404(analysis_id)
        report = service.get_report_or_404(analysis_id)
        artifacts = service.db.list_artifacts(analysis_id)
        return templates.TemplateResponse(
            request,
            "analysis_detail.html",
            {
                "active": "analyses",
                "analysis": analysis,
                "tasks": tasks,
                "report": report,
                "artifacts": artifacts,
            },
        )

    @app.post("/analyses/{analysis_id}/cancel")
    def web_cancel_analysis(analysis_id: str) -> RedirectResponse:
        service.cancel_analysis(analysis_id)
        return RedirectResponse(url=f"/analyses/{analysis_id}", status_code=303)

    @app.post("/analyses/{analysis_id}/rerun")
    def web_rerun_analysis(analysis_id: str) -> RedirectResponse:
        result = service.rerun_analysis(analysis_id)
        return RedirectResponse(url=f"/analyses/{result['analysis']['id']}", status_code=303)

    @app.get("/nodes")
    def web_nodes(request: Request) -> object:
        return templates.TemplateResponse(
            request,
            "nodes.html",
            {
                "active": "nodes",
                "nodes": service.db.list_nodes(),
                "machines": service.db.list_machines(),
            },
        )

    @app.get("/api/v1/health")
    def health() -> dict:
        return {
            "status": "ok",
            "database": str(settings.database_path),
            "storage": str(settings.storage_dir),
            "vm_only_execution": True,
        }

    @app.post("/api/v1/analyses", response_model=SubmissionResponse)
    async def submit_analysis(
        file: UploadFile = File(...),
        options: str | None = Form(default=None),
    ) -> dict:
        parsed = parse_submission_options(options)
        try:
            file.file.seek(0)
            return service.submit_file(file.file, file.filename or "sample.bin", file.content_type, parsed)
        finally:
            await file.close()

    @app.get("/api/v1/analyses/{analysis_id}", response_model=AnalysisView)
    def get_analysis(analysis_id: str) -> dict:
        return service.get_analysis_or_404(analysis_id)

    @app.get("/api/v1/analyses/{analysis_id}/tasks", response_model=list[TaskView])
    def get_tasks(analysis_id: str) -> list[dict]:
        return service.get_tasks_or_404(analysis_id)

    @app.get("/api/v1/analyses/{analysis_id}/report")
    def get_report(analysis_id: str) -> dict:
        return service.get_report_or_404(analysis_id)

    @app.get("/api/v1/analyses/{analysis_id}/artifacts", response_model=list[ArtifactView])
    def get_artifacts(analysis_id: str) -> list[dict]:
        service.get_analysis_or_404(analysis_id)
        return service.db.list_artifacts(analysis_id)

    @app.post("/api/v1/analyses/{analysis_id}/cancel", response_model=AnalysisView)
    def cancel_analysis(analysis_id: str) -> dict:
        return service.cancel_analysis(analysis_id)

    @app.post("/api/v1/analyses/{analysis_id}/rerun", response_model=SubmissionResponse)
    def rerun_analysis(analysis_id: str) -> dict:
        return service.rerun_analysis(analysis_id)

    @app.get("/api/v1/tasks/{task_id}/sample")
    def download_task_sample(task_id: int, lease_token: str = Query(...)) -> FileResponse:
        path, filename, media_type = service.get_task_sample_file_or_404(task_id, lease_token)
        return FileResponse(path, media_type=media_type, filename=filename)

    @app.post("/api/v1/tasks/{task_id}/artifacts", response_model=ArtifactView)
    async def upload_task_artifact(
        task_id: int,
        file: UploadFile = File(...),
        lease_token: str = Form(...),
        artifact_type: str = Form(default="log", alias="type"),
        name: str | None = Form(default=None),
    ) -> dict:
        try:
            data = await file.read()
            artifact_name = name or file.filename or "artifact.bin"
            return service.create_task_artifact(task_id, lease_token, artifact_type, artifact_name, data)
        finally:
            await file.close()

    @app.post("/api/v1/tasks/{task_id}/events", response_model=TaskIngestResponse)
    def upload_task_events(task_id: int, batch: TaskEventBatch) -> dict:
        return service.ingest_task_events(task_id, batch.lease_token, batch.source, batch.events)

    @app.post("/api/v1/tasks/{task_id}/result-status", response_model=TaskIngestResponse)
    def upload_task_result_status(task_id: int, message: TaskResultStatusMessage) -> dict:
        return service.ingest_task_result_status(
            task_id,
            message.lease_token,
            message.status,
            message.message,
            message.detail,
        )

    @app.get("/api/v1/artifacts/{artifact_id}")
    def download_artifact(artifact_id: str) -> FileResponse:
        path, filename, media_type = service.get_artifact_file_or_404(artifact_id)
        return FileResponse(path, media_type=media_type, filename=filename)

    @app.post("/api/v1/nodes/register")
    def register_node(registration: NodeRegistration) -> dict:
        return service.register_node(registration)

    @app.get("/api/v1/nodes")
    def list_nodes() -> list[dict]:
        return service.db.list_nodes()

    @app.get("/api/v1/machines")
    def list_machines() -> list[dict]:
        return service.db.list_machines()

    @app.post("/api/v1/tasks/lease", response_model=TaskLeaseResponse)
    def lease_task(request: NodeLeaseRequest) -> dict:
        return service.lease_task(request.node_id, request.lease_seconds)

    @app.post("/api/v1/tasks/leases/recover", response_model=list[TaskView])
    def recover_expired_leases() -> list[dict]:
        return service.recover_expired_leases()

    @app.post("/api/v1/tasks/{task_id}/status", response_model=TaskView)
    def update_task_status(task_id: int, update: TaskStatusUpdate) -> dict:
        return service.update_task_status(
            task_id,
            update.status,
            update.lease_token,
            update.error_code,
            update.error_message,
        )

    return app
