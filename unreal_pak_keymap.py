"""Strict, bounded discovery of packaged Unreal ``DefaultInput.ini`` files.

The module performs random-access, read-only inspection of Unreal Pak versions
8 through 11.  It validates the footer, SHA-1 protected index regions, target
entry metadata and compression-block boundaries, then reads only a file whose
internal path ends in ``Config/DefaultInput.ini``.  It never extracts a full
archive and never executes a game binary.

Oodle data is accepted only when the stream identifies itself as Kraken.  The
native decoder is invoked through :mod:`unreal_kraken_helper` in a separate
process; this module deliberately never imports ``kraken_decompressor``.

Results label the content as a packaged game default.  They must not be used as
evidence that a player has launched the game or retained these bindings after
customizing controls.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import BinaryIO, Iterator, Sequence


_PAK_MAGIC = 0x5A6F12E1
_SHA1_BYTES = 20
_RATIO_SLACK_BYTES = 4096
_TARGET_FILE = "defaultinput.ini"
_TARGET_DIRECTORY = "config"


@dataclass(frozen=True)
class UnrealPakLimits:
    """Resource and structural bounds for untrusted Pak files."""

    max_pak_bytes: int = 16 * 1024**4
    max_index_bytes: int = 32 * 1024 * 1024
    max_secondary_index_bytes: int = 32 * 1024 * 1024
    max_string_bytes: int = 64 * 1024
    max_entries: int = 500_000
    max_directories: int = 250_000
    max_nonencoded_entries: int = 32_768
    max_blocks_per_entry: int = 65_535
    max_config_blocks: int = 128
    max_config_bytes: int = 4 * 1024 * 1024
    max_compressed_config_bytes: int = 4 * 1024 * 1024
    max_compressed_block_bytes: int = 4 * 1024 * 1024
    max_compression_ratio: int = 512
    max_paks: int = 64
    max_directory_entries: int = 50_000
    max_scan_depth: int = 16
    scan_timeout_seconds: float = 6.0
    parse_timeout_seconds: float = 20.0
    discovery_timeout_seconds: float = 30.0
    kraken_timeout_seconds: float = 6.0


@dataclass(frozen=True)
class KrakenHelperConfig:
    """Command used to run the isolated native decoder.

    ``command=None`` uses the current source interpreter and the sibling helper
    script.  Frozen applications may pass either a separately built helper EXE
    or the same EXE plus an early helper-mode switch, for example
    ``KrakenHelperConfig((sys.executable,
    "--internal-unreal-kraken-helper"))``.
    """

    command: tuple[str, ...] | None = None
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class UnrealPakDefaultInputResult:
    """Result of one bounded packaged-default discovery operation."""

    config_text: str = ""
    found: bool = False
    recognized_pak: bool = False
    source_paks: tuple[Path, ...] = ()
    internal_path: str = ""
    mount_point: str = ""
    pak_version: int | None = None
    pak_layout: str = ""
    compression_method: str = ""
    content_sha1: str = ""
    diagnostic: str = ""
    error_code: str = ""
    scanned_paks: int = 0
    truncated: bool = False
    config_scope: str = "game_default"
    is_player_configuration: bool = False

    @property
    def source_pak(self) -> Path | None:
        return self.source_paks[0] if self.source_paks else None

    @property
    def recognized_config(self) -> bool:
        return self.found

    @property
    def is_default_configuration(self) -> bool:
        return self.found and not self.is_player_configuration


class _PakError(ValueError):
    def __init__(self, code: str, message: str, *, recognized: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.recognized = recognized


class _Budget:
    def __init__(self, seconds: float) -> None:
        if seconds <= 0:
            raise _PakError("invalid_limits", "解析超时限制必须大于 0。")
        self.deadline = time.monotonic() + seconds
        self.operations = 0

    def check(self, *, force: bool = False) -> None:
        self.operations += 1
        if (force or (self.operations & 0x3FF) == 0) and time.monotonic() > self.deadline:
            raise _PakError("parse_timeout", "Pak 解析超过安全时间上限。")


class _Reader:
    def __init__(
        self,
        data: bytes,
        budget: _Budget,
        limits: UnrealPakLimits,
        *,
        label: str,
    ) -> None:
        self.data = data
        self.view = memoryview(data)
        self.pos = 0
        self.budget = budget
        self.limits = limits
        self.label = label

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos

    def _take(self, size: int) -> memoryview:
        self.budget.check()
        if size < 0 or size > self.remaining:
            raise _PakError("truncated_index", f"{self.label} 数据被截断。")
        start = self.pos
        self.pos += size
        return self.view[start : start + size]

    def read(self, size: int) -> bytes:
        return bytes(self._take(size))

    def skip(self, size: int) -> None:
        self._take(size)

    def u8(self) -> int:
        return self._take(1)[0]

    def u32(self) -> int:
        value = struct.unpack_from("<I", self.data, self.pos)[0] if self.remaining >= 4 else None
        self._take(4)
        assert value is not None
        return value

    def i32(self) -> int:
        value = struct.unpack_from("<i", self.data, self.pos)[0] if self.remaining >= 4 else None
        self._take(4)
        assert value is not None
        return value

    def u64(self) -> int:
        value = struct.unpack_from("<Q", self.data, self.pos)[0] if self.remaining >= 8 else None
        self._take(8)
        assert value is not None
        return value

    def fstring(self) -> str:
        length = self.i32()
        if length == 0:
            return ""
        if length > 0:
            if length > self.limits.max_string_bytes:
                raise _PakError("string_too_large", f"{self.label} 字符串超过安全上限。")
            raw = self.read(length)
            if not raw or raw[-1] != 0 or b"\x00" in raw[:-1]:
                raise _PakError("invalid_string", f"{self.label} ANSI 字符串终止符无效。")
            payload = raw[:-1]
            try:
                return payload.decode("utf-8")
            except UnicodeDecodeError:
                return payload.decode("latin-1")
        units = -length
        if units > self.limits.max_string_bytes // 2:
            raise _PakError("string_too_large", f"{self.label} UTF-16 字符串超过安全上限。")
        raw = self.read(units * 2)
        if len(raw) < 2 or raw[-2:] != b"\x00\x00":
            raise _PakError("invalid_string", f"{self.label} UTF-16 字符串终止符无效。")
        try:
            value = raw[:-2].decode("utf-16-le", errors="strict")
        except UnicodeDecodeError as exc:
            raise _PakError("invalid_string", f"{self.label} UTF-16 字符串无效。") from exc
        if "\x00" in value:
            raise _PakError("invalid_string", f"{self.label} 字符串包含嵌入空字符。")
        return value

    def require_end(self) -> None:
        if self.remaining != 0:
            raise _PakError("index_trailing_data", f"{self.label} 存在未解释的尾随数据。")


@dataclass(frozen=True)
class _FooterLayout:
    name: str
    version: int
    size: int
    compression_count: int
    compression_index_bytes: int
    frozen_flag: bool = False


_FOOTER_LAYOUTS = (
    _FooterLayout("v11", 11, 221, 5, 4),
    _FooterLayout("v10", 10, 221, 5, 4),
    _FooterLayout("v9", 9, 222, 5, 4, True),
    _FooterLayout("v8b", 8, 221, 5, 4),
    _FooterLayout("v8a", 8, 189, 4, 1),
)


@dataclass(frozen=True)
class _Footer:
    layout: _FooterLayout
    footer_start: int
    index_offset: int
    index_size: int
    index_hash: bytes
    compression_names: tuple[str | None, ...]
    mount_point: str = ""
    index_data: bytes = b""


@dataclass(frozen=True)
class _Block:
    start: int
    end: int


@dataclass(frozen=True)
class _Entry:
    offset: int
    compressed_size: int
    uncompressed_size: int
    compression_slot: int | None
    content_hash: bytes | None
    blocks: tuple[_Block, ...] | None
    flags: int
    compression_block_size: int

    @property
    def encrypted(self) -> bool:
        return bool(self.flags & 1)

    @property
    def deleted(self) -> bool:
        return bool(self.flags & 2)


@dataclass(frozen=True)
class _Target:
    internal_path: str
    entry: _Entry | None
    deleted: bool = False


def _checked_region(
    offset: int,
    size: int,
    *,
    upper: int,
    maximum: int,
    label: str,
) -> tuple[int, int]:
    if offset < 0 or size <= 0 or size > maximum:
        raise _PakError("invalid_region", f"{label} 的偏移或大小超过安全上限。")
    end = offset + size
    if end < offset or end > upper:
        raise _PakError("invalid_region", f"{label} 超出 Pak 文件边界。")
    return offset, end


def _read_exact_at(handle: BinaryIO, offset: int, size: int, *, label: str) -> bytes:
    try:
        handle.seek(offset, os.SEEK_SET)
        data = handle.read(size)
    except OSError as exc:
        raise _PakError("read_failed", f"读取{label}失败。") from exc
    if len(data) != size:
        raise _PakError("truncated_file", f"{label}被截断。")
    return data


def _parse_compression_name(raw: bytes) -> str | None:
    nul = raw.find(b"\x00")
    if nul < 0:
        payload = raw
    else:
        payload = raw[:nul]
        if any(raw[nul + 1 :]):
            raise _PakError("invalid_footer", "Footer 压缩法名称的填充字节无效。")
    if not payload:
        return None
    try:
        name = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise _PakError("invalid_footer", "Footer 压缩法名称不是 ASCII。") from exc
    if not re.fullmatch(r"[A-Za-z0-9_+.-]{1,32}", name):
        raise _PakError("invalid_footer", "Footer 压缩法名称格式无效。")
    return name


def _parse_footer_candidate(
    handle: BinaryIO,
    file_size: int,
    layout: _FooterLayout,
    limits: UnrealPakLimits,
) -> _Footer | None:
    if file_size < layout.size:
        return None
    footer_start = file_size - layout.size
    data = _read_exact_at(handle, footer_start, layout.size, label=" Pak Footer")
    # All supported layouts start with a 16-byte encryption GUID and a bool.
    magic, version = struct.unpack_from("<II", data, 17)
    if magic != _PAK_MAGIC or version != layout.version:
        return None
    encrypted = data[16]
    if encrypted not in (0, 1):
        raise _PakError("invalid_footer", "Pak Footer 的加密标记无效。")
    if encrypted:
        raise _PakError("encrypted_index", "Pak 索引已加密，未提供密钥时必须拒绝。")
    index_offset, index_size = struct.unpack_from("<QQ", data, 25)
    index_hash = data[41:61]
    cursor = 61
    if layout.frozen_flag:
        frozen = data[cursor]
        cursor += 1
        if frozen not in (0, 1):
            raise _PakError("invalid_footer", "Pak FrozenIndex 标记无效。")
        if frozen:
            raise _PakError("frozen_index", "冻结式 Pak v9 索引不在安全支持范围内。")
    names: list[str | None] = []
    for _ in range(layout.compression_count):
        names.append(_parse_compression_name(data[cursor : cursor + 32]))
        cursor += 32
    if cursor != len(data):
        raise _PakError("invalid_footer", "Pak Footer 布局不完整。")
    _checked_region(
        index_offset,
        index_size,
        upper=footer_start,
        maximum=limits.max_index_bytes,
        label="主索引",
    )
    index_data = _read_exact_at(handle, index_offset, index_size, label=" Pak 主索引")
    if hashlib.sha1(index_data).digest() != index_hash:
        raise _PakError("index_hash_mismatch", "Pak 主索引 SHA-1 校验失败。")
    return _Footer(
        layout=layout,
        footer_start=footer_start,
        index_offset=index_offset,
        index_size=index_size,
        index_hash=index_hash,
        compression_names=tuple(names),
        index_data=index_data,
    )


def _read_footer(handle: BinaryIO, file_size: int, limits: UnrealPakLimits) -> _Footer:
    valid: list[_Footer] = []
    errors: list[_PakError] = []
    for layout in _FOOTER_LAYOUTS:
        try:
            parsed = _parse_footer_candidate(handle, file_size, layout, limits)
        except _PakError as exc:
            errors.append(exc)
            continue
        if parsed is not None:
            valid.append(parsed)
    if len(valid) == 1:
        return valid[0]
    if len(valid) > 1:
        raise _PakError("ambiguous_footer", "Pak Footer 同时匹配多个版本布局。")
    if errors:
        raise errors[0]
    raise _PakError("not_unreal_pak", "文件不是受支持的 Unreal Pak v8-v11。", recognized=False)


def _entry_serialized_size(layout: _FooterLayout, compressed: bool, block_count: int) -> int:
    size = 24 + layout.compression_index_bytes + _SHA1_BYTES + 1 + 4
    if compressed:
        size += 4 + 16 * block_count
    return size


def _read_compression_slot(reader: _Reader, layout: _FooterLayout) -> int | None:
    raw = reader.u8() if layout.compression_index_bytes == 1 else reader.u32()
    return None if raw == 0 else raw - 1


def _parse_full_entry(
    reader: _Reader,
    footer: _Footer,
    *,
    retain_blocks: bool = True,
    retained_block_limit: int | None = None,
) -> _Entry:
    offset = reader.u64()
    compressed_size = reader.u64()
    uncompressed_size = reader.u64()
    compression_slot = _read_compression_slot(reader, footer.layout)
    content_hash = reader.read(_SHA1_BYTES)
    blocks: tuple[_Block, ...] | None = None
    if compression_slot is not None:
        count = reader.u32()
        if count <= 0 or count > reader.limits.max_blocks_per_entry:
            raise _PakError("invalid_entry", "Pak 条目的压缩块数量无效。")
        if retain_blocks and retained_block_limit is not None and count > retained_block_limit:
            raise _PakError("block_count_limit", "目标条目的压缩块数量超过安全上限。")
        if retain_blocks:
            blocks = tuple(_Block(reader.u64(), reader.u64()) for _ in range(count))
        else:
            reader.skip(16 * count)
    flags = reader.u8()
    compression_block_size = reader.u32()
    return _Entry(
        offset=offset,
        compressed_size=compressed_size,
        uncompressed_size=uncompressed_size,
        compression_slot=compression_slot,
        content_hash=content_hash,
        blocks=blocks,
        flags=flags,
        compression_block_size=compression_block_size,
    )


def _parse_encoded_entry(
    reader: _Reader,
    footer: _Footer,
    *,
    retain_blocks: bool = True,
    retained_block_limit: int | None = None,
) -> _Entry:
    bits = reader.u32()
    raw_compression = (bits >> 23) & 0x3F
    compression_slot = None if raw_compression == 0 else raw_compression - 1
    encrypted = bool(bits & (1 << 22))
    block_count = (bits >> 6) & 0xFFFF
    block_size = bits & 0x3F
    if block_size == 0x3F:
        block_size = reader.u32()
    else:
        block_size <<= 11

    def var_int(bit: int) -> int:
        return reader.u32() if bits & (1 << bit) else reader.u64()

    offset = var_int(31)
    uncompressed_size = var_int(30)
    compressed_size = uncompressed_size if compression_slot is None else var_int(29)
    if block_count > reader.limits.max_blocks_per_entry:
        raise _PakError("invalid_entry", "编码条目的压缩块数量超过安全上限。")
    if retain_blocks and retained_block_limit is not None and block_count > retained_block_limit:
        raise _PakError("block_count_limit", "目标编码条目的压缩块数量超过安全上限。")
    offset_base = _entry_serialized_size(
        footer.layout,
        compression_slot is not None,
        block_count,
    )
    blocks: tuple[_Block, ...] | None
    if block_count == 1 and not encrypted:
        blocks = (_Block(offset_base, offset_base + compressed_size),) if retain_blocks else None
    elif block_count > 0:
        built: list[_Block] = []
        cursor = offset_base
        for _ in range(block_count):
            compressed_block_size = reader.u32()
            end = cursor + compressed_block_size
            if end < cursor:
                raise _PakError("invalid_entry", "编码条目的压缩块大小溢出。")
            if retain_blocks:
                built.append(_Block(cursor, end))
            cursor += (compressed_block_size + 15) & ~15 if encrypted else compressed_block_size
        blocks = tuple(built) if retain_blocks else None
    else:
        blocks = None
    return _Entry(
        offset=offset,
        compressed_size=compressed_size,
        uncompressed_size=uncompressed_size,
        compression_slot=compression_slot,
        content_hash=None,
        blocks=blocks,
        flags=1 if encrypted else 0,
        compression_block_size=block_size,
    )


def _validate_internal_path(path: str) -> tuple[str, ...]:
    if not path or "\\" in path or "\x00" in path:
        raise _PakError("unsafe_index_path", "Pak 索引包含不安全路径。")
    trimmed = path.strip("/")
    if not trimmed:
        return ()
    parts = tuple(trimmed.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise _PakError("unsafe_index_path", "Pak 索引包含路径穿越或空路径段。")
    return parts


def _is_target_path(path: str) -> bool:
    parts = _validate_internal_path(path)
    return (
        len(parts) >= 2
        and parts[-1].casefold() == _TARGET_FILE
        and parts[-2].casefold() == _TARGET_DIRECTORY
    )


def _combine_fdi_path(directory: str, file_name: str) -> str:
    directory_parts = _validate_internal_path(directory) if directory not in {"", "/"} else ()
    if not file_name or "/" in file_name or "\\" in file_name or file_name in {".", ".."}:
        raise _PakError("unsafe_index_path", "Pak 完整目录索引包含不安全文件名。")
    if "\x00" in file_name:
        raise _PakError("unsafe_index_path", "Pak 完整目录索引包含空字符。")
    return "/".join((*directory_parts, file_name))


def _validate_target_entry_metadata(
    entry: _Entry,
    footer: _Footer,
    limits: UnrealPakLimits,
) -> str:
    if entry.flags & ~0x03:
        raise _PakError("invalid_entry", "目标条目包含未知标记。")
    if entry.deleted:
        raise _PakError("deleted_entry", "目标 DefaultInput.ini 条目已被标记删除。")
    if entry.encrypted:
        raise _PakError("encrypted_entry", "目标 DefaultInput.ini 数据已加密，必须拒绝。")
    if entry.offset < 0 or entry.offset >= footer.index_offset:
        raise _PakError("invalid_entry_offset", "目标条目偏移超出 Pak 数据区。")
    if entry.uncompressed_size <= 0 or entry.uncompressed_size > limits.max_config_bytes:
        raise _PakError("config_size_limit", "DefaultInput.ini 解压大小超过安全上限。")
    if entry.compressed_size <= 0 or entry.compressed_size > limits.max_compressed_config_bytes:
        raise _PakError("config_size_limit", "DefaultInput.ini 存储大小超过安全上限。")
    if entry.compression_slot is None:
        if entry.compressed_size != entry.uncompressed_size or entry.blocks:
            raise _PakError("invalid_entry", "未压缩目标条目的大小或块表无效。")
        return "None"
    if entry.compression_slot >= len(footer.compression_names):
        raise _PakError("invalid_compression_slot", "目标条目引用了不存在的压缩法槽位。")
    name = footer.compression_names[entry.compression_slot]
    if name is None:
        raise _PakError("invalid_compression_slot", "目标条目的压缩法槽位为空。")
    if name.casefold() not in {"zlib", "gzip", "oodle"}:
        raise _PakError("unsupported_compression", f"不支持目标条目的压缩法 {name}。")
    blocks = entry.blocks or ()
    if not blocks or len(blocks) > limits.max_config_blocks:
        raise _PakError("block_count_limit", "DefaultInput.ini 压缩块数量超过安全上限。")
    if entry.compression_block_size <= 0 or entry.compression_block_size > limits.max_config_bytes:
        raise _PakError("invalid_block_size", "DefaultInput.ini 压缩块解压大小无效。")
    expected_count = (
        entry.uncompressed_size + entry.compression_block_size - 1
    ) // entry.compression_block_size
    if expected_count != len(blocks):
        raise _PakError("invalid_block_count", "DefaultInput.ini 压缩块数量与解压大小不一致。")
    total = 0
    for block in blocks:
        if block.start < 0 or block.end <= block.start:
            raise _PakError("invalid_block_range", "DefaultInput.ini 压缩块范围无效。")
        size = block.end - block.start
        if size > limits.max_compressed_block_bytes:
            raise _PakError("block_size_limit", "DefaultInput.ini 单个压缩块超过安全上限。")
        total += size
    if total != entry.compressed_size:
        raise _PakError("invalid_block_ranges", "DefaultInput.ini 压缩块总大小不一致。")
    return name


@dataclass(frozen=True)
class _SecondaryIndex:
    label: str
    offset: int
    size: int
    digest: bytes


def _read_optional_secondary(reader: _Reader, label: str) -> _SecondaryIndex | None:
    present = reader.u32()
    if present not in (0, 1):
        raise _PakError("invalid_index", f"{label}存在标记无效。")
    if not present:
        return None
    return _SecondaryIndex(
        label=label,
        offset=reader.u64(),
        size=reader.u64(),
        digest=reader.read(_SHA1_BYTES),
    )


def _read_secondary_data(
    handle: BinaryIO,
    footer: _Footer,
    secondary: _SecondaryIndex,
    limits: UnrealPakLimits,
) -> bytes:
    _checked_region(
        secondary.offset,
        secondary.size,
        upper=footer.footer_start,
        maximum=limits.max_secondary_index_bytes,
        label=secondary.label,
    )
    data = _read_exact_at(
        handle,
        secondary.offset,
        secondary.size,
        label=f" Pak {secondary.label}",
    )
    if hashlib.sha1(data).digest() != secondary.digest:
        raise _PakError("secondary_hash_mismatch", f"{secondary.label} SHA-1 校验失败。")
    return data


def _validate_non_overlapping_regions(
    footer: _Footer,
    secondaries: Sequence[_SecondaryIndex | None],
) -> None:
    regions = [
        (footer.index_offset, footer.index_offset + footer.index_size, "主索引")
    ]
    regions.extend(
        (item.offset, item.offset + item.size, item.label)
        for item in secondaries
        if item is not None
    )
    regions.sort()
    for (_, previous_end, previous_label), (current_start, _, current_label) in zip(
        regions, regions[1:]
    ):
        if current_start < previous_end:
            raise _PakError(
                "overlapping_indices",
                f"{previous_label}与{current_label}区域重叠。",
            )


def _parse_full_directory_index(
    data: bytes,
    record_count: int,
    budget: _Budget,
    limits: UnrealPakLimits,
) -> tuple[int | None, str, int]:
    reader = _Reader(data, budget, limits, label="完整目录索引")
    directory_count = reader.u32()
    if directory_count > limits.max_directories:
        raise _PakError("directory_count_limit", "完整目录索引的目录数量超过安全上限。")
    target_reference: int | None = None
    target_path = ""
    total_files = 0
    for _ in range(directory_count):
        directory = reader.fstring()
        if directory not in {"", "/"}:
            _validate_internal_path(directory)
        file_count = reader.u32()
        if file_count > limits.max_entries - total_files:
            raise _PakError("entry_count_limit", "完整目录索引的文件数量超过安全上限。")
        for _ in range(file_count):
            file_name = reader.fstring()
            reference = reader.i32()
            total_files += 1
            internal_path = _combine_fdi_path(directory, file_name)
            if not _is_target_path(internal_path):
                continue
            if target_reference is not None:
                raise _PakError("ambiguous_target", "同一 Pak 中存在多个 DefaultInput.ini 目标。")
            target_reference = reference
            target_path = internal_path
    reader.require_end()
    if total_files > record_count:
        raise _PakError("invalid_index", "完整目录索引文件数大于主索引记录数。")
    return target_reference, target_path, total_files


def _parse_legacy_index(
    footer: _Footer,
    budget: _Budget,
    limits: UnrealPakLimits,
) -> tuple[_Footer, _Target | None]:
    reader = _Reader(footer.index_data, budget, limits, label="Pak 主索引")
    mount_point = reader.fstring()
    record_count = reader.u32()
    if record_count > limits.max_entries:
        raise _PakError("entry_count_limit", "Pak 主索引记录数超过安全上限。")
    target: _Target | None = None
    for _ in range(record_count):
        internal_path = reader.fstring().lstrip("/")
        _validate_internal_path(internal_path)
        is_target = _is_target_path(internal_path)
        entry = _parse_full_entry(
            reader,
            footer,
            retain_blocks=is_target,
            retained_block_limit=limits.max_config_blocks if is_target else None,
        )
        if not is_target:
            continue
        if target is not None:
            raise _PakError("ambiguous_target", "同一 Pak 中存在多个 DefaultInput.ini 目标。")
        target = _Target(internal_path, entry, entry.deleted)
    reader.require_end()
    return replace(footer, mount_point=mount_point), target


def _parse_path_hash_index_header(
    footer: _Footer,
    handle: BinaryIO,
    budget: _Budget,
    limits: UnrealPakLimits,
) -> tuple[_Footer, _Target | None]:
    reader = _Reader(footer.index_data, budget, limits, label="Pak 主索引")
    mount_point = reader.fstring()
    record_count = reader.u32()
    if record_count > limits.max_entries:
        raise _PakError("entry_count_limit", "Pak 主索引记录数超过安全上限。")
    reader.u64()  # Path hash seed; discovery uses the authenticated FDI.
    path_hash_index = _read_optional_secondary(reader, "路径哈希索引")
    full_directory_index = _read_optional_secondary(reader, "完整目录索引")
    encoded_size = reader.u32()
    if encoded_size > limits.max_index_bytes or encoded_size > reader.remaining:
        raise _PakError("encoded_index_limit", "编码条目表大小超过安全上限。")
    encoded_entries = reader.read(encoded_size)
    nonencoded_count = reader.u32()
    if nonencoded_count > min(record_count, limits.max_nonencoded_entries):
        raise _PakError("nonencoded_entry_limit", "非编码条目数量超过安全上限。")
    nonencoded_spans: list[tuple[int, int]] = []
    for _ in range(nonencoded_count):
        start = reader.pos
        _parse_full_entry(reader, footer, retain_blocks=False)
        nonencoded_spans.append((start, reader.pos))
    reader.require_end()

    _validate_non_overlapping_regions(footer, (path_hash_index, full_directory_index))
    if path_hash_index is not None:
        # The path-hash region is not used for discovery, but its authenticated
        # bytes and bounds are still verified so no unchecked index region is
        # silently accepted.
        _read_secondary_data(handle, footer, path_hash_index, limits)
    if full_directory_index is None:
        return replace(footer, mount_point=mount_point), None
    fdi_data = _read_secondary_data(handle, footer, full_directory_index, limits)
    target_reference, target_path, _total_files = _parse_full_directory_index(
        fdi_data,
        record_count,
        budget,
        limits,
    )

    expected_encoded_count = record_count - nonencoded_count
    encoded_reader = _Reader(encoded_entries, budget, limits, label="编码条目表")
    target_entry: _Entry | None = None
    target_offset = target_reference if target_reference is not None and target_reference >= 0 else None
    for _ in range(expected_encoded_count):
        start = encoded_reader.pos
        is_target = target_offset == start
        entry = _parse_encoded_entry(
            encoded_reader,
            footer,
            retain_blocks=is_target,
            retained_block_limit=limits.max_config_blocks if is_target else None,
        )
        if target_offset == start:
            target_entry = entry
    encoded_reader.require_end()

    updated_footer = replace(footer, mount_point=mount_point)
    if target_reference is None:
        return updated_footer, None
    if target_reference == -(2**31):
        return updated_footer, _Target(target_path, None, True)
    if target_reference >= 0:
        if target_entry is None:
            raise _PakError("invalid_entry_reference", "目标条目偏移不是编码条目边界。")
        return updated_footer, _Target(target_path, target_entry, target_entry.deleted)
    nonencoded_index = -target_reference - 1
    if nonencoded_index < 0 or nonencoded_index >= len(nonencoded_spans):
        raise _PakError("invalid_entry_reference", "目标条目引用了不存在的非编码条目。")
    start, end = nonencoded_spans[nonencoded_index]
    target_reader = _Reader(
        footer.index_data[start:end],
        budget,
        limits,
        label="目标非编码条目",
    )
    entry = _parse_full_entry(
        target_reader,
        footer,
        retained_block_limit=limits.max_config_blocks,
    )
    target_reader.require_end()
    return updated_footer, _Target(target_path, entry, entry.deleted)


def _locate_target(
    footer: _Footer,
    handle: BinaryIO,
    budget: _Budget,
    limits: UnrealPakLimits,
) -> tuple[_Footer, _Target | None]:
    if footer.layout.version >= 10:
        return _parse_path_hash_index_header(footer, handle, budget, limits)
    return _parse_legacy_index(footer, budget, limits)


def _read_data_entry(
    handle: BinaryIO,
    index_entry: _Entry,
    footer: _Footer,
    budget: _Budget,
    limits: UnrealPakLimits,
) -> tuple[_Entry, int]:
    base_size = 24 + footer.layout.compression_index_bytes + _SHA1_BYTES
    if index_entry.offset + base_size > footer.index_offset:
        raise _PakError("invalid_entry_offset", "目标条目头部超出 Pak 数据区。")
    prefix = _read_exact_at(handle, index_entry.offset, base_size, label="目标条目头部")
    slot_offset = 24
    if footer.layout.compression_index_bytes == 1:
        raw_slot = prefix[slot_offset]
    else:
        raw_slot = struct.unpack_from("<I", prefix, slot_offset)[0]
    compression_slot = None if raw_slot == 0 else raw_slot - 1
    if compression_slot is None:
        header_size = base_size + 5
    else:
        count_bytes = _read_exact_at(
            handle,
            index_entry.offset + base_size,
            4,
            label="目标条目块计数",
        )
        block_count = struct.unpack("<I", count_bytes)[0]
        if block_count <= 0 or block_count > limits.max_config_blocks:
            raise _PakError("block_count_limit", "目标条目头部的压缩块数量超过安全上限。")
        header_size = base_size + 4 + 16 * block_count + 5
    if index_entry.offset + header_size > footer.index_offset:
        raise _PakError("invalid_entry_offset", "目标条目头部越过 Pak 主索引。")
    header = _read_exact_at(handle, index_entry.offset, header_size, label="目标条目头部")
    reader = _Reader(header, budget, limits, label="目标条目头部")
    data_entry = _parse_full_entry(
        reader,
        footer,
        retained_block_limit=limits.max_config_blocks,
    )
    reader.require_end()
    return data_entry, header_size


def _require_matching_entries(index_entry: _Entry, data_entry: _Entry) -> None:
    comparable_index = (
        index_entry.compressed_size,
        index_entry.uncompressed_size,
        index_entry.compression_slot,
        index_entry.blocks,
        index_entry.flags,
        index_entry.compression_block_size,
    )
    comparable_data = (
        data_entry.compressed_size,
        data_entry.uncompressed_size,
        data_entry.compression_slot,
        data_entry.blocks,
        data_entry.flags,
        data_entry.compression_block_size,
    )
    if comparable_index != comparable_data:
        raise _PakError("entry_header_mismatch", "索引条目与数据区条目头部不一致。")
    if index_entry.content_hash is not None and index_entry.content_hash != data_entry.content_hash:
        raise _PakError("entry_hash_mismatch", "索引条目与数据区条目的内容哈希不一致。")


def _read_target_payload(
    handle: BinaryIO,
    entry: _Entry,
    header_size: int,
    footer: _Footer,
    limits: UnrealPakLimits,
) -> bytes:
    data_start = entry.offset + header_size
    data_end = data_start + entry.compressed_size
    if data_end < data_start or data_end > footer.index_offset:
        raise _PakError("invalid_entry_range", "目标条目数据越过 Pak 主索引。")
    if entry.blocks:
        expected_relative = header_size
        for block in entry.blocks:
            if block.start != expected_relative:
                raise _PakError("invalid_block_ranges", "目标压缩块不是严格连续布局。")
            expected_relative = block.end
        if expected_relative != header_size + entry.compressed_size:
            raise _PakError("invalid_block_ranges", "目标压缩块没有完整覆盖存储数据。")
    payload = _read_exact_at(handle, data_start, entry.compressed_size, label="目标条目数据")
    if entry.content_hash is None or hashlib.sha1(payload).digest() != entry.content_hash:
        raise _PakError("entry_hash_mismatch", "DefaultInput.ini 存储数据 SHA-1 校验失败。")
    return payload


def _decompress_deflate_block(data: bytes, expected_size: int, *, gzip_stream: bool) -> bytes:
    try:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if gzip_stream else zlib.MAX_WBITS)
        decoded = decoder.decompress(data, expected_size + 1)
    except zlib.error as exc:
        raise _PakError("decompression_failed", "Deflate 压缩块解压失败。") from exc
    if (
        len(decoded) != expected_size
        or not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
    ):
        raise _PakError("decompression_mismatch", "Deflate 压缩块长度或尾部状态无效。")
    return decoded


def _resolved_helper_command(config: KrakenHelperConfig) -> tuple[str, ...]:
    if config.command is not None:
        command = tuple(str(item) for item in config.command)
        if not command or any(not item or "\x00" in item for item in command):
            raise _PakError("invalid_helper", "Kraken helper 命令无效。")
        return command
    if getattr(sys, "frozen", False):
        raise _PakError(
            "helper_unavailable",
            "冻结程序必须显式提供 Kraken helper 命令。",
        )
    helper_path = Path(__file__).with_name("unreal_kraken_helper.py")
    if not helper_path.is_file():
        raise _PakError("helper_unavailable", "未找到独立 Kraken helper。")
    return (sys.executable, "-I", str(helper_path))


class _WindowsKillJob:
    """Best-effort Windows Job Object that owns a helper process tree."""

    __slots__ = ("_handle", "_kernel32")

    def __init__(self, handle: object, kernel32: object) -> None:
        self._handle = handle
        self._kernel32 = kernel32

    def terminate(self) -> None:
        handle = self._handle
        if not handle:
            return
        try:
            self._kernel32.TerminateJobObject(handle, 2)
        except (AttributeError, OSError, ValueError):
            pass
        self.close()

    def close(self) -> None:
        handle = self._handle
        if not handle:
            return
        self._handle = None
        try:
            self._kernel32.CloseHandle(handle)
        except (AttributeError, OSError, ValueError):
            pass


def _create_windows_kill_job(
    process: subprocess.Popen[bytes],
) -> _WindowsKillJob | None:
    """Attach ``process`` to a KILL_ON_JOB_CLOSE Job when Windows permits it."""

    if os.name != "nt" or not hasattr(process, "_handle"):
        return None
    job: _WindowsKillJob | None = None
    try:
        from ctypes import wintypes

        class _IoCounters(ctypes.Structure):
            _fields_ = (
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            )

        class _BasicLimitInformation(ctypes.Structure):
            _fields_ = (
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            )

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = (
                ("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            )

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = (
            wintypes.HANDLE,
            wintypes.HANDLE,
        )
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        job = _WindowsKillJob(handle, kernel32)
        information = _ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(
            handle,
            9,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            job.close()
            return None
        process_handle = wintypes.HANDLE(int(process._handle))
        if not kernel32.AssignProcessToJobObject(handle, process_handle):
            job.close()
            return None
        return job
    except (AttributeError, OSError, TypeError, ValueError):
        if job is not None:
            job.close()
        return None


def _resume_windows_suspended_process(process: subprocess.Popen[bytes]) -> bool:
    """Resume a CREATE_SUSPENDED process after its Job assignment."""

    if os.name != "nt" or not hasattr(process, "_handle"):
        return True
    try:
        from ctypes import wintypes

        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
        ntdll.NtResumeProcess.restype = ctypes.c_long
        status = ntdll.NtResumeProcess(wintypes.HANDLE(int(process._handle)))
        return status >= 0
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _terminate_helper_process(
    process: subprocess.Popen[bytes],
    job: _WindowsKillJob | None,
) -> None:
    """Terminate the helper tree when possible and always reap its parent."""

    if process.poll() is None:
        if job is not None:
            job.terminate()
        else:
            try:
                process.kill()
            except OSError:
                pass
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            pass
    except OSError:
        pass


@contextmanager
def _temporary_helper_root() -> Iterator[Path]:
    """Create a private helper directory whose cleanup cannot mask results."""

    temporary = tempfile.TemporaryDirectory(
        prefix="unreal_kraken_",
        ignore_cleanup_errors=True,
    )
    try:
        yield Path(temporary.name)
    finally:
        try:
            temporary.cleanup()
        except OSError:
            # Windows virus scanners and a just-terminated process may retain a
            # short-lived handle.  The original decoder outcome is authoritative.
            pass


def _decompress_kraken_block(
    data: bytes,
    expected_size: int,
    limits: UnrealPakLimits,
    helper: KrakenHelperConfig,
) -> bytes:
    if len(data) < 2:
        raise _PakError("invalid_oodle_stream", "Oodle 压缩块头部被截断。")
    first, second = data[0], data[1]
    if (first & 0x0F) != 0x0C or ((first >> 4) & 0x03) != 0:
        raise _PakError("invalid_oodle_stream", "Oodle 压缩块头部无效。")
    if (second & 0x7F) != 6:
        raise _PakError("unsupported_oodle_codec", "Oodle 数据不是 Kraken 编码，必须拒绝。")
    if expected_size > len(data) * limits.max_compression_ratio + _RATIO_SLACK_BYTES:
        raise _PakError("compression_ratio_limit", "Kraken 解压倍率超过安全上限。")

    command = _resolved_helper_command(helper)
    timeout = helper.timeout_seconds or limits.kraken_timeout_seconds
    if timeout <= 0:
        raise _PakError("invalid_limits", "Kraken helper 超时必须大于 0。")
    with _temporary_helper_root() as root:
        input_path = root / "input.bin"
        output_path = root / "output.bin"
        input_path.write_bytes(data)
        arguments = [
            *command,
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--expected-size",
            str(expected_size),
            "--max-input",
            str(limits.max_compressed_block_bytes),
            "--max-output",
            str(limits.max_config_bytes),
            "--max-ratio",
            str(limits.max_compression_ratio),
        ]
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment["PYTHONNOUSERSITE"] = "1"
        creationflags = 0
        helper_suspended = os.name == "nt"
        if helper_suspended:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | 0x00000004
        try:
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=root,
                env=environment,
                shell=False,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise _PakError("helper_launch_failed", "无法启动独立 Kraken helper。") from exc
        job = _create_windows_kill_job(process)
        if helper_suspended and not _resume_windows_suspended_process(process):
            _terminate_helper_process(process, job)
            if job is not None:
                job.close()
            raise _PakError(
                "helper_launch_failed",
                "无法在安全 Job 边界内启动 Kraken helper。",
            )
        try:
            try:
                return_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                _terminate_helper_process(process, job)
                raise _PakError(
                    "helper_timeout",
                    "Kraken helper 运行超时并已终止。",
                ) from exc
            except OSError as exc:
                _terminate_helper_process(process, job)
                raise _PakError(
                    "helper_execution_failed",
                    "等待 Kraken helper 完成时发生错误。",
                ) from exc
        finally:
            if job is not None:
                job.close()
        if return_code != 0:
            raise _PakError(
                "kraken_failed",
                f"Kraken helper 解压失败（退出码 {return_code}）。",
            )
        try:
            if output_path.is_symlink() or not output_path.is_file():
                raise _PakError("helper_output_invalid", "Kraken helper 未生成常规输出文件。")
            size = output_path.stat().st_size
        except OSError as exc:
            raise _PakError("helper_output_invalid", "无法检查 Kraken helper 输出。") from exc
        if size != expected_size or size > limits.max_config_bytes:
            raise _PakError("helper_output_invalid", "Kraken helper 输出大小不一致。")
        try:
            with output_path.open("rb") as handle:
                decoded = handle.read(expected_size + 1)
        except OSError as exc:
            raise _PakError("helper_output_invalid", "无法读取 Kraken helper 输出。") from exc
        if len(decoded) != expected_size:
            raise _PakError("helper_output_invalid", "Kraken helper 输出被截断。")
        return decoded


def _decompress_target(
    payload: bytes,
    entry: _Entry,
    compression_method: str,
    limits: UnrealPakLimits,
    helper: KrakenHelperConfig,
) -> bytes:
    if compression_method == "None":
        if len(payload) != entry.uncompressed_size:
            raise _PakError("entry_size_mismatch", "未压缩目标条目的大小不一致。")
        return payload
    if entry.uncompressed_size > len(payload) * limits.max_compression_ratio + _RATIO_SLACK_BYTES:
        raise _PakError("compression_ratio_limit", "DefaultInput.ini 总解压倍率超过安全上限。")
    output = bytearray()
    remaining = entry.uncompressed_size
    payload_cursor = 0
    for block in entry.blocks or ():
        compressed_size = block.end - block.start
        compressed = payload[payload_cursor : payload_cursor + compressed_size]
        if len(compressed) != compressed_size:
            raise _PakError("invalid_block_ranges", "DefaultInput.ini 压缩块被截断。")
        expected = min(entry.compression_block_size, remaining)
        if expected <= 0:
            raise _PakError("invalid_block_count", "DefaultInput.ini 存在多余压缩块。")
        if expected > len(compressed) * limits.max_compression_ratio + _RATIO_SLACK_BYTES:
            raise _PakError("compression_ratio_limit", "单个压缩块解压倍率超过安全上限。")
        lowered = compression_method.casefold()
        if lowered == "zlib":
            decoded = _decompress_deflate_block(compressed, expected, gzip_stream=False)
        elif lowered == "gzip":
            decoded = _decompress_deflate_block(compressed, expected, gzip_stream=True)
        elif lowered == "oodle":
            decoded = _decompress_kraken_block(compressed, expected, limits, helper)
        else:  # Guarded by _validate_target_entry_metadata.
            raise _PakError("unsupported_compression", "目标条目的压缩法不受支持。")
        output.extend(decoded)
        if len(output) > limits.max_config_bytes:
            raise _PakError("config_size_limit", "DefaultInput.ini 输出超过安全上限。")
        payload_cursor += compressed_size
        remaining -= expected
    if payload_cursor != len(payload) or remaining != 0 or len(output) != entry.uncompressed_size:
        raise _PakError("decompression_mismatch", "DefaultInput.ini 解压后的总大小不一致。")
    return bytes(output)


def _decode_config_text(data: bytes) -> str:
    try:
        if data.startswith(b"\xff\xfe"):
            text = data[2:].decode("utf-16-le", errors="strict")
        elif data.startswith(b"\xfe\xff"):
            text = data[2:].decode("utf-16-be", errors="strict")
        else:
            text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise _PakError("config_encoding", "DefaultInput.ini 不是严格 UTF-8 或带 BOM 的 UTF-16。") from exc
    if "\x00" in text:
        raise _PakError("config_encoding", "DefaultInput.ini 包含嵌入空字符。")
    if not re.search(r"(?im)^\s*\[/Script/Engine\.InputSettings\]\s*$", text):
        raise _PakError("config_schema", "目标文件缺少 Unreal InputSettings 节。")
    return text


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(callable(is_junction) and is_junction())
    except OSError:
        return True


def _validate_limits(limits: UnrealPakLimits) -> None:
    integer_fields = (
        limits.max_pak_bytes,
        limits.max_index_bytes,
        limits.max_secondary_index_bytes,
        limits.max_string_bytes,
        limits.max_entries,
        limits.max_directories,
        limits.max_nonencoded_entries,
        limits.max_blocks_per_entry,
        limits.max_config_blocks,
        limits.max_config_bytes,
        limits.max_compressed_config_bytes,
        limits.max_compressed_block_bytes,
        limits.max_compression_ratio,
        limits.max_paks,
        limits.max_directory_entries,
        limits.max_scan_depth,
    )
    if any(value <= 0 for value in integer_fields):
        raise _PakError("invalid_limits", "Pak 安全限制必须全部大于 0。", recognized=False)
    if limits.max_config_bytes > 16 * 1024 * 1024 or limits.max_compressed_block_bytes > 16 * 1024 * 1024:
        raise _PakError("invalid_limits", "Kraken helper 的输入或输出限制超过硬上限。", recognized=False)
    if limits.max_compression_ratio > 4096:
        raise _PakError("invalid_limits", "解压倍率限制超过 helper 硬上限。", recognized=False)
    if (
        limits.scan_timeout_seconds <= 0
        or limits.parse_timeout_seconds <= 0
        or limits.discovery_timeout_seconds <= 0
        or limits.kraken_timeout_seconds <= 0
    ):
        raise _PakError("invalid_limits", "所有超时限制必须大于 0。", recognized=False)


def _require_unchanged_file(handle: BinaryIO, initial: os.stat_result) -> None:
    try:
        final = os.fstat(handle.fileno())
    except OSError as exc:
        raise _PakError("pak_changed", "无法复核 Pak 文件状态，结果已拒绝。") from exc
    initial_identity = (
        initial.st_dev,
        initial.st_ino,
        initial.st_size,
        initial.st_mtime_ns,
    )
    final_identity = (
        final.st_dev,
        final.st_ino,
        final.st_size,
        final.st_mtime_ns,
    )
    if final_identity != initial_identity:
        raise _PakError("pak_changed", "Pak 文件在解析期间发生变化，结果已拒绝。")


def read_default_input_from_pak(
    pak_path: str | os.PathLike[str],
    *,
    limits: UnrealPakLimits | None = None,
    kraken_helper: KrakenHelperConfig | None = None,
) -> UnrealPakDefaultInputResult:
    """Read one authenticated packaged default from a specific Pak.

    A failure is returned as data rather than raised, which makes the function
    suitable for GUI discovery flows.  ``recognized_pak`` distinguishes a
    corrupt/unsupported Unreal Pak from an unrelated file using ``.pak``.
    """

    active_limits = limits or UnrealPakLimits()
    helper = kraken_helper or KrakenHelperConfig()
    path = Path(pak_path)
    footer: _Footer | None = None
    resolved = path
    try:
        _validate_limits(active_limits)
        if _is_link_or_junction(path) or not path.is_file():
            raise _PakError("invalid_pak_path", "Pak 路径不是常规文件。", recognized=False)
        resolved = path.resolve(strict=True)
        budget = _Budget(active_limits.parse_timeout_seconds)
        with resolved.open("rb") as handle:
            initial = os.fstat(handle.fileno())
            if initial.st_size <= 0 or initial.st_size > active_limits.max_pak_bytes:
                raise _PakError("pak_size_limit", "Pak 文件大小超过安全上限。", recognized=False)
            footer = _read_footer(handle, initial.st_size, active_limits)
            footer, target = _locate_target(footer, handle, budget, active_limits)
            if target is None:
                budget.check(force=True)
                _require_unchanged_file(handle, initial)
                return UnrealPakDefaultInputResult(
                    recognized_pak=True,
                    source_paks=(resolved,),
                    mount_point=footer.mount_point,
                    pak_version=footer.layout.version,
                    pak_layout=footer.layout.name,
                    diagnostic="Pak 索引有效，但未找到 */Config/DefaultInput.ini。",
                    scanned_paks=1,
                )
            if target.deleted or target.entry is None:
                raise _PakError("deleted_entry", "Pak 中的 DefaultInput.ini 已被删除或裁剪。")
            compression_method = _validate_target_entry_metadata(
                target.entry,
                footer,
                active_limits,
            )
            data_entry, header_size = _read_data_entry(
                handle,
                target.entry,
                footer,
                budget,
                active_limits,
            )
            _require_matching_entries(target.entry, data_entry)
            physical_entry = replace(data_entry, offset=target.entry.offset)
            data_method = _validate_target_entry_metadata(
                physical_entry,
                footer,
                active_limits,
            )
            if data_method.casefold() != compression_method.casefold():
                raise _PakError("entry_header_mismatch", "索引与数据头的压缩法不一致。")
            payload = _read_target_payload(
                handle,
                physical_entry,
                header_size,
                footer,
                active_limits,
            )
            decoded = _decompress_target(
                payload,
                physical_entry,
                compression_method,
                active_limits,
                helper,
            )
            text = _decode_config_text(decoded)
            budget.check(force=True)
            _require_unchanged_file(handle, initial)
        return UnrealPakDefaultInputResult(
            config_text=text,
            found=True,
            recognized_pak=True,
            source_paks=(resolved,),
            internal_path=target.internal_path,
            mount_point=footer.mount_point,
            pak_version=footer.layout.version,
            pak_layout=footer.layout.name,
            compression_method=compression_method,
            content_sha1=hashlib.sha1(decoded).hexdigest(),
            diagnostic=(
                "已验证并读取游戏安装包中的默认键位配置；"
                "该内容不是玩家实际改键记录。"
            ),
            scanned_paks=1,
        )
    except _PakError as exc:
        return UnrealPakDefaultInputResult(
            recognized_pak=exc.recognized,
            source_paks=(resolved,) if resolved != Path("") else (),
            pak_version=footer.layout.version if footer else None,
            pak_layout=footer.layout.name if footer else "",
            mount_point=footer.mount_point if footer else "",
            diagnostic=str(exc),
            error_code=exc.code,
            scanned_paks=1,
        )
    except (OSError, MemoryError, struct.error) as exc:
        return UnrealPakDefaultInputResult(
            recognized_pak=footer is not None,
            source_paks=(resolved,),
            pak_version=footer.layout.version if footer else None,
            pak_layout=footer.layout.name if footer else "",
            diagnostic=f"读取 Pak 时发生受控错误：{type(exc).__name__}。",
            error_code="io_or_memory_error",
            scanned_paks=1,
        )


def _scan_pak_candidates(
    game_directory: str | os.PathLike[str],
    limits: UnrealPakLimits,
) -> tuple[Path, tuple[Path, ...], int, bool]:
    root_path = Path(game_directory)
    if _is_link_or_junction(root_path) or not root_path.is_dir():
        raise _PakError(
            "invalid_game_directory",
            "游戏目录不是可安全扫描的常规目录。",
            recognized=False,
        )
    try:
        root = root_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _PakError(
            "invalid_game_directory",
            "无法解析游戏目录。",
            recognized=False,
        ) from exc

    deadline = time.monotonic() + limits.scan_timeout_seconds
    stack: list[tuple[Path, int]] = [(root, 0)]
    candidates: list[Path] = []
    inspected_entries = 0
    truncated = False

    while stack:
        if time.monotonic() > deadline:
            truncated = True
            break
        current, depth = stack.pop()
        if _is_link_or_junction(current) or not current.is_dir():
            truncated = True
            break
        try:
            iterator = os.scandir(current)
        except OSError:
            truncated = True
            break
        try:
            with iterator:
                for directory_entry in iterator:
                    inspected_entries += 1
                    if inspected_entries > limits.max_directory_entries:
                        truncated = True
                        break
                    if time.monotonic() > deadline:
                        truncated = True
                        break
                    child = Path(directory_entry.path)
                    try:
                        if directory_entry.is_symlink():
                            continue
                        is_junction = getattr(child, "is_junction", None)
                        if callable(is_junction) and is_junction():
                            continue
                        is_directory = directory_entry.is_dir(follow_symlinks=False)
                        is_file = directory_entry.is_file(follow_symlinks=False)
                    except OSError:
                        truncated = True
                        break
                    if is_directory:
                        if depth >= limits.max_scan_depth:
                            truncated = True
                            break
                        stack.append((child, depth + 1))
                    elif is_file and child.suffix.casefold() == ".pak":
                        if len(candidates) >= limits.max_paks:
                            truncated = True
                            break
                        candidates.append(child)
        except OSError:
            truncated = True
        if truncated:
            break

    ordered = tuple(sorted(candidates, key=lambda item: str(item).casefold()))
    return root, ordered, inspected_entries, truncated


def discover_default_input_from_game_directory(
    game_directory: str | os.PathLike[str],
    *,
    limits: UnrealPakLimits | None = None,
    kraken_helper: KrakenHelperConfig | None = None,
) -> UnrealPakDefaultInputResult:
    """Discover an authenticated packaged default below a game directory.

    The scan is recursive but link/junction safe and bounded by count, depth and
    time.  A truncated scan is never allowed to return a usable default because
    an unvisited patch Pak could have higher precedence.  Likewise, differing
    defaults in multiple Paks are rejected instead of guessing load order.
    """

    active_limits = limits or UnrealPakLimits()
    helper = kraken_helper or KrakenHelperConfig()
    root = Path(game_directory)
    try:
        _validate_limits(active_limits)
        root, candidates, _entry_count, truncated = _scan_pak_candidates(
            game_directory,
            active_limits,
        )
    except _PakError as exc:
        return UnrealPakDefaultInputResult(
            recognized_pak=exc.recognized,
            diagnostic=str(exc),
            error_code=exc.code,
            truncated=exc.code == "scan_truncated",
        )
    except (OSError, MemoryError) as exc:
        return UnrealPakDefaultInputResult(
            diagnostic=f"扫描游戏目录时发生受控错误：{type(exc).__name__}。",
            error_code="scan_failed",
            truncated=True,
        )

    if truncated:
        return UnrealPakDefaultInputResult(
            source_paks=candidates,
            diagnostic=(
                "Pak 候选扫描达到时间、数量或深度安全上限；"
                "为避免遗漏补丁 Pak，未采用任何打包默认键位。"
            ),
            error_code="scan_truncated",
            truncated=True,
        )

    started = time.monotonic()
    parsed_count = 0
    recognized_sources: list[Path] = []
    matches: list[UnrealPakDefaultInputResult] = []
    for candidate in candidates:
        remaining = active_limits.discovery_timeout_seconds - (time.monotonic() - started)
        if remaining <= 0:
            return UnrealPakDefaultInputResult(
                recognized_pak=bool(recognized_sources),
                source_paks=tuple(recognized_sources),
                diagnostic=(
                    "Pak 解析达到总体时间安全上限；"
                    "为避免遗漏补丁 Pak，未采用任何打包默认键位。"
                ),
                error_code="discovery_timeout",
                scanned_paks=parsed_count,
                truncated=True,
            )
        per_pak_limits = replace(
            active_limits,
            parse_timeout_seconds=min(active_limits.parse_timeout_seconds, remaining),
        )
        result = read_default_input_from_pak(
            candidate,
            limits=per_pak_limits,
            kraken_helper=helper,
        )
        parsed_count += 1
        if result.recognized_pak:
            recognized_sources.append(result.source_pak or candidate)
        if result.error_code:
            if result.error_code == "not_unreal_pak" and not result.recognized_pak:
                continue
            return replace(
                result,
                found=False,
                config_text="",
                content_sha1="",
                diagnostic=(
                    f"候选 Pak {candidate.name} 无法安全解析：{result.diagnostic}"
                ),
                scanned_paks=parsed_count,
            )
        if result.found:
            matches.append(result)

    if not matches:
        if recognized_sources:
            diagnostic = "已验证 Unreal Pak 索引，但未找到 */Config/DefaultInput.ini。"
        elif candidates:
            diagnostic = "未发现受支持的 Unreal Pak v8-v11。"
        else:
            diagnostic = "游戏目录内未找到 Pak 文件。"
        return UnrealPakDefaultInputResult(
            recognized_pak=bool(recognized_sources),
            source_paks=tuple(recognized_sources),
            diagnostic=diagnostic,
            scanned_paks=parsed_count,
        )

    first = matches[0]
    if any(
        match.content_sha1 != first.content_sha1
        or match.config_text != first.config_text
        for match in matches[1:]
    ):
        sources = tuple(
            match.source_pak
            for match in matches
            if match.source_pak is not None
        )
        return UnrealPakDefaultInputResult(
            recognized_pak=True,
            source_paks=sources,
            diagnostic=(
                "多个 Pak 包含不同的 DefaultInput.ini；"
                "无法通用且安全地推断补丁加载顺序，结果已拒绝。"
            ),
            error_code="ambiguous_defaults",
            scanned_paks=parsed_count,
        )

    sources = tuple(
        match.source_pak
        for match in matches
        if match.source_pak is not None
    )
    return replace(
        first,
        source_paks=sources,
        diagnostic=(
            "已验证并读取游戏安装包中的默认键位配置；"
            "该内容不是玩家实际改键记录。"
        ),
        scanned_paks=parsed_count,
    )


__all__ = (
    "KrakenHelperConfig",
    "UnrealPakDefaultInputResult",
    "UnrealPakLimits",
    "discover_default_input_from_game_directory",
    "read_default_input_from_pak",
)
