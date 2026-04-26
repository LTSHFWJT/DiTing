from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_GUEST_AGENT_PORT = 8765


@dataclass(frozen=True)
class MachineConfig:
    name: str
    platform: str
    os_version: str | None = None
    arch: str | None = None
    ip: str | None = None
    tags: list[str] = field(default_factory=list)
    state: str = "available"
    agent_port: int = DEFAULT_GUEST_AGENT_PORT
    guest_url: str | None = None
    backend: str | None = None
    vm_name: str | None = None
    snapshot: str | None = None
    qcow2_path: str | None = None
    overlay_dir: str | None = None
    interface: str | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "MachineConfig":
        arch = data.get("arch") or data.get("architecture")
        return cls(
            name=str(data["name"]),
            platform=str(data["platform"]).lower(),
            os_version=_string_or_none(data.get("os_version") or data.get("version")),
            arch=_string_or_none(arch),
            ip=_string_or_none(data.get("ip")),
            tags=[str(item) for item in data.get("tags", [])],
            state=str(data.get("state") or "available"),
            agent_port=int(data.get("agent_port") or DEFAULT_GUEST_AGENT_PORT),
            guest_url=_string_or_none(data.get("guest_url")),
            backend=_string_or_none(data.get("backend")),
            vm_name=_string_or_none(data.get("vm_name") or data.get("domain")),
            snapshot=_string_or_none(data.get("snapshot") or data.get("snapshot_name")),
            qcow2_path=_string_or_none(data.get("qcow2_path")),
            overlay_dir=_string_or_none(data.get("overlay_dir")),
            interface=_string_or_none(data.get("interface")),
        )

    def to_registration(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "platform": self.platform,
            "os_version": self.os_version,
            "arch": self.arch,
            "ip": self.ip,
            "tags": self.tags,
            "state": self.state,
        }

    def resolved_guest_url(self) -> str | None:
        if self.guest_url:
            return self.guest_url.rstrip("/")
        if self.ip:
            return f"http://{self.ip}:{self.agent_port}"
        return None

    def matches_task_machine(self, machine_id: str | None) -> bool:
        if not machine_id:
            return False
        return machine_id == self.name or machine_id.endswith(f":{self.name}")


@dataclass(frozen=True)
class CaptureConfig:
    enabled: bool = False
    tool: str = "tcpdump"
    interface: str | None = None
    extra_args: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "CaptureConfig":
        data = data or {}
        return cls(
            enabled=bool(data.get("enabled", False)),
            tool=str(data.get("tool") or "tcpdump"),
            interface=_string_or_none(data.get("interface")),
            extra_args=[str(item) for item in data.get("extra_args", [])],
        )


@dataclass(frozen=True)
class NodeAgentConfig:
    node_id: str
    name: str
    server_url: str
    api_addr: str | None = None
    plan_dir: Path = Path(".diting-node-plans")
    work_dir: Path = Path(".diting-node-work")
    lease_seconds: int = 300
    machinery_backend: str = "noop"
    resultserver_url: str | None = None
    guest_health_timeout: int = 60
    guest_poll_interval: float = 2.0
    task_timeout_grace: int = 30
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    machines: list[MachineConfig] = field(default_factory=list)
    security: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "NodeAgentConfig":
        node = data.get("node", {})
        machinery = data.get("machinery", {})
        guest = data.get("guest", {})
        machines = [_machine_from_yaml_item(item) for item in data.get("machines", [])]
        security = dict(data.get("security", {}))
        security.setdefault("vm_only_execution", True)
        security.setdefault("executes_samples_on_host", False)

        return cls(
            node_id=str(node.get("id") or data.get("node_id") or "node-1"),
            name=str(node.get("name") or data.get("name") or node.get("id") or "node-1"),
            server_url=str(node.get("server_url") or data.get("server_url") or "http://127.0.0.1:8000"),
            api_addr=_string_or_none(node.get("api_addr") or data.get("api_addr")),
            plan_dir=Path(node.get("plan_dir") or data.get("plan_dir") or ".diting-node-plans"),
            work_dir=Path(node.get("work_dir") or data.get("work_dir") or ".diting-node-work"),
            lease_seconds=int(node.get("lease_seconds") or data.get("lease_seconds") or 300),
            machinery_backend=str(machinery.get("backend") or data.get("machinery_backend") or "noop"),
            resultserver_url=_string_or_none(data.get("resultserver_url") or node.get("resultserver_url")),
            guest_health_timeout=int(guest.get("health_timeout") or data.get("guest_health_timeout") or 60),
            guest_poll_interval=float(guest.get("poll_interval") or data.get("guest_poll_interval") or 2.0),
            task_timeout_grace=int(guest.get("task_timeout_grace") or data.get("task_timeout_grace") or 30),
            capture=CaptureConfig.from_mapping(data.get("capture")),
            machines=machines,
            security=security,
        )

    def machine_for_task(self, task: dict[str, Any]) -> MachineConfig:
        machine_id = task.get("machine_id")
        for machine in self.machines:
            if machine.matches_task_machine(machine_id):
                return machine
        platform = str(task.get("platform") or "").lower()
        for machine in self.machines:
            if machine.platform == platform:
                return machine
        raise NodeConfigError(f"no configured machine matches task machine_id={machine_id!r}")

    def machine_registration(self) -> list[dict[str, Any]]:
        return [machine.to_registration() for machine in self.machines]


class NodeConfigError(RuntimeError):
    pass


def load_node_config(path: Path) -> NodeAgentConfig:
    data = _load_mapping(path)
    return NodeAgentConfig.from_mapping(data)


def _load_mapping(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(raw)
    else:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as exc:
            raise NodeConfigError("YAML config requires PyYAML. Install project dependencies first.") from exc
        data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise NodeConfigError("node config root must be a mapping")
    return data


def _machine_from_yaml_item(item: Any) -> MachineConfig:
    if isinstance(item, dict) and "name" in item:
        return MachineConfig.from_mapping(item)
    if isinstance(item, dict) and len(item) == 1:
        name, value = next(iter(item.items()))
        if not isinstance(value, dict):
            raise NodeConfigError(f"machine {name!r} must be a mapping")
        return MachineConfig.from_mapping({"name": name, **value})
    raise NodeConfigError("machine entries must be mappings")


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
