from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from diting_sandbox.core.timeutil import iso_now

from .config import CaptureConfig, MachineConfig


@dataclass
class CaptureHandle:
    path: Path
    process: subprocess.Popen[str] | None = None


class PacketCaptureManager:
    def __init__(self, config: CaptureConfig):
        self.config = config

    def start(self, machine: MachineConfig, task: dict[str, Any], work_dir: Path) -> tuple[CaptureHandle | None, list[dict[str, Any]]]:
        if not self.config.enabled:
            return None, [capture_event("capture.start.skipped", machine, task, {"reason": "disabled"})]
        if self.config.tool != "tcpdump":
            return None, [capture_event("capture.start.skipped", machine, task, {"reason": f"unsupported tool {self.config.tool}"})]

        interface = self.config.interface or machine.interface
        if not interface:
            return None, [capture_event("capture.start.skipped", machine, task, {"reason": "missing interface"})]

        pcap_path = work_dir / "artifacts" / f"task-{task['id']}.pcap"
        pcap_path.parent.mkdir(parents=True, exist_ok=True)
        args = ["tcpdump", "-i", interface, "-U", "-w", str(pcap_path)]
        args.extend(self.config.extra_args)
        if machine.ip:
            args.extend(["host", machine.ip])
        try:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except FileNotFoundError:
            return None, [capture_event("capture.start.failed", machine, task, {"code": "TCPDUMP_NOT_FOUND"})]

        return CaptureHandle(path=pcap_path, process=process), [
            capture_event("capture.start", machine, task, {"tool": "tcpdump", "interface": interface, "path": str(pcap_path)})
        ]

    def stop(self, handle: CaptureHandle | None, machine: MachineConfig, task: dict[str, Any]) -> tuple[Path | None, list[dict[str, Any]]]:
        if handle is None:
            return None, []
        process = handle.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if handle.path.exists() and handle.path.stat().st_size > 0:
            return handle.path, [capture_event("capture.stop", machine, task, {"path": str(handle.path)})]
        return None, [capture_event("capture.stop.empty", machine, task, {"path": str(handle.path)})]


def capture_event(event: str, machine: MachineConfig, task: dict[str, Any], detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "event": event,
        "source": "node_agent",
        "task_id": task.get("id"),
        "machine": machine.name,
        "platform": machine.platform,
        "detail": detail or {},
        "timestamp": iso_now(),
    }
