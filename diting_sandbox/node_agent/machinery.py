from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from diting_sandbox.core.timeutil import iso_now

from .config import MachineConfig


@dataclass
class MachineRuntime:
    machine: MachineConfig
    task: dict[str, Any]
    work_dir: Path
    overlay_path: Path | None = None
    details: dict[str, Any] = field(default_factory=dict)


class Machinery(Protocol):
    name: str

    def prepare(self, machine: MachineConfig, task: dict[str, Any], work_dir: Path) -> tuple[MachineRuntime, list[dict[str, Any]]]:
        ...

    def cleanup(self, runtime: MachineRuntime) -> list[dict[str, Any]]:
        ...

    def dump_memory(self, runtime: MachineRuntime, output_path: Path) -> list[dict[str, Any]]:
        ...

    def screenshot(self, runtime: MachineRuntime, output_path: Path) -> list[dict[str, Any]]:
        ...


class MachineryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class NoopMachinery:
    name = "noop"

    def prepare(self, machine: MachineConfig, task: dict[str, Any], work_dir: Path) -> tuple[MachineRuntime, list[dict[str, Any]]]:
        runtime = MachineRuntime(machine=machine, task=task, work_dir=work_dir)
        return runtime, [
            machinery_event("machinery.restore_snapshot.skipped", machine, task, {"backend": self.name}),
            machinery_event("machinery.start_vm.skipped", machine, task, {"backend": self.name}),
        ]

    def cleanup(self, runtime: MachineRuntime) -> list[dict[str, Any]]:
        return [
            machinery_event("machinery.stop_vm.skipped", runtime.machine, runtime.task, {"backend": self.name}),
            machinery_event("machinery.destroy_overlay.skipped", runtime.machine, runtime.task, {"backend": self.name}),
        ]

    def dump_memory(self, runtime: MachineRuntime, output_path: Path) -> list[dict[str, Any]]:
        return [machinery_event("machinery.dump_memory.skipped", runtime.machine, runtime.task, {"path": str(output_path)})]

    def screenshot(self, runtime: MachineRuntime, output_path: Path) -> list[dict[str, Any]]:
        return [machinery_event("machinery.screenshot.skipped", runtime.machine, runtime.task, {"path": str(output_path)})]


