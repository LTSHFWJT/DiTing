from __future__ import annotations

from dataclasses import dataclass

from diting_sandbox.core.policy import HostExecutionBlocked, VM_ONLY_EXECUTION_POLICY


@dataclass(frozen=True)
class GuestExecutionPlan:
    analysis_id: str
    task_id: int
    platform: str
    sample_storage_key: str
    timeout: int
    route: str


def execute_on_host_is_forbidden() -> None:
    raise HostExecutionBlocked(VM_ONLY_EXECUTION_POLICY)

