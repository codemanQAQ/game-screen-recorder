"""Strict, bounded adapter for numeric keyboard/mouse INI keymaps.

The format handled here identifies itself structurally.  A valid player file
contains a numeric ``FILE_VERSION`` in ``[META]`` and paired ``ACTION_*`` /
``ACTION_*_mod`` entries in both keyboard/mouse sections.  Executable discovery
likewise relies only on those literal format anchors plus a safe relative
``*Keys.ini`` declaration; game names, executable names and store IDs are never
used as selectors.

No executable code is loaded or run.  PE files and INI files are only read as
bounded byte strings.
"""

from __future__ import annotations

import configparser
import io
import os
import re
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


Keymap = dict[str, dict[str, str]]


@dataclass(frozen=True)
class NumericIniLimits:
    max_ini_bytes: int = 512 * 1024
    max_actions: int = 256
    min_actions: int = 8
    max_executable_bytes: int = 64 * 1024**2
    max_executables: int = 32
    max_entries: int = 8_192
    max_depth: int = 5
    timeout_seconds: float = 4.0


@dataclass(frozen=True)
class NumericIniParseResult:
    keymap: Keymap
    recognized_config: bool
    diagnostic: str = ""
    action_count: int = 0

    @property
    def recognized(self) -> bool:
        return self.recognized_config


@dataclass(frozen=True)
class NumericIniDeclaration:
    relative_paths: tuple[str, ...]
    action_names: frozenset[str]


@dataclass(frozen=True)
class NumericIniDiscoveryResult:
    keymap: Keymap
    recognized: bool
    requires_game_launch: bool
    diagnostic: str
    source_file: Path | None = None
    executable_path: Path | None = None
    declared_paths: tuple[Path, ...] = ()
    scanned_executables: int = 0
    truncated: bool = False
    has_verified_player_config: bool = False


# DirectInput keyboard scan codes.  The values in this INI format are DIK
# values themselves (unlike formats which add a private numeric base).
_DIK_KEYS: dict[int, str] = {
    0x01: "Esc",
    0x02: "1",
    0x03: "2",
    0x04: "3",
    0x05: "4",
    0x06: "5",
    0x07: "6",
    0x08: "7",
    0x09: "8",
    0x0A: "9",
    0x0B: "0",
    0x0C: "-",
    0x0D: "=",
    0x0E: "Backspace",
    0x0F: "Tab",
    0x10: "Q",
    0x11: "W",
    0x12: "E",
    0x13: "R",
    0x14: "T",
    0x15: "Y",
    0x16: "U",
    0x17: "I",
    0x18: "O",
    0x19: "P",
    0x1A: "[",
    0x1B: "]",
    0x1C: "Enter",
    0x1D: "Ctrl",
    0x1E: "A",
    0x1F: "S",
    0x20: "D",
    0x21: "F",
    0x22: "G",
    0x23: "H",
    0x24: "J",
    0x25: "K",
    0x26: "L",
    0x27: ";",
    0x28: "'",
    0x29: "Tilde",
    0x2A: "Shift",
    0x2B: "\\",
    0x2C: "Z",
    0x2D: "X",
    0x2E: "C",
    0x2F: "V",
    0x30: "B",
    0x31: "N",
    0x32: "M",
    0x33: ",",
    0x34: ".",
    0x35: "/",
    0x36: "Shift",
    0x37: "NumPadMultiply",
    0x38: "Alt",
    0x39: "Space",
    0x3A: "CapsLock",
    0x3B: "F1",
    0x3C: "F2",
    0x3D: "F3",
    0x3E: "F4",
    0x3F: "F5",
    0x40: "F6",
    0x41: "F7",
    0x42: "F8",
    0x43: "F9",
    0x44: "F10",
    0x45: "NumLock",
    0x46: "ScrollLock",
    0x47: "NumPad7",
    0x48: "NumPad8",
    0x49: "NumPad9",
    0x4A: "NumPadSubtract",
    0x4B: "NumPad4",
    0x4C: "NumPad5",
    0x4D: "NumPad6",
    0x4E: "NumPadAdd",
    0x4F: "NumPad1",
    0x50: "NumPad2",
    0x51: "NumPad3",
    0x52: "NumPad0",
    0x53: "NumPadDecimal",
    0x57: "F11",
    0x58: "F12",
    0x64: "F13",
    0x65: "F14",
    0x66: "F15",
    0x9C: "NumPadEnter",
    0x9D: "Ctrl",
    0xB5: "NumPadDivide",
    0xB7: "PrintScreen",
    0xB8: "Alt",
    0xC5: "Pause",
    0xC7: "Home",
    0xC8: "Up",
    0xC9: "PageUp",
    0xCB: "Left",
    0xCD: "Right",
    0xCF: "End",
    0xD0: "Down",
    0xD1: "PageDown",
    0xD2: "Insert",
    0xD3: "Delete",
    0xDB: "Win",
    0xDC: "Win",
    0xDD: "Apps",
}

