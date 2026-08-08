from __future__ import annotations

import os
import re
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RegistryKeymapLimits:
    max_executable_bytes: int = 64 * 1024**2
    max_executables: int = 32
    max_entries: int = 8_192
    max_depth: int = 5
    max_registry_values: int = 256
    timeout_seconds: float = 4.0


@dataclass(frozen=True)
class RegistrySchema:
    registry_path: str
    action_ids: dict[int, str]


@dataclass(frozen=True)
class RegistryKeymapResult:
    keymap: dict[str, dict[str, str]]
    registry_path: str | None
    used_registry: bool
    requires_game_launch: bool
    diagnostic: str
    recognized: bool
    executable_path: Path | None = None
    scanned_executables: int = 0
    truncated: bool = False


_ACTION_TOKEN_DETAILS: dict[str, tuple[int, str, str]] = {
    "ButtonAction_Melee": (0, "近战攻击", ""),
    "ButtonAction_Interact": (1, "互动", ""),
    "ButtonAction_Roll": (2, "翻滚或闪避", ""),
    "ButtonAction_Jump": (3, "跳跃", ""),
    "ButtonAction_Weapon_Aim": (4, "瞄准", ""),
    "ButtonAction_Weapon_Primary": (5, "主要攻击", ""),
    "ButtonAction_Instinct": (6, "生存本能", ""),
    "ButtonAction_Weapon_Alternate": (7, "次要攻击", ""),
    "ButtonAction_ShoulderSwap": (8, "切换瞄准肩位", ""),
    "ButtonAction_Weapon_Zoom": (9, "武器缩放", ""),
    "ButtonAction_Map": (11, "地图", ""),
    "ButtonAction_Weapon_Bow": (12, "选择弓箭", ""),
    "ButtonAction_Weapon_Pistol": (13, "选择手枪", ""),
    "ButtonAction_Weapon_Shotgun": (14, "选择霰弹枪", ""),
    "ButtonAction_Weapon_SMG": (15, "选择步枪", ""),
    "ButtonAction_Move_Right": (16, "向右移动", "R"),
    "ButtonAction_Move_Left": (17, "向左移动", "L"),
    "ButtonAction_Move_Back": (18, "后退", "B"),
    "ButtonAction_Move_Forward": (19, "前进", "W"),
    "ButtonAction_Walk": (20, "行走", ""),
    "ButtonAction_Weapon_Next": (21, "下一把武器", ""),
    "ButtonAction_Weapon_Prev": (22, "上一把武器", ""),
    "ButtonAction_Reload": (23, "重新装填", ""),
    "ButtonAction_TextChat": (24, "文字聊天", ""),
    "ButtonAction_TextChatTeam": (25, "队伍文字聊天", ""),
    "ButtonAction_VoicechatSpeak": (26, "语音聊天", ""),
}

_ACTION_DETAILS_BY_ID: dict[int, tuple[str, str]] = {
    action_id: (action, direction)
    for action_id, action, direction in _ACTION_TOKEN_DETAILS.values()
}
_ACTION_DETAILS_BY_ID[10] = ("暂停菜单", "")

# The defaults belong to this exact, structurally verified action-table schema.
# They are never selected by a directory name, executable name or Steam app ID.
_SCHEMA_DEFAULT_BINDINGS: dict[int, str] = {
    0: "161",
    1: "146",
    2: "170",
    3: "185",
    4: "98",
    5: "97",
    6: "144",
    7: "100,185",
    8: "174",
    9: "172",
    10: "129",
    11: "143",
    12: "130",
    13: "131",
    14: "132",
    15: "133",
    16: "333,160",
    17: "331,158",
    18: "336,159",
    19: "328,145",
    20: "157",
    21: "103",
    22: "104",
    23: "147",
    24: "148",
    25: "149",
    26: "",
}

