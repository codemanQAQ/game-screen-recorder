"""Strict, bounded parser for player key mappings stored in Unreal GVAS files.

The parser recognizes the save structurally.  It does not select a game by its
directory name, executable name or store ID.  A supported player options save
has an ``*OptionSaveGame`` class, an ``OptionSaveData`` tagged struct, and may
contain a ``*KeyConfigSettings`` tagged struct.  The latter can contain the
mouse/keyboard Action, Axis and UI mapping collections used by this schema.

Only the legacy UE 4.12--5.3 tagged-property representation is accepted.  All
reads and collection counts are bounded.  Unknown active key values or an
ambiguous collection layout reject the complete key map instead of producing
plausible but unverified bindings.
"""

from __future__ import annotations

import os
import re
import struct
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable


Keymap = dict[str, dict[str, str]]


@dataclass(frozen=True)
class GvasKeymapLimits:
    max_file_bytes: int = 16 * 1024 * 1024
    max_string_bytes: int = 256 * 1024
    max_string_chars: int = 65_536
    max_custom_versions: int = 2_048
    max_properties: int = 8_192
    max_depth: int = 32
    max_mappings: int = 4_096
    max_candidates: int = 64
    max_directory_entries: int = 4_096
    timeout_seconds: float = 4.0


@dataclass(frozen=True)
class GvasPlayerKeymapResult:
    keymap: Keymap
    recognized: bool
    has_player_file: bool
    needs_key_change: bool
    diagnostic: str
    source_file: Path | None = None
    has_verified_player_config: bool = False
    unsupported_schema: bool = False
    gvas_class: str = ""
    mapping_count: int = 0
    scanned_files: int = 0
    truncated: bool = False
    has_key_config: bool = False

    @property
    def recognized_config(self) -> bool:
        return self.recognized


@dataclass(frozen=True)
class _Header:
    engine_major: int
    engine_minor: int
    save_class: str


@dataclass(frozen=True)
class _Tag:
    name: str
    type_name: str
    size: int
    array_index: int
    payload: bytes
    struct_name: str = ""
    enum_name: str = ""
    inner_type: str = ""
    key_type: str = ""
    value_type: str = ""
    bool_value: bool | None = None


class _ParseError(ValueError):
    pass


class _UnsupportedSchema(_ParseError):
    pass


class _Budget:
    def __init__(self, limits: GvasKeymapLimits) -> None:
        self.limits = limits
        self.deadline = time.monotonic() + limits.timeout_seconds
        self.properties = 0

    def check(self) -> None:
        if time.monotonic() > self.deadline:
            raise _ParseError("解析超时")

    def property(self) -> None:
        self.check()
        self.properties += 1
        if self.properties > self.limits.max_properties:
            raise _ParseError("属性数量超过安全上限")