_MOUSE_INPUTS: dict[int, tuple[str, str]] = {
    256: ("leftClick", "mouse"),
    257: ("rightClick", "mouse"),
    258: ("middleClick", "mouse"),
    # The format subtracts 0x100 before indexing its internal mouse-name
    # table.  Indices 3/4 are explicitly registered as mouseButton04/05;
    # indices 5-7 have no registered names and remain deliberately rejected.
    259: ("mouseButton4", "mouse"),
    260: ("mouseButton5", "mouse"),
    268: ("mouseWheelUp", "mouse"),
    269: ("mouseWheelDown", "mouse"),
}
_MOUSE_MOVEMENT_CODES = frozenset({264, 265, 266, 267})
_UNBOUND_CODE = 300
_CHORD_MODIFIERS = frozenset({"Ctrl", "Alt", "Shift", "Win"})


# Semantics belong to the ACTION schema, not to any game directory or AppID.
# Every emitted label is intentionally Chinese because the recorder rejects
# English action semantics at confirmation time.
_ACTION_DETAILS: dict[str, tuple[str, str]] = {
    "ACTION_Move_Forward": ("前进", "W"),
    "ACTION_Move_Back": ("后退", "B"),
    "ACTION_Move_Right": ("向右移动", "R"),
    "ACTION_Move_Left": ("向左移动", "L"),
    "ACTION_Move_Shift": ("行走", ""),
    "ACTION_Camera_Up": ("镜头向上", ""),
    "ACTION_Camera_Down": ("镜头向下", ""),
    "ACTION_Camera_Right": ("镜头向右", ""),
    "ACTION_Camera_Left": ("镜头向左", ""),
    "ACTION_Camera_Lockon": ("锁定目标或重置镜头", ""),
    "ACTION_Equip_ChangeRightWep": ("切换右手武器", ""),
    "ACTION_Equip_ChangeLeftWep": ("切换左手武器", ""),
    "ACTION_Equip_ChangeGoods": ("切换使用道具", ""),
    "ACTION_Equip_ChangeMagic": ("切换法术", ""),
    "ACTION_Action_RightAction": ("右手武器普通攻击", ""),
    "ACTION_Action_RightSubAction": ("右手武器重攻击", ""),
    "ACTION_Action_LeftAction": ("左手武器动作", ""),
    "ACTION_Action_LeftSubAction": ("左手武器特殊动作", ""),
    "ACTION_Action_UseGoods": ("使用道具", ""),
    "ACTION_Action_BackStep": ("冲刺、后撤步或翻滚", ""),
    "ACTION_Action_Jump": ("跳跃", ""),
    "ACTION_Action_Action": ("互动或确认", ""),
    "ACTION_Action_ChangeArmStyle": ("切换单持或双持", ""),
    "ACTION_MenuDisp_MenuDisp": ("打开菜单", ""),
    "ACTION_MenuDisp_Gesture": ("打开肢体动作菜单", ""),
    "ACTION_MenuCtrl_Up": ("菜单向上", ""),
    "ACTION_MenuCtrl_Down": ("菜单向下", ""),
    "ACTION_MenuCtrl_Left": ("菜单向左", ""),
    "ACTION_MenuCtrl_Right": ("菜单向右", ""),
    "ACTION_MenuCtrl_Enterv": ("菜单确认", ""),
    "ACTION_MenuCtrl_Cancel": ("菜单取消", ""),
    "ACTION_MenuCtrl_Func1": ("菜单功能一", ""),
    "ACTION_MenuCtrl_Func2": ("菜单功能二", ""),
    "ACTION_MenuCtrl_NextMenu": ("切换到下一个菜单", ""),
    "ACTION_MenuCtrl_PrevMenu": ("切换到上一个菜单", ""),
    "ACTION_MenuCtrl_ScrollUp": ("菜单向上滚动", ""),
    "ACTION_MenuCtrl_ScrollDown": ("菜单向下滚动", ""),
    "ACTION_MenuCtrl_Explanation": ("显示说明", ""),
    "ACTION_Move_WalkForward": ("步行前进", "W"),
    "ACTION_Move_WalkBack": ("步行后退", "B"),
    "ACTION_Move_WalkRight": ("步行向右", "R"),
    "ACTION_Move_WalkLeft": ("步行向左", "L"),
    "ACTION_MenuCtrl_SpecFunc1": ("菜单特殊功能一", ""),
    "ACTION_MenuCtrl_SpecFunc2": ("菜单特殊功能二", ""),
}

