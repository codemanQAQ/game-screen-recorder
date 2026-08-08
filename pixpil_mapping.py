"""Safely recover literal Pixpil input-mapping configs from LuaJIT bytecode.

This module is deliberately *not* a Lua or LuaJIT runtime.  It parses a raw,
little-endian LuaJIT v2 dump and performs a small, path-insensitive data-flow
analysis over literal/register/table operations only.  No function, bytecode,
native code, hook, or embedded payload is ever executed.

``extract_mapping_configs`` raises :class:`LuaJitMappingError` when the input
is malformed, uses an unsupported dump variant, or exceeds a safety limit.  A
valid dump without a recoverable config returns an empty dict.  Candidates are
accepted only inside a child prototype that the root chunk explicitly creates
with ``FNEW`` and installs as a named table method.  That method must assign a
literal table to its receiver under an ASCII name ending in ``MappingConfig``;
the table must contain a valid keyboard, mouse, or joystick ``mappings`` map.

Limits of the analysis:

* It supports LuaJIT bytecode version 2, little-endian raw dumps only.
* It follows literal data flow and recognizes simple ``getPlatformName`` string
  guards so console-only branches are excluded from the Windows/default
  result.  Other branches, loops, calls, globals, upvalues, arithmetic, and
  metatables are not evaluated.
* Ordinary writes use Lua overwrite/delete semantics.  Mutations after an
  unresolved control-flow decision taint the candidate; recognized platform
  guards select the Windows path.  Backward edges are scanned once, not looped.
* Calls produce unknown results.  A directly passed table and all of its
  register aliases are tainted.  Recursive child-table side effects cannot be
  proven, and configs assembled by executing helpers are not recovered.
* Only the validated ``mappings`` subtree is returned.  Unrelated dynamic
  metadata on the same outer config does not cause false rejection.

The implementation has explicit bounds for input size, prototypes,
instructions, constants, strings, table items, dynamically cloned tables,
captures, output depth, and output size.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field
from typing import Final

__all__ = ["LuaJitMappingError", "extract_mapping_configs"]


class LuaJitMappingError(ValueError):
    """The LuaJIT dump is malformed, unsupported, or exceeds a safety limit."""


# Parsing limits.  These are intentionally much larger than the observed
# Pixpil modules while still bounding every attacker-controlled collection.
_MAX_INPUT_BYTES: Final = 8 * 1024 * 1024
_MAX_CHUNK_NAME_BYTES: Final = 64 * 1024
_MAX_PROTOTYPE_BYTES: Final = 4 * 1024 * 1024
_MAX_PROTOTYPES: Final = 4_096
_MAX_TOTAL_INSTRUCTIONS: Final = 1_000_000
_MAX_TOTAL_CONSTANTS: Final = 500_000
_MAX_STRING_BYTES: Final = 1 * 1024 * 1024
_MAX_TOTAL_STRING_BYTES: Final = 8 * 1024 * 1024
_MAX_TABLE_ITEMS: Final = 100_000
_MAX_TOTAL_TEMPLATE_ITEMS: Final = 500_000

# Static-analysis/output limits.
_MAX_DYNAMIC_TABLES: Final = 50_000
_MAX_DYNAMIC_ITEMS: Final = 500_000
_MAX_CAPTURES: Final = 256
_MAX_CONFIGS: Final = 128
_MAX_OUTPUT_DEPTH: Final = 32
_MAX_OUTPUT_ITEMS: Final = 100_000
_MAX_ACTIONS_PER_DEVICE: Final = 8_192
_MAX_BINDINGS_PER_ACTION: Final = 32
_MAX_TEXT_LENGTH: Final = 512
_MAX_SYMBOL_DEPTH: Final = 16

_DUMP_MAGIC: Final = b"\x1bLJ"
_DUMP_VERSION: Final = 2
_FLAG_BIG_ENDIAN: Final = 0x01
_FLAG_STRIPPED: Final = 0x02
# Standard LuaJIT v2 flags: BE, STRIP, FFI, and FR2.  Fork-specific flags are
# rejected because their binary layout has not been verified here.
_ALLOWED_DUMP_FLAGS: Final = 0x0F

_KNOWN_DEVICES: Final = frozenset({"keyboard", "mouse", "joystick"})
_MAPPING_CONFIG_NAME = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]{0,111}MappingConfig$"
)


# LuaJIT 2.1 opcodes used by the restricted evaluator.
_OP_MOV: Final = 0x12
_OP_KSTR: Final = 0x27
_OP_KCDATA: Final = 0x28
_OP_KSHORT: Final = 0x29
_OP_KNUM: Final = 0x2A
_OP_KPRI: Final = 0x2B
_OP_KNIL: Final = 0x2C
_OP_TNEW: Final = 0x34
_OP_TDUP: Final = 0x35
_OP_GGET: Final = 0x36
_OP_TGETV: Final = 0x38
_OP_TGETS: Final = 0x39
_OP_TGETB: Final = 0x3A
_OP_TGETR: Final = 0x3B
_OP_TSETV: Final = 0x3C
_OP_TSETS: Final = 0x3D
_OP_TSETB: Final = 0x3E
_OP_TSETM: Final = 0x3F
_OP_TSETR: Final = 0x40
_OP_CALLM: Final = 0x41
_OP_CALL: Final = 0x42
_OP_ITERC: Final = 0x45
_OP_ITERN: Final = 0x46
_OP_VARG: Final = 0x47
_MAX_KNOWN_OPCODE: Final = 0x60

_UNKNOWN = object()
_HOST_PLATFORM = object()
_INVALID_CAPTURE = object()

# These are engine/platform identifiers, not game-specific names.  They are
# used only when a value is proven to be the result of a method literally
# named getPlatformName.  Unknown identifiers remain path-insensitive.
_WINDOWS_PLATFORM_NAMES: Final = frozenset({"pc", "win", "win32", "windows"})
_NON_WINDOWS_PLATFORM_NAMES: Final = frozenset(
    {
        "android",
        "gdk",
        "ios",
        "linux",
        "mac",
        "macos",
        "ns",
        "osx",
        "playstation",
        "ps4",
        "ps5",
        "switch",
        "xbox",
        "xboxone",
        "xboxseries",
        "xs",
    }
)


class _Reader:
    __slots__ = ("_data", "position")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self.position = 0

    @property
    def remaining(self) -> int:
        return len(self._data) - self.position

    def read(self, size: int) -> bytes:
        if size < 0 or size > self.remaining:
            raise LuaJitMappingError(
                f"unexpected end of LuaJIT dump at offset {self.position}"
            )
        start = self.position
        self.position += size
        return self._data[start : start + size]

    def read_u8(self) -> int:
        if self.position >= len(self._data):
            raise LuaJitMappingError(
                f"unexpected end of LuaJIT dump at offset {self.position}"
            )
        value = self._data[self.position]
        self.position += 1
        return value

    def peek_u8(self) -> int:
        if self.position >= len(self._data):
            raise LuaJitMappingError(
                f"unexpected end of LuaJIT dump at offset {self.position}"
            )
        return self._data[self.position]

    def read_uleb128(self) -> int:
        value = 0
        for shift in range(0, 35, 7):
            byte = self.read_u8()
            value |= (byte & 0x7F) << shift
            if byte < 0x80:
                if value > 0xFFFFFFFF:
                    raise LuaJitMappingError("LuaJIT ULEB128 value exceeds 32 bits")
                return value
        raise LuaJitMappingError("invalid or overlong LuaJIT ULEB128 value")

    def read_uleb128_33(self) -> int:
        """Read LuaJIT's number-constant ULEB encoding with a tag low bit."""

        first = self.read_u8()
        value = first >> 1
        if first < 0x80:
            return value
        value &= 0x3F
        shift = 6
        for _ in range(5):
            byte = self.read_u8()
            value |= (byte & 0x7F) << shift
            if byte < 0x80:
                if value > 0xFFFFFFFF:
                    raise LuaJitMappingError(
                        "LuaJIT 33-bit ULEB128 payload exceeds 32 bits"
                    )
                return value
            shift += 7
        raise LuaJitMappingError("invalid or overlong LuaJIT 33-bit ULEB128")


