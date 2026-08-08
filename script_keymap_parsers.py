"""Static parsers for key maps embedded in common game script files.

Nothing in this module imports, evaluates, or executes a game script.  It only
accepts small, literal table/object fragments with an explicit key-map root
name.  This deliberately conservative contract makes it suitable for scanning
untrusted game installations.

Recognized forms include:

* Ren'Py ``config.keymap = {...}`` and ``default keymap = {...}`` dictionaries;
* LÖVE/Lua ``controls = {...}`` / ``keybindings = {...}`` literal tables;
* JavaScript/JSON-like ``controls`` / ``keybindings`` literal objects.

All successful results use the recorder's native ``{input: record}`` shape.
At least three statically valid action bindings are required.  Loose text,
computed expressions, calls, variables, localization maps and visual settings
are rejected.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable


Keymap = dict[str, dict[str, str]]

MAX_SCRIPT_BYTES = 1024 * 1024
MAX_SCRIPT_TOKENS = 24_000
MAX_SCRIPT_NESTING = 32
MAX_SCRIPT_NODES = 20_000
MAX_SCRIPT_ACTIONS = 512
MAX_BINDINGS_PER_ACTION = 8
MIN_VALID_BINDINGS = 3

SCRIPT_KEYMAP_SUFFIXES = frozenset(
    {".rpy", ".py", ".lua", ".js", ".cjs", ".mjs", ".json", ".json5", ".gml"}
)


@dataclass(frozen=True)
class ScriptKeymapParse:
    """Result of parsing one script without executing it."""

    keymap: Keymap
    recognized_config: bool
    authoritative: bool
    schema: str = ""


@dataclass(frozen=True)
class _Scalar:
    text: str
    quoted: bool


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str


_ROOT_NAMES = frozenset(
    {
        "bindings",
        "controlbindings",
        "controlmap",
        "controls",
        "inputbindings",
        "inputmap",
        "keyboardcontrols",
        "keyboardmapping",
        "keybindings",
        "keymap",
        "keymapper",
        "keymaps",
    }
)

_WRAPPER_NAMES = frozenset({"actions", "bindings", "keys", "mapping", "mappings"})

_DEVICE_GROUPS = {
    "keyboard": "keyboard",
    "keys": "keyboard",
    "mouse": "mouse",
    "mousebuttons": "mouse",
    "controller": "gamepad",
    "gamepad": "gamepad",
    "joystick": "gamepad",
    "pad": "gamepad",
}

_BINDING_FIELDS = {
    "bind": None,
    "binding": None,
    "bindings": None,
    "button": None,
    "buttons": None,
    "default": None,
    "input": None,
    "inputs": None,
    "key": "keyboard",
    "keys": "keyboard",
    "keyboard": "keyboard",
    "mouse": "mouse",
    "mousebutton": "mouse",
    "primary": None,
    "secondary": None,
    "alternate": None,
    "controller": "gamepad",
    "gamepad": "gamepad",
    "joystick": "gamepad",
    "pad": "gamepad",
}

_METADATA_ACTIONS = frozenset(
    {
        "alpha",
        "background",
        "blue",
        "border",
        "brightness",
        "color",
        "colour",
        "enabled",
        "font",
        "foreground",
        "gamma",
        "green",
        "height",
        "hue",
        "language",
        "layout",
        "locale",
        "name",
        "opacity",
        "quality",
        "red",
        "resolution",
        "saturation",
        "scale",
        "scheme",
        "size",
        "style",
        "text",
        "texture",
        "theme",
        "version",
        "width",
    }
)

_KEYBOARD_ALIASES = {
    "backspace": "Backspace",
    "capslock": "CapsLock",
    "delete": "Delete",
    "del": "Delete",
    "down": "Down",
    "downarrow": "Down",
    "arrowdown": "Down",
    "end": "End",
    "enter": "Enter",
    "escape": "Esc",
    "esc": "Esc",
    "home": "Home",
    "insert": "Insert",
    "left": "Left",
    "leftarrow": "Left",
    "arrowleft": "Left",
    "leftalt": "Alt",
    "lalt": "Alt",
    "leftcontrol": "Ctrl",
    "leftctrl": "Ctrl",
    "lctrl": "Ctrl",
    "leftmeta": "Win",
    "leftsuper": "Win",
    "leftshift": "Shift",
    "lshift": "Shift",
    "menu": "Menu",
    "numlock": "NumLock",
    "pagedown": "PageDown",
    "pageup": "PageUp",
    "pause": "Pause",
    "printscreen": "PrintScreen",
    "return": "Enter",
    "right": "Right",
    "rightarrow": "Right",
    "arrowright": "Right",
    "rightalt": "Alt",
    "ralt": "Alt",
    "rightcontrol": "Ctrl",
    "rightctrl": "Ctrl",
    "rctrl": "Ctrl",
    "rightmeta": "Win",
    "rightsuper": "Win",
    "rightshift": "Shift",
    "rshift": "Shift",
    "scrolllock": "ScrollLock",
    "space": "Space",
    "spacebar": "Space",
    "tab": "Tab",
    "up": "Up",
    "uparrow": "Up",
    "arrowup": "Up",
    "alt": "Alt",
    "control": "Ctrl",
    "ctrl": "Ctrl",
    "meta": "Win",
    "shift": "Shift",
    "super": "Win",
    "win": "Win",
    "windows": "Win",
    "backquote": "Tilde",
    "grave": "Tilde",
    "tilde": "Tilde",
    "minus": "-",
    "equals": "=",
    "equal": "=",
    "comma": ",",
    "period": ".",
    "slash": "/",
    "semicolon": ";",
    "apostrophe": "'",
    "quote": "'",
    "leftbracket": "[",
    "rightbracket": "]",
    "backslash": "\\",
}

_MOUSE_ALIASES = {
    "left": "leftClick",
    "leftclick": "leftClick",
    "mouse0": "leftClick",
    "mouse1": "leftClick",
    "mousebutton1": "leftClick",
    "button1": "leftClick",
    "leftbutton": "leftClick",
    "mousedown1": "leftClick",
    "mouseup1": "leftClick",
    "middle": "middleClick",
    "middleclick": "middleClick",
    "mouse2": "rightClick",
    "mousebutton2": "rightClick",
    "button2": "rightClick",
    "rightbutton": "rightClick",
    "mousedown2": "middleClick",
    "mouseup2": "middleClick",
    "right": "rightClick",
    "rightclick": "rightClick",
    "mouse3": "middleClick",
    "mousebutton3": "middleClick",
    "button3": "middleClick",
    "middlebutton": "middleClick",
    "mousedown3": "rightClick",
    "mouseup3": "rightClick",
    "mouse4": "mouseButton4",
    "mousebutton4": "mouseButton4",
    "mouse5": "mouseButton5",
    "mousebutton5": "mouseButton5",
    "mousedown4": "mouseWheelUp",
    "mouseup4": "mouseWheelUp",
    "wheelup": "mouseWheelUp",
    "mousewheelup": "mouseWheelUp",
    "mousedown5": "mouseWheelDown",
    "mouseup5": "mouseWheelDown",
    "wheeldown": "mouseWheelDown",
    "mousewheeldown": "mouseWheelDown",
}

_GAMEPAD_ALIASES = {
    "a": "gamepadA",
    "b": "gamepadB",
    "x": "gamepadX",
    "y": "gamepadY",
    "buttona": "gamepadA",
    "buttonb": "gamepadB",
    "buttonx": "gamepadX",
    "buttony": "gamepadY",
    "buttonsouth": "gamepadA",
    "buttoneast": "gamepadB",
    "buttonwest": "gamepadX",
    "buttonnorth": "gamepadY",
    "controllerfacebuttonsouth": "gamepadA",
    "controllerfacebuttoneast": "gamepadB",
    "controllerfacebuttonwest": "gamepadX",
    "controllerfacebuttonnorth": "gamepadY",
    "sdlcontrollerbuttona": "gamepadA",
    "sdlcontrollerbuttonb": "gamepadB",
    "sdlcontrollerbuttonx": "gamepadX",
    "sdlcontrollerbuttony": "gamepadY",
    "gamepada": "gamepadA",
    "gamepadb": "gamepadB",
    "gamepadx": "gamepadX",
    "gamepady": "gamepadY",
    "leftbumper": "gamepadLeftShoulder",
    "leftshoulder": "gamepadLeftShoulder",
    "lb": "gamepadLeftShoulder",
    "rightbumper": "gamepadRightShoulder",
    "rightshoulder": "gamepadRightShoulder",
    "rb": "gamepadRightShoulder",
    "lefttrigger": "gamepadLeftTrigger",
    "lt": "gamepadLeftTrigger",
    "righttrigger": "gamepadRightTrigger",
    "rt": "gamepadRightTrigger",
    "leftstick": "gamepadLeftThumb",
    "leftthumb": "gamepadLeftThumb",
    "l3": "gamepadLeftThumb",
    "rightstick": "gamepadRightThumb",
    "rightthumb": "gamepadRightThumb",
    "r3": "gamepadRightThumb",
    "back": "gamepadBack",
    "select": "gamepadBack",
    "start": "gamepadStart",
    "dpadup": "gamepadDpadUp",
    "dpaddown": "gamepadDpadDown",
    "dpadleft": "gamepadDpadLeft",
    "dpadright": "gamepadDpadRight",
    "dpleft": "gamepadDpadLeft",
    "dpright": "gamepadDpadRight",
    "dpup": "gamepadDpadUp",
    "dpdown": "gamepadDpadDown",
}

_UNBOUND = frozenset({"", "disabled", "none", "null", "unbound", "undefined", "unknown"})
_LITERAL_KEYS = frozenset({"-", "=", "'", ",", ".", "/", ";", "[", "]", "\\"})
_MODIFIER_ORDER = ("Ctrl", "Alt", "Shift", "Win")

_GAME_MAKER_VK_ALIASES = {
    "vkenter": "Enter",
    "vkreturn": "Enter",
    "vkshift": "Shift",
    "vkcontrol": "Ctrl",
    "vkalt": "Alt",
    "vkescape": "Esc",
    "vkspace": "Space",
    "vkbackspace": "Backspace",
    "vktab": "Tab",
    "vkpause": "Pause",
    "vkprintscreen": "PrintScreen",
    "vkleft": "Left",
    "vkright": "Right",
    "vkup": "Up",
    "vkdown": "Down",
    "vkhome": "Home",
    "vkend": "End",
    "vkdelete": "Delete",
    "vkinsert": "Insert",
    "vkpageup": "PageUp",
    "vkpagedown": "PageDown",
    "vkadd": "NumPadAdd",
    "vksubtract": "NumPadSubtract",
    "vkmultiply": "NumPadMultiply",
    "vkdivide": "NumPadDivide",
    "vkdecimal": "NumPadDecimal",
}


def is_script_keymap_candidate(file_name: str | Path) -> bool:
    """Return whether a file has a supported script/text suffix."""

    return Path(str(file_name or "")).suffix.casefold() in SCRIPT_KEYMAP_SUFFIXES


def parse_script_keymap_file(path: str | Path) -> ScriptKeymapParse:
    """Read and statically parse one bounded script file."""

    file_path = Path(path)
    try:
        with file_path.open("rb") as stream:
            raw = stream.read(MAX_SCRIPT_BYTES + 1)
    except (OSError, ValueError):
        return _unrecognized()
    if not raw or len(raw) > MAX_SCRIPT_BYTES:
        return _unrecognized()
    return parse_script_keymap(raw, source_name=file_path.name)


def parse_script_keymap(
    source: str | bytes,
    *,
    source_name: str | Path = "",
) -> ScriptKeymapParse:
    """Statically extract strongly structured action-to-input maps.

    Computed values are ignored rather than evaluated.  A map is recognized
    only when an explicit key-map root contains at least three valid bindings.
    """

    text = _decode_source(source)
    if text is None:
        return _unrecognized()

    renpy_maps = _parse_renpy_assignments(text)
    parsed = _best_map_result(renpy_maps, schema="renpy_keymap")
    if parsed.recognized_config:
        return parsed

    tokens = _tokenize(text)
    if tokens is None:
        return _unrecognized()
    table_maps = _parse_named_literal_tables(tokens)
    suffix = Path(str(source_name or "")).suffix.casefold()
    if suffix == ".lua":
        schema = "lua_action_table"
    elif suffix in {".js", ".cjs", ".mjs", ".json", ".json5"}:
        schema = "javascript_action_object"
    elif suffix == ".gml":
        schema = "gamemaker_action_table"
    else:
        schema = "script_action_map"
    return _best_map_result(table_maps, schema=schema)


def _decode_source(source: str | bytes) -> str | None:
    if isinstance(source, str):
        if not source:
            return None
        if len(source.encode("utf-8", errors="ignore")) > MAX_SCRIPT_BYTES:
            return None
        return source
    if not isinstance(source, bytes) or not source or len(source) > MAX_SCRIPT_BYTES:
        return None
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return source.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def _unrecognized() -> ScriptKeymapParse:
    return ScriptKeymapParse({}, False, False, "")


def _compact(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _best_map_result(candidates: Iterable[dict[object, object]], *, schema: str) -> ScriptKeymapParse:
    best: Keymap = {}
    best_count = 0
    best_schema = schema
    for candidate in candidates:
        parsed, valid_count, detected_schema = _convert_action_map(candidate)
        if valid_count >= MIN_VALID_BINDINGS and valid_count > best_count:
            best = parsed
            best_count = valid_count
            best_schema = detected_schema or schema
    if best_count < MIN_VALID_BINDINGS:
        return _unrecognized()
    # Script tables can be defaults or later be amended in another file, so
    # discovery should merge them rather than treating the first one as final.
    return ScriptKeymapParse(best, True, False, best_schema)


def _parse_renpy_assignments(text: str) -> list[dict[object, object]]:
    pattern = re.compile(
        r"(?m)^\s*(?:config\s*\.\s*keymap|default\s+keymap)\s*=\s*"
    )
    results: list[dict[object, object]] = []
    for match in pattern.finditer(text):
        brace = _skip_space_and_comments(text, match.end())
        if brace >= len(text) or text[brace] != "{":
            continue
        end = _balanced_brace_end(text, brace)
        if end is None:
            continue
        try:
            expression = ast.parse(text[brace:end], mode="eval")
            value, count = _static_ast_value(expression.body, depth=0, count=0)
        except (MemoryError, RecursionError, SyntaxError, ValueError):
            continue
        if count <= MAX_SCRIPT_NODES and isinstance(value, dict):
            results.append(value)
    return results


def _skip_space_and_comments(text: str, start: int) -> int:
    index = start
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        if text[index] == "#":
            newline = text.find("\n", index + 1)
            index = len(text) if newline < 0 else newline + 1
            continue
        break
    return index


def _balanced_brace_end(text: str, start: int) -> int | None:
    depth = 0
    quote = ""
    triple = False
    escaped = False
    index = start
    while index < len(text):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif triple and text.startswith(quote * 3, index):
                quote = ""
                triple = False
                index += 2
            elif not triple and char == quote:
                quote = ""
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            triple = text.startswith(char * 3, index)
            if triple:
                index += 2
        elif char == "#":
            newline = text.find("\n", index + 1)
            index = len(text) if newline < 0 else newline
        elif char == "{":
            depth += 1
            if depth > MAX_SCRIPT_NESTING:
                return None
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
            if depth < 0:
                return None
        index += 1
    return None


def _static_ast_value(node: ast.AST, *, depth: int, count: int) -> tuple[object, int]:
    count += 1
    if depth > MAX_SCRIPT_NESTING or count > MAX_SCRIPT_NODES:
        raise ValueError("static literal limits exceeded")
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, count
    if isinstance(node, (ast.List, ast.Tuple)):
        values: list[object] = []
        for child in node.elts:
            value, count = _static_ast_value(child, depth=depth + 1, count=count)
            values.append(value)
        return values, count
    if isinstance(node, ast.Dict):
        if len(node.keys) > MAX_SCRIPT_ACTIONS:
            raise ValueError("too many actions")
        result: dict[object, object] = {}
        for key_node, value_node in zip(node.keys, node.values):
            if key_node is None:
                raise ValueError("dictionary unpacking is not static")
            key, count = _static_ast_value(key_node, depth=depth + 1, count=count)
            value, count = _static_ast_value(value_node, depth=depth + 1, count=count)
            if not isinstance(key, str):
                raise ValueError("non-string action")
            result[key] = value
        return result, count
    raise ValueError("computed expression")


def _tokenize(text: str) -> list[_Token] | None:
    tokens: list[_Token] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if text.startswith("//", index) or text.startswith("--", index):
            if text.startswith("--[[", index):
                end = text.find("]]", index + 4)
                if end < 0:
                    return None
                index = end + 2
            else:
                end = text.find("\n", index + 2)
                index = length if end < 0 else end + 1
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                return None
            index = end + 2
            continue
        if char == "#":
            end = text.find("\n", index + 1)
            index = length if end < 0 else end + 1
            continue
        if char in {'"', "'", "`"}:
            token, index = _read_script_string(text, index)
            if token is None:
                return None
            tokens.append(token)
        elif char.isalpha() or char in "_$":
            end = index + 1
            while end < length and (text[end].isalnum() or text[end] in "_$-"):
                end += 1
            tokens.append(_Token("ident", text[index:end]))
            index = end
        elif char.isdigit() or (char in "+-" and index + 1 < length and text[index + 1].isdigit()):
            end = index + 1
            while end < length and (text[end].isalnum() or text[end] in ".xX"):
                end += 1
            tokens.append(_Token("number", text[index:end]))
            index = end
        elif char in "{}[]:=,;().":
            tokens.append(_Token("punct", char))
            index += 1
        else:
            # Operators/calls/computed syntax remain explicit unsupported
            # tokens; a containing literal will consequently fail to parse.
            tokens.append(_Token("other", char))
            index += 1
        if len(tokens) > MAX_SCRIPT_TOKENS:
            return None
    return tokens


def _read_script_string(text: str, start: int) -> tuple[_Token | None, int]:
    quote = text[start]
    value: list[str] = []
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == quote:
            string_value = "".join(value)
            if quote == "`" and "${" in string_value:
                return None, index + 1
            return _Token("string", string_value), index + 1
        if char == "\\":
            index += 1
            if index >= len(text):
                return None, index
            escaped = text[index]
            simple = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}
            value.append(simple.get(escaped, escaped))
            index += 1
            continue
        if char in "\r\n" and quote != "`":
            return None, index
        value.append(char)
        index += 1
    return None, index


class _LiteralParser:
    def __init__(self, tokens: list[_Token], start: int) -> None:
        self.tokens = tokens
        self.position = start
        self.nodes = 0

    def parse(self) -> tuple[object, int] | None:
        try:
            value = self._value(0)
        except (IndexError, ValueError):
            return None
        return value, self.position

    def _touch(self, depth: int) -> None:
        self.nodes += 1
        if depth > MAX_SCRIPT_NESTING or self.nodes > MAX_SCRIPT_NODES:
            raise ValueError("literal limits exceeded")

    def _value(self, depth: int) -> object:
        self._touch(depth)
        token = self.tokens[self.position]
        if token.text == "{":
            return self._braced(depth + 1)
        if token.text == "[":
            return self._array(depth + 1)
        self.position += 1
        if token.kind == "string":
            return _Scalar(token.text, True)
        if token.kind in {"ident", "number"}:
            return _Scalar(token.text, False)
        raise ValueError("computed value")

    def _array(self, depth: int) -> list[object]:
        self.position += 1
        result: list[object] = []
        while self.position < len(self.tokens) and self.tokens[self.position].text != "]":
            result.append(self._value(depth))
            if len(result) > MAX_SCRIPT_ACTIONS:
                raise ValueError("too many entries")
            if self.position < len(self.tokens) and self.tokens[self.position].text in {",", ";"}:
                self.position += 1
            elif self.position < len(self.tokens) and self.tokens[self.position].text != "]":
                raise ValueError("invalid array")
        if self.position >= len(self.tokens):
            raise ValueError("unterminated array")
        self.position += 1
        return result

    def _braced(self, depth: int) -> object:
        self.position += 1
        pairs: dict[str, object] = {}
        sequence: list[object] = []
        while self.position < len(self.tokens) and self.tokens[self.position].text != "}":
            key = self._property_key()
            if key is not None:
                value = self._value(depth)
                pairs[key] = value
            else:
                sequence.append(self._value(depth))
            if len(pairs) + len(sequence) > MAX_SCRIPT_ACTIONS:
                raise ValueError("too many entries")
            if self.position < len(self.tokens) and self.tokens[self.position].text in {",", ";"}:
                self.position += 1
            elif self.position < len(self.tokens) and self.tokens[self.position].text != "}":
                raise ValueError("invalid table")
        if self.position >= len(self.tokens):
            raise ValueError("unterminated table")
        self.position += 1
        if pairs and sequence:
            raise ValueError("mixed tables are not accepted")
        return pairs if pairs else sequence

    def _property_key(self) -> str | None:
        start = self.position
        token = self.tokens[start]
        if token.kind in {"ident", "string", "number"}:
            if start + 1 < len(self.tokens) and self.tokens[start + 1].text in {":", "="}:
                self.position = start + 2
                return token.text
        if (
            token.text == "["
            and start + 3 < len(self.tokens)
            and self.tokens[start + 1].kind == "string"
            and self.tokens[start + 2].text == "]"
            and self.tokens[start + 3].text in {":", "="}
        ):
            self.position = start + 4
            return self.tokens[start + 1].text
        return None


def _parse_named_literal_tables(tokens: list[_Token]) -> list[dict[object, object]]:
    results: list[dict[object, object]] = []
    seen_starts: set[int] = set()
    for index, token in enumerate(tokens):
        if token.kind not in {"ident", "string"} or _compact(token.text) not in _ROOT_NAMES:
            continue
        if index + 2 >= len(tokens) or tokens[index + 1].text not in {"=", ":"}:
            continue
        if tokens[index + 2].text != "{" or index + 2 in seen_starts:
            continue
        parser = _LiteralParser(tokens, index + 2)
        parsed = parser.parse()
        if parsed is None:
            continue
        value, _end = parsed
        seen_starts.add(index + 2)
        if isinstance(value, dict):
            results.append(value)
    return results


def _convert_action_map(document: dict[object, object]) -> tuple[Keymap, int, str | None]:
    mappings: Keymap = {}
    valid_count = 0
    action_count = 0

    numeric_bindings = _convert_javascript_keycode_map(document)
    if numeric_bindings is not None:
        return numeric_bindings[0], numeric_bindings[1], "javascript_keycode_map"

    def consume(action: object, value: object, device_hint: str | None = None) -> None:
        nonlocal valid_count, action_count
        if action_count >= MAX_SCRIPT_ACTIONS or not _valid_action(action):
            return
        inputs = _extract_inputs(value, device_hint=device_hint, depth=0)
        if not inputs:
            return
        action_count += 1
        display_action = _display_action(str(action))
        for input_name, input_type in inputs[:MAX_BINDINGS_PER_ACTION]:
            _add_mapping(mappings, input_name, input_type, display_action)
            valid_count += 1

    # A common layout groups all actions under keyboard/gamepad, while another
    # wraps them in an ``actions`` member.  Only one structural level is opened.
    for key, value in document.items():
        compact_key = _compact(key)
        if compact_key in _DEVICE_GROUPS and isinstance(value, dict):
            for action, binding in value.items():
                consume(action, binding, _DEVICE_GROUPS[compact_key])
        elif compact_key in _WRAPPER_NAMES and isinstance(value, dict):
            for action, binding in value.items():
                consume(action, binding)
        else:
            consume(key, value)
    return mappings, valid_count, None


def _convert_javascript_keycode_map(document: dict[object, object]) -> tuple[Keymap, int] | None:
    """Convert RPG Maker/NW.js ``Input.keyMapper``-style literal maps."""

    numeric_items = [(str(key), value) for key, value in document.items() if str(key).isdigit()]
    if len(numeric_items) < MIN_VALID_BINDINGS or len(numeric_items) != len(document):
        return None
    mappings: Keymap = {}
    valid_count = 0
    for raw_code, raw_action in numeric_items[:MAX_SCRIPT_ACTIONS]:
        if isinstance(raw_action, _Scalar):
            action = raw_action.text if raw_action.quoted else ""
        elif isinstance(raw_action, str):
            action = raw_action
        else:
            action = ""
        input_name = _javascript_keycode_name(int(raw_code))
        if input_name is None or not _valid_action(action):
            continue
        _add_mapping(mappings, input_name, "keyboard", _display_action(action))
        valid_count += 1
    return mappings, valid_count


def _javascript_keycode_name(code: int) -> str | None:
    fixed = {
        8: "Backspace",
        9: "Tab",
        13: "Enter",
        16: "Shift",
        17: "Ctrl",
        18: "Alt",
        19: "Pause",
        20: "CapsLock",
        27: "Esc",
        32: "Space",
        33: "PageUp",
        34: "PageDown",
        35: "End",
        36: "Home",
        37: "Left",
        38: "Up",
        39: "Right",
        40: "Down",
        45: "Insert",
        46: "Delete",
        91: "Win",
        92: "Win",
        93: "Menu",
        106: "NumPadMultiply",
        107: "NumPadAdd",
        109: "NumPadSubtract",
        110: "NumPadDecimal",
        111: "NumPadDivide",
        144: "NumLock",
        145: "ScrollLock",
        186: ";",
        187: "=",
        188: ",",
        189: "-",
        190: ".",
        191: "/",
        192: "Tilde",
        219: "[",
        220: "\\",
        221: "]",
        222: "'",
    }
    if code in fixed:
        return fixed[code]
    if 48 <= code <= 57:
        return chr(code)
    if 65 <= code <= 90:
        return chr(code)
    if 96 <= code <= 105:
        return f"NumPad{code - 96}"
    if 112 <= code <= 135:
        return f"F{code - 111}"
    return None


def _valid_action(action: object) -> bool:
    if not isinstance(action, str):
        return False
    clean = action.strip()
    compact = _compact(clean)
    if not clean or len(clean) > 128 or not re.search(r"[A-Za-z]", clean):
        return False
    if compact in _ROOT_NAMES or compact in _DEVICE_GROUPS or compact in _BINDING_FIELDS:
        return False
    if compact in _METADATA_ACTIONS:
        return False
    return True


def _extract_inputs(
    value: object,
    *,
    device_hint: str | None,
    depth: int,
) -> list[tuple[str, str]]:
    if depth > 4:
        return []
    if isinstance(value, str):
        canonical = _canonical_input(value, device_hint=device_hint)
        return [canonical] if canonical else []
    if isinstance(value, _Scalar):
        if not value.quoted:
            if device_hint == "mouse" and value.text.isdigit():
                mouse_button = _MOUSE_ALIASES.get(f"mouse{value.text}")
                return [(mouse_button, "mouse")] if mouse_button else []
            game_maker_key = _canonical_game_maker_vk(value.text)
            return [(game_maker_key, "keyboard")] if game_maker_key else []
        canonical = _canonical_input(value.text, device_hint=device_hint)
        return [canonical] if canonical else []
    if isinstance(value, list):
        result: list[tuple[str, str]] = []
        for item in value[:MAX_BINDINGS_PER_ACTION]:
            result.extend(_extract_inputs(item, device_hint=device_hint, depth=depth + 1))
        return _deduplicate(result)
    if isinstance(value, dict):
        result = []
        for field, child in list(value.items())[:32]:
            compact_field = _compact(field)
            if compact_field not in _BINDING_FIELDS:
                continue
            child_hint = _BINDING_FIELDS[compact_field] or device_hint
            result.extend(_extract_inputs(child, device_hint=child_hint, depth=depth + 1))
        return _deduplicate(result)
    return []


def _deduplicate(values: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _canonical_input(raw_input: object, *, device_hint: str | None = None) -> tuple[str, str] | None:
    if not isinstance(raw_input, str):
        return None
    name = raw_input.strip().strip('"\'')
    if not name:
        return None

    unity_path = re.fullmatch(r"<([^>]+)>/(.+)", name)
    if unity_path:
        device_hint = {
            "keyboard": "keyboard",
            "mouse": "mouse",
            "gamepad": "gamepad",
        }.get(unity_path.group(1).casefold())
        if device_hint is None:
            return None
        name = unity_path.group(2)

    # Ren'Py event prefixes are declarative key specifications, not Python.
    renpy = _canonical_renpy_spec(name)
    if renpy is not None and device_hint in {None, "keyboard", "mouse", "gamepad"}:
        return renpy

    chord_parts = _split_chord(name)
    if len(chord_parts) > 1:
        modifiers: set[str] = set()
        main_key = ""
        for part in chord_parts:
            canonical = _canonical_single(part, device_hint="keyboard")
            if canonical is None or canonical[1] != "keyboard":
                return None
            if canonical[0] in _MODIFIER_ORDER:
                modifiers.add(canonical[0])
            elif main_key:
                return None
            else:
                main_key = canonical[0]
        if not main_key or not modifiers:
            return None
        prefix = [modifier for modifier in _MODIFIER_ORDER if modifier in modifiers]
        return ("+".join([*prefix, main_key]), "keyboard")

    return _canonical_single(name, device_hint=device_hint)


def _canonical_renpy_spec(name: str) -> tuple[str, str] | None:
    compact = _compact(name)
    if compact in _MOUSE_ALIASES and (
        compact.startswith("mousedown") or compact.startswith("mouseup")
    ):
        return _MOUSE_ALIASES[compact], "mouse"

    pad_match = re.fullmatch(r"(?:pad|joy|gamepad)[_:-](.+)", name, flags=re.IGNORECASE)
    if pad_match:
        pad_name = re.sub(r"(?i)(?:[_-](?:press|release))$", "", pad_match.group(1))
        mapped = _GAMEPAD_ALIASES.get(_compact(pad_name))
        return (mapped, "gamepad") if mapped else None

    match = re.fullmatch(
        r"(?P<prefix>(?:(?:any|anymod|repeat|norepeat|keydown|keyup|ctrl|control|alt|shift|meta|super|osctrl|noshift|noalt|nometa|noosctrl)[_-]+)*)K_(?P<key>.+)",
        name,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    prefixes = {_compact(part) for part in re.split(r"[_-]+", match.group("prefix") or "") if part}
    modifier_map = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift", "meta": "Win", "super": "Win", "osctrl": "Win"}
    modifiers = {mapped for part, mapped in modifier_map.items() if part in prefixes}
    base = _canonical_single(match.group("key"), device_hint="keyboard")
    if base is None:
        return None
    if not modifiers:
        return base
    if base[0] in _MODIFIER_ORDER:
        modifiers.add(base[0])
        return None
    prefix = [modifier for modifier in _MODIFIER_ORDER if modifier in modifiers]
    return "+".join([*prefix, base[0]]), "keyboard"


def _split_chord(name: str) -> list[str]:
    if "+" in name:
        return [part.strip() for part in name.split("+") if part.strip()]
    if re.match(r"(?i)^(?:ctrl|control|alt|shift|meta|super|win)-", name):
        return [part.strip() for part in name.split("-") if part.strip()]
    return [name]


def _canonical_single(name: str, *, device_hint: str | None) -> tuple[str, str] | None:
    clean = name.strip()
    compact = _compact(clean)
    if compact in _UNBOUND:
        return None

    if device_hint == "gamepad":
        mapped = _GAMEPAD_ALIASES.get(compact)
        return (mapped, "gamepad") if mapped else None
    if device_hint == "mouse":
        mapped = _MOUSE_ALIASES.get(compact)
        return (mapped, "mouse") if mapped else None

    if compact in _MOUSE_ALIASES and (
        compact.startswith("mouse") or compact.startswith("wheel") or compact.endswith("click")
    ):
        return _MOUSE_ALIASES[compact], "mouse"
    if compact in _GAMEPAD_ALIASES and (
        compact.startswith(("gamepad", "button", "controller", "sdlcontroller", "dpad", "dp"))
        or compact in {"lb", "rb", "lt", "rt", "l3", "r3"}
    ):
        return _GAMEPAD_ALIASES[compact], "gamepad"

    mapped = _KEYBOARD_ALIASES.get(compact)
    if mapped:
        return mapped, "keyboard"
    if clean in _LITERAL_KEYS:
        return clean, "keyboard"

    key_code = re.fullmatch(r"key([a-z])", compact)
    if key_code:
        return key_code.group(1).upper(), "keyboard"
    digit_code = re.fullmatch(r"(?:digit|key|d)([0-9])", compact)
    if digit_code:
        return digit_code.group(1), "keyboard"
    if re.fullmatch(r"[a-z0-9]", compact):
        return compact.upper(), "keyboard"
    function_key = re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", compact)
    if function_key:
        return f"F{function_key.group(1)}", "keyboard"
    numpad = re.fullmatch(r"(?:kp|numpad)([0-9])", compact)
    if numpad:
        return f"NumPad{numpad.group(1)}", "keyboard"
    numpad_aliases = {
        "kpenter": "NumPadEnter",
        "kpplus": "NumPadAdd",
        "kpminus": "NumPadSubtract",
        "kpmultiply": "NumPadMultiply",
        "kpdivide": "NumPadDivide",
        "kpperiod": "NumPadDecimal",
        "numpadenter": "NumPadEnter",
        "numpadadd": "NumPadAdd",
        "numpadsubtract": "NumPadSubtract",
        "numpadmultiply": "NumPadMultiply",
        "numpaddivide": "NumPadDivide",
        "numpaddecimal": "NumPadDecimal",
    }
    if compact in numpad_aliases:
        return numpad_aliases[compact], "keyboard"
    return None


def _canonical_game_maker_vk(value: str) -> str | None:
    """Resolve only GameMaker's documented ``vk_*`` literal constants."""

    compact = _compact(value)
    mapped = _GAME_MAKER_VK_ALIASES.get(compact)
    if mapped:
        return mapped
    function_key = re.fullmatch(r"vkf([1-9]|1[0-2])", compact)
    if function_key:
        return f"F{function_key.group(1)}"
    numpad = re.fullmatch(r"vknumpad([0-9])", compact)
    if numpad:
        return f"NumPad{numpad.group(1)}"
    return None