_ACTION_NAME_RE = re.compile(r"ACTION_[A-Za-z][A-Za-z0-9_]{1,94}\Z")
_PRIMARY_ONLY_ACTION_RE = re.compile(r"ACTION_MenuCtrl_SpecFunc[1-9][0-9]*\Z")
_INTEGER_RE = re.compile(r"[0-9]{1,3}\Z")
_SAFE_PATH_COMPONENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.\-]{0,63}\Z")
_ASCII_STRING_RE = re.compile(rb"(?<![\x20-\x7e])[\x20-\x7e]{5,260}\x00")
_PE_ACTION_RE = re.compile(
    rb"(?<![A-Za-z0-9_])(ACTION_[A-Za-z][A-Za-z0-9_]{1,94})\x00"
)
_MAX_PE_ACTION_GAP = 64
_MAX_PE_ACTION_TABLE_SPAN = 64 * 1024


def decode_numeric_input(code: int) -> tuple[str, str] | None:
    """Decode one ordinary DIK or supported mouse value.

    ``None`` is returned for unbound, mouse-motion and unsupported values.  A
    caller parsing a real binding must distinguish those categories before
    deciding whether ``None`` is a harmless skip or an error.
    """

    if code in _DIK_KEYS:
        return _DIK_KEYS[code], "keyboard"
    return _MOUSE_INPUTS.get(code)


def _decode_text(raw: bytes) -> str | None:
    try:
        if raw.startswith(b"\xff\xfe"):
            return raw[2:].decode("utf-16-le")
        if raw.startswith(b"\xfe\xff"):
            return raw[2:].decode("utf-16-be")
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _invalid(message: str, action_count: int = 0) -> NumericIniParseResult:
    return NumericIniParseResult({}, False, message, action_count)


def _section_by_name(
    parser: configparser.ConfigParser, expected: str
) -> configparser.SectionProxy | None:
    matches = [section for section in parser.sections() if section.casefold() == expected.casefold()]
    if len(matches) != 1:
        return None
    return parser[matches[0]]