@dataclass(slots=True)
class _ParseBudget:
    prototypes: int = 0
    instructions: int = 0
    constants: int = 0
    string_bytes: int = 0
    template_items: int = 0

    def add_string(self, size: int) -> None:
        if size > _MAX_STRING_BYTES:
            raise LuaJitMappingError("LuaJIT string exceeds the safety limit")
        self.string_bytes += size
        if self.string_bytes > _MAX_TOTAL_STRING_BYTES:
            raise LuaJitMappingError(
                "LuaJIT strings exceed the cumulative safety limit"
            )

    def add_template_items(self, count: int) -> None:
        if count > _MAX_TABLE_ITEMS:
            raise LuaJitMappingError("LuaJIT template table exceeds the safety limit")
        self.template_items += count
        if self.template_items > _MAX_TOTAL_TEMPLATE_ITEMS:
            raise LuaJitMappingError(
                "LuaJIT template tables exceed the cumulative safety limit"
            )


@dataclass(frozen=True, slots=True)
class _LuaKey:
    kind: str
    value: object


def _lua_key(value: object) -> _LuaKey | None:
    if isinstance(value, str):
        return _LuaKey("string", value)
    if isinstance(value, bytes):
        return _LuaKey("bytes", value)
    if type(value) is bool:
        return _LuaKey("boolean", value)
    if type(value) is int:
        return _LuaKey("number", value)
    if type(value) is float:
        if not math.isfinite(value):
            return None
        if value == 0:
            value = 0
        elif value.is_integer():
            value = int(value)
        return _LuaKey("number", value)
    return None


@dataclass(slots=True)
class _Table:
    values: dict[_LuaKey, object] = field(default_factory=dict)
    written: set[_LuaKey] = field(default_factory=set)
    tainted: bool = False

    def lookup(self, key_value: object) -> object:
        key = _lua_key(key_value)
        if key is None or key not in self.written:
            return _UNKNOWN
        return self.values.get(key)


@dataclass(slots=True)
class _SelfObject:
    values: dict[_LuaKey, object] = field(default_factory=dict)
    written: set[_LuaKey] = field(default_factory=set)
    tainted: bool = False

    def lookup(self, key_value: object) -> object:
        key = _lua_key(key_value)
        if key is None or key not in self.written:
            return _UNKNOWN
        return self.values.get(key)


@dataclass(frozen=True, slots=True)
class _Symbol:
    """A non-executable global/member access path used only as provenance."""

    path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _FunctionRef:
    prototype_index: int


@dataclass(frozen=True, slots=True)
class _Prototype:
    parameter_count: int
    frame_size: int
    bytecode: bytes
    gc_constants: tuple[object, ...]
    number_constants: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _ParsedDump:
    prototypes: tuple[_Prototype, ...]
    root_prototype: int


@dataclass(slots=True)
class _ExecutionBudget:
    dynamic_tables: int = 0
    dynamic_items: int = 0
    captures: int = 0

    def add_table(self, item_count: int) -> None:
        self.dynamic_tables += 1
        self.dynamic_items += item_count
        if self.dynamic_tables > _MAX_DYNAMIC_TABLES:
            raise LuaJitMappingError(
                "static analysis created too many table objects"
            )
        if self.dynamic_items > _MAX_DYNAMIC_ITEMS:
            raise LuaJitMappingError(
                "static analysis created too many table items"
            )

    def add_assignment(self) -> None:
        self.dynamic_items += 1
        if self.dynamic_items > _MAX_DYNAMIC_ITEMS:
            raise LuaJitMappingError(
                "static analysis created too many table items"
            )

    def add_capture(self) -> None:
        self.captures += 1
        if self.captures > _MAX_CAPTURES:
            raise LuaJitMappingError("too many MappingConfig assignments")


def _decode_lua_string(raw: bytes) -> str | bytes:
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        # Lua strings are arbitrary byte sequences.  Keeping invalid UTF-8 as
        # bytes prevents replacement-character collisions and makes such a
        # value ineligible for a public JSON-like mapping config.
        return raw


