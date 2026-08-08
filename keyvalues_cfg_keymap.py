"""Bounded parsers for id Tech CFG and Valve KeyValues key bindings.

The module is deliberately independent from the recorder UI.  Its public
``keymap`` values use the recorder's native shape::

    {"W": {"type": "keyboard", "action": "Move forward",
           "movement_direction": "W"}}

Only two strong structures are recognized:

* id Tech/Quake-style ``bind``, ``unbind`` and ``unbindall`` console
  statements (for example q3config.cfg, DoomConfig.cfg and wolfconfig.cfg);
* direct input-to-command scalar pairs inside a Valve KeyValues/KV3 object
  named exactly ``bindings`` (including textual Source 2 ``*.vcfg`` files).

The parser never executes commands, expands ``exec`` files, follows includes,
or recursively walks decoded objects.  Text size, token count, token length,
nesting depth and emitted binding count are all capped.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterator


Keymap = dict[str, dict[str, str]]

MAX_KEY_CONFIG_BYTES = 2 * 1024 * 1024
MAX_KEY_CONFIG_CHARS = 2 * 1024 * 1024
MAX_CONSOLE_STATEMENTS = 20_000
MAX_STATEMENT_CHARS = 16_384
MAX_KEYVALUES_TOKENS = 50_000
MAX_KEYVALUES_TOKEN_CHARS = 4_096
MAX_KEYVALUES_NESTING = 64
MAX_PARSED_BINDINGS = 2_048


@dataclass(frozen=True)
class KeyvaluesCfgParse:
    """Result of parsing one text document.

    ``recognized_config`` is kept separate from ``keymap`` because an
    authoritative config may intentionally contain ``unbindall`` or an empty
    ``bindings`` object.
    """

    keymap: Keymap
    recognized_config: bool
    format_name: str = ""
    truncated: bool = False

    @property
    def recognized(self) -> bool:
        """Compatibility alias used by the other standalone adapters."""

        return self.recognized_config


_STRONG_EMPTY_KEYVALUES_NAMES = re.compile(
    r"(?:^|[_ .-])(?:user[_-]?keys?|key[_-]?bindings?|keybinds?|bindings?|binds?)"
    r"(?:[_ .-]|$)",
    re.IGNORECASE,
)


def is_keyvalues_cfg_candidate(file_name: str | Path) -> bool:
    """Return whether a file name is worth offering to this parser.

    Any CFG is accepted because id Tech games and mods use many custom names;
    recognition still requires an explicit bind command.  Textual VCFG files
    are likewise cheap to validate structurally.  Generic VDF/KV/TXT files are
    admitted only when their names explicitly suggest controls or bindings.
    """

    path = Path(file_name)
    name = path.name.casefold()
    suffix = path.suffix.casefold()
    if suffix in {".cfg", ".vcfg"}:
        return True
    if suffix not in {".vdf", ".kv", ".keyvalues", ".txt"}:
        return False
    compact = re.sub(r"[^a-z0-9]+", "", path.stem.casefold())
    return any(word in compact for word in ("bind", "key", "input", "control"))


_KEYBOARD_ALIASES = {
    "space": "Space",
    "spacebar": "Space",
    "enter": "Enter",
    "return": "Enter",
    "kpenter": "Enter",
    "tab": "Tab",
    "backspace": "Backspace",
    "escape": "Esc",
    "esc": "Esc",
    "pause": "Pause",
    "break": "Pause",
    "capslock": "CapsLock",
    "numlock": "NumLock",
    "scrolllock": "ScrollLock",
    "shift": "Shift",
    "lshift": "Shift",
    "rshift": "Shift",
    "leftshift": "Shift",
    "rightshift": "Shift",
    "ctrl": "Ctrl",
    "control": "Ctrl",
    "lctrl": "Ctrl",
    "rctrl": "Ctrl",
    "leftctrl": "Ctrl",
    "rightctrl": "Ctrl",
    "alt": "Alt",
    "lalt": "Alt",
    "ralt": "Alt",
    "leftalt": "Alt",
    "rightalt": "Alt",
    "win": "Win",
    "lwin": "Win",
    "rwin": "Win",
    "super": "Win",
    "uparrow": "Up",
    "up": "Up",
    "downarrow": "Down",
    "down": "Down",
    "leftarrow": "Left",
    "left": "Left",
    "rightarrow": "Right",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "ins": "Insert",
    "insert": "Insert",
    "del": "Delete",
    "delete": "Delete",
    "pgup": "PageUp",
    "pageup": "PageUp",
    "pgdn": "PageDown",
    "pagedown": "PageDown",
    "tilde": "Tilde",
    "grave": "Tilde",
    "backquote": "Tilde",
    "semicolon": ";",
    "minus": "-",
    "equals": "=",
    "equal": "=",
    "comma": ",",
    "period": ".",
    "slash": "/",
    "apostrophe": "'",
    "quote": "'",
    "leftbracket": "[",
    "rightbracket": "]",
    "backslash": "\\",
    "kpins": "NumPad0",
    "kpend": "NumPad1",
    "kpdownarrow": "NumPad2",
    "kppgdn": "NumPad3",
    "kpleftarrow": "NumPad4",
    "kp5": "NumPad5",
    "kprightarrow": "NumPad6",
    "kphome": "NumPad7",
    "kpuparrow": "NumPad8",
    "kppgup": "NumPad9",
    "kpslash": "NumPadDivide",
    "kpdivide": "NumPadDivide",
    "kpstar": "NumPadMultiply",
    "kpmultiply": "NumPadMultiply",
    "kpminus": "NumPadSubtract",
    "kpplus": "NumPadAdd",
    "kpdel": "NumPadDecimal",
    "kpdecimal": "NumPadDecimal",
    "kpnumlock": "NumLock",
}

_MOUSE_ALIASES = {
    "mouse1": "leftClick",
    "mouse2": "rightClick",
    "mouse3": "middleClick",
    "mouse4": "mouseButton4",
    "mouse5": "mouseButton5",
    "mwheelup": "mouseWheelUp",
    "wheeldown": "mouseWheelDown",
    "mwheeldown": "mouseWheelDown",
    "wheelup": "mouseWheelUp",
}

_GAMEPAD_ALIASES = {
    "joy1": "gamepadA",
    "joy2": "gamepadB",
    "joy3": "gamepadX",
    "joy4": "gamepadY",
    "joy5": "gamepadLeftShoulder",
    "joy6": "gamepadRightShoulder",
    "joy7": "gamepadBack",
    "joy8": "gamepadStart",
    "joy9": "gamepadLeftThumb",
    "joy10": "gamepadRightThumb",
    "aux1": "gamepadA",
    "aux2": "gamepadB",
    "aux3": "gamepadX",
    "aux4": "gamepadY",
    "xbuttona": "gamepadA",
    "xbuttonb": "gamepadB",
    "xbuttonx": "gamepadX",
    "xbuttony": "gamepadY",
    "xshoulderleft": "gamepadLeftShoulder",
    "xshoulderright": "gamepadRightShoulder",
    "xbuttonleftshoulder": "gamepadLeftShoulder",
    "xbuttonrightshoulder": "gamepadRightShoulder",
    "xtriggerleft": "gamepadLeftTrigger",
    "xtriggerright": "gamepadRightTrigger",
    "xback": "gamepadBack",
    "xstart": "gamepadStart",
    "xstick1": "gamepadLeftThumb",
    "xstick2": "gamepadRightThumb",
    "xbuttonback": "gamepadBack",
    "xbuttonstart": "gamepadStart",
    "xbuttonstick1": "gamepadLeftThumb",
    "xbuttonstick2": "gamepadRightThumb",
    "xdup": "gamepadDpadUp",
    "xddown": "gamepadDpadDown",
    "xdleft": "gamepadDpadLeft",
    "xdright": "gamepadDpadRight",
    "xdpadup": "gamepadDpadUp",
    "xdpaddown": "gamepadDpadDown",
    "xdpadleft": "gamepadDpadLeft",
    "xdpadright": "gamepadDpadRight",
    "xbuttonup": "gamepadDpadUp",
    "xbuttondown": "gamepadDpadDown",
    "xbuttonleft": "gamepadDpadLeft",
    "xbuttonright": "gamepadDpadRight",
    "povup": "gamepadDpadUp",
    "povdown": "gamepadDpadDown",
    "povleft": "gamepadDpadLeft",
    "povright": "gamepadDpadRight",
}

_LITERAL_KEYS = {"-", "=", "'", ",", ".", "/", ";", "[", "]", "\\"}
_UNBOUND_COMMANDS = {"", "none", "null", "unbound", "disabled", "<unbound>"}


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _canonical_input(raw_input: str) -> tuple[str, str] | None:
    name = raw_input.strip()
    if len(name) >= 2 and name[0] == name[-1] and name[0] in {'"', "'"}:
        name = name[1:-1].strip()
    if not name:
        return None
    if name in _LITERAL_KEYS:
        return name, "keyboard"
    compact = _compact(name)
    if compact in _MOUSE_ALIASES:
        return _MOUSE_ALIASES[compact], "mouse"
    if compact in _GAMEPAD_ALIASES:
        return _GAMEPAD_ALIASES[compact], "gamepad"
    if compact in _KEYBOARD_ALIASES:
        return _KEYBOARD_ALIASES[compact], "keyboard"
    if len(compact) == 1 and compact.isalnum():
        return compact.upper(), "keyboard"
    if re.fullmatch(r"f(?:[1-9]|1[0-9]|2[0-4])", compact):
        return compact.upper(), "keyboard"
    numpad = re.fullmatch(r"(?:kp|numpad)([0-9])", compact)
    if numpad:
        return f"NumPad{numpad.group(1)}", "keyboard"
    return None


_ACTION_LABELS = {
    "forward": "Move forward",
    "moveforward": "Move forward",
    "back": "Move backward",
    "backward": "Move backward",
    "moveback": "Move backward",
    "movebackward": "Move backward",
    "moveleft": "Move left",
    "moveright": "Move right",
    "moveup": "Jump",
    "movedown": "Crouch",
    "left": "Turn left",
    "right": "Turn right",
    "lookup": "Look up",
    "lookdown": "Look down",
    "attack": "Attack",
    "attack2": "Secondary attack",
    "altattack": "Secondary attack",
    "jump": "Jump",
    "duck": "Crouch",
    "crouch": "Crouch",
    "speed": "Run",
    "sprint": "Sprint",
    "use": "Use",
    "activate": "Use",
    "reload": "Reload",
    "zoom": "Zoom",
    "strafe": "Strafe",
    "scores": "Show scores",
    "showscores": "Show scores",
    "togglemenu": "Menu",
    "cancelselect": "Cancel",
    "pause": "Pause",
    "screenshot": "Screenshot",
    "screenshotjpeg": "Screenshot",
    "weapnext": "Next weapon",
    "weapprev": "Previous weapon",
    "invnext": "Next item",
    "invprev": "Previous item",
    "lastinv": "Previous weapon",
    "drop": "Drop",
    "voicerecord": "Voice chat",
    "messagemode": "Chat",
    "messagemode2": "Team chat",
    "buymenu": "Buy menu",
    "radio": "Radio menu",
    "spraymenu": "Spray menu",
}

_MOVEMENT_DIRECTIONS = {
    "forward": "W",
    "moveforward": "W",
    "back": "B",
    "backward": "B",
    "moveback": "B",
    "movebackward": "B",
    "moveleft": "L",
    "moveright": "R",
}


def _command_name(command: str) -> str:
    token = command.strip().split(None, 1)[0] if command.strip() else ""
    return token.lstrip("+_-").casefold()


def _humanize_one_command(command: str) -> str:
    raw = command.strip()
    if not raw:
        return ""
    first, _, arguments = raw.partition(" ")
    normalized = first.lstrip("+_-").casefold()
    if normalized in _ACTION_LABELS:
        return _ACTION_LABELS[normalized]
    if re.fullmatch(r"slot[0-9]+", normalized):
        return f"Weapon slot {normalized[4:]}"
    display = first.lstrip("+_-")
    display = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", display)
    display = re.sub(r"(?<=[A-Za-z])(?=[0-9])", " ", display)
    display = re.sub(r"[_./-]+", " ", display)
    display = re.sub(r"\s+", " ", display).strip()
    if arguments.strip():
        safe_arguments = re.sub(r"[/\r\n]+", ": ", arguments.strip())
        safe_arguments = re.sub(r"\s+", " ", safe_arguments).strip()
        display = f"{display} {safe_arguments}".strip()
    return (display[:1].upper() + display[1:])[:120] if display else ""


def _split_bound_commands(command_line: str) -> list[str]:
    # Quotes have already been removed around the whole bind value.  Console
    # command lists conventionally use semicolons; cap the number of labels so
    # a macro cannot inflate the emitted JSON.
    return [part.strip() for part in command_line.split(";") if part.strip()][:4]


def _mapping_for_command(
    canonical: tuple[str, str], command_line: str
) -> dict[str, str] | None:
    commands = _split_bound_commands(command_line)
    if not commands:
        return None
    labels: list[str] = []
    for command in commands:
        label = _humanize_one_command(command)
        if label and label not in labels:
            labels.append(label)
    if not labels:
        return None
    input_name, device_type = canonical
    mapping = {"type": device_type, "action": " / ".join(labels)[:240]}
    for command in commands:
        direction = _MOVEMENT_DIRECTIONS.get(_command_name(command))
        if direction:
            mapping["movement_direction"] = direction
            break
    return mapping


def _console_statements(text: str) -> tuple[list[str], bool]:
    """Split CFG statements without treating quoted semicolons as separators."""

    statements: list[str] = []
    current: list[str] = []
    quoted = False
    escaped = False
    line_comment = False
    block_comment = False
    index = 0
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if line_comment:
            if char in "\r\n":
                line_comment = False
                statement = "".join(current).strip()
                if statement:
                    statements.append(statement)
                    if len(statements) > MAX_CONSOLE_STATEMENTS:
                        return [], False
                current.clear()
            index += 1
            continue
        if block_comment:
            if char == "*" and following == "/":
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quoted:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            index += 1
            continue
        if char == "/" and following == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and following == "*":
            block_comment = True
            index += 2
            continue
        if char == '"':
            quoted = True
            current.append(char)
            index += 1
            continue
        if char == ";" or char in "\r\n":
            statement = "".join(current).strip()
            if statement:
                if len(statement) > MAX_STATEMENT_CHARS:
                    return [], False
                statements.append(statement)
                if len(statements) > MAX_CONSOLE_STATEMENTS:
                    return [], False
            current.clear()
            if char == "\r" and following == "\n":
                index += 2
            else:
                index += 1
            continue
        current.append(char)
        if len(current) > MAX_STATEMENT_CHARS:
            return [], False
        index += 1
    if quoted or escaped or block_comment:
        return [], False
    statement = "".join(current).strip()
    if statement:
        statements.append(statement)
    return statements, len(statements) <= MAX_CONSOLE_STATEMENTS


def _console_tokens(statement: str) -> tuple[list[str], bool]:
    tokens: list[str] = []
    current: list[str] = []
    started = False
    quoted = False
    escaped = False
    for char in statement:
        if quoted:
            if escaped:
                if char in {'"', "\\"}:
                    current.append(char)
                else:
                    current.extend(("\\", char))
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            else:
                current.append(char)
            continue
        if char == '"':
            quoted = True
            started = True
        elif char.isspace():
            if started:
                tokens.append("".join(current))
                current.clear()
                started = False
        else:
            current.append(char)
            started = True
    if quoted or escaped:
        return [], False
    if started:
        tokens.append("".join(current))
    return tokens, True


def parse_idtech_bind_cfg(text: str) -> KeyvaluesCfgParse:
    """Parse an id Tech/Quake console CFG in execution order."""

    if not isinstance(text, str) or "\x00" in text:
        return KeyvaluesCfgParse({}, False)
    if len(text) > MAX_KEY_CONFIG_CHARS:
        return KeyvaluesCfgParse({}, False, truncated=True)
    statements, valid = _console_statements(text)
    if not valid:
        return KeyvaluesCfgParse({}, False)
    detected: Keymap = {}
    recognized = False
    emitted = 0
    for statement in statements:
        tokens, valid_tokens = _console_tokens(statement)
        if not valid_tokens or not tokens:
            continue
        directive = tokens[0].casefold()
        if directive == "unbindall" and len(tokens) == 1:
            detected.clear()
            recognized = True
            continue
        if directive == "unbind" and len(tokens) == 2:
            recognized = True
            canonical = _canonical_input(tokens[1])
            if canonical:
                detected.pop(canonical[0], None)
            continue
        if directive != "bind" or len(tokens) < 3:
            continue
        recognized = True
        canonical = _canonical_input(tokens[1])
        if canonical is None:
            continue
        command_line = " ".join(tokens[2:]).strip()
        if command_line.casefold() in _UNBOUND_COMMANDS:
            detected.pop(canonical[0], None)
            continue
        mapping = _mapping_for_command(canonical, command_line)
        if mapping is None:
            detected.pop(canonical[0], None)
            continue
        detected[canonical[0]] = mapping
        emitted += 1
        if emitted > MAX_PARSED_BINDINGS:
            return KeyvaluesCfgParse({}, False, truncated=True)
    return KeyvaluesCfgParse(detected, recognized, "idtech_cfg" if recognized else "")


def _keyvalues_tokens(text: str) -> tuple[list[str], bool]:
    tokens: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        following = text[index + 1] if index + 1 < length else ""
        if char.isspace() or char == ",":
            index += 1
            continue
        if char == "/" and following == "/":
            newline = text.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if char == "/" and following == "*":
            end = text.find("*/", index + 2)
            if end < 0:
                return [], False
            index = end + 2
            continue
        if text.startswith("<!--", index):
            end = text.find("-->", index + 4)
            if end < 0:
                return [], False
            index = end + 3
            continue
        if char in "{}[]=":
            tokens.append(char)
            index += 1
        elif char == '"':
            index += 1
            value: list[str] = []
            escaped = False
            while index < length:
                char = text[index]
                if escaped:
                    if char in {'"', "\\"}:
                        value.append(char)
                    elif char == "n":
                        value.append("\n")
                    elif char == "t":
                        value.append("\t")
                    else:
                        value.extend(("\\", char))
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    break
                else:
                    value.append(char)
                if len(value) > MAX_KEYVALUES_TOKEN_CHARS:
                    return [], False
                index += 1
            if index >= length or escaped:
                return [], False
            tokens.append("".join(value))
            index += 1
        else:
            start = index
            while index < length:
                char = text[index]
                if char.isspace() or char in "{}[]=,\"":
                    break
                if char == "/" and index + 1 < length and text[index + 1] in "/*":
                    break
                index += 1
                if index - start > MAX_KEYVALUES_TOKEN_CHARS:
                    return [], False
            if index == start:
                return [], False
            tokens.append(text[start:index])
        if len(tokens) > MAX_KEYVALUES_TOKENS:
            return [], False
    return tokens, True


def _validate_keyvalues_delimiters(tokens: list[str]) -> bool:
    stack: list[str] = []
    pairs = {"}": "{", "]": "["}
    for token in tokens:
        if token in {"{", "["}:
            stack.append(token)
            if len(stack) > MAX_KEYVALUES_NESTING:
                return False
        elif token in pairs:
            if not stack or stack.pop() != pairs[token]:
                return False
    return not stack


def _matching_brace(tokens: list[str], opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(tokens)):
        if tokens[index] == "{":
            depth += 1
        elif tokens[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _direct_binding_pairs(
    tokens: list[str], opening: int, closing: int
) -> Iterator[tuple[str, str]]:
    index = opening + 1
    while index < closing:
        token = tokens[index]
        if token == "[":
            bracket_depth = 1
            index += 1
            while index < closing and bracket_depth:
                if tokens[index] == "[":
                    bracket_depth += 1
                elif tokens[index] == "]":
                    bracket_depth -= 1
                index += 1
            continue
        if token in {"=", "]"}:
            index += 1
            continue
        if token == "{":
            nested_end = _matching_brace(tokens, index)
            if nested_end is None or nested_end > closing:
                return
            index = nested_end + 1
            continue
        key = token
        index += 1
        if index < closing and tokens[index] == "=":
            index += 1
        if index >= closing:
            return
        value = tokens[index]
        if value == "{":
            nested_end = _matching_brace(tokens, index)
            if nested_end is None or nested_end > closing:
                return
            index = nested_end + 1
            continue
        if value == "[":
            bracket_depth = 1
            index += 1
            while index < closing and bracket_depth:
                if tokens[index] == "[":
                    bracket_depth += 1
                elif tokens[index] == "]":
                    bracket_depth -= 1
                index += 1
            continue
        if value in {"}", "]", "="}:
            index += 1
            continue
        index += 1
        yield key, value


def parse_valve_keyvalues_bindings(
    text: str, *, file_name: str | Path = ""
) -> KeyvaluesCfgParse:
    """Parse direct input-command pairs under exact ``bindings`` objects."""

    if not isinstance(text, str) or "\x00" in text:
        return KeyvaluesCfgParse({}, False)
    if len(text) > MAX_KEY_CONFIG_CHARS:
        return KeyvaluesCfgParse({}, False, truncated=True)
    tokens, valid = _keyvalues_tokens(text)
    if not valid or not _validate_keyvalues_delimiters(tokens):
        return KeyvaluesCfgParse({}, False)
    detected: Keymap = {}
    supported_pairs = 0
    binding_objects = 0
    emitted = 0
    index = 0
    while index < len(tokens):
        if tokens[index].casefold() != "bindings":
            index += 1
            continue
        opening = index + 1
        if opening < len(tokens) and tokens[opening] == "=":
            opening += 1
        if opening >= len(tokens) or tokens[opening] != "{":
            index += 1
            continue
        closing = _matching_brace(tokens, opening)
        if closing is None:
            return KeyvaluesCfgParse({}, False)
        binding_objects += 1
        for raw_input, command_line in _direct_binding_pairs(tokens, opening, closing):
            canonical = _canonical_input(raw_input)
            if canonical is None:
                continue
            supported_pairs += 1
            if command_line.strip().casefold() in _UNBOUND_COMMANDS:
                detected.pop(canonical[0], None)
                continue
            mapping = _mapping_for_command(canonical, command_line)
            if mapping is None:
                detected.pop(canonical[0], None)
                continue
            detected[canonical[0]] = mapping
            emitted += 1
            if emitted > MAX_PARSED_BINDINGS:
                return KeyvaluesCfgParse({}, False, truncated=True)
        index = closing + 1

    name = Path(file_name).name
    strong_empty_name = bool(_STRONG_EMPTY_KEYVALUES_NAMES.search(name))
    recognized = supported_pairs > 0 or (binding_objects > 0 and strong_empty_name)
    if not recognized:
        return KeyvaluesCfgParse({}, False)
    suffix = Path(name).suffix.casefold()
    format_name = "source2_vcfg" if suffix == ".vcfg" else "valve_keyvalues"
    return KeyvaluesCfgParse(detected, True, format_name)


def parse_keyvalues_cfg(
    text: str, *, file_name: str | Path = ""
) -> KeyvaluesCfgParse:
    """Auto-detect one supported text config without broad heuristics."""

    if not isinstance(text, str) or "\x00" in text:
        return KeyvaluesCfgParse({}, False)
    if len(text) > MAX_KEY_CONFIG_CHARS:
        return KeyvaluesCfgParse({}, False, truncated=True)

    cfg = parse_idtech_bind_cfg(text)
    if cfg.recognized_config or cfg.truncated:
        return cfg
    return parse_valve_keyvalues_bindings(text, file_name=file_name)


def _decode_config_bytes(data: bytes) -> str | None:
    encodings = ["utf-8-sig"]
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings.insert(0, "utf-16")
    encodings.extend(("gb18030", "cp1252"))
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def parse_keyvalues_cfg_file(path: str | Path) -> KeyvaluesCfgParse:
    """Read and parse a config with a strict byte limit and no path traversal."""

    candidate = Path(path)
    try:
        if not candidate.is_file() or candidate.is_symlink():
            return KeyvaluesCfgParse({}, False)
        with candidate.open("rb") as stream:
            data = stream.read(MAX_KEY_CONFIG_BYTES + 1)
    except OSError:
        return KeyvaluesCfgParse({}, False)
    if len(data) > MAX_KEY_CONFIG_BYTES:
        return KeyvaluesCfgParse({}, False, truncated=True)
    text = _decode_config_bytes(data)
    if text is None:
        return KeyvaluesCfgParse({}, False)
    return parse_keyvalues_cfg(text, file_name=candidate.name)


__all__ = [
    "Keymap",
    "KeyvaluesCfgParse",
    "MAX_KEY_CONFIG_BYTES",
    "MAX_KEY_CONFIG_CHARS",
    "MAX_KEYVALUES_NESTING",
    "MAX_KEYVALUES_TOKENS",
    "is_keyvalues_cfg_candidate",
    "parse_idtech_bind_cfg",
    "parse_keyvalues_cfg",
    "parse_keyvalues_cfg_file",
    "parse_valve_keyvalues_bindings",
]