_MOUSE_CODES: dict[int, tuple[str, str]] = {
    97: ("leftClick", "mouse"),
    98: ("rightClick", "mouse"),
    100: ("middleClick", "mouse"),
    101: ("mouseButton4", "mouse"),
    102: ("mouseButton5", "mouse"),
    103: ("mouseWheelUp", "mouse"),
    104: ("mouseWheelDown", "mouse"),
    105: ("mouseWheelLeft", "mouse"),
    106: ("mouseWheelRight", "mouse"),
}

# DirectInput DIK scan codes. Crystal/Foundation keyboard identifiers use
# DIK + 128; keeping this table explicit prevents arbitrary integers from
# becoming plausible-looking keyboard events.
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

_REQUIRED_MOUSE_TOKENS = (
    b"Input_LMouseButton\0",
    b"Input_RMouseButton\0",
    b"Input_MMouseButton\0",
    b"Input_MouseButton4\0",
    b"Input_MouseButton5\0",
    b"Input_MouseWheelUp\0",
    b"Input_MouseWheelDown\0",
    b"Input_MouseWheelLeft\0",
    b"Input_MouseWheelRight\0",
)
_REGISTRY_PATH_RE = re.compile(
    rb"(?i)(SOFTWARE\\[ -~]{3,220}?\\Controls)\x00"
)
_NOISY_EXECUTABLE_PREFIXES = (
    "crash",
    "dotnet",
    "dxsetup",
    "installer",
    "setup",
    "unins",
    "vcredist",
)


def _is_link_like(path: Path) -> bool:
    try:
        return path.is_symlink() or (
            hasattr(path, "is_junction") and path.is_junction()
        )
    except OSError:
        return True