class LibvirtMachinery:
    name = "libvirt"

    def __init__(self, command_timeout: int = 120):
        self.command_timeout = command_timeout

    def prepare(self, machine: MachineConfig, task: dict[str, Any], work_dir: Path) -> tuple[MachineRuntime, list[dict[str, Any]]]:
        domain = _vm_name(machine)
        events: list[dict[str, Any]] = []
        if machine.snapshot:
            self._run(["virsh", "snapshot-revert", domain, machine.snapshot, "--running"])
            events.append(machinery_event("machinery.restore_snapshot", machine, task, {"domain": domain, "snapshot": machine.snapshot}))
        else:
            events.append(machinery_event("machinery.restore_snapshot.missing", machine, task, {"domain": domain}))

        self._ensure_started(domain)
        events.append(machinery_event("machinery.start_vm", machine, task, {"domain": domain}))
        return MachineRuntime(machine=machine, task=task, work_dir=work_dir, details={"domain": domain}), events

    def cleanup(self, runtime: MachineRuntime) -> list[dict[str, Any]]:
        domain = str(runtime.details.get("domain") or _vm_name(runtime.machine))
        self._run(["virsh", "destroy", domain], check=False)
        events = [machinery_event("machinery.stop_vm", runtime.machine, runtime.task, {"domain": domain})]
        if runtime.overlay_path:
            runtime.overlay_path.unlink(missing_ok=True)
            events.append(machinery_event("machinery.destroy_overlay", runtime.machine, runtime.task, {"path": str(runtime.overlay_path)}))
        return events

    def dump_memory(self, runtime: MachineRuntime, output_path: Path) -> list[dict[str, Any]]:
        domain = str(runtime.details.get("domain") or _vm_name(runtime.machine))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._run(["virsh", "dump", "--memory-only", domain, str(output_path)])
        return [machinery_event("machinery.dump_memory", runtime.machine, runtime.task, {"path": str(output_path)})]

    def screenshot(self, runtime: MachineRuntime, output_path: Path) -> list[dict[str, Any]]:
        domain = str(runtime.details.get("domain") or _vm_name(runtime.machine))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._run(["virsh", "screenshot", domain, str(output_path)])
        return [machinery_event("machinery.screenshot", runtime.machine, runtime.task, {"path": str(output_path)})]

    def _ensure_started(self, domain: str) -> None:
        result = self._run(["virsh", "domstate", domain], check=False)
        if "running" not in result.stdout.lower():
            self._run(["virsh", "start", domain])

    def _run(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
            )
        except FileNotFoundError as exc:
            raise MachineryError("MACHINERY_COMMAND_NOT_FOUND", f"command not found: {args[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise MachineryError("MACHINERY_COMMAND_TIMEOUT", f"command timed out: {' '.join(args)}") from exc
        if check and result.returncode != 0:
            raise MachineryError(
                "MACHINERY_COMMAND_FAILED",
                f"command failed ({result.returncode}): {' '.join(args)}\n{result.stderr.strip()}",
            )
        return result


class VirtualBoxMachinery:
    name = "virtualbox"

    def __init__(self, command_timeout: int = 120):
        self.command_timeout = command_timeout

    def prepare(self, machine: MachineConfig, task: dict[str, Any], work_dir: Path) -> tuple[MachineRuntime, list[dict[str, Any]]]:
        vm = _vm_name(machine)
        events: list[dict[str, Any]] = []
        if machine.snapshot:
            self._run(["VBoxManage", "snapshot", vm, "restore", machine.snapshot])
            events.append(machinery_event("machinery.restore_snapshot", machine, task, {"vm": vm, "snapshot": machine.snapshot}))
        else:
            events.append(machinery_event("machinery.restore_snapshot.missing", machine, task, {"vm": vm}))
        self._run(["VBoxManage", "startvm", vm, "--type", "headless"])
        events.append(machinery_event("machinery.start_vm", machine, task, {"vm": vm}))
        return MachineRuntime(machine=machine, task=task, work_dir=work_dir, details={"vm": vm}), events

    def cleanup(self, runtime: MachineRuntime) -> list[dict[str, Any]]:
        vm = str(runtime.details.get("vm") or _vm_name(runtime.machine))
        self._run(["VBoxManage", "controlvm", vm, "poweroff"], check=False)
        return [machinery_event("machinery.stop_vm", runtime.machine, runtime.task, {"vm": vm})]

    def dump_memory(self, runtime: MachineRuntime, output_path: Path) -> list[dict[str, Any]]:
        raise MachineryError("MACHINERY_UNSUPPORTED", "VirtualBox memory dump is not implemented yet")

    def screenshot(self, runtime: MachineRuntime, output_path: Path) -> list[dict[str, Any]]:
        vm = str(runtime.details.get("vm") or _vm_name(runtime.machine))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._run(["VBoxManage", "controlvm", vm, "screenshotpng", str(output_path)])
        return [machinery_event("machinery.screenshot", runtime.machine, runtime.task, {"path": str(output_path)})]

    def _run(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=self.command_timeout)
        except FileNotFoundError as exc:
            raise MachineryError("MACHINERY_COMMAND_NOT_FOUND", f"command not found: {args[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise MachineryError("MACHINERY_COMMAND_TIMEOUT", f"command timed out: {' '.join(args)}") from exc
        if check and result.returncode != 0:
            raise MachineryError("MACHINERY_COMMAND_FAILED", f"command failed ({result.returncode}): {' '.join(args)}\n{result.stderr.strip()}")
        return result


class HyperVMachinery:
    name = "hyperv"

    def __init__(self, command_timeout: int = 120):
        self.command_timeout = command_timeout

    def prepare(self, machine: MachineConfig, task: dict[str, Any], work_dir: Path) -> tuple[MachineRuntime, list[dict[str, Any]]]:
        vm = _vm_name(machine)
        events: list[dict[str, Any]] = []
        if machine.snapshot:
            self._powershell(f"Restore-VMSnapshot -VMName {_ps_quote(vm)} -Name {_ps_quote(machine.snapshot)} -Confirm:$false")
            events.append(machinery_event("machinery.restore_snapshot", machine, task, {"vm": vm, "snapshot": machine.snapshot}))
        else:
            events.append(machinery_event("machinery.restore_snapshot.missing", machine, task, {"vm": vm}))
        self._powershell(f"Start-VM -Name {_ps_quote(vm)}")
        events.append(machinery_event("machinery.start_vm", machine, task, {"vm": vm}))
        return MachineRuntime(machine=machine, task=task, work_dir=work_dir, details={"vm": vm}), events

    def cleanup(self, runtime: MachineRuntime) -> list[dict[str, Any]]:
        vm = str(runtime.details.get("vm") or _vm_name(runtime.machine))
        self._powershell(f"Stop-VM -Name {_ps_quote(vm)} -TurnOff -Force", check=False)
        return [machinery_event("machinery.stop_vm", runtime.machine, runtime.task, {"vm": vm})]

    def dump_memory(self, runtime: MachineRuntime, output_path: Path) -> list[dict[str, Any]]:
        raise MachineryError("MACHINERY_UNSUPPORTED", "Hyper-V memory dump is not implemented yet")

    def screenshot(self, runtime: MachineRuntime, output_path: Path) -> list[dict[str, Any]]:
        raise MachineryError("MACHINERY_UNSUPPORTED", "Hyper-V screenshot is not implemented yet")

    def _powershell(self, command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command]
        try:
            result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=self.command_timeout)
        except FileNotFoundError as exc:
            raise MachineryError("MACHINERY_COMMAND_NOT_FOUND", "PowerShell command not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise MachineryError("MACHINERY_COMMAND_TIMEOUT", f"command timed out: {command}") from exc
        if check and result.returncode != 0:
            raise MachineryError("MACHINERY_COMMAND_FAILED", f"command failed ({result.returncode}): {command}\n{result.stderr.strip()}")
        return result


def create_machinery_backend(name: str) -> Machinery:
    normalized = name.lower()
    if normalized in {"noop", "dry-run", "dry_run"}:
        return NoopMachinery()
    if normalized in {"libvirt", "kvm", "qemu"}:
        return LibvirtMachinery()
    if normalized in {"virtualbox", "vbox"}:
        return VirtualBoxMachinery()
    if normalized in {"hyperv", "hyper-v"}:
        return HyperVMachinery()
    raise MachineryError("MACHINERY_BACKEND_UNKNOWN", f"unknown machinery backend: {name}")


def machinery_event(event: str, machine: MachineConfig, task: dict[str, Any], detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "event": event,
        "source": "node_agent",
        "task_id": task.get("id"),
        "machine": machine.name,
        "platform": machine.platform,
        "detail": detail or {},
        "timestamp": iso_now(),
    }


def _vm_name(machine: MachineConfig) -> str:
    return machine.vm_name or machine.name


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
