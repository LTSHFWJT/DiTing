from __future__ import annotations

import json
import mimetypes
import uuid
from pathlib import Path
from typing import Any
from urllib import error, parse, request


class APIError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"API error {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class SandboxApiClient:
    def __init__(self, server_url: str, timeout: float = 30):
        self.server_url = server_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        return self.get_json("/api/v1/health")

    def register_node(
        self,
        *,
        node_id: str | None,
        name: str,
        api_addr: str | None,
        capabilities: dict[str, Any],
        machines: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.post_json(
            "/api/v1/nodes/register",
            {
                "id": node_id,
                "name": name,
                "api_addr": api_addr,
                "capabilities": capabilities,
                "machines": machines,
            },
        )

    def lease_task(self, node_id: str, lease_seconds: int = 300) -> dict[str, Any]:
        return self.post_json(
            "/api/v1/tasks/lease",
            {"node_id": node_id, "lease_seconds": lease_seconds},
        )

    def recover_expired_leases(self) -> list[dict[str, Any]]:
        data = self.post_json("/api/v1/tasks/leases/recover", {})
        if not isinstance(data, list):
            raise APIError(500, "unexpected recover leases response")
        return data

    def update_task_status(
        self,
        task_id: int,
        status: str,
        lease_token: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        return self.post_json(
            f"/api/v1/tasks/{task_id}/status",
            {
                "status": status,
                "lease_token": lease_token,
                "error_code": error_code,
                "error_message": error_message,
            },
        )

    def upload_events(
        self,
        task_id: int,
        lease_token: str,
        source: str,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.post_json(
            f"/api/v1/tasks/{task_id}/events",
            {
                "lease_token": lease_token,
                "source": source,
                "events": events,
            },
        )

    def result_status(
        self,
        task_id: int,
        lease_token: str,
        status: str,
        message: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.post_json(
            f"/api/v1/tasks/{task_id}/result-status",
            {
                "lease_token": lease_token,
                "status": status,
                "message": message,
                "detail": detail or {},
            },
        )

    def download_task_sample(
        self,
        task_id: int,
        lease_token: str,
        destination: Path,
    ) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        query = parse.urlencode({"lease_token": lease_token})
        req = request.Request(
            self._url(f"/api/v1/tasks/{task_id}/sample?{query}"),
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                with destination.open("wb") as fp:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        fp.write(chunk)
        except error.HTTPError as exc:
            raise self._api_error(exc) from exc
        except error.URLError as exc:
            raise APIError(0, str(exc.reason)) from exc
        return destination

    def upload_artifact(
        self,
        task_id: int,
        lease_token: str,
        path: Path,
        artifact_type: str,
        name: str | None = None,
    ) -> dict[str, Any]:
        fields = {
            "lease_token": lease_token,
            "type": artifact_type,
            "name": name or path.name,
        }
        body, content_type = _encode_multipart(fields, "file", path)
        req = request.Request(
            self._url(f"/api/v1/tasks/{task_id}/artifacts"),
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        return self._read_json(req)

    def get_json(self, path: str) -> dict[str, Any]:
        req = request.Request(self._url(path), method="GET")
        return self._read_json(req)

    def post_json(self, path: str, payload: dict[str, Any]) -> Any:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self._url(path),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._read_json(req)

    def _read_json(self, req: request.Request) -> Any:
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
        except error.HTTPError as exc:
            raise self._api_error(exc) from exc
        except error.URLError as exc:
            raise APIError(0, str(exc.reason)) from exc
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _api_error(self, exc: error.HTTPError) -> APIError:
        detail = exc.read().decode("utf-8", errors="replace")
        return APIError(exc.code, detail)

    def _url(self, path: str) -> str:
        return self.server_url + path


def _encode_multipart(fields: dict[str, str], file_field: str, path: Path) -> tuple[bytes, str]:
    boundary = f"----diting-{uuid.uuid4().hex}"
    content_type = f"multipart/form-data; boundary={boundary}"
    parts: list[bytes] = []

    for key, value in fields.items():
        parts.append(f"--{boundary}\r\n".encode("ascii"))
        parts.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        parts.append(str(value).encode("utf-8"))
        parts.append(b"\r\n")

    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    parts.append(f"--{boundary}\r\n".encode("ascii"))
    parts.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(path.read_bytes())
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(parts), content_type