def _paired_action_values(
    section: configparser.SectionProxy,
    *,
    limits: NumericIniLimits,
) -> tuple[dict[str, tuple[int, int]], str | None]:
    raw: dict[str, str] = {}
    folded: set[str] = set()
    for option, value in section.items():
        if option.casefold() in folded:
            return {}, f"[{section.name}] 存在大小写重复键：{option}"
        folded.add(option.casefold())
        if not (option.startswith("ACTION_") and (_ACTION_NAME_RE.fullmatch(option) or option.endswith("_mod"))):
            return {}, f"[{section.name}] 包含非 ACTION 数值项：{option}"
        raw[option] = value.strip()
        if len(raw) > limits.max_actions * 2:
            return {}, f"[{section.name}] 动作项超过安全上限"

    bases = {name for name in raw if not name.endswith("_mod")}
    modifiers = {name[:-4] for name in raw if name.endswith("_mod")}
    if bases != modifiers:
        missing_mod = sorted(bases - modifiers)
        missing_base = sorted(modifiers - bases)
        detail = (missing_mod or missing_base)[0] if (missing_mod or missing_base) else ""
        return {}, f"[{section.name}] ACTION 与 ACTION_mod 未成对：{detail}"
    if not limits.min_actions <= len(bases) <= limits.max_actions:
        return {}, f"[{section.name}] 动作数量不在有效范围内：{len(bases)}"

    parsed: dict[str, tuple[int, int]] = {}
    for action in sorted(bases):
        primary_text = raw[action]
        modifier_text = raw[f"{action}_mod"]
        if not _INTEGER_RE.fullmatch(primary_text) or not _INTEGER_RE.fullmatch(modifier_text):
            return {}, f"[{section.name}] {action} 不是合法十进制数值键位"
        primary = int(primary_text)
        modifier = int(modifier_text)
        if primary > _UNBOUND_CODE or modifier > _UNBOUND_CODE:
            return {}, f"[{section.name}] {action} 的键位数值超出格式范围"
        parsed[action] = (primary, modifier)
    return parsed, None


def _add_mapping(
    keymap: Keymap,
    input_name: str,
    device_type: str,
    action: str,
    direction: str,
) -> str | None:
    existing = keymap.get(input_name)
    if existing is None:
        mapping = {"type": device_type, "action": action}
        if direction:
            mapping["movement_direction"] = direction
        keymap[input_name] = mapping
        return None
    if existing.get("type") != device_type:
        return f"{input_name} 同时被识别为不同输入设备"
    existing_direction = str(existing.get("movement_direction", ""))
    if direction and existing_direction and existing_direction != direction:
        return f"{input_name} 同时绑定了冲突的移动方向"
    actions = [item.strip() for item in existing["action"].split(" / ")]
    if action not in actions:
        combined = " / ".join((*actions, action))
        if len(combined) > 240:
            return f"{input_name} 的动作语义总长度超过安全上限"
        existing["action"] = combined
    if direction and not existing_direction:
        existing["movement_direction"] = direction
    return None


