from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from .client import APIError, SandboxApiClient
from .config import MachineConfig, NodeAgentConfig, load_node_config
from .runner import NodeAgentRunner


DEFAULT_SERVER = "http://127.0.0.1:8000"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="DiTing node agent control CLI. It never executes submitted files on the host.",
    )
    parser.add_argument("--server", help="central API base URL")
    parser.add_argument("--config", type=Path, help="node YAML/JSON config file")
    subcommands = parser.add_subparsers(dest="command", required=True)

    register = subcommands.add_parser("register", help="register this node and its VM pool")
    register.add_argument("--node-id")
    register.add_argument("--name")
    register.add_argument("--api-addr")
    register.add_argument(
        "--machine",
        action="append",
        default=[],
        metavar="NAME:PLATFORM:OS_VERSION:ARCH[:IP]",
        help="VM machine entry, for example win10-01:windows:10:amd64:192.168.30.11",
    )
    register.set_defaults(func=_register)

    lease = subcommands.add_parser("lease", help="lease one queued task for this node")
    lease.add_argument("--node-id")
    lease.add_argument("--lease-seconds", type=int)
    lease.add_argument("--plan-dir", type=Path, help="write the returned guest plan as JSON")
    lease.set_defaults(func=_lease)

    run_once = subcommands.add_parser("run-once", help="lease and run one task through the Node Agent lifecycle")
    run_once.add_argument("--node-id")
    run_once.add_argument("--lease-seconds", type=int)
    run_once.add_argument("--plan-dir", type=Path)
    run_once.add_argument("--work-dir", type=Path)
    run_once.add_argument("--machinery")
    run_once.add_argument("--register", action="store_true", help="register the node before leasing")
    run_once.set_defaults(func=_run_once)

    run_loop = subcommands.add_parser("run-loop", help="continuously lease and run tasks")
    run_loop.add_argument("--node-id")
    run_loop.add_argument("--lease-seconds", type=int)
    run_loop.add_argument("--plan-dir", type=Path)
    run_loop.add_argument("--work-dir", type=Path)
    run_loop.add_argument("--machinery")
    run_loop.add_argument("--register", action="store_true", help="register the node before leasing")
    run_loop.add_argument("--interval", type=float, default=5.0)
    run_loop.add_argument("--max-tasks", type=int)
    run_loop.set_defaults(func=_run_loop)

    recover = subcommands.add_parser("recover-leases", help="ask the server to requeue expired task leases")
    recover.set_defaults(func=_recover_leases)

    status = subcommands.add_parser("status", help="update a task status")
    status.add_argument("--task-id", required=True, type=int)
    status.add_argument("--status", required=True)
    status.add_argument("--lease-token")
    status.add_argument("--error-code")
    status.add_argument("--error-message")
    status.set_defaults(func=_status)

    events = subcommands.add_parser("events", help="upload task behavior events from a JSON or JSONL file")
    events.add_argument("--task-id", required=True, type=int)
    events.add_argument("--lease-token", required=True)
    events.add_argument("--source", default="node_agent")
    events.add_argument("--file", required=True, type=Path)
    events.set_defaults(func=_events)

    artifact = subcommands.add_parser("artifact", help="upload a task artifact file")
    artifact.add_argument("--task-id", required=True, type=int)
    artifact.add_argument("--lease-token", required=True)
    artifact.add_argument("--type", default="log")
    artifact.add_argument("--name")
    artifact.add_argument("--file", required=True, type=Path)
    artifact.set_defaults(func=_artifact)

    result_status = subcommands.add_parser("result-status", help="upload a ResultServer-style task status message")
    result_status.add_argument("--task-id", required=True, type=int)
    result_status.add_argument("--lease-token", required=True)
    result_status.add_argument("--status", required=True)
    result_status.add_argument("--message")
    result_status.add_argument("--detail-json", default="{}")
    result_status.set_defaults(func=_result_status)

    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except APIError as exc:
        raise SystemExit(str(exc)) from exc

    if result is not None:
        print(json.dumps(result, indent=2, sort_keys=True))


def _register(args: argparse.Namespace) -> dict[str, Any]:
    config = _build_config(args)
    machines = [_parse_machine(item).to_registration() for item in args.machine] or config.machine_registration()
    if not machines:
        raise SystemExit("register requires --machine or machines in --config")
    client = _client(args, config)
    return client.register_node(
        node_id=args.node_id or config.node_id,
        name=args.name or config.name,
        api_addr=args.api_addr or config.api_addr,
        capabilities={
            "vm_only_execution": True,
            "executes_samples_on_host": False,
            "machinery_backend": config.machinery_backend,
        },
        machines=machines,
    )