def _read_table_value(reader: _Reader, budget: _ParseBudget) -> object:
    value_type = reader.read_uleb128()
    if value_type >= 5:
        size = value_type - 5
        budget.add_string(size)
        return _decode_lua_string(reader.read(size))
    if value_type == 0:
        return None
    if value_type == 1:
        return False
    if value_type == 2:
        return True
    if value_type == 3:
        value = reader.read_uleb128()
        return value - 0x100000000 if value & 0x80000000 else value
    if value_type == 4:
        low = reader.read_uleb128()
        high = reader.read_uleb128()
        return struct.unpack("<d", struct.pack("<II", low, high))[0]
    raise LuaJitMappingError("invalid LuaJIT template-table value type")


def _read_template_table(reader: _Reader, budget: _ParseBudget) -> _Table:
    array_count = reader.read_uleb128()
    hash_count = reader.read_uleb128()
    encoded_items = array_count + hash_count * 2
    budget.add_template_items(encoded_items)

    array = [_read_table_value(reader, budget) for _ in range(array_count)]
    if array and array[0] is not None:
        raise LuaJitMappingError(
            "LuaJIT template table has a non-nil internal index zero"
        )

    table = _Table()
    # LuaJIT dumps the internal array slot zero first.  It is a sentinel and
    # not the Lua key 0; logical arrays therefore begin at index one.
    for index, value in enumerate(array[1:], start=1):
        if value is None:
            continue
        key = _LuaKey("number", index)
        table.written.add(key)
        table.values[key] = value

    for _ in range(hash_count):
        raw_key = _read_table_value(reader, budget)
        value = _read_table_value(reader, budget)
        key = _lua_key(raw_key)
        if key is None:
            raise LuaJitMappingError("LuaJIT template table has an invalid key")
        if key in table.written:
            raise LuaJitMappingError("LuaJIT template table has a duplicate key")
        if value is None:
            continue
        table.written.add(key)
        table.values[key] = value
    return table


def _read_number_constant(reader: _Reader) -> object:
    is_double = bool(reader.peek_u8() & 1)
    low = reader.read_uleb128_33()
    if not is_double:
        return low - 0x100000000 if low & 0x80000000 else low
    high = reader.read_uleb128()
    return struct.unpack("<d", struct.pack("<II", low, high))[0]


def _parse_prototype(
    data: bytes,
    *,
    stripped: bool,
    budget: _ParseBudget,
    child_stack: list[int],
) -> _Prototype:
    reader = _Reader(data)
    reader.read_u8()  # Prototype flags; they do not affect literal data flow.
    parameter_count = reader.read_u8()
    frame_size = reader.read_u8()
    upvalue_count = reader.read_u8()
    gc_constant_count = reader.read_uleb128()
    number_constant_count = reader.read_uleb128()
    instruction_count = reader.read_uleb128()

    if parameter_count > frame_size:
        raise LuaJitMappingError("LuaJIT prototype has more parameters than slots")

    budget.constants += gc_constant_count + number_constant_count
    if budget.constants > _MAX_TOTAL_CONSTANTS:
        raise LuaJitMappingError("LuaJIT constants exceed the safety limit")
    budget.instructions += instruction_count
    if budget.instructions > _MAX_TOTAL_INSTRUCTIONS:
        raise LuaJitMappingError("LuaJIT instructions exceed the safety limit")

    debug_size = 0
    if not stripped:
        debug_size = reader.read_uleb128()
        if debug_size:
            reader.read_uleb128()  # First source line.
            reader.read_uleb128()  # Source line span.

    bytecode = reader.read(instruction_count * 4)
    for offset in range(0, len(bytecode), 4):
        if bytecode[offset] > _MAX_KNOWN_OPCODE:
            raise LuaJitMappingError(
                f"unknown LuaJIT v2 opcode 0x{bytecode[offset]:02x}"
            )
    reader.read(upvalue_count * 2)

    gc_constants: list[object] = []
    for _ in range(gc_constant_count):
        constant_type = reader.read_uleb128()
        if constant_type >= 5:
            size = constant_type - 5
            budget.add_string(size)
            gc_constants.append(_decode_lua_string(reader.read(size)))
        elif constant_type == 0:  # Child prototype reference.
            if not child_stack:
                raise LuaJitMappingError(
                    "LuaJIT prototype references a missing child prototype"
                )
            gc_constants.append(_FunctionRef(child_stack.pop()))
        elif constant_type == 1:
            gc_constants.append(_read_template_table(reader, budget))
        elif constant_type in {2, 3}:  # Signed/unsigned 64-bit cdata.
            reader.read_uleb128()
            reader.read_uleb128()
            gc_constants.append(_UNKNOWN)
        elif constant_type == 4:  # Complex-number cdata.
            for _part in range(4):
                reader.read_uleb128()
            gc_constants.append(_UNKNOWN)
        else:  # Defensive; all non-negative ULEB values are covered above.
            raise LuaJitMappingError("invalid LuaJIT GC constant type")

    numbers = tuple(
        _read_number_constant(reader) for _ in range(number_constant_count)
    )
    if debug_size:
        reader.read(debug_size)
    if reader.remaining:
        raise LuaJitMappingError("LuaJIT prototype has trailing or mis-sized data")

    parsed = _Prototype(
        parameter_count=parameter_count,
        frame_size=frame_size,
        bytecode=bytecode,
        gc_constants=tuple(gc_constants),
        number_constants=numbers,
    )
    _validate_prototype_operands(parsed, upvalue_count=upvalue_count)
    return parsed


