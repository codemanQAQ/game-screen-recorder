from __future__ import annotations

import gzip
import hashlib
import os
import shutil
import struct
import sys
import tempfile
import textwrap
import time
import unittest
import zlib
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import unreal_kraken_helper as kraken_cli
import unreal_pak_keymap as pak_module

from unreal_pak_keymap import (
    KrakenHelperConfig,
    UnrealPakLimits,
    discover_default_input_from_game_directory,
    read_default_input_from_pak,
)


_MAGIC = 0x5A6F12E1
_DEFAULT_CONFIG = (
    "[/Script/Engine.InputSettings]\r\n"
    '+ActionMappings=(ActionName="Jump",bShift=False,bCtrl=False,'
    'bAlt=False,bCmd=False,Key=SpaceBar)\r\n'
).encode("utf-8")


def _fstring(value: str) -> bytes:
    payload = value.encode("utf-8") + b"\0"
    return struct.pack("<i", len(payload)) + payload


@dataclass(frozen=True)
class _BuiltPak:
    data: bytes
    footer_start: int
    index_offset: int
    index_size: int
    fdi_offset: int | None
    payload_offset: int
    header_size: int


def _replace_pak_data(built: _BuiltPak, data: bytes | bytearray) -> _BuiltPak:
    return _BuiltPak(
        data=bytes(data),
        footer_start=built.footer_start,
        index_offset=built.index_offset,
        index_size=built.index_size,
        fdi_offset=built.fdi_offset,
        payload_offset=built.payload_offset,
        header_size=built.header_size,
    )


def _footer_layout(version: int, variant: str) -> tuple[int, int, int, bool]:
    if version == 8 and variant == "v8a":
        return 189, 4, 1, False
    if version == 9:
        return 222, 5, 4, True
    return 221, 5, 4, False


def _full_entry(
    *,
    compression_index_bytes: int,
    compressed_size: int,
    uncompressed_size: int,
    compression_slot: int | None,
    content_hash: bytes,
    blocks: tuple[tuple[int, int], ...],
    block_size: int,
    encrypted: bool = False,
) -> bytes:
    result = struct.pack("<QQQ", 0, compressed_size, uncompressed_size)
    raw_slot = 0 if compression_slot is None else compression_slot + 1
    if compression_index_bytes == 1:
        result += struct.pack("<B", raw_slot)
    else:
        result += struct.pack("<I", raw_slot)
    result += content_hash
    if compression_slot is not None:
        result += struct.pack("<I", len(blocks))
        result += b"".join(struct.pack("<QQ", start, end) for start, end in blocks)
    result += struct.pack("<BI", 1 if encrypted else 0, block_size)
    return result


def _encoded_entry(
    *,
    compressed_size: int,
    uncompressed_size: int,
    compression_slot: int | None,
    block_sizes: tuple[int, ...],
    block_size: int,
    encrypted: bool = False,
) -> bytes:
    if compression_slot is None:
        block_code = 0
    elif block_size % 2048 == 0 and block_size // 2048 < 0x3F:
        block_code = block_size // 2048
    else:
        block_code = 0x3F
    bits = block_code | (len(block_sizes) << 6)
    if encrypted:
        bits |= 1 << 22
    if compression_slot is not None:
        bits |= (compression_slot + 1) << 23
    bits |= (1 << 31) | (1 << 30)
    if compression_slot is not None:
        bits |= 1 << 29
    result = struct.pack("<I", bits)
    if block_code == 0x3F:
        result += struct.pack("<I", block_size)
    result += struct.pack("<II", 0, uncompressed_size)
    if compression_slot is not None:
        result += struct.pack("<I", compressed_size)
    if encrypted or len(block_sizes) != 1:
        result += b"".join(struct.pack("<I", size) for size in block_sizes)
    return result


def _compress_blocks(
    raw: bytes,
    method: str,
    block_size: int,
    *,
    compressed_override: bytes | None = None,
) -> tuple[tuple[bytes, ...], int]:
    if method == "None":
        return (raw,), 0
    if compressed_override is not None:
        return (compressed_override,), len(raw)
    chunks = tuple(raw[index : index + block_size] for index in range(0, len(raw), block_size))
    if method == "Zlib":
        return tuple(zlib.compress(chunk) for chunk in chunks), block_size
    if method == "Gzip":
        return tuple(gzip.compress(chunk, mtime=0) for chunk in chunks), block_size
    if method == "Oodle":
        raise ValueError("Oodle synthetic data requires compressed_override")
    raise ValueError(method)


