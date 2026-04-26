from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_path: Path
    storage_dir: Path
    default_timeout: int = 120
    default_priority: int = 1
    default_route: str = "drop"
    max_file_size: int = 4 * 1024 * 1024 * 1024


def load_settings() -> Settings:
    root = Path(os.environ.get("DITING_DATA_DIR", ".diting-data")).resolve()
    return Settings(
        data_dir=root,
        database_path=root / "metadata.sqlite3",
        storage_dir=root / "storage",
        default_timeout=int(os.environ.get("DITING_DEFAULT_TIMEOUT", "120")),
        default_priority=int(os.environ.get("DITING_DEFAULT_PRIORITY", "1")),
        default_route=os.environ.get("DITING_DEFAULT_ROUTE", "drop"),
        max_file_size=int(os.environ.get("DITING_MAX_FILE_SIZE", str(4 * 1024 * 1024 * 1024))),
    )

