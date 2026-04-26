from __future__ import annotations

import zipfile

from diting_sandbox.core.identification import identify_file

from .helpers import make_work_dir, remove_work_dir


def test_identify_pe_as_windows_without_execution():
    work_dir = make_work_dir("identify-pe")
    try:
        sample = work_dir / "sample.exe"
        sample.write_bytes(b"MZ" + b"\x00" * 128)

        result = identify_file(sample, "sample.exe")
    finally:
        remove_work_dir(work_dir)

    assert result.selected is True
    assert result.file_type == "pe"
    assert result.platforms == ["windows"]


def test_identify_pe_metadata_without_execution():
    work_dir = make_work_dir("identify-pe-metadata")
    try:
        sample = work_dir / "sample.exe"
        data = bytearray(b"MZ" + b"\x00" * 254)
        data[0x3C:0x40] = (0x80).to_bytes(4, "little")
        data[0x80:0x84] = b"PE\x00\x00"
        data[0x84:0x86] = (0x8664).to_bytes(2, "little")
        data[0x86:0x88] = (3).to_bytes(2, "little")
        data[0x94:0x96] = (240).to_bytes(2, "little")
        data[0x98:0x9A] = (0x20B).to_bytes(2, "little")
        sample.write_bytes(bytes(data))

        result = identify_file(sample, "sample.exe")
    finally:
        remove_work_dir(work_dir)

    assert result.file_type == "pe"
    assert result.arch == "amd64"
    assert result.metadata["valid_pe_header"] is True
    assert result.metadata["number_of_sections"] == 3


def test_identify_elf_as_linux_without_execution():
    work_dir = make_work_dir("identify-elf")
    try:
        sample = work_dir / "sample"
        header = bytearray(b"\x7fELF" + b"\x02\x01\x01" + b"\x00" * 32)
        header[18:20] = (0x3E).to_bytes(2, "little")
        sample.write_bytes(bytes(header))

        result = identify_file(sample, "sample")
    finally:
        remove_work_dir(work_dir)

    assert result.selected is True
    assert result.file_type == "elf"
    assert result.platforms == ["linux"]
    assert result.arch == "amd64"


def test_identify_zip_children_without_extraction_or_execution():
    work_dir = make_work_dir("identify-zip")
    try:
        sample = work_dir / "bundle.zip"
        with zipfile.ZipFile(sample, "w") as archive:
            archive.writestr("bin/tool.exe", b"MZ")
            archive.writestr("linux/run.sh", b"#!/bin/sh\ntrue\n")

        result = identify_file(sample, "bundle.zip")
    finally:
        remove_work_dir(work_dir)

    assert result.file_type == "zip"
    assert result.platforms == ["windows", "linux"]
    assert result.metadata["archive"]["inspection"] == "metadata_only"
    children = {child["name"]: child for child in result.children}
    assert children["bin/tool.exe"]["platforms"] == ["windows"]
    assert children["linux/run.sh"]["platforms"] == ["linux"]