def _build_pak(
    *,
    version: int = 11,
    variant: str = "v11",
    method: str = "None",
    config: bytes = _DEFAULT_CONFIG,
    block_size: int = 64,
    compressed_override: bytes | None = None,
    directory: str = "Project/Config/",
    file_name: str = "DefaultInput.ini",
    target_reference: int = 0,
    encrypted_index_entry: bool = False,
    block_range_shift: int = 0,
) -> _BuiltPak:
    _footer_size, compression_count, compression_index_bytes, frozen = _footer_layout(
        version, variant
    )
    compressed_blocks, effective_block_size = _compress_blocks(
        config,
        method,
        block_size,
        compressed_override=compressed_override,
    )
    payload = b"".join(compressed_blocks)
    compression_slot = None if method == "None" else 0
    block_count = 0 if compression_slot is None else len(compressed_blocks)
    header_size = 24 + compression_index_bytes + 20 + 1 + 4
    if compression_slot is not None:
        header_size += 4 + 16 * block_count
    block_ranges: list[tuple[int, int]] = []
    cursor = header_size
    for compressed_block in compressed_blocks if compression_slot is not None else ():
        block_ranges.append(
            (
                cursor + block_range_shift,
                cursor + len(compressed_block) + block_range_shift,
            )
        )
        cursor += len(compressed_block)
    content_hash = hashlib.sha1(payload).digest()
    data_header = _full_entry(
        compression_index_bytes=compression_index_bytes,
        compressed_size=len(payload),
        uncompressed_size=len(config),
        compression_slot=compression_slot,
        content_hash=content_hash,
        blocks=tuple(block_ranges),
        block_size=effective_block_size,
        encrypted=encrypted_index_entry,
    )
    assert len(data_header) == header_size
    data_region = data_header + payload
    index_offset = len(data_region)

    fdi_offset: int | None = None
    if version >= 10:
        encoded = _encoded_entry(
            compressed_size=len(payload),
            uncompressed_size=len(config),
            compression_slot=compression_slot,
            block_sizes=tuple(map(len, compressed_blocks)) if compression_slot is not None else (),
            block_size=effective_block_size,
            encrypted=encrypted_index_entry,
        )
        fdi = (
            struct.pack("<I", 1)
            + _fstring(directory)
            + struct.pack("<I", 1)
            + _fstring(file_name)
            + struct.pack("<i", target_reference)
        )

        def make_main(actual_fdi_offset: int) -> bytes:
            return (
                _fstring("../../../")
                + struct.pack("<I", 1)
                + struct.pack("<Q", 12345)
                + struct.pack("<I", 0)
                + struct.pack("<IQQ", 1, actual_fdi_offset, len(fdi))
                + hashlib.sha1(fdi).digest()
                + struct.pack("<I", len(encoded))
                + encoded
                + struct.pack("<I", 0)
            )

        provisional = make_main(0)
        fdi_offset = index_offset + len(provisional)
        main_index = make_main(fdi_offset)
        assert len(main_index) == len(provisional)
        secondary = fdi
    else:
        index_entry = _full_entry(
            compression_index_bytes=compression_index_bytes,
            compressed_size=len(payload),
            uncompressed_size=len(config),
            compression_slot=compression_slot,
            content_hash=content_hash,
            blocks=tuple(block_ranges),
            block_size=effective_block_size,
            encrypted=encrypted_index_entry,
        )
        main_index = (
            _fstring("../../../")
            + struct.pack("<I", 1)
            + _fstring(f"{directory.strip('/')}/{file_name}")
            + index_entry
        )
        secondary = b""

    footer_start = index_offset + len(main_index) + len(secondary)
    footer = b"\0" * 16 + b"\0"
    footer += struct.pack("<IIQQ", _MAGIC, version, index_offset, len(main_index))
    footer += hashlib.sha1(main_index).digest()
    if frozen:
        footer += b"\0"
    names = [method if method != "None" else "Zlib"] + [""] * (compression_count - 1)
    for name in names:
        encoded_name = name.encode("ascii")
        footer += encoded_name + b"\0" * (32 - len(encoded_name))
    expected_footer_size = _footer_layout(version, variant)[0]
    assert len(footer) == expected_footer_size
    return _BuiltPak(
        data=data_region + main_index + secondary + footer,
        footer_start=footer_start,
        index_offset=index_offset,
        index_size=len(main_index),
        fdi_offset=fdi_offset,
        payload_offset=header_size,
        header_size=header_size,
    )