class _Reader:
    def __init__(
        self,
        data: bytes,
        budget: _Budget,
        *,
        label: str = "GVAS",
    ) -> None:
        self.data = data
        self.pos = 0
        self.budget = budget
        self.label = label

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos

    def read(self, size: int) -> bytes:
        self.budget.check()
        if size < 0 or size > self.remaining:
            raise _ParseError(f"{self.label} 数据被截断")
        start = self.pos
        self.pos += size
        return self.data[start : start + size]

    def u8(self) -> int:
        return self.read(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.read(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.read(4))[0]

    def fstring(self) -> str:
        length = self.i32()
        if length == 0:
            return ""
        if length == -(2**31):
            raise _ParseError("FString 长度无效")
        wide = length < 0
        units = -length if wide else length
        if units < 1 or units > self.budget.limits.max_string_chars + 1:
            raise _ParseError("FString 长度超过安全上限")
        byte_count = units * (2 if wide else 1)
        if byte_count > self.budget.limits.max_string_bytes:
            raise _ParseError("FString 字节数超过安全上限")
        raw = self.read(byte_count)
        terminator = b"\x00\x00" if wide else b"\x00"
        if not raw.endswith(terminator):
            raise _ParseError("FString 缺少结尾空字符")
        body = raw[: -len(terminator)]
        try:
            text = body.decode("utf-16-le" if wide else "utf-8")
        except UnicodeDecodeError as exc:
            raise _ParseError("FString 编码无效") from exc
        if "\x00" in text:
            raise _ParseError("FString 含嵌入空字符")
        return text


_PROPERTY_TYPES = frozenset(
    {
        "ArrayProperty",
        "BoolProperty",
        "ByteProperty",
        "DoubleProperty",
        "EnumProperty",
        "FloatProperty",
        "Int16Property",
        "Int64Property",
        "Int8Property",
        "IntProperty",
        "MapProperty",
        "NameProperty",
        "ObjectProperty",
        "SetProperty",
        "SoftObjectProperty",
        "StrProperty",
        "StructProperty",
        "TextProperty",
        "UInt16Property",
        "UInt32Property",
        "UInt64Property",
        "UInt8Property",
    }
)


def _read_header(reader: _Reader) -> _Header:
    if reader.read(4) != b"GVAS":
        raise _ParseError("不是 GVAS 文件")
    save_version = reader.u32()
    if not 1 <= save_version <= 10:
        raise _ParseError("GVAS SaveGame 版本不受支持")
    reader.u32()  # UE4 package version
    if save_version >= 3:
        reader.u32()  # UE5 package version
    engine_major = reader.u16()
    engine_minor = reader.u16()
    reader.u16()  # patch
    reader.u32()  # changelist/build
    reader.fstring()  # branch
    if not 4 <= engine_major <= 6:
        raise _ParseError("Unreal Engine 版本不受支持")
    if (engine_major, engine_minor) < (4, 12):
        raise _UnsupportedSchema("不支持 UE 4.12 之前的无属性 GUID 格式")
    custom_format = reader.u32()
    if custom_format > 16:
        raise _ParseError("自定义版本格式无效")
    custom_count = reader.u32()
    if custom_count > reader.budget.limits.max_custom_versions:
        raise _ParseError("自定义版本数量超过安全上限")
    reader.read(custom_count * 20)  # GUID + int32 version
    save_class = reader.fstring()
    if (engine_major, engine_minor) >= (5, 4):
        raise _UnsupportedSchema("暂不支持 UE 5.4 及以上的 CompleteTypeName 属性标签")
    return _Header(engine_major, engine_minor, save_class)


def _read_optional_guid(reader: _Reader) -> None:
    marker = reader.u8()
    if marker not in (0, 1):
        raise _ParseError("属性 GUID 标记无效")
    if marker:
        reader.read(16)


def _read_tag(reader: _Reader) -> _Tag | None:
    name = reader.fstring()
    if name == "None":
        return None
    if not name or len(name) > 512:
        raise _ParseError("属性名无效")
    reader.budget.property()
    type_name = reader.fstring()
    if type_name not in _PROPERTY_TYPES:
        raise _UnsupportedSchema(f"不支持的属性类型：{type_name or '<空>'}")
    size = reader.u32()
    array_index = reader.u32()
    if size > reader.remaining:
        raise _ParseError(f"属性 {name} 的长度越界")

    struct_name = ""
    enum_name = ""
    inner_type = ""
    key_type = ""
    value_type = ""
    bool_value: bool | None = None
    if type_name == "StructProperty":
        struct_name = reader.fstring()
        reader.read(16)
    elif type_name == "BoolProperty":
        raw_bool = reader.u8()
        if raw_bool not in (0, 1):
            raise _ParseError(f"布尔属性 {name} 的值无效")
        bool_value = bool(raw_bool)
    elif type_name in {"ByteProperty", "EnumProperty"}:
        enum_name = reader.fstring()
    elif type_name in {"ArrayProperty", "SetProperty"}:
        inner_type = reader.fstring()
        if inner_type not in _PROPERTY_TYPES:
            raise _UnsupportedSchema(f"属性 {name} 的元素类型不受支持：{inner_type}")
    elif type_name == "MapProperty":
        key_type = reader.fstring()
        value_type = reader.fstring()
        if key_type not in _PROPERTY_TYPES or value_type not in _PROPERTY_TYPES:
            raise _UnsupportedSchema(f"映射属性 {name} 的键值类型不受支持")

    _read_optional_guid(reader)
    if size > reader.remaining:
        raise _ParseError(f"属性 {name} 的数据长度越界")
    payload = reader.read(size)
    return _Tag(
        name=name,
        type_name=type_name,
        size=size,
        array_index=array_index,
        payload=payload,
        struct_name=struct_name,
        enum_name=enum_name,
        inner_type=inner_type,
        key_type=key_type,
        value_type=value_type,
        bool_value=bool_value,
    )


def _read_tag_stream(reader: _Reader, *, depth: int) -> list[_Tag]:
    if depth > reader.budget.limits.max_depth:
        raise _ParseError("属性嵌套深度超过安全上限")
    tags: list[_Tag] = []
    while True:
        tag = _read_tag(reader)
        if tag is None:
            return tags
        tags.append(tag)


def _parse_struct_payload(tag: _Tag, budget: _Budget, *, depth: int) -> list[_Tag]:
    if tag.type_name != "StructProperty":
        raise _UnsupportedSchema(f"{tag.name} 不是结构属性")
    reader = _Reader(tag.payload, budget, label=tag.name)
    tags = _read_tag_stream(reader, depth=depth)
    if reader.remaining:
        raise _UnsupportedSchema(f"{tag.name} 结构末尾存在未解析数据")
    return tags


def _class_tail(value: str) -> str:
    return value.rsplit(".", 1)[-1].rsplit("/", 1)[-1]


def _is_player_option_document(header: _Header, root_tags: list[_Tag]) -> _Tag | None:
    if not _class_tail(header.save_class).casefold().endswith("optionsavegame"):
        return None
    matches = [
        tag
        for tag in root_tags
        if tag.name.casefold() == "optionsavedata"
        and tag.type_name == "StructProperty"
        and _class_tail(tag.struct_name).casefold().endswith("optionsavedata")
    ]
    if len(matches) != 1:
        return None
    return matches[0]


_MOUSE_KEYS = {
    "leftmousebutton": ("leftClick", "mouse"),
    "rightmousebutton": ("rightClick", "mouse"),
    "middlemousebutton": ("middleClick", "mouse"),
    "thumbmousebutton": ("mouseButton4", "mouse"),
    "thumbmousebutton2": ("mouseButton5", "mouse"),
    "mousescrollup": ("mouseWheelUp", "mouse"),
    "mousescrolldown": ("mouseWheelDown", "mouse"),
    "mousewheelup": ("mouseWheelUp", "mouse"),
    "mousewheeldown": ("mouseWheelDown", "mouse"),
    "mousescrollleft": ("mouseWheelLeft", "mouse"),
    "mousescrollright": ("mouseWheelRight", "mouse"),
    "mousewheelleft": ("mouseWheelLeft", "mouse"),
    "mousewheelright": ("mouseWheelRight", "mouse"),
}

_KEY_ALIASES = {
    "spacebar": "Space",
    "space": "Space",
    "escape": "Esc",
    "esc": "Esc",
    "return": "Enter",
    "enter": "Enter",
    "backspace": "Backspace",
    "tab": "Tab",
    "capslock": "CapsLock",
    "numlock": "NumLock",
    "scrolllock": "ScrollLock",
    "leftshift": "Shift",
    "rightshift": "Shift",
    "leftcontrol": "Ctrl",
    "rightcontrol": "Ctrl",
    "leftctrl": "Ctrl",
    "rightctrl": "Ctrl",
    "leftalt": "Alt",
    "rightalt": "Alt",
    "leftcommand": "Win",
    "rightcommand": "Win",
    "leftwindows": "Win",
    "rightwindows": "Win",
    "lwin": "Win",
    "rwin": "Win",
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "numpadzero": "NumPad0",
    "numpadone": "NumPad1",
    "numpadtwo": "NumPad2",
    "numpadthree": "NumPad3",
    "numpadfour": "NumPad4",
    "numpadfive": "NumPad5",
    "numpadsix": "NumPad6",
    "numpadseven": "NumPad7",
    "numpadeight": "NumPad8",
    "numpadnine": "NumPad9",
    "up": "Up",
    "uparrow": "Up",
    "down": "Down",
    "downarrow": "Down",
    "left": "Left",
    "leftarrow": "Left",
    "right": "Right",
    "rightarrow": "Right",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "home": "Home",
    "end": "End",
    "insert": "Insert",
    "delete": "Delete",
    "pause": "Pause",
    "printscreen": "PrintScreen",
    "snapshot": "PrintScreen",
    "apps": "Apps",
    "applicationkey": "Apps",
    "numpadmultiply": "NumPadMultiply",
    "multiply": "NumPadMultiply",
    "numpadadd": "NumPadAdd",
    "add": "NumPadAdd",
    "numpadsubtract": "NumPadSubtract",
    "subtract": "NumPadSubtract",
    "numpaddecimal": "NumPadDecimal",
    "decimal": "NumPadDecimal",
    "numpaddivide": "NumPadDivide",
    "divide": "NumPadDivide",
    "numpadenter": "NumPadEnter",
    "tilde": "Tilde",
    "backquote": "Tilde",
    "grave": "Tilde",
    "hyphen": "-",
    "minus": "-",
    "equals": "=",
    "leftbracket": "[",
    "rightbracket": "]",
    "backslash": "\\",
    "semicolon": ";",
    "apostrophe": "'",
    "comma": ",",
    "period": ".",
    "slash": "/",
}

_UNBOUND_KEYS = frozenset({"", "none", "invalid", "invalidkey", "e.keys::invalid"})
_NON_BUTTON_INPUTS = frozenset(
    {
        "mousex",
        "mousey",
        "mouse2d",
        "mousewheelaxis",
    }
)
_SAFE_ACTION = re.compile(r"^[\w .:/+\-]{1,256}$", re.UNICODE)


def _normalize_key(value: str) -> tuple[str, str] | None:
    raw = value.strip()
    folded = raw.casefold()
    if folded in _UNBOUND_KEYS or folded in _NON_BUTTON_INPUTS:
        return None
    if folded in _MOUSE_KEYS:
        return _MOUSE_KEYS[folded]
    if folded in _KEY_ALIASES:
        return _KEY_ALIASES[folded], "keyboard"
    if len(raw) == 1 and raw.isascii() and (raw.isalnum() or raw in "-=[]\\;',./"):
        return raw.upper() if raw.isalpha() else raw, "keyboard"
    if re.fullmatch(r"F(?:[1-9]|1[0-9]|2[0-4])", raw, re.IGNORECASE):
        return raw.upper(), "keyboard"
    numpad = re.fullmatch(r"NumPad([0-9])", raw, re.IGNORECASE)
    if numpad:
        return f"NumPad{numpad.group(1)}", "keyboard"
    raise _UnsupportedSchema(f"无法确认 Unreal FKey：{raw!r}")


def _one_fstring(payload: bytes, budget: _Budget, label: str) -> str:
    reader = _Reader(payload, budget, label=label)
    value = reader.fstring()
    if reader.remaining:
        raise _UnsupportedSchema(f"{label} 不是单一 FName/FString")
    return value


def _key_from_tag(tag: _Tag, budget: _Budget, *, depth: int) -> tuple[str, str] | None:
    if tag.type_name in {"NameProperty", "StrProperty", "EnumProperty"}:
        return _normalize_key(_one_fstring(tag.payload, budget, tag.name))
    if tag.type_name != "StructProperty" or not _class_tail(tag.struct_name).casefold().endswith("key"):
        raise _UnsupportedSchema(f"{tag.name} 不是受支持的 FKey 属性")
    try:
        return _normalize_key(_one_fstring(tag.payload, budget, tag.name))
    except _ParseError:
        nested = _parse_struct_payload(tag, budget, depth=depth + 1)
        names = {"keyname", "name"}
        candidates = [item for item in nested if item.name.casefold() in names]
        if len(candidates) != 1 or len(nested) != 1:
            raise _UnsupportedSchema(f"{tag.name} 的 FKey 结构无法唯一确认")
        return _key_from_tag(candidates[0], budget, depth=depth + 1)


def _validate_action(value: str) -> str:
    action = value.strip()
    if not _SAFE_ACTION.fullmatch(action) or action.casefold() in _UNBOUND_KEYS:
        raise _UnsupportedSchema(f"动作标识无效：{value!r}")
    return action


def _action_from_scalar(tag: _Tag, budget: _Budget) -> str:
    if tag.type_name not in {"NameProperty", "StrProperty", "EnumProperty"}:
        raise _UnsupportedSchema(f"{tag.name} 不是动作名称属性")
    return _validate_action(_one_fstring(tag.payload, budget, tag.name))


def _binding_struct(
    tags: list[_Tag],
    budget: _Budget,
    *,
    fallback_action: str | None,
    require_axis_name: bool,
    depth: int,
) -> tuple[str, list[tuple[str, str]]]:
    allowed = {"mainkey", "secondarykey", "axisname", "actionname", "name"}
    if any(tag.name.casefold() not in allowed for tag in tags):
        unknown = next(tag.name for tag in tags if tag.name.casefold() not in allowed)
        raise _UnsupportedSchema(f"键位结构含未知字段：{unknown}")
    by_name: dict[str, _Tag] = {}
    for tag in tags:
        folded = tag.name.casefold()
        if folded in by_name:
            raise _UnsupportedSchema(f"键位结构字段重复：{tag.name}")
        by_name[folded] = tag
    action = fallback_action
    for name in ("axisname", "actionname", "name"):
        if name in by_name:
            parsed = _action_from_scalar(by_name[name], budget)
            if action is not None and action != parsed:
                raise _UnsupportedSchema("映射键与结构内动作名称不一致")
            action = parsed
    if require_axis_name and "axisname" not in by_name:
        raise _UnsupportedSchema("轴映射缺少 AxisName")
    if action is None:
        raise _UnsupportedSchema("键位结构缺少动作名称")
    if "mainkey" not in by_name:
        raise _UnsupportedSchema(f"动作 {action} 缺少 MainKey")
    keys: list[tuple[str, str]] = []
    for name in ("mainkey", "secondarykey"):
        if name in by_name:
            parsed_key = _key_from_tag(by_name[name], budget, depth=depth + 1)
            if parsed_key is not None and parsed_key not in keys:
                keys.append(parsed_key)
    return action, keys


def _read_map_key(reader: _Reader, key_type: str) -> str:
    if key_type not in {"NameProperty", "StrProperty", "EnumProperty", "StructProperty"}:
        raise _UnsupportedSchema(f"不支持的动作映射键类型：{key_type}")
    # FName, FString, enum values and the schema's lightweight PalKeyAction
    # key are all a single archive string.  If a future struct adds fields,
    # this bounded read will fail at the following value tag and reject all.
    return _validate_action(reader.fstring())


def _parse_map_bindings(
    tag: _Tag,
    budget: _Budget,
    *,
    depth: int,
) -> list[tuple[str, tuple[str, str]]]:
    if tag.type_name != "MapProperty" or tag.value_type != "StructProperty":
        raise _UnsupportedSchema(f"{tag.name} 不是受支持的动作到键位结构映射")
    reader = _Reader(tag.payload, budget, label=tag.name)
    removed_count = reader.u32()
    if removed_count != 0:
        raise _UnsupportedSchema(f"{tag.name} 使用了无法验证的模板差量删除")
    count = reader.u32()
    if count > budget.limits.max_mappings:
        raise _ParseError("键位映射数量超过安全上限")
    output: list[tuple[str, tuple[str, str]]] = []
    for _ in range(count):
        action = _read_map_key(reader, tag.key_type)
        value_tags = _read_tag_stream(reader, depth=depth + 1)
        parsed_action, keys = _binding_struct(
            value_tags,
            budget,
            fallback_action=action,
            require_axis_name=False,
            depth=depth + 1,
        )
        output.extend((parsed_action, key) for key in keys)
    if reader.remaining:
        raise _UnsupportedSchema(f"{tag.name} 映射末尾存在未解析数据")
    return output


def _parse_axis_bindings(
    tag: _Tag,
    budget: _Budget,
    *,
    depth: int,
) -> list[tuple[str, tuple[str, str]]]:
    if tag.type_name != "ArrayProperty" or tag.inner_type != "StructProperty":
        raise _UnsupportedSchema(f"{tag.name} 不是受支持的结构数组")
    reader = _Reader(tag.payload, budget, label=tag.name)
    count = reader.u32()
    if count > budget.limits.max_mappings:
        raise _ParseError("轴映射数量超过安全上限")
    if count == 0:
        if reader.remaining:
            raise _UnsupportedSchema(f"空的 {tag.name} 含额外数据")
        return []
    inner_tag = _read_tag(reader)
    if inner_tag is None or inner_tag.type_name != "StructProperty":
        raise _UnsupportedSchema(f"{tag.name} 缺少数组元素结构标签")
    if reader.remaining:
        raise _UnsupportedSchema(f"{tag.name} 数组元素标签之后含额外数据")
    values = _Reader(inner_tag.payload, budget, label=f"{tag.name} 元素")
    output: list[tuple[str, tuple[str, str]]] = []
    for _ in range(count):
        value_tags = _read_tag_stream(values, depth=depth + 1)
        action, keys = _binding_struct(
            value_tags,
            budget,
            fallback_action=None,
            require_axis_name=True,
            depth=depth + 1,
        )
        output.extend((action, key) for key in keys)
    if values.remaining:
        raise _UnsupportedSchema(f"{tag.name} 数组末尾存在未解析数据")
    return output


def _build_keymap(bindings: list[tuple[str, tuple[str, str]]]) -> Keymap:
    grouped: dict[str, tuple[str, set[str]]] = {}
    for action, (input_name, device) in bindings:
        current = grouped.get(input_name)
        if current is None:
            grouped[input_name] = (device, {action})
        else:
            current_device, actions = current
            if current_device != device:
                raise _UnsupportedSchema(f"输入 {input_name} 的设备类型冲突")
            actions.add(action)
    return {
        input_name: {
            "type": device,
            "action": "；".join(sorted(actions, key=str.casefold)),
            "movement_direction": "",
        }
        for input_name, (device, actions) in sorted(
            grouped.items(), key=lambda item: item[0].casefold()
        )
    }


def _parse_key_config(tag: _Tag, budget: _Budget, *, depth: int) -> Keymap:
    if tag.type_name != "StructProperty" or not _class_tail(tag.struct_name).casefold().endswith(
        "keyconfigsettings"
    ):
        raise _UnsupportedSchema("KeyConfigSettings 的结构类型不匹配")
    fields = _parse_struct_payload(tag, budget, depth=depth + 1)
    mouse_names = {
        "mouseandkeyboardactionmappings",
        "mouseandkeyboardaxismappings",
        "mouseandkeyboarduiinputmappings",
    }
    gamepad_names = {
        "gamepadactionmappings",
        "gamepadaxismappings",
        "gamepaduiinputmappings",
    }
    seen: set[str] = set()
    bindings: list[tuple[str, tuple[str, str]]] = []
    for field in fields:
        name = field.name.casefold()
        if name in seen:
            raise _UnsupportedSchema(f"KeyConfigSettings 字段重复：{field.name}")
        seen.add(name)
        if name in gamepad_names:
            # The recorder captures keyboard and mouse only.  The payload size
            # is still validated by the outer tagged-property parser.
            continue
        if name not in mouse_names:
            raise _UnsupportedSchema(f"KeyConfigSettings 含未知字段：{field.name}")
        if name == "mouseandkeyboardaxismappings":
            bindings.extend(_parse_axis_bindings(field, budget, depth=depth + 1))
        else:
            bindings.extend(_parse_map_bindings(field, budget, depth=depth + 1))
    if not seen.intersection(mouse_names):
        raise _UnsupportedSchema("KeyConfigSettings 中没有鼠标键盘映射字段")
    if len(bindings) > budget.limits.max_mappings * 2:
        raise _ParseError("输出键位数量超过安全上限")
    return _build_keymap(bindings)


def _read_file(path: Path, limits: GvasKeymapLimits) -> bytes:
    if path.is_symlink() or (
        hasattr(path, "is_junction") and path.is_junction()
    ):
        raise _ParseError("拒绝跟随符号链接或目录联接")
    stat = path.stat()
    if not path.is_file():
        raise _ParseError("候选路径不是文件")
    if stat.st_size < 32:
        raise _ParseError("文件过小")
    if stat.st_size > limits.max_file_bytes:
        raise _ParseError("文件大小超过安全上限")
    with path.open("rb") as handle:
        data = handle.read(limits.max_file_bytes + 1)
    if len(data) > limits.max_file_bytes:
        raise _ParseError("文件读取量超过安全上限")
    return data


def parse_gvas_player_keymap(
    path: str | os.PathLike[str],
    *,
    limits: GvasKeymapLimits | None = None,
) -> GvasPlayerKeymapResult:
    """Parse one explicit GVAS candidate without modifying it.

    ``recognized`` means a structurally valid player options GVAS was found;
    it does not mean mappings were present.  In particular, an options save
    which only contains common settings returns ``recognized=True`` and
    ``needs_key_change=True``.
    """

    selected = Path(path)
    active_limits = limits or GvasKeymapLimits()
    budget = _Budget(active_limits)
    key_config_seen = False
    try:
        data = _read_file(selected, active_limits)
        reader = _Reader(data, budget)
        header = _read_header(reader)
        root_tags = _read_tag_stream(reader, depth=0)
        if reader.remaining and any(reader.read(reader.remaining)):
            raise _ParseError("GVAS 根属性后存在非零尾随数据")
        option_tag = _is_player_option_document(header, root_tags)
        if option_tag is None:
            return GvasPlayerKeymapResult(
                {}, False, False, False, "GVAS 不是受支持的玩家选项文档", selected,
                gvas_class=header.save_class,
            )
        option_fields = _parse_struct_payload(option_tag, budget, depth=1)
        key_fields = [
            tag
            for tag in option_fields
            if tag.name.casefold() == "keyconfigsettings"
            or _class_tail(tag.struct_name).casefold().endswith("keyconfigsettings")
        ]
        key_config_seen = bool(key_fields)
        if not key_fields:
            return GvasPlayerKeymapResult(
                keymap={},
                recognized=True,
                has_player_file=True,
                needs_key_change=True,
                diagnostic=(
                    "已找到并成功解析玩家配置文件；这证明游戏已经生成玩家设置。"
                    "但文件中没有已序列化的键位段（KeyConfigSettings）。"
                    "若一直使用游戏内置默认键位，这是正常状态：单纯启动、实际游玩或"
                    "正常退出都不会把内置默认键位表复制到玩家存档。可以尝试在游戏设置中"
                    "真正改动并保存一个键位后重试；是否会写出完整映射取决于游戏。"
                ),
                source_file=selected,
                gvas_class=header.save_class,
            )
        if len(key_fields) != 1:
            raise _UnsupportedSchema("发现多个 KeyConfigSettings，无法唯一确认")
        keymap = _parse_key_config(key_fields[0], budget, depth=2)
        if not keymap:
            return GvasPlayerKeymapResult(
                {}, True, True, True,
                "已解析 KeyConfigSettings，但其中没有可用的鼠标键盘键位；"
                "本次没有生成映射。可以尝试在游戏内改动并保存键位后重试，"
                "或导入并人工核对 Keymap。",
                selected,
                gvas_class=header.save_class,
                has_key_config=True,
            )
        return GvasPlayerKeymapResult(
            keymap=keymap,
            recognized=True,
            has_player_file=True,
            needs_key_change=False,
            diagnostic=f"已从玩家 GVAS 配置解析 {len(keymap)} 个鼠标键盘输入。",
            source_file=selected,
            has_verified_player_config=True,
            gvas_class=header.save_class,
            mapping_count=len(keymap),
            has_key_config=True,
        )
    except FileNotFoundError:
        return GvasPlayerKeymapResult({}, False, False, False, "候选文件不存在", selected)
    except PermissionError:
        return GvasPlayerKeymapResult({}, False, False, False, "没有权限读取候选文件", selected)
    except _UnsupportedSchema as exc:
        # Preserve strong recognition if the header and option structure can be
        # established in a second, bounded pass.  This distinguishes a known
        # player file with a future mapping layout from arbitrary binary data.
        recognized = False
        save_class = ""
        try:
            data = _read_file(selected, active_limits)
            retry_budget = _Budget(active_limits)
            retry = _Reader(data, retry_budget)
            retry_header = _read_header(retry)
            retry_tags = _read_tag_stream(retry, depth=0)
            recognized = _is_player_option_document(retry_header, retry_tags) is not None
            save_class = retry_header.save_class
        except (OSError, _ParseError):
            pass
        return GvasPlayerKeymapResult(
            {}, recognized, recognized, False,
            f"已识别玩家 GVAS，但键位结构不受支持，已拒绝整份映射：{exc}" if recognized else f"GVAS 结构不受支持：{exc}",
            selected,
            unsupported_schema=recognized,
            gvas_class=save_class,
            has_key_config=key_config_seen,
        )
    except (OSError, _ParseError) as exc:
        return GvasPlayerKeymapResult({}, False, False, False, f"无法解析 GVAS：{exc}", selected)


def _candidate_files(
    candidates: Iterable[str | os.PathLike[str]],
    limits: GvasKeymapLimits,
) -> tuple[list[Path], bool]:
    """Expand only explicit files and fixed Unreal option-save locations."""

    deadline = time.monotonic() + limits.timeout_seconds
    output: list[Path] = []
    seen: set[str] = set()
    truncated = False

    def add(path: Path) -> None:
        nonlocal truncated
        if len(output) >= limits.max_candidates:
            truncated = True
            return
        identity = os.path.normcase(os.path.abspath(path))
        link_like = path.is_symlink() or (
            hasattr(path, "is_junction") and path.is_junction()
        )
        if identity not in seen and path.is_file() and not link_like:
            seen.add(identity)
            output.append(path)

    for raw in candidates:
        if time.monotonic() > deadline:
            truncated = True
            break
        path = Path(raw)
        if path.is_file():
            add(path)
            continue
        if not path.is_dir() or path.is_symlink():
            continue
        add(path / "UserOption.sav")
        add(path / "Saved" / "SaveGames" / "UserOption.sav")
        try:
            with os.scandir(path) as entries:
                for index, entry in enumerate(entries):
                    if index >= limits.max_directory_entries:
                        truncated = True
                        break
                    if time.monotonic() > deadline:
                        truncated = True
                        break
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            add(Path(entry.path) / "Saved" / "SaveGames" / "UserOption.sav")
                    except OSError:
                        continue
        except OSError:
            continue
    return output, truncated


def discover_gvas_player_keymap(
    candidates: Iterable[str | os.PathLike[str]],
    *,
    limits: GvasKeymapLimits | None = None,
) -> GvasPlayerKeymapResult:
    """Find a supported GVAS in explicit roots using a fixed-depth search.

    The function never recursively scans an installation tree.  Callers should
    pass already-associated player-data roots or files.
    """

    active_limits = limits or GvasKeymapLimits()
    started = time.monotonic()
    files, truncated = _candidate_files(candidates, active_limits)
    deadline = started + max(0.01, active_limits.timeout_seconds)
    scanned = 0
    for index, path in enumerate(files, start=1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            truncated = True
            break
        candidate_limits = replace(
            active_limits,
            timeout_seconds=max(0.001, remaining),
        )
        result = parse_gvas_player_keymap(path, limits=candidate_limits)
        scanned = index
        result = GvasPlayerKeymapResult(
            **{
                **result.__dict__,
                "scanned_files": index,
                "truncated": truncated,
            }
        )
        # ``files`` preserves the caller's currentness ordering.  The first
        # structurally recognized player options save is therefore
        # authoritative even when it has no serialized KeyConfigSettings or
        # uses a newer unsupported key-config layout.  Falling through to an
        # older verified save would silently resurrect stale bindings.
        if result.recognized:
            return result
    return GvasPlayerKeymapResult(
        {}, False, False, False,
        "未在给定的玩家数据候选位置发现受支持的 GVAS 键位配置。",
        scanned_files=scanned,
        truncated=truncated,
    )


__all__ = [
    "GvasKeymapLimits",
    "GvasPlayerKeymapResult",
    "discover_gvas_player_keymap",
    "parse_gvas_player_keymap",
]