def _parse_dump(data: bytes) -> _ParsedDump:
    if len(data) < 5:
        raise LuaJitMappingError("LuaJIT dump is too short")
    if len(data) > _MAX_INPUT_BYTES:
        raise LuaJitMappingError("LuaJIT dump exceeds the input safety limit")

    reader = _Reader(data)
    if reader.read(3) != _DUMP_MAGIC:
        raise LuaJitMappingError("input is not a raw LuaJIT dump")
    version = reader.read_u8()
    if version != _DUMP_VERSION:
        raise LuaJitMappingError(
            f"unsupported LuaJIT bytecode version {version}; expected 2"
        )
    flags = reader.read_uleb128()
    if flags & _FLAG_BIG_ENDIAN:
        raise LuaJitMappingError("big-endian LuaJIT dumps are not supported")
    if flags & ~_ALLOWED_DUMP_FLAGS:
        raise LuaJitMappingError("LuaJIT dump uses unknown header flags")

    stripped = bool(flags & _FLAG_STRIPPED)
    budget = _ParseBudget()
    if not stripped:
        chunk_name_size = reader.read_uleb128()
        if chunk_name_size > _MAX_CHUNK_NAME_BYTES:
            raise LuaJitMappingError("LuaJIT chunk name exceeds the safety limit")
        reader.read(chunk_name_size)

    prototypes: list[_Prototype] = []
    child_stack: list[int] = []
    terminated = False
    while reader.remaining:
        prototype_size = reader.read_uleb128()
        if prototype_size == 0:
            terminated = True
            break
        if prototype_size > _MAX_PROTOTYPE_BYTES:
            raise LuaJitMappingError("LuaJIT prototype exceeds the safety limit")
        budget.prototypes += 1
        if budget.prototypes > _MAX_PROTOTYPES:
            raise LuaJitMappingError("too many LuaJIT prototypes")
        prototype = _parse_prototype(
            reader.read(prototype_size),
            stripped=stripped,
            budget=budget,
            child_stack=child_stack,
        )
        prototypes.append(prototype)
        child_stack.append(len(prototypes) - 1)

    if not terminated or reader.remaining:
        raise LuaJitMappingError("LuaJIT dump has no valid final terminator")
    if len(child_stack) != 1:
        raise LuaJitMappingError("LuaJIT dump does not contain exactly one root")
    return _ParsedDump(tuple(prototypes), child_stack[0])


def _clone_table(
    source: _Table,
    budget: _ExecutionBudget,
    *,
    memo: dict[int, _Table] | None = None,
    depth: int = 0,
) -> _Table:
    if depth > _MAX_OUTPUT_DEPTH:
        raise LuaJitMappingError("literal table nesting exceeds the safety limit")
    if memo is None:
        memo = {}
    existing = memo.get(id(source))
    if existing is not None:
        return existing

    budget.add_table(len(source.written))
    cloned = _Table(tainted=source.tainted)
    memo[id(source)] = cloned
    cloned.written.update(source.written)
    for key, value in source.values.items():
        if isinstance(value, _Table):
            value = _clone_table(
                value,
                budget,
                memo=memo,
                depth=depth + 1,
            )
        cloned.values[key] = value
    return cloned


def _constant(constants: tuple[object, ...], raw_index: int) -> object:
    # LuaJIT encodes KGC operands in reverse constant-pool order.
    index = len(constants) - raw_index - 1
    if not 0 <= index < len(constants):
        raise LuaJitMappingError("LuaJIT instruction has an invalid constant index")
    return constants[index]


def _number_constant(constants: tuple[object, ...], index: int) -> object:
    # Unlike KGC operands, numeric-constant operands are direct indexes.
    if not 0 <= index < len(constants):
        raise LuaJitMappingError(
            "LuaJIT instruction has an invalid numeric-constant index"
        )
    return constants[index]


def _validate_prototype_operands(
    prototype: _Prototype, *, upvalue_count: int
) -> None:
    """Validate standard LuaJIT 2.1 operand domains without executing them."""

    frame = prototype.frame_size
    instruction_count = len(prototype.bytecode) // 4

    def register(index: int) -> None:
        if not 0 <= index < frame:
            raise LuaJitMappingError(
                "LuaJIT instruction has an invalid register operand"
            )

    def gc(index: int) -> object:
        return _constant(prototype.gc_constants, index)

    def number(index: int) -> object:
        return _number_constant(prototype.number_constants, index)

    for pc in range(instruction_count):
        offset = pc * 4
        opcode = prototype.bytecode[offset]
        a = prototype.bytecode[offset + 1]
        c = prototype.bytecode[offset + 2]
        b = prototype.bytecode[offset + 3]
        d = c | (b << 8)

        if opcode in {0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x0C, 0x0D}:
            register(a)
            register(d)
        elif opcode in {0x06, 0x07}:
            register(a)
            gc(d)
        elif opcode in {0x08, 0x09}:
            register(a)
            number(d)
        elif opcode in {0x0A, 0x0B}:
            register(a)
            if d > 2:
                raise LuaJitMappingError("LuaJIT comparison has invalid primitive")
        elif opcode in {0x0E, 0x0F}:
            register(d)
        elif opcode in {0x10, 0x11}:
            register(a)
        elif opcode in {0x12, 0x13, 0x14, 0x15}:
            register(a)
            register(d)
        elif 0x16 <= opcode <= 0x1F:
            register(a)
            register(b)
            number(c)
        elif 0x20 <= opcode <= 0x25:
            register(a)
            register(b)
            register(c)
        elif opcode == 0x26:
            register(a)
            register(b)
            register(c)
            if b > c:
                raise LuaJitMappingError("LuaJIT CAT has an invalid register range")
        elif opcode in {0x27, 0x28}:
            register(a)
            gc(d)
        elif opcode == 0x29:
            register(a)
        elif opcode == 0x2A:
            register(a)
            number(d)
        elif opcode == 0x2B:
            register(a)
            if d > 2:
                raise LuaJitMappingError("LuaJIT KPRI has an invalid primitive")
        elif opcode == 0x2C:
            register(a)
            register(d)
            if a > d:
                raise LuaJitMappingError("LuaJIT KNIL has an invalid range")
        elif opcode == 0x2D:
            register(a)
            if d >= upvalue_count:
                raise LuaJitMappingError("LuaJIT UGET has an invalid upvalue")
        elif opcode == 0x2E:
            if a >= upvalue_count:
                raise LuaJitMappingError("LuaJIT USETV has an invalid upvalue")
            register(d)
        elif opcode == 0x2F:
            if a >= upvalue_count:
                raise LuaJitMappingError("LuaJIT USETS has an invalid upvalue")
            gc(d)
        elif opcode == 0x30:
            if a >= upvalue_count:
                raise LuaJitMappingError("LuaJIT USETN has an invalid upvalue")
            number(d)
        elif opcode == 0x31:
            if a >= upvalue_count or d > 2:
                raise LuaJitMappingError("LuaJIT USETP has an invalid operand")
        elif opcode == 0x32:  # UCLO also carries a jump offset.
            if a > frame:
                raise LuaJitMappingError("LuaJIT UCLO has an invalid base")
            target = pc + 1 + (d - 0x8000)
            if not 0 <= target <= instruction_count:
                raise LuaJitMappingError("LuaJIT UCLO target is out of range")
        elif opcode == 0x33:
            register(a)
            if not isinstance(gc(d), _FunctionRef):
                raise LuaJitMappingError("LuaJIT FNEW does not reference a child")
        elif opcode == 0x34:
            register(a)
        elif opcode == 0x35:
            register(a)
            if not isinstance(gc(d), _Table):
                raise LuaJitMappingError("LuaJIT TDUP does not reference a table")
        elif opcode == 0x36:
            register(a)
            gc(d)
        elif opcode == 0x37:
            register(a)
            gc(d)
        elif opcode in {0x38, 0x3B, 0x3C, 0x40}:
            register(a)
            register(b)
            register(c)
        elif opcode in {0x39, 0x3D}:
            register(a)
            register(b)
            gc(c)
        elif opcode in {0x3A, 0x3E}:
            register(a)
            register(b)
        elif opcode == 0x3F:
            if a == 0:
                raise LuaJitMappingError("LuaJIT TSETM has an invalid base")
            register(a - 1)
            number(d)
        elif opcode in {0x41, 0x42}:
            register(a)
            if b and a + max(1, b - 1) > frame:
                raise LuaJitMappingError("LuaJIT CALL result range is invalid")
            if c and a + c > frame:
                raise LuaJitMappingError("LuaJIT CALL argument range is invalid")
        elif opcode in {0x43, 0x44}:
            register(a)
            if d and a + d > frame:
                raise LuaJitMappingError("LuaJIT tail-call argument range is invalid")
        elif opcode in {0x45, 0x46, 0x47, 0x48}:
            register(a)
        elif opcode in {0x49, 0x4A}:
            register(a)
            if d and a + d - 1 > frame:
                raise LuaJitMappingError("LuaJIT return range is invalid")
        elif opcode == 0x4B:
            pass
        elif opcode == 0x4C:
            register(a)
        elif 0x4D <= opcode <= 0x57:
            register(a)
            target = pc + 1 + (d - 0x8000)
            if not 0 <= target <= instruction_count:
                raise LuaJitMappingError("LuaJIT branch target is out of range")
        elif opcode == 0x58:
            if a > frame:
                raise LuaJitMappingError("LuaJIT JMP has an invalid base")
            target = pc + 1 + (d - 0x8000)
            if not 0 <= target <= instruction_count:
                raise LuaJitMappingError("LuaJIT branch target is out of range")
        elif 0x59 <= opcode <= 0x60:
            # Function headers are VM-internal and carry no dump-level
            # constant references.  Their layout is already bounded by the
            # four-byte instruction framing.
            pass


