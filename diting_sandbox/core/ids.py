from __future__ import annotations

import secrets
from datetime import UTC, datetime


def new_analysis_id() -> str:
    today = datetime.now(UTC).strftime("%Y%m%d")
    return f"{today}-{secrets.token_hex(4).upper()}"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_object_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"