def _u16(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 2 > len(data):
        raise ValueError("truncated integer")
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError("truncated integer")
    return struct.unpack_from("<I", data, offset)[0]


def _pe32_offset_to_va(data: bytes, file_offset: int) -> int | None:
    try:
        if data[:2] != b"MZ":
            return None
        pe_offset = _u32(data, 0x3C)
        if pe_offset > len(data) - 24 or data[pe_offset : pe_offset + 4] != b"PE\0\0":
            return None
        if _u16(data, pe_offset + 4) != 0x014C:
            return None
        section_count = _u16(data, pe_offset + 6)
        optional_size = _u16(data, pe_offset + 20)
        optional_offset = pe_offset + 24
        if optional_size < 96 or optional_offset + optional_size > len(data):
            return None
        if _u16(data, optional_offset) != 0x010B:
            return None
        image_base = _u32(data, optional_offset + 28)
        section_offset = optional_offset + optional_size
        if not 1 <= section_count <= 96:
            return None
        if section_offset + section_count * 40 > len(data):
            return None
        for index in range(section_count):
            header = section_offset + index * 40
            virtual_address = _u32(data, header + 12)
            raw_size = _u32(data, header + 16)
            raw_offset = _u32(data, header + 20)
            if raw_size == 0 or raw_offset > len(data):
                continue
            safe_size = min(raw_size, len(data) - raw_offset)
            if raw_offset <= file_offset < raw_offset + safe_size:
                return image_base + virtual_address + file_offset - raw_offset
    except (ValueError, struct.error):
        return None
    return None


def _pe32_executable_file_ranges(data: bytes) -> tuple[tuple[int, int], ...]:
    try:
        pe_offset = _u32(data, 0x3C)
        if data[:2] != b"MZ" or data[pe_offset : pe_offset + 4] != b"PE\0\0":
            return ()
        if _u16(data, pe_offset + 4) != 0x014C:
            return ()
        section_count = _u16(data, pe_offset + 6)
        optional_size = _u16(data, pe_offset + 20)
        section_offset = pe_offset + 24 + optional_size
        if not 1 <= section_count <= 96:
            return ()
        if section_offset + section_count * 40 > len(data):
            return ()
        ranges: list[tuple[int, int]] = []
        for index in range(section_count):
            header = section_offset + index * 40
            raw_size = _u32(data, header + 16)
            raw_offset = _u32(data, header + 20)
            characteristics = _u32(data, header + 36)
            if not characteristics & 0x20000000 or raw_offset >= len(data):
                continue
            safe_size = min(raw_size, len(data) - raw_offset)
            if safe_size:
                ranges.append((raw_offset, raw_offset + safe_size))
        return tuple(ranges)
    except (ValueError, struct.error):
        return ()


def _find_all(data: bytes, needle: bytes, *, limit: int = 16) -> list[int]:
    positions: list[int] = []
    offset = 0
    while len(positions) < limit:
        offset = data.find(needle, offset)
        if offset < 0:
            break
        positions.append(offset)
        offset += 1
    return positions


def _next_known_initializer_instruction(
    data: bytes,
    offset: int,
) -> tuple[int, str, int | None] | None:
    if offset >= len(data):
        return None
    opcode = data[offset]
    if opcode in {0x68, 0xA3, 0xE8} or 0xB8 <= opcode <= 0xBF:
        if offset + 5 > len(data):
            return None
        return offset + 5, "call" if opcode == 0xE8 else "other", None
    if data[offset : offset + 2] == b"\xC7\x05":
        if offset + 10 > len(data):
            return None
        return offset + 10, "constant", _u32(data, offset + 6)
    if data[offset : offset + 2] == b"\x8B\xCE":
        return offset + 2, "other", None
    return None


def _action_id_after_label_call(data: bytes, xref: int) -> int | None:
    offset = xref
    calls = 0
    candidate: int | None = None
    for _ in range(40):
        if offset - xref > 192:
            return None
        instruction = _next_known_initializer_instruction(data, offset)
        if instruction is None:
            return None
        offset, kind, value = instruction
        if kind == "call":
            calls += 1
            if calls == 2:
                return candidate
        elif kind == "constant" and calls == 1 and candidate is None:
            if value is not None and 0 <= value <= 63:
                candidate = value
    return None


def _validated_registry_paths(data: bytes) -> tuple[str, ...]:
    paths: dict[str, str] = {}
    for match in _REGISTRY_PATH_RE.finditer(data):
        try:
            raw_path = match.group(1).decode("ascii")
        except UnicodeDecodeError:
            continue
        parts = raw_path.split("\\")
        if not 3 <= len(parts) <= 8:
            continue
        if parts[0].casefold() != "software" or parts[-1].casefold() != "controls":
            continue
        if any(
            not part
            or part in {".", ".."}
            or any(character in part for character in "/:*?\"<>|")
            for part in parts
        ):
            continue
        normalized = "Software\\" + "\\".join(parts[1:])
        paths.setdefault(normalized.casefold(), normalized)
    return tuple(paths.values())


def inspect_pe_for_registry_schema(data: bytes) -> RegistrySchema | None:
    """Recognize a bounded PE32 action/registry schema without executing it."""
    if len(data) < 4096:
        return None
    registry_paths = _validated_registry_paths(data)
    if len(registry_paths) != 1:
        return None
    if any(token not in data for token in _REQUIRED_MOUSE_TOKENS):
        return None
    action_tokens = {
        match.group(0)[:-1].decode("ascii")
        for match in re.finditer(rb"ButtonAction_[A-Za-z0-9_]+\x00", data)
    }
    if action_tokens != set(_ACTION_TOKEN_DETAILS):
        return None
    executable_ranges = _pe32_executable_file_ranges(data)
    if not executable_ranges:
        return None

    discovered: dict[int, str] = {}
    for token, (expected_id, _action, _direction) in _ACTION_TOKEN_DETAILS.items():
        encoded = token.encode("ascii") + b"\0"
        string_offsets = _find_all(data, encoded, limit=3)
        if len(string_offsets) != 1:
            return None
        string_va = _pe32_offset_to_va(data, string_offsets[0])
        if string_va is None or string_va > 0xFFFFFFFF:
            return None
        xrefs = [
            xref
            for xref in _find_all(
                data,
                b"\x68" + struct.pack("<I", string_va),
                limit=4,
            )
            if any(start <= xref < end for start, end in executable_ranges)
        ]
        extracted_ids = {
            action_id
            for xref in xrefs
            if (action_id := _action_id_after_label_call(data, xref)) is not None
        }
        if extracted_ids != {expected_id} or expected_id in discovered:
            return None
        discovered[expected_id] = token

    if set(discovered) != {
        details[0] for details in _ACTION_TOKEN_DETAILS.values()
    }:
        return None
    return RegistrySchema(registry_paths[0], discovered)


def decode_crystal_binding(value: int | str) -> tuple[str, str] | None:
    """Decode one verified Crystal input identifier to a recorder input."""
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return None
    if numeric in _MOUSE_CODES:
        return _MOUSE_CODES[numeric]
    key = _DIK_KEYS.get(numeric - 128)
    if key is None:
        return None
    return key, "keyboard"


def _parse_binding_text(value: object) -> tuple[int, ...] | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    if not clean:
        return ()
    if not re.fullmatch(r"[0-9]{1,4}(?:\s*,\s*[0-9]{1,4})*", clean):
        return None
    parsed = tuple(int(part.strip()) for part in clean.split(","))
    if len(parsed) > 8 or any(number > 1024 for number in parsed):
        return None
    return parsed


def _add_mapping(
    keymap: dict[str, dict[str, str]],
    input_name: str,
    device_type: str,
    action: str,
    direction: str,
) -> None:
    existing = keymap.get(input_name)
    if existing is None:
        mapping = {"type": device_type, "action": action}
        if direction:
            mapping["movement_direction"] = direction
        keymap[input_name] = mapping
        return
    actions = [item.strip() for item in existing["action"].split(" / ")]
    if action not in actions:
        existing["action"] = " / ".join([*actions, action])[:240]
    if direction and not existing.get("movement_direction"):
        existing["movement_direction"] = direction


def _keymap_from_registry_values(
    values: dict[int, str],
) -> dict[str, dict[str, str]]:
    keymap: dict[str, dict[str, str]] = {}
    for action_id, binding_text in sorted(values.items()):
        details = _ACTION_DETAILS_BY_ID.get(action_id)
        if details is None:
            continue
        bindings = _parse_binding_text(binding_text)
        if bindings is None:
            continue
        action, direction = details
        for binding in bindings:
            decoded = decode_crystal_binding(binding)
            if decoded is None:
                continue
            input_name, device_type = decoded
            _add_mapping(
                keymap,
                input_name,
                device_type,
                action,
                direction,
            )
    return keymap


def _undecoded_registry_bindings(values: dict[int, str]) -> tuple[tuple[int, int], ...]:
    unresolved: list[tuple[int, int]] = []
    for action_id, binding_text in sorted(values.items()):
        if action_id not in _ACTION_DETAILS_BY_ID:
            continue
        bindings = _parse_binding_text(binding_text)
        if bindings is None:
            continue
        unresolved.extend(
            (action_id, binding)
            for binding in bindings
            if decode_crystal_binding(binding) is None
        )
    return tuple(unresolved)


def _valid_registry_schema(values: dict[int, str]) -> bool:
    # The game writes a complete table. Requiring the core 0..23 slots keeps a
    # coincidental numeric registry key from becoming authoritative.
    if not set(range(24)).issubset(values):
        return False
    for action_id in range(24):
        if _parse_binding_text(values[action_id]) is None:
            return False
    return True


def _read_one_registry_view(
    registry_path: str,
    winreg_module: Any,
    view_flag: int,
    limits: RegistryKeymapLimits,
) -> tuple[str, dict[int, str] | None]:
    key_read = int(getattr(winreg_module, "KEY_READ", 0x20019))
    root = getattr(winreg_module, "HKEY_CURRENT_USER")
    try:
        key = winreg_module.OpenKey(root, registry_path, 0, key_read | view_flag)
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "error", None
    values: dict[int, str] = {}
    invalid_known_value = False
    enumeration_error = False
    try:
        query_info = getattr(winreg_module, "QueryInfoKey", None)
        expected_count: int | None = None
        if callable(query_info):
            try:
                expected_count = int(query_info(key)[1])
            except OSError:
                enumeration_error = True
            if expected_count is not None and (
                expected_count < 0
                or expected_count > limits.max_registry_values
            ):
                enumeration_error = True
        index_limit = (
            expected_count
            if expected_count is not None and not enumeration_error
            else limits.max_registry_values
        )
        ended = False
        for index in range(index_limit):
            try:
                name, value, value_type = winreg_module.EnumValue(key, index)
            except OSError as exc:
                winerror = getattr(exc, "winerror", None)
                # Tests and small compatible adapters may not expose
                # QueryInfoKey or winerror; a bare OSError then means EOF.
                if expected_count is None and winerror in {None, 259}:
                    ended = True
                    break
                enumeration_error = True
                break
            if not isinstance(name, str) or not name.isdecimal():
                continue
            action_id = int(name)
            if not 0 <= action_id <= 255:
                continue
            allowed_types = {
                int(getattr(winreg_module, "REG_SZ", 1)),
                int(getattr(winreg_module, "REG_EXPAND_SZ", 2)),
            }
            if int(value_type) not in allowed_types or not isinstance(value, str):
                if action_id < 27:
                    invalid_known_value = True
                continue
            values[action_id] = value
        if expected_count is None and not ended and index_limit:
            enumeration_error = True
    finally:
        try:
            winreg_module.CloseKey(key)
        except (AttributeError, OSError):
            pass
    if enumeration_error:
        return "error", None
    if invalid_known_value or not _valid_registry_schema(values):
        return "invalid", None
    return "valid", values


def _read_registry_bindings(
    registry_path: str,
    winreg_module: Any,
    limits: RegistryKeymapLimits,
) -> tuple[str, dict[int, str] | None]:
    flags: list[int] = []
    for name in ("KEY_WOW64_32KEY", "KEY_WOW64_64KEY"):
        value = int(getattr(winreg_module, name, 0))
        if value not in flags:
            flags.append(value)
    saw_invalid = False
    saw_error = False
    for flag in flags:
        status, values = _read_one_registry_view(
            registry_path,
            winreg_module,
            flag,
            limits,
        )
        if status == "valid":
            return status, values
        saw_invalid = saw_invalid or status == "invalid"
        saw_error = saw_error or status == "error"
    if saw_error:
        return "error", None
    if saw_invalid:
        return "invalid", None
    return "missing", None


def _iter_executable_candidates(
    root: Path,
    limits: RegistryKeymapLimits,
    deadline: float,
) -> tuple[tuple[Path, ...], bool]:
    candidates: list[Path] = []
    entries = 0
    truncated = False
    root_parts = len(root.parts)
    for current_root, directory_names, file_names in os.walk(root, followlinks=False):
        if time.monotonic() >= deadline:
            truncated = True
            break
        current = Path(current_root)
        depth = len(current.parts) - root_parts
        # ``max_depth`` defines the scanner's normal search scope.  It is not
        # a consumed resource budget: large engines routinely carry unrelated
        # ThirdParty/Plugin trees below this depth.  Treating every such tree
        # as truncation made an otherwise complete, bounded scan report that
        # the directory had hit a safety limit.  Keep ``truncated`` for actual
        # exhaustion (deadline, entry count or executable count), consistent
        # with the bounded AppData/Documents discovery code.
        directory_names[:] = [
            name
            for name in directory_names
            if depth < limits.max_depth
            and not _is_link_like(current / name)
        ]
        entries += len(directory_names) + len(file_names)
        if entries > limits.max_entries:
            truncated = True
            break
        for file_name in file_names:
            if time.monotonic() >= deadline:
                truncated = True
                break
            if not file_name.casefold().endswith(".exe"):
                continue
            path = current / file_name
            if _is_link_like(path):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if not 4096 <= size <= limits.max_executable_bytes:
                continue
            candidates.append(path)
            if len(candidates) >= limits.max_executables:
                truncated = True
                break
        if len(candidates) >= limits.max_executables or entries > limits.max_entries:
            break
    candidates.sort(
        key=lambda path: (
            any(path.name.casefold().startswith(prefix) for prefix in _NOISY_EXECUTABLE_PREFIXES),
            len(path.parts) - root_parts,
            path.name.casefold(),
        )
    )
    return tuple(candidates), truncated


def discover_registry_keymap(
    game_dir: Path,
    *,
    winreg_module: Any | None = None,
    limits: RegistryKeymapLimits | None = None,
) -> RegistryKeymapResult:
    """Discover a strict Foundation/Crystal registry keymap from a game tree."""
    bounds = limits or RegistryKeymapLimits()
    root = Path(game_dir).resolve()
    if not root.is_dir():
        raise ValueError("选择的游戏目录不存在或不可访问")
    enumeration_deadline = time.monotonic() + max(
        0.1,
        float(bounds.timeout_seconds),
    )
    candidates, scan_truncated = _iter_executable_candidates(
        root,
        bounds,
        enumeration_deadline,
    )
    parsing_deadline = time.monotonic() + max(0.1, float(bounds.timeout_seconds))
    scanned = 0
    parsing_truncated = False
    for executable in candidates:
        if scanned and time.monotonic() >= parsing_deadline:
            parsing_truncated = True
            break
        scanned += 1
        try:
            with executable.open("rb") as handle:
                data = handle.read(bounds.max_executable_bytes + 1)
        except OSError:
            continue
        if len(data) > bounds.max_executable_bytes:
            continue
        schema = inspect_pe_for_registry_schema(data)
        if schema is None:
            continue
        registry = winreg_module
        if registry is None and sys.platform == "win32":
            try:
                import winreg as registry  # type: ignore[no-redef]
            except ImportError:
                registry = None
        if registry is None:
            status, registry_values = "missing", None
        else:
            status, registry_values = _read_registry_bindings(
                schema.registry_path,
                registry,
                bounds,
            )
        display_path = f"HKCU\\{schema.registry_path}"
        if status == "valid" and registry_values is not None:
            undecoded = _undecoded_registry_bindings(registry_values)
            if undecoded:
                details = "、".join(
                    f"动作槽{action_id}=输入码{binding}"
                    for action_id, binding in undecoded[:8]
                )
                if len(undecoded) > 8:
                    details += f"，另有{len(undecoded) - 8}项"
                return RegistryKeymapResult(
                    {},
                    schema.registry_path,
                    False,
                    True,
                    (
                        f"已读取 {display_path}，但发现暂不支持的实际按键码：{details}。"
                        "为避免导入不完整映射，当前表格未被替换；请手动校正或导入"
                        "已确认的 Keymap。"
                    ),
                    True,
                    executable,
                    scanned,
                )
            return RegistryKeymapResult(
                _keymap_from_registry_values(registry_values),
                schema.registry_path,
                True,
                False,
                f"已从当前用户注册表 {display_path} 读取实际键位。",
                True,
                executable,
                scanned,
            )
        if status in {"invalid", "error"}:
            reason = "结构不完整" if status == "invalid" else "当前无法读取"
            return RegistryKeymapResult(
                {},
                schema.registry_path,
                False,
                True,
                (
                    f"已识别该游戏的注册表键位格式，但 {display_path} {reason}。"
                    "请启动游戏、进入按键设置并正常退出，然后重新选择游戏目录。"
                ),
                True,
                executable,
                scanned,
            )
        return RegistryKeymapResult(
            _keymap_from_registry_values(_SCHEMA_DEFAULT_BINDINGS),
            schema.registry_path,
            False,
            True,
            (
                f"未找到当前玩家注册表 {display_path}；已根据可执行文件内完整动作表"
                "载入该格式的默认键位，这些不是玩家自定义改键。启动游戏、设置键位并"
                "正常退出后，请重新选择游戏目录以读取实际设置。"
            ),
            True,
            executable,
            scanned,
        )
    return RegistryKeymapResult(
        {},
        None,
        False,
        False,
        "",
        False,
        None,
        scanned,
        scan_truncated or parsing_truncated,
    )