def _register(registers: list[object], index: int) -> object:
    if not 0 <= index < len(registers):
        raise LuaJitMappingError("LuaJIT instruction has an invalid register index")
    return registers[index]


def _set_register(registers: list[object], index: int, value: object) -> None:
    if not 0 <= index < len(registers):
        raise LuaJitMappingError("LuaJIT instruction has an invalid register index")
    registers[index] = value


def _lookup_table(target: object, key: object) -> object:
    if isinstance(target, (_Table, _SelfObject)):
        return target.lookup(key)
    if (
        isinstance(target, _Symbol)
        and isinstance(key, str)
        and len(target.path) < _MAX_SYMBOL_DEPTH
    ):
        return _Symbol(target.path + (key,))
    return _UNKNOWN


def _key_text(key: _LuaKey) -> str | None:
    return key.value if key.kind == "string" and isinstance(key.value, str) else None


def _assign(
    target: object,
    raw_key: object,
    value: object,
    *,
    budget: _ExecutionBudget,
    captures: dict[str, _Table | object],
    ambiguous_control: bool,
) -> None:
    key = _lua_key(raw_key)
    if key is None or not isinstance(target, (_Table, _SelfObject)):
        return

    budget.add_assignment()
    target.written.add(key)
    if value is None:
        target.values.pop(key, None)
    else:
        target.values[key] = value

    if ambiguous_control:
        target.tainted = True
        if isinstance(value, _Table):
            value.tainted = True

    if not isinstance(target, _SelfObject):
        return
    name = _key_text(key)
    if name is None or _MAPPING_CONFIG_NAME.fullmatch(name) is None:
        return
    budget.add_capture()
    if ambiguous_control or target.tainted or not isinstance(value, _Table):
        captures[name] = _INVALID_CAPTURE
        return
    # Freeze the config at the assignment point.  Later opaque consumers such
    # as mapping:load(config) must not retroactively taint this literal value.
    captures[name] = _clone_table(value, budget)


def _clear_call_results(registers: list[object], a: int, result_code: int) -> None:
    _register(registers, a)  # Validate A even for a zero-result call.
    if result_code == 0:
        end = len(registers)
    else:
        # B is encoded as result-count + 1.  Clear A as well for B == 1 so
        # stale literal values cannot survive an opaque call.
        end = min(len(registers), a + max(1, result_code - 1))
    for index in range(a, end):
        registers[index] = _UNKNOWN


def _clear_range(registers: list[object], start: int, count: int) -> None:
    if not 0 <= start < len(registers):
        raise LuaJitMappingError("LuaJIT instruction has an invalid register index")
    for index in range(start, min(len(registers), start + count)):
        registers[index] = _UNKNOWN


def _taint_value(value: object) -> None:
    if not isinstance(value, (_Table, _SelfObject)):
        return
    # Object identity carries taint to every register alias.  Deliberately do
    # not recursively taint child tables: a consumer commonly receives the
    # outer config while later literal configs reuse an inner immutable button
    # array.  Recursive side effects cannot be proven and remain unsupported.
    value.tainted = True


def _taint_call_arguments(
    registers: list[object], a: int, argument_code: int
) -> None:
    _register(registers, a)
    end = len(registers) if argument_code == 0 else min(
        len(registers), a + argument_code
    )
    for index in range(a + 1, end):
        _taint_value(registers[index])


