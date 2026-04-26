from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GuestAgentTask:
    analysis_id: str
    task_id: int
    sample_path: str
    analyzer_path: str
    timeout: int
    resultserver_url: str

