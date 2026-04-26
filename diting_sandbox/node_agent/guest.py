from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib import error, parse, request


class GuestAgentError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class GuestAgentClient:
    def __init__(self, base_url: str, timeout: float = 15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        return self._json_request("GET", "/health")

    def status(self) -> dict[str, Any]:
        return self._json_request("GET", "/status")

    def wait_healthy(self, timeout_seconds: int, interval_seconds: float = 2.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        last_error: GuestAgentError | None = None
        while time.monotonic() <= deadline:
            try:
                health = self.health()
                if health.get("status") == "ok" and health.get("is_guest_vm") is True:
                    return health
                last_error = GuestAgentError(
                    "GUEST_CONTEXT_INVALID",
                    f"guest agent did not report guest_vm context: {health}",
                )
            except GuestAgentError as exc:
                last_error = exc
            time.sleep(interval_seconds)
        raise last_error or GuestAgentError("GUEST_UNAVAILABLE", "guest agent did not become healthy")

    def store_file(self, local_path: Path, filename: str | None = None) -> dict[str, Any]:
        safe_name = Path(filename or local_path.name).name or "sample.bin"
        query = parse.urlencode({"filename": safe_name})
        return self._json_request(
            "POST",
            f"/store?{query}",
            data=local_path.read_bytes(),
            headers={"Content-Type": "application/octet-stream"},
        )

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
        return self._json_request(
            "POST",
            "/execute",
            {
                "analysis_id": analysis_id,
                "task_id": task_id,
                "sample_path": sample_path,
                "timeout": timeout,
                "resultserver_url": resultserver_url,
                "arguments": arguments,
            },
        )

    def _json_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        body = data
        request_headers = dict(headers or {})
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        req = request.Request(
            self.base_url + path,
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise GuestAgentError(_guest_error_code(exc.code), detail, exc.code) from exc
        except error.URLError as exc:
            raise GuestAgentError("GUEST_CONNECTION_FAILED", str(exc.reason)) from exc
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))


def _guest_error_code(status_code: int) -> str:
    if status_code == 403:
        return "GUEST_POLICY_REFUSED"
    if status_code == 404:
        return "GUEST_ENDPOINT_NOT_FOUND"
    if status_code == 501:
        return "GUEST_EXECUTE_NOT_IMPLEMENTED"
    return "GUEST_AGENT_HTTP_ERROR"