def parse_numeric_keymap_ini_text(
    text: str,
    *,
    expected_actions: Iterable[str] | None = None,
    limits: NumericIniLimits | None = None,
) -> NumericIniParseResult:
    """Parse one in-memory numeric INI after validating its full structure."""

    limits = limits or NumericIniLimits()
    if not isinstance(text, str) or not text or "\x00" in text:
        return _invalid("文件不是有效的文本 INI")

    parser = configparser.ConfigParser(
        interpolation=None,
        strict=True,
        delimiters=("=",),
        comment_prefixes=("#", ";"),
        inline_comment_prefixes=None,
        empty_lines_in_values=False,
    )
    parser.optionxform = str
    try:
        parser.read_file(io.StringIO(text))
    except (configparser.Error, UnicodeError, ValueError) as exc:
        return _invalid(f"INI 语法无效：{exc}")

    meta = _section_by_name(parser, "META")
    primary_section = _section_by_name(parser, "KEYBOARD_AND_MOUSE")
    alternate_section = _section_by_name(parser, "KEYBOARD_AND_MOUSE_ALT")
    if meta is None or primary_section is None or alternate_section is None:
        return _invalid("缺少 META 或两组 KEYBOARD_AND_MOUSE 格式段")

    version_values = [
        value.strip()
        for option, value in meta.items()
        if option.casefold() == "file_version"
    ]
    if len(version_values) != 1 or not _INTEGER_RE.fullmatch(version_values[0]):
        return _invalid("[META] FILE_VERSION 必须是唯一的十进制整数")
    version = int(version_values[0])
    if not 1 <= version <= 999:
        return _invalid("[META] FILE_VERSION 超出有效范围")

    primary, error = _paired_action_values(primary_section, limits=limits)
    if error:
        return _invalid(error)
    alternate, error = _paired_action_values(alternate_section, limits=limits)
    if error:
        return _invalid(error, len(primary))

    primary_actions = set(primary)
    alternate_actions = set(alternate)
    # This schema deliberately omits its numbered menu-special functions from
    # the alternate table; every other ACTION must be present.  This is an
    # exact schema rule, not a permissive arbitrary-subset fallback.
    primary_only_actions = {
        action
        for action in primary_actions
        if _PRIMARY_ONLY_ACTION_RE.fullmatch(action)
    }
    expected_alternate_actions = primary_actions - primary_only_actions
    if alternate_actions != expected_alternate_actions:
        return _invalid(
            "备用键位段 ACTION 表不符合主表的精确 schema 关系",
            len(primary),
        )
    if expected_actions is not None:
        expected = frozenset(expected_actions)
        if primary_actions != expected:
            return _invalid("INI ACTION 表与可执行文件声明不一致", len(primary))

    keymap: Keymap = {}
    for section_name, bindings in (
        (primary_section.name, primary),
        (alternate_section.name, alternate),
    ):
        for action_name, (primary_code, modifier_code) in bindings.items():
            if primary_code == _UNBOUND_CODE or primary_code in _MOUSE_MOVEMENT_CODES:
                if modifier_code != _UNBOUND_CODE:
                    return _invalid(
                        f"[{section_name}] {action_name} 的主键未绑定或为鼠标移动，"
                        "但修饰键并非 300",
                        len(primary),
                    )
                continue
            decoded = decode_numeric_input(primary_code)
            if decoded is None:
                return _invalid(
                    f"[{section_name}] {action_name} 使用了未知输入码 {primary_code}",
                    len(primary),
                )
            details = _ACTION_DETAILS.get(action_name)
            if details is None:
                return _invalid(
                    f"[{section_name}] {action_name} 尚无可靠中文动作语义",
                    len(primary),
                )
            input_name, device_type = decoded
            if modifier_code != _UNBOUND_CODE:
                modifier = _DIK_KEYS.get(modifier_code)
                if modifier not in _CHORD_MODIFIERS:
                    return _invalid(
                        f"[{section_name}] {action_name} 使用了不支持的修饰键码 {modifier_code}",
                        len(primary),
                    )
                if input_name in _CHORD_MODIFIERS:
                    return _invalid(
                        f"[{section_name}] {action_name} 的组合键缺少普通主键",
                        len(primary),
                    )
                input_name = f"{modifier}+{input_name}"
            action, direction = details
            conflict = _add_mapping(
                keymap, input_name, device_type, action, direction
            )
            if conflict:
                return _invalid(conflict, len(primary))

    return NumericIniParseResult(
        keymap,
        True,
        f"已读取玩家数值 INI 键位（版本 {version}，{len(primary)} 项动作）。",
        len(primary),
    )


def parse_numeric_keymap_ini(
    path: str | Path,
    *,
    expected_actions: Iterable[str] | None = None,
    limits: NumericIniLimits | None = None,
) -> NumericIniParseResult:
    """Read and strictly parse one bounded numeric keymap INI file."""

    limits = limits or NumericIniLimits()
    source = Path(path)
    try:
        size = source.stat().st_size
        if size <= 0 or size > limits.max_ini_bytes:
            return _invalid("INI 文件大小不在安全范围内")
        raw = source.read_bytes()
    except OSError as exc:
        return _invalid(f"无法读取 INI：{exc}")
    if len(raw) > limits.max_ini_bytes:
        return _invalid("INI 文件超过安全上限")
    text = _decode_text(raw)
    if text is None:
        return _invalid("INI 文本编码无效")
    return parse_numeric_keymap_ini_text(
        text, expected_actions=expected_actions, limits=limits
    )


