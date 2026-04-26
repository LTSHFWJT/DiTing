from __future__ import annotations

import shutil
import uuid
from pathlib import Path


def make_work_dir(name: str) -> Path:
    root = Path("test-work") / f"{name}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def remove_work_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)