class UnrealPakKeymapTests(unittest.TestCase):
    def _read(self, built: _BuiltPak, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.pak"
            path.write_bytes(built.data)
            return read_default_input_from_pak(path, **kwargs)

    def test_v11_uncompressed_success_is_labeled_packaged_default(self) -> None:
        result = self._read(_build_pak())
        self.assertTrue(result.found, result.diagnostic)
        self.assertTrue(result.recognized_pak)
        self.assertEqual(result.pak_version, 11)
        self.assertEqual(result.compression_method, "None")
        self.assertEqual(result.config_text.encode(), _DEFAULT_CONFIG)
        self.assertEqual(result.config_scope, "game_default")
        self.assertTrue(result.is_default_configuration)
        self.assertFalse(result.is_player_configuration)

    def test_windowed_helper_failure_has_stable_exit_without_stderr(self) -> None:
        arguments = [
            "--input",
            "missing.bin",
            "--output",
            "output.bin",
            "--expected-size",
            "1",
            "--max-input",
            "1",
            "--max-output",
            "1",
            "--max-ratio",
            "1",
        ]
        with patch.object(kraken_cli.sys, "stderr", None), patch.object(
            kraken_cli.sys, "stdout", None
        ):
            self.assertEqual(kraken_cli.main(arguments), 2)

    def test_all_supported_footer_layouts(self) -> None:
        for version, variant in ((8, "v8a"), (8, "v8b"), (9, "v9"), (10, "v10"), (11, "v11")):
            with self.subTest(version=version, variant=variant):
                result = self._read(_build_pak(version=version, variant=variant))
                self.assertTrue(result.found, result.diagnostic)
                self.assertEqual(result.pak_version, version)
                self.assertEqual(result.pak_layout, variant)

    def test_zlib_and_gzip_multiblock(self) -> None:
        for method in ("Zlib", "Gzip"):
            with self.subTest(method=method):
                result = self._read(_build_pak(method=method, block_size=47))
                self.assertTrue(result.found, result.diagnostic)
                self.assertEqual(result.compression_method, method)
                self.assertEqual(result.config_text.encode(), _DEFAULT_CONFIG)

    def test_main_index_hash_mismatch_is_rejected(self) -> None:
        built = _build_pak()
        damaged = bytearray(built.data)
        damaged[built.index_offset] ^= 1
        result = self._read(_replace_pak_data(built, damaged))
        self.assertFalse(result.found)
        self.assertEqual(result.error_code, "index_hash_mismatch")

    def test_secondary_index_hash_mismatch_is_rejected(self) -> None:
        built = _build_pak()
        self.assertIsNotNone(built.fdi_offset)
        damaged = bytearray(built.data)
        damaged[built.fdi_offset or 0] ^= 1
        result = self._read(_replace_pak_data(built, damaged))
        self.assertFalse(result.found)
        self.assertEqual(result.error_code, "secondary_hash_mismatch")

    def test_footer_index_region_out_of_bounds_is_rejected(self) -> None:
        built = _build_pak()
        damaged = bytearray(built.data)
        struct.pack_into("<Q", damaged, built.footer_start + 25, built.footer_start + 1)
        result = self._read(_replace_pak_data(built, damaged))
        self.assertFalse(result.found)
        self.assertEqual(result.error_code, "invalid_region")

    def test_target_reference_must_be_encoded_entry_boundary(self) -> None:
        result = self._read(_build_pak(target_reference=1))
        self.assertFalse(result.found)
        self.assertEqual(result.error_code, "invalid_entry_reference")

    def test_data_header_mismatch_is_rejected(self) -> None:
        built = _build_pak()
        damaged = bytearray(built.data)
        struct.pack_into("<Q", damaged, 8, len(_DEFAULT_CONFIG) + 1)
        result = self._read(_replace_pak_data(built, damaged))
        self.assertFalse(result.found)
        self.assertEqual(result.error_code, "entry_header_mismatch")

    def test_payload_hash_mismatch_is_rejected_before_decompression(self) -> None:
        built = _build_pak(method="Zlib")
        damaged = bytearray(built.data)
        damaged[built.payload_offset] ^= 1
        result = self._read(_replace_pak_data(built, damaged))
        self.assertFalse(result.found)
        self.assertEqual(result.error_code, "entry_hash_mismatch")

    def test_compression_blocks_must_cover_payload_contiguously(self) -> None:
        result = self._read(
            _build_pak(
                version=9,
                variant="v9",
                method="Zlib",
                block_range_shift=1,
            )
        )
        self.assertFalse(result.found)
        self.assertEqual(result.error_code, "invalid_block_ranges")

    def test_authenticated_traversal_path_is_rejected(self) -> None:
        result = self._read(_build_pak(directory="../Config/"))
        self.assertFalse(result.found)
        self.assertEqual(result.error_code, "unsafe_index_path")

    def test_deflate_trailing_data_is_rejected(self) -> None:
        payload = zlib.compress(_DEFAULT_CONFIG) + b"trailing"
        result = self._read(
            _build_pak(
                method="Zlib",
                compressed_override=payload,
                block_size=len(_DEFAULT_CONFIG),
            )
        )
        self.assertFalse(result.found)
        self.assertEqual(result.error_code, "decompression_mismatch")

    def test_encrypted_index_and_entry_are_rejected(self) -> None:
        built = _build_pak()
        encrypted_footer = bytearray(built.data)
        encrypted_footer[built.footer_start + 16] = 1
        footer_result = self._read(_replace_pak_data(built, encrypted_footer))
        self.assertEqual(footer_result.error_code, "encrypted_index")

        entry_result = self._read(_build_pak(method="Zlib", encrypted_index_entry=True))
        self.assertEqual(entry_result.error_code, "encrypted_entry")

    def test_compression_ratio_limit_is_rejected(self) -> None:
        large = b"[/Script/Engine.InputSettings]\n" + b"A" * 20_000
        result = self._read(
            _build_pak(method="Zlib", config=large, block_size=len(large)),
            limits=UnrealPakLimits(max_compression_ratio=1),
        )
        self.assertEqual(result.error_code, "compression_ratio_limit")

    def test_oodle_uses_explicit_helper_subprocess(self) -> None:
        compressed = b"\x8c\x06FAKE"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pak_path = root / "oodle.pak"
            pak_path.write_bytes(
                _build_pak(method="Oodle", compressed_override=compressed).data
            )
            helper_path = root / "fake_helper.py"
            helper_path.write_text(
                textwrap.dedent(
                    f"""
                    import pathlib, sys
                    arguments = sys.argv[1:]
                    assert arguments[0] == '--kraken-helper'
                    output = pathlib.Path(arguments[arguments.index('--output') + 1])
                    output.write_bytes({_DEFAULT_CONFIG!r})
                    """
                ),
                encoding="utf-8",
            )
            result = read_default_input_from_pak(
                pak_path,
                kraken_helper=KrakenHelperConfig(
                    (sys.executable, "-I", str(helper_path), "--kraken-helper"),
                    timeout_seconds=2,
                ),
            )
        self.assertTrue(result.found, result.diagnostic)
        self.assertEqual(result.compression_method, "Oodle")
        self.assertNotIn("kraken_decompressor", sys.modules)

    def test_non_kraken_oodle_stream_is_rejected_without_helper(self) -> None:
        result = self._read(
            _build_pak(method="Oodle", compressed_override=b"\x8c\x0aFAKE")
        )
        self.assertEqual(result.error_code, "unsupported_oodle_codec")

    def test_oodle_helper_timeout_is_controlled(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = Path(directory)
            pak_path = root / "oodle.pak"
            pak_path.write_bytes(
                _build_pak(method="Oodle", compressed_override=b"\x8c\x06FAKE").data
            )
            helper_path = root / "slow_helper.py"
            helper_path.write_text("import time\ntime.sleep(2)\n", encoding="utf-8")
            original_create_job = pak_module._create_windows_kill_job
            job_results: list[bool] = []

            def traced_create_job(process):
                job = original_create_job(process)
                job_results.append(job is not None)
                return job

            errors: list[str] = []
            with patch.object(
                pak_module,
                "_create_windows_kill_job",
                side_effect=traced_create_job,
            ):
                for _ in range(5):
                    result = read_default_input_from_pak(
                        pak_path,
                        kraken_helper=KrakenHelperConfig(
                            (sys.executable, "-I", str(helper_path)),
                            timeout_seconds=0.05,
                        ),
                    )
                    errors.append(result.error_code)
        self.assertEqual(errors, ["helper_timeout"] * 5)
        self.assertEqual(len(job_results), 5)
        if os.name == "nt":
            self.assertTrue(all(job_results))

    def test_oodle_timeout_uses_job_termination_hook(self) -> None:
        class RecordingJob:
            def __init__(self, process) -> None:
                self.process = process
                self.terminate_calls = 0
                self.close_calls = 0

            def terminate(self) -> None:
                self.terminate_calls += 1
                self.process.kill()

            def close(self) -> None:
                self.close_calls += 1

        jobs: list[RecordingJob] = []

        def create_job(process):
            job = RecordingJob(process)
            jobs.append(job)
            return job

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = Path(directory)
            pak_path = root / "oodle.pak"
            pak_path.write_bytes(
                _build_pak(method="Oodle", compressed_override=b"\x8c\x06FAKE").data
            )
            helper_path = root / "slow_helper.py"
            helper_path.write_text("import time\ntime.sleep(2)\n", encoding="utf-8")
            with patch.object(
                pak_module,
                "_create_windows_kill_job",
                side_effect=create_job,
            ):
                result = read_default_input_from_pak(
                    pak_path,
                    kraken_helper=KrakenHelperConfig(
                        (sys.executable, "-I", str(helper_path)),
                        timeout_seconds=0.05,
                    ),
                )
        self.assertEqual(result.error_code, "helper_timeout")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].terminate_calls, 1)
        self.assertEqual(jobs[0].close_calls, 1)

    def test_oodle_timeout_falls_back_to_direct_kill_without_job(self) -> None:
        original_kill = pak_module.subprocess.Popen.kill

        def recorded_kill(process) -> None:
            original_kill(process)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = Path(directory)
            pak_path = root / "oodle.pak"
            pak_path.write_bytes(
                _build_pak(method="Oodle", compressed_override=b"\x8c\x06FAKE").data
            )
            helper_path = root / "slow_helper.py"
            helper_path.write_text("import time\ntime.sleep(2)\n", encoding="utf-8")
            with (
                patch.object(
                    pak_module,
                    "_create_windows_kill_job",
                    return_value=None,
                ),
                patch.object(
                    pak_module.subprocess.Popen,
                    "kill",
                    autospec=True,
                    side_effect=recorded_kill,
                ) as kill,
            ):
                result = read_default_input_from_pak(
                    pak_path,
                    kraken_helper=KrakenHelperConfig(
                        (sys.executable, "-I", str(helper_path)),
                        timeout_seconds=0.05,
                    ),
                )
        self.assertEqual(result.error_code, "helper_timeout")
        kill.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "Windows Job Object test")
    def test_windows_job_timeout_terminates_helper_child_tree(self) -> None:
        def process_is_active(process_id: int) -> bool:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = (
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            )
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = (
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.DWORD),
            )
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(0x1000, False, process_id)
            if not handle:
                return False
            try:
                exit_code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == 259
            finally:
                kernel32.CloseHandle(handle)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = Path(directory)
            pak_path = root / "oodle.pak"
            pak_path.write_bytes(
                _build_pak(method="Oodle", compressed_override=b"\x8c\x06FAKE").data
            )
            child_pid_path = root / "child.pid"
            helper_path = root / "tree_helper.py"
            helper_path.write_text(
                textwrap.dedent(
                    """
                    import pathlib, subprocess, sys, time
                    child = subprocess.Popen(
                        [sys.executable, '-c', 'import time; time.sleep(30)'],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    pathlib.Path(sys.argv[1]).write_text(str(child.pid))
                    time.sleep(30)
                    """
                ),
                encoding="utf-8",
            )
            result = read_default_input_from_pak(
                pak_path,
                kraken_helper=KrakenHelperConfig(
                    (sys.executable, "-I", str(helper_path), str(child_pid_path)),
                    timeout_seconds=1.0,
                ),
            )
            self.assertTrue(child_pid_path.is_file())
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 2.0
            while process_is_active(child_pid) and time.monotonic() < deadline:
                time.sleep(0.02)
            child_active = process_is_active(child_pid)
        self.assertEqual(result.error_code, "helper_timeout")
        self.assertFalse(child_active)

    @unittest.skipUnless(os.name == "nt", "Windows suspended-process test")
    def test_resume_failure_kills_suspended_helper(self) -> None:
        original_kill = pak_module.subprocess.Popen.kill

        def recorded_kill(process) -> None:
            original_kill(process)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = Path(directory)
            pak_path = root / "oodle.pak"
            pak_path.write_bytes(
                _build_pak(method="Oodle", compressed_override=b"\x8c\x06FAKE").data
            )
            helper_path = root / "unused_helper.py"
            helper_path.write_text("raise SystemExit(0)\n", encoding="utf-8")
            with (
                patch.object(
                    pak_module,
                    "_create_windows_kill_job",
                    return_value=None,
                ),
                patch.object(
                    pak_module,
                    "_resume_windows_suspended_process",
                    return_value=False,
                ),
                patch.object(
                    pak_module.subprocess.Popen,
                    "kill",
                    autospec=True,
                    side_effect=recorded_kill,
                ) as kill,
            ):
                result = read_default_input_from_pak(
                    pak_path,
                    kraken_helper=KrakenHelperConfig(
                        (sys.executable, "-I", str(helper_path)),
                        timeout_seconds=1,
                    ),
                )
        self.assertEqual(result.error_code, "helper_launch_failed")
        kill.assert_called_once()

    def test_helper_cleanup_lock_does_not_override_timeout(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = Path(directory)
            pak_path = root / "oodle.pak"
            pak_path.write_bytes(
                _build_pak(method="Oodle", compressed_override=b"\x8c\x06FAKE").data
            )
            helper_path = root / "slow_helper.py"
            helper_path.write_text("import time\ntime.sleep(2)\n", encoding="utf-8")
            helper_root = root / "locked-helper-root"
            helper_root.mkdir()

            class LockedTemporaryDirectory:
                name = str(helper_root)

                def __init__(self) -> None:
                    self.cleanup_calls = 0

                def cleanup(self) -> None:
                    self.cleanup_calls += 1
                    raise PermissionError("simulated Windows file lock")

            locked_directory = LockedTemporaryDirectory()
            with patch.object(
                pak_module.tempfile,
                "TemporaryDirectory",
                return_value=locked_directory,
            ) as temporary_directory:
                result = read_default_input_from_pak(
                    pak_path,
                    kraken_helper=KrakenHelperConfig(
                        (sys.executable, "-I", str(helper_path)),
                        timeout_seconds=0.05,
                    ),
                )
            shutil.rmtree(helper_root, ignore_errors=True)
        self.assertEqual(result.error_code, "helper_timeout")
        temporary_directory.assert_called_once_with(
            prefix="unreal_kraken_",
            ignore_cleanup_errors=True,
        )
        self.assertEqual(locked_directory.cleanup_calls, 1)

    def test_directory_scan_rejects_differing_defaults(self) -> None:
        second_config = _DEFAULT_CONFIG.replace(b"Jump", b"Crouch")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "base.pak").write_bytes(_build_pak(config=_DEFAULT_CONFIG).data)
            (root / "patch.pak").write_bytes(_build_pak(config=second_config).data)
            result = discover_default_input_from_game_directory(root)
        self.assertFalse(result.found)
        self.assertEqual(result.error_code, "ambiguous_defaults")
        self.assertEqual(result.scanned_paks, 2)

    def test_directory_scan_truncation_never_returns_partial_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.pak").write_bytes(_build_pak().data)
            (root / "b.pak").write_bytes(_build_pak().data)
            result = discover_default_input_from_game_directory(
                root,
                limits=UnrealPakLimits(max_directory_entries=1),
            )
        self.assertFalse(result.found)
        self.assertTrue(result.truncated)
        self.assertEqual(result.error_code, "scan_truncated")

    def test_directory_depth_limit_is_reported_as_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "one" / "two"
            nested.mkdir(parents=True)
            (nested / "deep.pak").write_bytes(_build_pak().data)
            result = discover_default_input_from_game_directory(
                root,
                limits=UnrealPakLimits(max_scan_depth=1),
            )
        self.assertFalse(result.found)
        self.assertTrue(result.truncated)
        self.assertEqual(result.error_code, "scan_truncated")


if __name__ == "__main__":
    unittest.main()
