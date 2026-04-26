from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utcnow().isoformat()


def iso_after(seconds: int) -> str:
    return (utcnow() + timedelta(seconds=seconds)).isoformat()

