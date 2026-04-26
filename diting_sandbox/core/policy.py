from __future__ import annotations


VM_ONLY_EXECUTION_POLICY = (
    "Submitted files and extracted children must only be executed inside "
    "Windows/Linux analysis virtual machines. Server, processing worker, "
    "node agent, host shell, containers, and CI may only read, transfer, "
    "unpack, and statically parse bytes."
)


class HostExecutionBlocked(RuntimeError):
    """Raised when code attempts to execute an untrusted target on the host."""


def assert_vm_execution_context(is_guest_vm: bool) -> None:
    if not is_guest_vm:
        raise HostExecutionBlocked(VM_ONLY_EXECUTION_POLICY)

