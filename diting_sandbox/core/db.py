from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .timeutil import iso_now


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def upsert_sample(self, sample: Any, mime: str | None = None) -> int:
        now = iso_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO samples
                    (sha256, sha1, md5, sha512, size, filename, mime, storage_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sha256) DO UPDATE SET
                    filename=excluded.filename,
                    mime=COALESCE(excluded.mime, samples.mime)
                """,
                (
                    sample.sha256,
                    sample.sha1,
                    sample.md5,
                    sample.sha512,
                    sample.size,
                    sample.filename,
                    mime,
                    sample.storage_key,
                    now,
                ),
            )
            row = conn.execute("SELECT id FROM samples WHERE sha256 = ?", (sample.sha256,)).fetchone()
            return int(row["id"])

    def create_analysis(
        self,
        analysis_id: str,
        sample_id: int,
        settings: dict[str, Any],
        identification: dict[str, Any],
        submitter: str | None = None,
    ) -> None:
        now = iso_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO analyses
                    (id, sample_id, status, submitter, settings_json, identification_json, created_at, updated_at)
                VALUES (?, ?, 'queued', ?, ?, ?, ?, ?)
                """,
                (
                    analysis_id,
                    sample_id,
                    submitter,
                    json.dumps(settings, sort_keys=True),
                    json.dumps(identification, sort_keys=True),
                    now,
                    now,
                ),
            )

    def create_tasks(self, analysis_id: str, tasks: Iterable[dict[str, Any]]) -> list[int]:
        now = iso_now()
        task_ids: list[int] = []
        with self.connect() as conn:
            for task in tasks:
                cur = conn.execute(
                    """
                    INSERT INTO tasks
                        (analysis_id, platform, os_version, arch, status, timeout, route, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                    """,
                    (
                        analysis_id,
                        task["platform"],
                        task.get("os_version"),
                        task.get("arch"),
                        task["timeout"],
                        task["route"],
                        now,
                        now,
                    ),
                )
                task_ids.append(int(cur.lastrowid))
        return task_ids

    def get_analysis(self, analysis_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT a.*, s.sha256, s.filename, s.size, s.mime, s.storage_key AS sample_storage_key
                FROM analyses a
                JOIN samples s ON s.id = a.sample_id
                WHERE a.id = ?
                """,
                (analysis_id,),
            ).fetchone()
        return row_to_dict(row)

    def list_analyses(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT a.*, s.sha256, s.filename, s.size, s.mime, s.storage_key AS sample_storage_key
                FROM analyses a
                JOIN samples s ON s.id = a.sample_id
                ORDER BY a.created_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def list_tasks(self, analysis_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE analysis_id = ? ORDER BY id",
                (analysis_id,),
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return row_to_dict(row)

    def register_node(self, node_id: str, name: str, api_addr: str | None, capabilities: dict[str, Any]) -> None:
        now = iso_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO nodes (id, name, api_addr, status, capabilities_json, last_seen_at, created_at, updated_at)
                VALUES (?, ?, ?, 'online', ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    api_addr=excluded.api_addr,
                    status='online',
                    capabilities_json=excluded.capabilities_json,
                    last_seen_at=excluded.last_seen_at,
                    updated_at=excluded.updated_at
                """,
                (node_id, name, api_addr, json.dumps(capabilities, sort_keys=True), now, now, now),
            )

    def upsert_machine(self, machine: dict[str, Any]) -> None:
        now = iso_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO machines
                    (id, node_id, name, platform, os_version, arch, ip, tags_json, state, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    platform=excluded.platform,
                    os_version=excluded.os_version,
                    arch=excluded.arch,
                    ip=excluded.ip,
                    tags_json=excluded.tags_json,
                    state=excluded.state,
                    updated_at=excluded.updated_at
                """,
                (
                    machine["id"],
                    machine["node_id"],
                    machine["name"],
                    machine["platform"],
                    machine.get("os_version"),
                    machine.get("arch"),
                    machine.get("ip"),
                    json.dumps(machine.get("tags", []), sort_keys=True),
                    machine.get("state", "available"),
                    now,
                    now,
                ),
            )

    def list_nodes(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM nodes ORDER BY name").fetchall()
        return [row_to_dict(row) for row in rows]

    def list_machines(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM machines ORDER BY node_id, name").fetchall()
        return [row_to_dict(row) for row in rows]

    def lease_task(self, node_id: str, lease_token: str, lease_expires_at: str) -> dict[str, Any] | None:
        now = iso_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT t.*, m.id AS candidate_machine_id
                FROM tasks t
                JOIN machines m
                    ON m.platform = t.platform
                   AND m.node_id = ?
                   AND m.state = 'available'
                WHERE t.status = 'queued'
                ORDER BY t.id
                LIMIT 1
                """,
                (node_id,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None

            task_id = int(row["id"])
            machine_id = row["candidate_machine_id"]
            conn.execute(
                """
                UPDATE tasks
                SET status='leasing',
                    node_id=?,
                    machine_id=?,
                    lease_token=?,
                    lease_expires_at=?,
                    updated_at=?
                WHERE id=? AND status='queued'
                """,
                (node_id, machine_id, lease_token, lease_expires_at, now, task_id),
            )
            conn.execute(
                "UPDATE machines SET state='leased', updated_at=? WHERE id=?",
                (now, machine_id),
            )
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            conn.execute("COMMIT")
        return row_to_dict(task)

    def requeue_expired_leases(self, now: str) -> list[dict[str, Any]]:
        expired_statuses = ("leasing", "starting_vm", "preparing_guest", "running", "collecting")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT *
                FROM tasks
                WHERE lease_expires_at IS NOT NULL
                  AND lease_expires_at < ?
                  AND status IN (?, ?, ?, ?, ?)
                ORDER BY id
                """,
                (now, *expired_statuses),
            ).fetchall()
            expired = [row_to_dict(row) for row in rows]
            for task in expired:
                machine_id = task["machine_id"]
                if machine_id:
                    conn.execute(
                        "UPDATE machines SET state='available', updated_at=? WHERE id=?",
                        (now, machine_id),
                    )
                conn.execute(
                    """
                    UPDATE tasks
                    SET status='queued',
                        node_id=NULL,
                        machine_id=NULL,
                        lease_token=NULL,
                        lease_expires_at=NULL,
                        error_code='LEASE_EXPIRED',
                        error_message='task lease expired and was requeued',
                        updated_at=?
                    WHERE id=?
                    """,
                    (now, task["id"]),
                )
            conn.execute("COMMIT")
        for task in expired:
            self._refresh_analysis_status(task["analysis_id"])
        return expired

    def update_task_status(
        self,
        task_id: int,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any] | None:
        now = iso_now()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE tasks
                SET status=?, error_code=?, error_message=?, updated_at=?
                WHERE id=?
                """,
                (status, error_code, error_message, now, task_id),
            )
            machine_id = row["machine_id"]
            if machine_id and status in {"finished", "failed", "cancelled"}:
                conn.execute(
                    "UPDATE machines SET state='available', updated_at=? WHERE id=?",
                    (now, machine_id),
                )
            updated = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        self._refresh_analysis_status(row["analysis_id"])
        return row_to_dict(updated)

    def cancel_analysis(self, analysis_id: str) -> list[dict[str, Any]]:
        now = iso_now()
        terminal = ("finished", "failed", "cancelled")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT * FROM tasks WHERE analysis_id=? AND status NOT IN (?, ?, ?) ORDER BY id",
                (analysis_id, *terminal),
            ).fetchall()
            cancelled = [row_to_dict(row) for row in rows]
            for task in cancelled:
                machine_id = task["machine_id"]
                if machine_id:
                    conn.execute(
                        "UPDATE machines SET state='available', updated_at=? WHERE id=?",
                        (now, machine_id),
                    )
                conn.execute(
                    """
                    UPDATE tasks
                    SET status='cancelled',
                        lease_token=NULL,
                        lease_expires_at=NULL,
                        error_code='CANCELLED',
                        error_message='analysis was cancelled',
                        updated_at=?
                    WHERE id=?
                    """,
                    (now, task["id"]),
                )
            conn.execute(
                "UPDATE analyses SET status='cancelled', updated_at=? WHERE id=?",
                (now, analysis_id),
            )
            conn.execute("COMMIT")
        return cancelled

    def list_artifacts(self, analysis_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE analysis_id=? ORDER BY created_at",
                (analysis_id,),
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        return row_to_dict(row)

    def get_task_artifact(self, task_id: int, artifact_type: str, name: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM artifacts
                WHERE task_id=? AND type=? AND name=?
                LIMIT 1
                """,
                (task_id, artifact_type, name),
            ).fetchone()
        return row_to_dict(row)

    def add_artifact(
        self,
        artifact_id: str,
        analysis_id: str,
        task_id: int,
        artifact_type: str,
        name: str,
        storage_key: str,
        size: int,
        sha256: str | None,
    ) -> dict[str, Any]:
        now = iso_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO artifacts
                    (id, analysis_id, task_id, type, name, storage_key, size, sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    analysis_id,
                    task_id,
                    artifact_type,
                    name,
                    storage_key,
                    size,
                    sha256,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        return row_to_dict(row)

    def upsert_task_artifact(
        self,
        artifact_id: str,
        analysis_id: str,
        task_id: int,
        artifact_type: str,
        name: str,
        storage_key: str,
        size: int,
        sha256: str | None,
    ) -> dict[str, Any]:
        now = iso_now()
        existing = self.get_task_artifact(task_id, artifact_type, name)
        with self.connect() as conn:
            if existing:
                conn.execute(
                    """
                    UPDATE artifacts
                    SET storage_key=?, size=?, sha256=?
                    WHERE id=?
                    """,
                    (storage_key, size, sha256, existing["id"]),
                )
                row = conn.execute("SELECT * FROM artifacts WHERE id=?", (existing["id"],)).fetchone()
            else:
                conn.execute(
                    """
                    INSERT INTO artifacts
                        (id, analysis_id, task_id, type, name, storage_key, size, sha256, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        analysis_id,
                        task_id,
                        artifact_type,
                        name,
                        storage_key,
                        size,
                        sha256,
                        now,
                    ),
                )
                row = conn.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        return row_to_dict(row)

    def upsert_report(
        self,
        analysis_id: str,
        report_key: str,
        score: int,
        family: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        now = iso_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO reports (analysis_id, report_key, score, family, tags_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(analysis_id) DO UPDATE SET
                    report_key=excluded.report_key,
                    score=excluded.score,
                    family=excluded.family,
                    tags_json=excluded.tags_json
                """,
                (analysis_id, report_key, score, family, json.dumps(tags or [], sort_keys=True), now),
            )

    def _refresh_analysis_status(self, analysis_id: str) -> None:
        with self.connect() as conn:
            rows = conn.execute("SELECT status FROM tasks WHERE analysis_id=?", (analysis_id,)).fetchall()
            if not rows:
                return
            statuses = {row["status"] for row in rows}
            if statuses <= {"finished"}:
                status = "finished"
            elif statuses <= {"cancelled"}:
                status = "cancelled"
            elif "failed" in statuses and statuses <= {"finished", "failed"}:
                status = "failed"
            elif "running" in statuses or "leasing" in statuses:
                status = "running"
            else:
                status = "queued"
            conn.execute("UPDATE analyses SET status=?, updated_at=? WHERE id=?", (status, iso_now(), analysis_id))


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for key in ("settings_json", "identification_json", "capabilities_json", "tags_json"):
        if key in data and data[key]:
            target_key = key.removesuffix("_json")
            data[target_key] = json.loads(data[key])
            del data[key]
    return data


SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL UNIQUE,
    sha1 TEXT NOT NULL,
    md5 TEXT NOT NULL,
    sha512 TEXT NOT NULL,
    size INTEGER NOT NULL,
    filename TEXT NOT NULL,
    mime TEXT,
    storage_key TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY,
    sample_id INTEGER NOT NULL REFERENCES samples(id),
    status TEXT NOT NULL,
    submitter TEXT,
    settings_json TEXT NOT NULL,
    identification_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id TEXT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    os_version TEXT,
    arch TEXT,
    status TEXT NOT NULL,
    node_id TEXT,
    machine_id TEXT,
    timeout INTEGER NOT NULL,
    route TEXT NOT NULL,
    lease_token TEXT,
    lease_expires_at TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    api_addr TEXT,
    status TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS machines (
    id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    platform TEXT NOT NULL,
    os_version TEXT,
    arch TEXT,
    ip TEXT,
    tags_json TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(node_id, name)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    analysis_id TEXT PRIMARY KEY REFERENCES analyses(id) ON DELETE CASCADE,
    report_key TEXT NOT NULL,
    score INTEGER NOT NULL DEFAULT 0,
    family TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_analysis ON tasks(analysis_id);
CREATE INDEX IF NOT EXISTS idx_machines_node_platform ON machines(node_id, platform, state);
"""