def _safe_relative_keys_path(value: str) -> str | None:
    if not value or len(value) > 240 or value.startswith(("/", "\\")):
        return None
    if any(character in value for character in (":", "%", "\x00")):
        return None
    parts = value.replace("/", "\\").split("\\")
    if not 1 <= len(parts) <= 8:
        return None
    if any(
        part in {"", ".", ".."} or not _SAFE_PATH_COMPONENT_RE.fullmatch(part)
        for part in parts
    ):
        return None
    if not parts[-1].casefold().endswith("keys.ini"):
        return None
    return "\\".join(parts)


def _pe_readable_section_ranges(data: bytes) -> tuple[tuple[int, int], ...] | None:
    if len(data) < 0x100 or data[:2] != b"MZ":
        return None
    try:
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        if pe_offset < 0x40 or pe_offset + 24 > len(data):
            return None
        if data[pe_offset : pe_offset + 4] != b"PE\0\0":
            return None
        machine, section_count = struct.unpack_from("<HH", data, pe_offset + 4)
        optional_size = struct.unpack_from("<H", data, pe_offset + 20)[0]
        if machine not in {0x014C, 0x8664} or not 1 <= section_count <= 96:
            return None
        optional_offset = pe_offset + 24
        if optional_offset + optional_size > len(data) or optional_size < 2:
            return None
        magic = struct.unpack_from("<H", data, optional_offset)[0]
        if magic not in {0x010B, 0x020B}:
            return None
        section_table = optional_offset + optional_size
        if section_table + section_count * 40 > len(data):
            return None
        raw_ranges: list[tuple[int, int]] = []
        readable_ranges: list[tuple[int, int]] = []
        for index in range(section_count):
            offset = section_table + index * 40
            raw_size, raw_offset = struct.unpack_from("<II", data, offset + 16)
            characteristics = struct.unpack_from("<I", data, offset + 36)[0]
            if raw_size:
                if (
                    raw_offset < section_table + section_count * 40
                    or raw_offset > len(data)
                    or raw_size > len(data) - raw_offset
                ):
                    return None
                raw_range = (raw_offset, raw_offset + raw_size)
                raw_ranges.append(raw_range)
                if characteristics & 0x40000000:  # IMAGE_SCN_MEM_READ
                    readable_ranges.append(raw_range)
        ordered = sorted(raw_ranges)
        if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
            return None
        return tuple(readable_ranges) or None
    except (IndexError, struct.error):
        return None


def _range_is_in_section(
    start: int,
    end: int,
    section_ranges: tuple[tuple[int, int], ...],
) -> bool:
    return any(section_start <= start and end <= section_end
               for section_start, section_end in section_ranges)


def _literal_is_in_section(
    data: bytes,
    literal: bytes,
    section_ranges: tuple[tuple[int, int], ...],
) -> bool:
    start = 0
    while True:
        offset = data.find(literal, start)
        if offset < 0:
            return False
        preceding = data[offset - 1] if offset else 0
        if (
            not (preceding == 0x5F or 0x30 <= preceding <= 0x39
                 or 0x41 <= preceding <= 0x5A or 0x61 <= preceding <= 0x7A)
            and _range_is_in_section(offset, offset + len(literal), section_ranges)
        ):
            return True
        start = offset + 1


def _continuous_action_table(
    data: bytes,
    section_ranges: tuple[tuple[int, int], ...],
) -> tuple[frozenset[str], tuple[int, int]] | None:
    matches = [
        match
        for match in _PE_ACTION_RE.finditer(data)
        if _range_is_in_section(match.start(), match.end(), section_ranges)
    ]
    if not matches:
        return None
    runs: list[list[re.Match[bytes]]] = []
    current = [matches[0]]
    for match in matches[1:]:
        previous = current[-1]
        if (
            match.start() - previous.end() <= _MAX_PE_ACTION_GAP
            and match.end() - current[0].start() <= _MAX_PE_ACTION_TABLE_SPAN
        ):
            current.append(match)
        else:
            runs.append(current)
            current = [match]
    runs.append(current)

    candidates: list[tuple[frozenset[str], tuple[int, int]]] = []
    for run in runs:
        actions = frozenset(match.group(1).decode("ascii") for match in run)
        if len(actions) != len(run) or not 8 <= len(actions) <= 256:
            continue
        if not (
            any(action.startswith("ACTION_Move_") for action in actions)
            and any(action.startswith("ACTION_Action_") for action in actions)
            and any(action.startswith("ACTION_Menu") for action in actions)
        ):
            continue
        candidates.append((actions, (run[0].start(), run[-1].end())))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-len(item[0]), item[1][1] - item[1][0]))
    if len(candidates) > 1 and len(candidates[0][0]) == len(candidates[1][0]):
        return None
    return candidates[0]