def _lease(args: argparse.Namespace) -> dict[str, Any]:
    config = _build_config(args)
    node_id = args.node_id or config.node_id
    lease_seconds = args.lease_seconds or config.lease_seconds
    client = _client(args, config)
    lease = client.lease_task(node_id, lease_seconds)
    plan_dir = args.plan_dir or config.plan_dir
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / f"task-{lease['task']['id']}-guest-plan.json"
    plan_path.write_text(json.dumps(lease["guest_plan"], indent=2, sort_keys=True), encoding="utf-8")
    lease["guest_plan_path"] = str(plan_path)
    return lease


def _run_once(args: argparse.Namespace) -> dict[str, Any]:
    runner = _runner(args)
    return runner.run_once(register_first=args.register).to_dict()


def _run_loop(args: argparse.Namespace) -> dict[str, Any]:
    runner = _runner(args)
    results = runner.run_loop(
        interval_seconds=args.interval,
        max_tasks=args.max_tasks,
        register_first=args.register,
    )
    return {"results": [result.to_dict() for result in results]}


def _recover_leases(args: argparse.Namespace) -> list[dict[str, Any]]:
    return _client(args, _build_config(args)).recover_expired_leases()


def _status(args: argparse.Namespace) -> dict[str, Any]:
    return _client(args, _build_config(args)).update_task_status(
        args.task_id,
        args.status,
        args.lease_token,
        args.error_code,
        args.error_message,
    )


def _events(args: argparse.Namespace) -> dict[str, Any]:
    return _client(args, _build_config(args)).upload_events(
        args.task_id,
        args.lease_token,
        args.source,
        _read_events(args.file),
    )


def _artifact(args: argparse.Namespace) -> dict[str, Any]:
    return _client(args, _build_config(args)).upload_artifact(
        args.task_id,
        args.lease_token,
        args.file,
        args.type,
        args.name,
    )


def _result_status(args: argparse.Namespace) -> dict[str, Any]:
    try:
        detail = json.loads(args.detail_json)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--detail-json is not valid JSON: {exc}") from exc
    return _client(args, _build_config(args)).result_status(
        args.task_id,
        args.lease_token,
        args.status,
        args.message,
        detail,
    )


def _runner(args: argparse.Namespace) -> NodeAgentRunner:
    config = _build_config(args)
    return NodeAgentRunner(config, _client(args, config))


def _build_config(args: argparse.Namespace) -> NodeAgentConfig:
    if args.config:
        config = load_node_config(args.config)
    else:
        node_id = getattr(args, "node_id", None) or "node-1"
        config = NodeAgentConfig(
            node_id=node_id,
            name=node_id,
            server_url=args.server or DEFAULT_SERVER,
        )

    updates: dict[str, Any] = {}
    if args.server:
        updates["server_url"] = args.server
    if getattr(args, "node_id", None):
        updates["node_id"] = args.node_id
        updates["name"] = args.node_id if config.name == config.node_id else config.name
    if getattr(args, "lease_seconds", None):
        updates["lease_seconds"] = args.lease_seconds
    if getattr(args, "plan_dir", None):
        updates["plan_dir"] = args.plan_dir
    if getattr(args, "work_dir", None):
        updates["work_dir"] = args.work_dir
    if getattr(args, "machinery", None):
        updates["machinery_backend"] = args.machinery
    return replace(config, **updates) if updates else config


def _client(args: argparse.Namespace, config: NodeAgentConfig) -> SandboxApiClient:
    return SandboxApiClient(args.server or config.server_url or DEFAULT_SERVER)


def _read_events(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8")
    stripped = raw.lstrip()
    if not stripped:
        raise SystemExit("events file is empty")
    if stripped.startswith("["):
        data = json.loads(raw)
        if not isinstance(data, list):
            raise SystemExit("events JSON must be a list")
        return data

    events: list[dict[str, Any]] = []
    for lineno, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise SystemExit(f"JSONL line {lineno} must be an object")
        events.append(item)
    if not events:
        raise SystemExit("events file contains no JSON objects")
    return events


def _parse_machine(value: str) -> MachineConfig:
    parts = value.split(":")
    if len(parts) not in {4, 5}:
        raise SystemExit("--machine must be NAME:PLATFORM:OS_VERSION:ARCH[:IP]")
    name, platform, os_version, arch = parts[:4]
    data: dict[str, Any] = {
        "name": name,
        "platform": platform,
        "os_version": os_version,
        "arch": arch,
        "tags": [],
        "state": "available",
    }
    if len(parts) == 5:
        data["ip"] = parts[4]
    return MachineConfig.from_mapping(data)


if __name__ == "__main__":
    main(sys.argv[1:])