def _normalized_platform_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _host_platform_equals(left: object, right: object) -> bool | None:
    if left is not _HOST_PLATFORM or not isinstance(right, str):
        return None
    if len(right) > 64:
        return None
    normalized = _normalized_platform_name(right)
    if normalized in _WINDOWS_PLATFORM_NAMES:
        return True
    if normalized in _NON_WINDOWS_PLATFORM_NAMES:
        return False
    return None


def _jump_target(compare_pc: int, jump_word: bytes) -> int:
    if len(jump_word) != 4 or jump_word[0] != 0x58:
        raise LuaJitMappingError("invalid LuaJIT conditional jump")
    encoded = jump_word[2] | (jump_word[3] << 8)
    relative = encoded - 0x8000
    return compare_pc + 2 + relative


_METHOD_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _discover_receiver_methods(parsed: _ParsedDump) -> set[int]:
    """Find child prototypes explicitly installed as fields by the root.

    Treating every prototype's R0 as ``self`` admits arbitrary ordinary
    functions.  A receiver prototype must instead be materialized by FNEW in
    the root chunk and assigned to a named field of a statically known global
    or member path.  This mirrors Pixpil's CLASS method declarations without
    relying on a game name or source/debug metadata.
    """

    prototype = parsed.prototypes[parsed.root_prototype]
    registers: list[object] = [_UNKNOWN] * prototype.frame_size
    methods: set[int] = set()
    code = prototype.bytecode
    instruction_count = len(code) // 4
    pc = 0
    steps = 0
    ambiguous_control = False

    while pc < instruction_count:
        steps += 1
        if steps > instruction_count:
            raise LuaJitMappingError("method discovery exceeded its step limit")
        offset = pc * 4
        opcode = code[offset]
        a = code[offset + 1]
        c = code[offset + 2]
        b = code[offset + 3]
        d = c | (b << 8)

        if 0x00 <= opcode <= 0x11 and pc + 1 < instruction_count:
            if code[(pc + 1) * 4] == 0x58:
                ambiguous_control = True
                pc += 2
                continue
        if opcode == 0x58:
            target = pc + 1 + (d - 0x8000)
            if target > pc:
                pc = target
                continue
            ambiguous_control = True
        elif opcode in {0x43, 0x44, 0x49, 0x4A, 0x4B, 0x4C}:
            break

        if opcode == _OP_MOV:
            _set_register(registers, a, _register(registers, d))
        elif opcode == _OP_KSTR:
            _set_register(registers, a, _constant(prototype.gc_constants, d))
        elif opcode == 0x33:  # FNEW.
            child = _constant(prototype.gc_constants, d)
            if not isinstance(child, _FunctionRef):
                raise LuaJitMappingError("LuaJIT FNEW does not reference a child")
            _set_register(registers, a, child)
        elif opcode == _OP_GGET:
            name = _constant(prototype.gc_constants, d)
            value: object = _Symbol((name,)) if isinstance(name, str) else _UNKNOWN
            _set_register(registers, a, value)
        elif opcode == _OP_TGETS:
            target = _register(registers, b)
            key = _constant(prototype.gc_constants, c)
            _set_register(registers, a, _lookup_table(target, key))
        elif opcode in {_OP_TSETS, _OP_TSETV}:
            value = _register(registers, a)
            target = _register(registers, b)
            key = (
                _constant(prototype.gc_constants, c)
                if opcode == _OP_TSETS
                else _register(registers, c)
            )
            if (
                isinstance(value, _FunctionRef)
                and isinstance(target, _Symbol)
                and isinstance(key, str)
                and _METHOD_FIELD_NAME.fullmatch(key) is not None
                and 0 <= value.prototype_index < len(parsed.prototypes)
                and parsed.prototypes[value.prototype_index].parameter_count > 0
                and not ambiguous_control
            ):
                methods.add(value.prototype_index)
        elif opcode in {_OP_CALLM, _OP_CALL}:
            _clear_call_results(registers, a, b)
        elif opcode == _OP_KSHORT:
            _set_register(registers, a, d - 0x10000 if d & 0x8000 else d)
        elif opcode == _OP_KNUM:
            _set_register(
                registers, a, _number_constant(prototype.number_constants, d)
            )
        elif opcode == _OP_KPRI:
            _set_register(registers, a, (None, False, True)[d] if d < 3 else _UNKNOWN)
        elif opcode in {
            _OP_KCDATA,
            _OP_TNEW,
            _OP_TDUP,
            _OP_TGETV,
            _OP_TGETB,
            _OP_TGETR,
            0x2D,  # UGET
        } or opcode in {0x0C, 0x0D} or 0x12 <= opcode <= 0x26:
            # Clear every destination we do not need to model so stale FNEW or
            # Symbol values cannot produce a false method relationship.
            _set_register(registers, a, _UNKNOWN)
        elif opcode == _OP_KNIL:
            if a > d:
                raise LuaJitMappingError("LuaJIT KNIL has an invalid register range")
            _register(registers, a)
            _register(registers, d)
            for index in range(a, d + 1):
                registers[index] = None
        elif opcode in {_OP_ITERC, _OP_ITERN, _OP_VARG}:
            _clear_call_results(registers, a, b)

        pc += 1

    return methods


