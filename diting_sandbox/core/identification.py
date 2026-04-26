from __future__ import annotations

import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


WINDOWS_EXTENSIONS = {
    ".exe",
    ".dll",
    ".msi",
    ".ps1",
    ".bat",
    ".cmd",
    ".vbs",
    ".js",
    ".wsf",
    ".hta",
    ".lnk",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
}

LINUX_EXTENSIONS = {
    ".elf",
    ".so",
    ".sh",
    ".bash",
    ".py",
    ".pl",
    ".deb",
    ".rpm",
    ".appimage",
}

MULTI_PLATFORM_EXTENSIONS = {
    ".jar",
    ".pdf",
    ".zip",
    ".7z",
    ".rar",
    ".tar",
    ".gz",
}


@dataclass(frozen=True)
class Identification:
    selected: bool
    file_type: str
    platforms: list[str]
    arch: str | None
    dependencies: list[str]
    ignored: list[str]
    metadata: dict[str, Any] | None = None
    children: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict:
        return {
            "selected": self.selected,
            "file_type": self.file_type,
            "platforms": self.platforms,
            "arch": self.arch,
            "dependencies": self.dependencies,
            "ignored": self.ignored,
            "metadata": self.metadata or {},
            "children": self.children or [],
        }


def identify_file(path: Path, filename: str) -> Identification:
    # Read-only static identification. Do not execute target bytes here.
    with path.open("rb") as fp:
        header = fp.read(4096)

    suffix = Path(filename).suffix.lower()
    dependencies: list[str] = []
    arch: str | None = None

    if header.startswith(b"MZ"):
        metadata = _pe_metadata(header)
        arch = metadata.get("arch")
        return Identification(True, "pe", ["windows"], arch, dependencies, [], metadata)

    if header.startswith(b"\x7fELF"):
        metadata = _elf_metadata(header)
        arch = metadata.get("arch")
        return Identification(True, "elf", ["linux"], arch, dependencies, [], metadata)

    if header.startswith(b"#!"):
        platform = "windows" if suffix in {".ps1", ".bat", ".cmd"} else "linux"
        return Identification(True, "script", [platform], arch, dependencies, [], _script_metadata(header))

    if suffix == ".zip":
        return Identification(
            True,
            "zip",
            ["windows", "linux"],
            arch,
            dependencies,
            [],
            {"archive": {"kind": "zip", "inspection": "metadata_only"}},
            _zip_children(path),
        )

    if suffix in {".tar", ".gz"} and tarfile.is_tarfile(path):
        return Identification(
            True,
            "tar",
            ["windows", "linux"],
            arch,
            dependencies,
            [],
            {"archive": {"kind": "tar", "inspection": "metadata_only"}},
            _tar_children(path),
        )

    if suffix in WINDOWS_EXTENSIONS:
        if suffix in {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}:
            dependencies.append("office")
        return Identification(True, suffix.lstrip("."), ["windows"], arch, dependencies, [])

    if suffix in LINUX_EXTENSIONS:
        return Identification(True, suffix.lstrip("."), ["linux"], arch, dependencies, [])

    if suffix in MULTI_PLATFORM_EXTENSIONS:
        return Identification(True, suffix.lstrip("."), ["windows", "linux"], arch, dependencies, [])

    return Identification(True, "unknown", ["windows", "linux"], arch, dependencies, [])


def _elf_arch(header: bytes) -> str | None:
    if len(header) < 20:
        return None
    machine = int.from_bytes(header[18:20], "little")
    return {
        0x03: "x86",
        0x3E: "amd64",
        0x28: "arm",
        0xB7: "arm64",
    }.get(machine)


def _pe_metadata(header: bytes) -> dict[str, Any]:
    metadata: dict[str, Any] = {"format": "pe"}
    if len(header) < 0x40:
        return metadata
    pe_offset = int.from_bytes(header[0x3C:0x40], "little", signed=False)
    metadata["pe_offset"] = pe_offset
    if pe_offset <= 0 or pe_offset + 24 > len(header):
        metadata["valid_pe_header"] = False
        return metadata
    if header[pe_offset : pe_offset + 4] != b"PE\x00\x00":
        metadata["valid_pe_header"] = False
        return metadata

    machine = int.from_bytes(header[pe_offset + 4 : pe_offset + 6], "little")
    sections = int.from_bytes(header[pe_offset + 6 : pe_offset + 8], "little")
    optional_header_size = int.from_bytes(header[pe_offset + 20 : pe_offset + 22], "little")
    optional_offset = pe_offset + 24
    optional_magic = None
    if optional_offset + 2 <= len(header):
        optional_magic = int.from_bytes(header[optional_offset : optional_offset + 2], "little")
    metadata.update(
        {
            "valid_pe_header": True,
            "machine": hex(machine),
            "arch": _pe_arch(machine, optional_magic),
            "number_of_sections": sections,
            "optional_header_size": optional_header_size,
            "optional_header_magic": hex(optional_magic) if optional_magic is not None else None,
        }
    )
    return metadata


def _pe_arch(machine: int, optional_magic: int | None) -> str | None:
    arch = {
        0x014C: "x86",
        0x8664: "amd64",
        0x01C0: "arm",
        0xAA64: "arm64",
    }.get(machine)
    if arch:
        return arch
    if optional_magic == 0x20B:
        return "amd64"
    if optional_magic == 0x10B:
        return "x86"
    return None


def _elf_metadata(header: bytes) -> dict[str, Any]:
    metadata: dict[str, Any] = {"format": "elf"}
    if len(header) < 20:
        return metadata
    elf_class = header[4]
    endian_marker = header[5]
    byteorder = "big" if endian_marker == 2 else "little"
    machine = int.from_bytes(header[18:20], byteorder)
    entry = None
    if elf_class == 1 and len(header) >= 28:
        entry = int.from_bytes(header[24:28], byteorder)
    elif elf_class == 2 and len(header) >= 32:
        entry = int.from_bytes(header[24:32], byteorder)
    metadata.update(
        {
            "class": {1: "ELF32", 2: "ELF64"}.get(elf_class, "unknown"),
            "byteorder": byteorder,
            "machine": hex(machine),
            "arch": _elf_arch(header),
            "entrypoint": hex(entry) if entry is not None else None,
        }
    )
    return metadata


def _script_metadata(header: bytes) -> dict[str, Any]:
    first_line = header.splitlines()[0].decode("utf-8", errors="replace") if header else ""
    return {"script": {"shebang": first_line[:200]}}


def _zip_children(path: Path, limit: int = 200) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist()[:limit]:
                children.append(
                    {
                        "name": info.filename,
                        "size": info.file_size,
                        "compressed_size": info.compress_size,
                        "platforms": _platforms_for_name(info.filename),
                        "directory": info.is_dir(),
                    }
                )
    except (OSError, zipfile.BadZipFile):
        return []
    return children


def _tar_children(path: Path, limit: int = 200) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = []
    try:
        with tarfile.open(path) as archive:
            for member in archive.getmembers()[:limit]:
                children.append(
                    {
                        "name": member.name,
                        "size": member.size,
                        "platforms": _platforms_for_name(member.name),
                        "directory": member.isdir(),
                    }
                )
    except (OSError, tarfile.TarError):
        return []
    return children


def _platforms_for_name(name: str) -> list[str]:
    suffix = Path(name).suffix.lower()
    if suffix in WINDOWS_EXTENSIONS:
        return ["windows"]
    if suffix in LINUX_EXTENSIONS:
        return ["linux"]
    if suffix in MULTI_PLATFORM_EXTENSIONS:
        return ["windows", "linux"]
    return []
