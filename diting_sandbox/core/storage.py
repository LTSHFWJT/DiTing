from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Any


@dataclass(frozen=True)
class StoredSample:
    filename: str
    size: int
    md5: str
    sha1: str
    sha256: str
    sha512: str
    storage_key: str
    path: Path


@dataclass(frozen=True)
class StoredArtifact:
    id: str
    artifact_type: str
    name: str
    size: int
    sha256: str
    storage_key: str
    path: Path


class LocalObjectStorage:
    def __init__(self, root: Path, max_file_size: int):
        self.root = root
        self.max_file_size = max_file_size
        self.samples_root = root / "samples"
        self.analyses_root = root / "analyses"
        self.tmp_root = root / "tmp"

    def initialize(self) -> None:
        for path in (self.samples_root, self.analyses_root, self.tmp_root):
            path.mkdir(parents=True, exist_ok=True)

    def ingest_sample(self, stream: BinaryIO, filename: str) -> StoredSample:
        self.initialize()
        md5 = hashlib.md5()
        sha1 = hashlib.sha1()
        sha256 = hashlib.sha256()
        sha512 = hashlib.sha512()
        size = 0

        fd, tmp_name = tempfile.mkstemp(prefix="sample-", dir=self.tmp_root)
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_file_size:
                        raise ValueError(f"sample exceeds max_file_size={self.max_file_size}")
                    md5.update(chunk)
                    sha1.update(chunk)
                    sha256.update(chunk)
                    sha512.update(chunk)
                    out.write(chunk)

            sha256_hex = sha256.hexdigest()
            storage_key = f"samples/sha256/{sha256_hex[:2]}/{sha256_hex[2:4]}/{sha256_hex}"
            final_path = self.root / storage_key
            final_path.parent.mkdir(parents=True, exist_ok=True)
            if final_path.exists():
                tmp_path.unlink(missing_ok=True)
            else:
                shutil.move(str(tmp_path), final_path)

            return StoredSample(
                filename=filename,
                size=size,
                md5=md5.hexdigest(),
                sha1=sha1.hexdigest(),
                sha256=sha256_hex,
                sha512=sha512.hexdigest(),
                storage_key=storage_key,
                path=final_path,
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    def analysis_dir(self, analysis_id: str) -> Path:
        day = analysis_id.split("-", 1)[0]
        return self.analyses_root / day / analysis_id

    def task_dir(self, analysis_id: str, task_id: int) -> Path:
        return self.analysis_dir(analysis_id) / "tasks" / str(task_id)

    def write_analysis_json(self, analysis_id: str, name: str, data: dict[str, Any]) -> str:
        analysis_dir = self.analysis_dir(analysis_id)
        analysis_dir.mkdir(parents=True, exist_ok=True)
        rel_key = f"analyses/{analysis_id[:8]}/{analysis_id}/{name}"
        self._write_json_atomic(analysis_dir / name, data)
        return rel_key

    def write_task_json(self, analysis_id: str, task_id: int, name: str, data: dict[str, Any]) -> str:
        task_dir = self.task_dir(analysis_id, task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        rel_key = f"analyses/{analysis_id[:8]}/{analysis_id}/tasks/{task_id}/{name}"
        self._write_json_atomic(task_dir / name, data)
        return rel_key

    def append_task_jsonl(
        self,
        analysis_id: str,
        task_id: int,
        name: str,
        records: list[dict[str, Any]],
    ) -> tuple[str, int, str]:
        safe_name = Path(name).name
        if not safe_name.endswith(".jsonl"):
            raise ValueError("JSONL task log name must end with .jsonl")
        task_dir = self.task_dir(analysis_id, task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / safe_name

        encoded_lines = [
            json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            for record in records
        ]
        current_size = path.stat().st_size if path.exists() else 0
        new_size = current_size + sum(len(line) for line in encoded_lines)
        if new_size > self.max_file_size:
            raise ValueError(f"task log exceeds max_file_size={self.max_file_size}")

        with path.open("ab") as fp:
            for line in encoded_lines:
                fp.write(line)

        rel_key = str(path.relative_to(self.root)).replace("\\", "/")
        return rel_key, new_size, self._sha256_file(path)

    def read_task_jsonl(self, analysis_id: str, task_id: int, name: str) -> list[dict[str, Any]]:
        path = self.task_dir(analysis_id, task_id) / Path(name).name
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    records.append({"event": "log.decode_error", "raw": line})
        return records

    def read_analysis_json(self, analysis_id: str, name: str) -> dict[str, Any] | None:
        path = self.analysis_dir(analysis_id) / name
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def sample_path(self, storage_key: str) -> Path:
        return self._path_from_storage_key(storage_key)

    def artifact_path(self, storage_key: str) -> Path:
        return self._path_from_storage_key(storage_key)

    def store_artifact(
        self,
        analysis_id: str,
        task_id: int,
        artifact_id: str,
        artifact_type: str,
        name: str,
        data: bytes,
    ) -> StoredArtifact:
        if len(data) > self.max_file_size:
            raise ValueError(f"artifact exceeds max_file_size={self.max_file_size}")

        safe_name = Path(name).name or "artifact.bin"
        artifact_dir = self.task_dir(analysis_id, task_id) / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_dir / f"{artifact_id}-{safe_name}"
        path.write_bytes(data)
        rel_key = str(path.relative_to(self.root)).replace("\\", "/")
        return StoredArtifact(
            id=artifact_id,
            artifact_type=artifact_type,
            name=safe_name,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            storage_key=rel_key,
            path=path,
        )

    @staticmethod
    def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    def _path_from_storage_key(self, storage_key: str) -> Path:
        path = (self.root / storage_key).resolve()
        root = self.root.resolve()
        if root != path and root not in path.parents:
            raise ValueError("storage key escapes storage root")
        return path

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fp:
            while True:
                chunk = fp.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()