def _interpret_prototype(
    prototype: _Prototype,
    *,
    budget: _ExecutionBudget,
    captures: list[tuple[str, _Table]],
    assume_receiver: bool,
) -> None:
    registers: list[object] = [_UNKNOWN] * prototype.frame_size
    if assume_receiver and prototype.parameter_count:
        _set_register(registers, 0, _SelfObject())

    code = prototype.bytecode
    instruction_count = len(code) // 4
    pc = 0
    steps = 0
    ambiguous_control = False
    final_captures: dict[str, _Table | object] = {}
    while pc < instruction_count:
        steps += 1
        if steps > instruction_count:
            raise LuaJitMappingError("static analysis exceeded its step limit")
        offset = pc * 4
        opcode = code[offset]
        a = code[offset + 1]
        c = code[offset + 2]
        b = code[offset + 3]
        d = c | (b << 8)

        if opcode > _MAX_KNOWN_OPCODE:
            raise LuaJitMappingError(f"unknown LuaJIT v2 opcode 0x{opcode:02x}")

        # LuaJIT comparison instructions conditionally skip the following JMP.
        # We only select a path when the compared register is proven to come
        # from getPlatformName and the literal is a recognized host/console
        # identifier.  This removes NS/XBox/etc. overrides without guessing at
        # unrelated application conditions.
        if 0x00 <= opcode <= 0x11 and pc + 1 < instruction_count:
            next_offset = (pc + 1) * 4
            if code[next_offset] == 0x58:
                equals: bool | None = None
                if opcode in {0x06, 0x07}:
                    left = _register(registers, a)
                    right = _constant(prototype.gc_constants, d)
                    equals = _host_platform_equals(left, right)
                if equals is not None:
                    condition = equals if opcode == 0x06 else not equals
                    if condition:
                        target_pc = _jump_target(
                            pc, code[next_offset : next_offset + 4]
                        )
                        if not 0 <= target_pc <= instruction_count:
                            raise LuaJitMappingError(
                                "LuaJIT branch target is outside its prototype"
                            )
                        if target_pc <= pc + 1:
                            raise LuaJitMappingError(
                                "backward platform-guarded branches are unsupported"
                            )
                        pc = target_pc
                    else:
                        pc += 2
                    continue

                # An unresolved branch makes any table mutation on the chosen
                # scan path ambiguous.  Skip the conditional JMP (fallthrough)
                # and conservatively reject configs affected afterwards.
                ambiguous_control = True
                pc += 2
                continue

        if opcode == _OP_MOV:
            _set_register(registers, a, _register(registers, d))
        elif opcode == _OP_KSTR:
            _set_register(registers, a, _constant(prototype.gc_constants, d))
        elif opcode == _OP_KCDATA:
            _constant(prototype.gc_constants, d)  # Validate the operand.
            _set_register(registers, a, _UNKNOWN)
        elif opcode == _OP_KSHORT:
            value = d - 0x10000 if d & 0x8000 else d
            _set_register(registers, a, value)
        elif opcode == _OP_KNUM:
            _set_register(
                registers, a, _number_constant(prototype.number_constants, d)
            )
        elif opcode == _OP_KPRI:
            primitives = (None, False, True)
            if d >= len(primitives):
                raise LuaJitMappingError("LuaJIT KPRI has an invalid primitive")
            _set_register(registers, a, primitives[d])
        elif opcode == _OP_KNIL:
            if a > d:
                raise LuaJitMappingError("LuaJIT KNIL has an invalid register range")
            _register(registers, a)
            _register(registers, d)
            for index in range(a, d + 1):
                registers[index] = None
        elif opcode == _OP_TNEW:
            budget.add_table(0)
            _set_register(registers, a, _Table())
        elif opcode == _OP_TDUP:
            template = _constant(prototype.gc_constants, d)
            if not isinstance(template, _Table):
                raise LuaJitMappingError("LuaJIT TDUP does not reference a table")
            _set_register(registers, a, _clone_table(template, budget))
        elif opcode == _OP_GGET:
            name = _constant(prototype.gc_constants, d)
            value: object = _Symbol((name,)) if isinstance(name, str) else _UNKNOWN
            _set_register(registers, a, value)
        elif opcode in {_OP_TGETV, _OP_TGETS, _OP_TGETB}:
            target = _register(registers, b)
            if opcode == _OP_TGETV:
                key = _register(registers, c)
            elif opcode == _OP_TGETS:
                key = _constant(prototype.gc_constants, c)
            else:
                key = c
            _set_register(registers, a, _lookup_table(target, key))
        elif opcode == _OP_TGETR:
            # TGETR operates on an internal array reference, not an ordinary
            # Lua table lookup; propagating a guessed value would be unsafe.
            _set_register(registers, a, _UNKNOWN)
        elif opcode in {_OP_TSETV, _OP_TSETS, _OP_TSETB}:
            value = _register(registers, a)
            target = _register(registers, b)
            if opcode == _OP_TSETV:
                key = _register(registers, c)
            elif opcode == _OP_TSETS:
                key = _constant(prototype.gc_constants, c)
            else:
                key = c
            _assign(
                target,
                key,
                value,
                budget=budget,
                captures=final_captures,
                ambiguous_control=ambiguous_control,
            )
        elif opcode == _OP_TSETM:
            target_index = a - 1
            target = _register(registers, target_index)
            if isinstance(target, _Table):
                target.tainted = True
        elif opcode == _OP_TSETR:
            target = _register(registers, b)
            if isinstance(target, _Table):
                target.tainted = True
        elif opcode in {_OP_CALLM, _OP_CALL}:
            callable_value = _register(registers, a)
            _taint_call_arguments(registers, a, c)
            _clear_call_results(registers, a, b)
            if (
                b == 2
                and isinstance(callable_value, _Symbol)
                and callable_value.path
                and callable_value.path[-1] == "getPlatformName"
            ):
                _set_register(registers, a, _HOST_PLATFORM)
        elif opcode in {0x43, 0x44}:  # CALLMT / CALLT tail calls.
            _taint_call_arguments(registers, a, d)
            break
        elif opcode in {_OP_ITERC, _OP_ITERN, _OP_VARG}:
            _clear_call_results(registers, a, b)
        elif opcode in {0x0C, 0x0D}:  # ISTC / ISFC conditionally copy to A.
            _set_register(registers, a, _UNKNOWN)
        elif 0x13 <= opcode <= 0x26:  # Unary, arithmetic, concat.
            _set_register(registers, a, _UNKNOWN)
        elif opcode in {0x2D, 0x33}:  # UGET / FNEW.
            _set_register(registers, a, _UNKNOWN)
        elif opcode in {0x4D, 0x4E, 0x4F, 0x50, 0x51}:
            # Numeric-loop instructions own four consecutive registers.
            _clear_range(registers, a, 4)
        elif opcode in {0x52, 0x53, 0x54}:
            _clear_range(registers, max(0, a - 1), 4)
        elif opcode == 0x58:  # JMP.
            relative = d - 0x8000
            target_pc = pc + 1 + relative
            if not 0 <= target_pc <= instruction_count:
                raise LuaJitMappingError(
                    "LuaJIT branch target is outside its prototype"
                )
            if target_pc > pc:
                pc = target_pc
                continue
            # Do not execute loops.  Scan a backward edge once and taint any
            # later literal construction as control-flow dependent.
            ambiguous_control = True
        elif opcode in {0x49, 0x4A, 0x4B, 0x4C}:  # Returns.
            break
        # Comparisons, tests without copy, stores to globals/upvalues,
        # branches, returns, loop markers, and function headers do not create
        # recoverable literal values and need no register update here.
        pc += 1

    for name, value in final_captures.items():
        if isinstance(value, _Table):
            captures.append((name, value))