def _display_action(action: str) -> str:
    clean = action.strip().replace("::", "/").replace("/", " / ")
    clean = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", clean)
    clean = re.sub(r"[_\-.]+", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:1].upper() + clean[1:] if clean else action.strip()


def _movement_direction(action: str) -> str | None:
    compact = _compact(action)
    directions = {
        "W": {"moveup", "movementup", "walkup", "up", "north"},
        "B": {"movedown", "movementdown", "walkdown", "down", "south"},
        "L": {"moveleft", "movementleft", "walkleft", "left", "west"},
        "R": {"moveright", "movementright", "walkright", "right", "east"},
    }
    return next((direction for direction, names in directions.items() if compact in names), None)


def _add_mapping(mappings: Keymap, input_name: str, input_type: str, action: str) -> None:
    record = mappings.get(input_name)
    if record is None:
        record = {"type": input_type, "action": action}
        movement = _movement_direction(action)
        if movement:
            record["movement_direction"] = movement
        mappings[input_name] = record
        return
    actions = [part.strip() for part in record.get("action", "").split(" / ") if part.strip()]
    if action not in actions:
        record["action"] = " / ".join([*actions, action])
    movement = _movement_direction(action)
    if movement and "movement_direction" not in record:
        record["movement_direction"] = movement


__all__ = [
    "MAX_BINDINGS_PER_ACTION",
    "MAX_SCRIPT_ACTIONS",
    "MAX_SCRIPT_BYTES",
    "MAX_SCRIPT_NESTING",
    "MAX_SCRIPT_NODES",
    "MAX_SCRIPT_TOKENS",
    "MIN_VALID_BINDINGS",
    "SCRIPT_KEYMAP_SUFFIXES",
    "ScriptKeymapParse",
    "is_script_keymap_candidate",
    "parse_script_keymap",
    "parse_script_keymap_file",
]