def inspect_pe_for_numeric_ini_declaration(data: bytes) -> NumericIniDeclaration | None:
    """Return a format declaration only when all strong PE anchors coexist."""

    if len(data) > NumericIniLimits().max_executable_bytes:
        return None
    section_ranges = _pe_readable_section_ranges(data)
    if section_ranges is None:
        return None
    required_literals = (
        b"KEYBOARD_AND_MOUSE\x00",
        b"KEYBOARD_AND_MOUSE_ALT\x00",
        b"FILE_VERSION\x00",
        b"META\x00",
        b"_mod\x00",
    )
    if any(
        not _literal_is_in_section(data, literal, section_ranges)
        for literal in required_literals
    ):
        return None

    action_table = _continuous_action_table(data, section_ranges)
    if action_table is None:
        return None
    actions, _action_range = action_table

    relative_paths: list[str] = []
    seen: set[str] = set()
    for match in _ASCII_STRING_RE.finditer(data):
        if not _range_is_in_section(match.start(), match.end(), section_ranges):
            continue
        value = match.group()[:-1].decode("ascii")
        safe = _safe_relative_keys_path(value)
        if safe is not None and safe.casefold() not in seen:
            seen.add(safe.casefold())
            relative_paths.append(safe)
    if not relative_paths:
        return None
    return NumericIniDeclaration(tuple(relative_paths), actions)


def inspect_numeric_ini_executable(
    path: str | Path,
    *,
    limits: NumericIniLimits | None = None,
) -> NumericIniDeclaration | None:
    """Read one bounded executable and inspect its numeric-INI declaration."""

    limits = limits or NumericIniLimits()
    executable = Path(path)
    try:
        size = executable.stat().st_size
        if size <= 0 or size > limits.max_executable_bytes:
            return None
        data = executable.read_bytes()
    except OSError:
        return None
    if len(data) > limits.max_executable_bytes:
        return None
    return inspect_pe_for_numeric_ini_declaration(data)


def _is_junction(path: Path) -> bool:
    checker = getattr(path, "is_junction", None)
    if checker is None:
        return False
    try:
        return bool(checker())
    except OSError:
        return True


def _candidate_executables(
    root: Path,
    limits: NumericIniLimits,
    deadline: float,
) -> tuple[list[Path], bool]:
    candidates: list[Path] = []
    entries_seen = 0
    truncated = False
    pending: list[tuple[Path, int]] = [(root, 0)]
    while pending:
        if time.monotonic() > deadline:
            truncated = True
            break
        directory, depth = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name.casefold())
        except OSError:
            continue
        for entry in entries:
            entries_seen += 1
            if entries_seen > limits.max_entries:
                truncated = True
                return candidates, truncated
            try:
                if entry.is_file(follow_symlinks=False) and entry.name.casefold().endswith(".exe"):
                    candidates.append(Path(entry.path))
                    if len(candidates) >= limits.max_executables:
                        truncated = True
                        return candidates, truncated
                elif (
                    depth < limits.max_depth
                    and entry.is_dir(follow_symlinks=False)
                    and not entry.is_symlink()
                    and not _is_junction(Path(entry.path))
                ):
                    pending.append((Path(entry.path), depth + 1))
            except OSError:
                continue
    return candidates, truncated


def _declared_local_paths(
    local_appdata: Path, declaration: NumericIniDeclaration
) -> tuple[Path, ...]:
    try:
        base = local_appdata.resolve(strict=False)
    except OSError:
        return ()
    resolved: list[Path] = []
    for relative in declaration.relative_paths:
        candidate = base.joinpath(*relative.split("\\"))
        try:
            candidate_resolved = candidate.resolve(strict=False)
            if os.path.commonpath((str(base), str(candidate_resolved))) != str(base):
                continue
        except (OSError, ValueError):
            continue
        resolved.append(candidate_resolved)
    return tuple(resolved)