class _CandidateRejected(Exception):
    pass


@dataclass(slots=True)
class _OutputBudget:
    items: int = 0

    def add(self, count: int) -> None:
        self.items += count
        if self.items > _MAX_OUTPUT_ITEMS:
            raise LuaJitMappingError("recovered mapping output exceeds safety limit")


def _materialize(
    value: object,
    *,
    budget: _OutputBudget,
    active: set[int],
    depth: int,
) -> object:
    if depth > _MAX_OUTPUT_DEPTH:
        raise _CandidateRejected("mapping table nesting is too deep")
    if value is _UNKNOWN:
        raise _CandidateRejected("mapping contains an unknown value")
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _CandidateRejected("mapping contains a non-finite number")
        return value
    if isinstance(value, bytes):
        raise _CandidateRejected("mapping contains a non-UTF-8 string")
    if not isinstance(value, _Table) or value.tainted:
        raise _CandidateRejected("mapping contains an unsupported value")

    identity = id(value)
    if identity in active:
        raise _CandidateRejected("mapping table is cyclic")
    active.add(identity)
    try:
        keys = tuple(value.values)
        budget.add(len(keys))
        if not keys:
            return {}

        if all(key.kind == "number" and type(key.value) is int for key in keys):
            indexes = sorted(int(key.value) for key in keys)
            if indexes != list(range(1, len(indexes) + 1)):
                raise _CandidateRejected("mapping array is sparse or non-positive")
            return [
                _materialize(
                    value.values[_LuaKey("number", index)],
                    budget=budget,
                    active=active,
                    depth=depth + 1,
                )
                for index in indexes
            ]

        if not all(
            key.kind == "string" and isinstance(key.value, str) for key in keys
        ):
            raise _CandidateRejected("mapping object has a non-string key")
        return {
            str(key.value): _materialize(
                value.values[key],
                budget=budget,
                active=active,
                depth=depth + 1,
            )
            for key in keys
        }
    finally:
        active.remove(identity)


def _valid_text(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= _MAX_TEXT_LENGTH
        and "\x00" not in value
    )


def _is_mapping_config(document: object) -> bool:
    if not isinstance(document, dict):
        return False
    mappings = document.get("mappings")
    if not isinstance(mappings, dict) or not mappings:
        return False
    if not set(mappings).issubset(_KNOWN_DEVICES):
        return False
    if not set(mappings).intersection(_KNOWN_DEVICES):
        return False

    binding_count = 0
    for source, actions in mappings.items():
        if source not in _KNOWN_DEVICES or not isinstance(actions, dict):
            return False
        if len(actions) > _MAX_ACTIONS_PER_DEVICE:
            return False
        for action, binding in actions.items():
            if not _valid_text(action):
                return False
            if binding is False:
                binding_count += 1
            elif _valid_text(binding):
                binding_count += 1
            elif isinstance(binding, list):
                if not 1 <= len(binding) <= _MAX_BINDINGS_PER_ACTION:
                    return False
                if not all(_valid_text(item) for item in binding):
                    return False
                binding_count += len(binding)
            else:
                return False
    return binding_count > 0


def extract_mapping_configs(data: bytes) -> dict[str, object]:
    """Recover literal ``self.<name>MappingConfig`` objects from a raw dump.

    Args:
        data: Complete, raw LuaJIT v2 bytecode dump (including ``ESC LJ``
            header and final zero prototype terminator).

    Returns:
        A deterministic dictionary keyed by the exact assigned property name,
        for example ``defaultInputMappingConfig`` and
        ``farmDefaultInputMappingConfig``.  Values are detached, JSON-like
        Python dictionaries/lists/scalars containing the validated
        ``mappings`` field; unrelated outer metadata is omitted.  Invalid
        look-alike tables are omitted.

    Raises:
        TypeError: If *data* is not bytes-like.
        LuaJitMappingError: If the dump is malformed/unsupported, a resource
            bound is exceeded, or the same config name resolves to conflicting
            valid literal objects.

    This function performs parsing and static data-flow recovery only.  It
    never executes LuaJIT instructions.
    """

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data must be a bytes-like object")
    if len(data) > _MAX_INPUT_BYTES:
        raise LuaJitMappingError("LuaJIT dump exceeds the input safety limit")
    raw = bytes(data)
    parsed = _parse_dump(raw)
    receiver_methods = _discover_receiver_methods(parsed)

    execution_budget = _ExecutionBudget()
    captures: list[tuple[str, _Table]] = []
    for prototype_index in sorted(receiver_methods):
        _interpret_prototype(
            parsed.prototypes[prototype_index],
            budget=execution_budget,
            captures=captures,
            assume_receiver=True,
        )

    output_budget = _OutputBudget()
    recovered: dict[str, object] = {}
    for name, table in captures:
        if table.tainted:
            continue
        mappings_value = table.lookup("mappings")
        try:
            mappings = _materialize(
                mappings_value,
                budget=output_budget,
                active=set(),
                depth=0,
            )
        except _CandidateRejected:
            continue
        document: object = {"mappings": mappings}
        if not _is_mapping_config(document):
            continue
        previous = recovered.get(name, _UNKNOWN)
        if previous is not _UNKNOWN and previous != document:
            raise LuaJitMappingError(
                f"conflicting literal mapping configs share the name {name!r}"
            )
        recovered[name] = document
        if len(recovered) > _MAX_CONFIGS:
            raise LuaJitMappingError("too many valid mapping configs")

    return dict(sorted(recovered.items()))