def discover_numeric_ini_keymap(
    game_directory: str | Path,
    *,
    local_appdata: str | Path | None = None,
    limits: NumericIniLimits | None = None,
) -> NumericIniDiscoveryResult:
    """Discover a declared player INI without using game-specific selectors."""

    limits = limits or NumericIniLimits()
    root = Path(game_directory)
    if not root.is_dir():
        return NumericIniDiscoveryResult(
            {}, False, False, "所选游戏目录不存在或不可读取。"
        )

    enumeration_deadline = time.monotonic() + limits.timeout_seconds
    executables, truncated = _candidate_executables(
        root, limits, enumeration_deadline
    )
    declaration: NumericIniDeclaration | None = None
    executable_path: Path | None = None
    scanned = 0
    parse_deadline = time.monotonic() + limits.timeout_seconds
    for executable in executables:
        if scanned and time.monotonic() > parse_deadline:
            truncated = True
            break
        scanned += 1
        declaration = inspect_numeric_ini_executable(executable, limits=limits)
        if declaration is not None:
            executable_path = executable
            break
    if declaration is None:
        return NumericIniDiscoveryResult(
            {}, False, False, "", scanned_executables=scanned, truncated=truncated
        )

    local_root_value = local_appdata
    if local_root_value is None:
        local_root_value = os.environ.get("LOCALAPPDATA")
    if not local_root_value:
        return NumericIniDiscoveryResult(
            {},
            True,
            True,
            "已识别数值 INI 键位格式，但系统未提供 LOCALAPPDATA，无法定位玩家配置。",
            executable_path=executable_path,
            scanned_executables=scanned,
            truncated=truncated,
        )

    declared_paths = _declared_local_paths(Path(local_root_value), declaration)
    existing: list[Path] = []
    for path in declared_paths:
        try:
            if path.is_file() and not path.is_symlink():
                existing.append(path)
        except OSError:
            continue
    if not existing:
        return NumericIniDiscoveryResult(
            {},
            True,
            True,
            "未找到可执行文件声明的玩家键位 INI。游戏可能尚未首次启动；"
            "请启动游戏、进入按键设置并正常退出后重新选择游戏目录。",
            executable_path=executable_path,
            declared_paths=declared_paths,
            scanned_executables=scanned,
            truncated=truncated,
        )

    try:
        existing.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
    except OSError:
        existing.sort(key=lambda path: str(path).casefold())
    parse_errors: list[str] = []
    for source in existing:
        parsed = parse_numeric_keymap_ini(
            source,
            expected_actions=declaration.action_names,
            limits=limits,
        )
        if parsed.recognized_config:
            return NumericIniDiscoveryResult(
                parsed.keymap,
                True,
                False,
                parsed.diagnostic,
                source_file=source,
                executable_path=executable_path,
                declared_paths=declared_paths,
                scanned_executables=scanned,
                truncated=truncated,
                has_verified_player_config=True,
            )
        parse_errors.append(f"{source.name}：{parsed.diagnostic}")

    return NumericIniDiscoveryResult(
        {},
        True,
        False,
        "已找到玩家键位 INI，但严格校验失败；为避免错误映射，未导入。"
        + (f" {parse_errors[0]}" if parse_errors else ""),
        source_file=existing[0],
        executable_path=executable_path,
        declared_paths=declared_paths,
        scanned_executables=scanned,
        truncated=truncated,
    )


__all__ = [
    "NumericIniDeclaration",
    "NumericIniDiscoveryResult",
    "NumericIniLimits",
    "NumericIniParseResult",
    "decode_numeric_input",
    "discover_numeric_ini_keymap",
    "inspect_numeric_ini_executable",
    "inspect_pe_for_numeric_ini_declaration",
    "parse_numeric_keymap_ini",
    "parse_numeric_keymap_ini_text",
]
