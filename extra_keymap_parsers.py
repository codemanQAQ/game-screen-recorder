"""Parsers for structured JSON game key-binding files.

The main recorder intentionally keeps its broad file scanner separate from this
module.  The public results use the same ``{input: {type, action, ...}}`` shape
as ``screen_recorder.KeymapDiscovery``, so callers can merge them without an
adapter or import this module without loading the recorder GUI dependencies.

Supported structural schemas:

* action-to-input maps such as Slay the Spire 2 ``settings.save``;
* Oxygen Not Included ``keybindings.json`` binding-entry arrays;
* Terraria/tModLoader ``input profiles.json`` selected profiles and exported
  ``KeyConfiguration``/``KeyStatus`` objects.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Iterable, Iterator


Keymap = dict[str, dict[str, str]]

MAX_STRUCTURED_JSON_BYTES = 4 * 1024 * 1024
MAX_DISCOVERY_ENTRIES = 30_000
MAX_DISCOVERY_FILES = 64
MAX_JSON_NESTING = 128
MAX_DOCUMENT_NODES = 100_000


@dataclass(frozen=True)
class StructuredJsonParse:
    """Result of recognizing one decoded JSON document."""

    keymap: Keymap
    recognized_config: bool
    authoritative: bool
    schema: str = ""
    overridden_actions: frozenset[str] = frozenset()


@dataclass(frozen=True)
class StructuredJsonDiscovery:
    """Filesystem discovery result, directly adaptable to KeymapDiscovery."""

    keymap: Keymap
    source_files: tuple[Path, ...]
    scanned_files: int
    truncated: bool
    recognized_config: bool
    authoritative: bool
    schemas: tuple[str, ...] = ()
    overridden_actions: frozenset[str] = frozenset()


_KEYBOARD_ALIASES = {
    "space": "Space",
    "spacebar": "Space",
    "return": "Enter",
    "enter": "Enter",
    "backspace": "Backspace",
    "tab": "Tab",
    "pause": "Pause",
    "capslock": "CapsLock",
    "numlock": "NumLock",
    "scrolllock": "ScrollLock",
    "escape": "Esc",
    "esc": "Esc",
    "leftshift": "Shift",
    "rightshift": "Shift",
    "shift": "Shift",
    "leftctrl": "Ctrl",
    "rightctrl": "Ctrl",
    "leftcontrol": "Ctrl",
    "rightcontrol": "Ctrl",
    "control": "Ctrl",
    "ctrl": "Ctrl",
    "leftalt": "Alt",
    "rightalt": "Alt",
    "alt": "Alt",
    "leftwindows": "Win",
    "rightwindows": "Win",
    "leftwin": "Win",
    "rightwin": "Win",
    "leftsuper": "Win",
    "rightsuper": "Win",
    "win": "Win",
    "uparrow": "Up",
    "up": "Up",
    "downarrow": "Down",
    "down": "Down",
    "leftarrow": "Left",
    "left": "Left",
    "rightarrow": "Right",
    "right": "Right",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "home": "Home",
    "end": "End",
    "insert": "Insert",
    "delete": "Delete",
    "grave": "Tilde",
    "backquote": "Tilde",
    "tilde": "Tilde",
    "minus": "-",
    "oemminus": "-",
    "equals": "=",
    "equal": "=",
    "oemplus": "=",
    "comma": ",",
    "oemcomma": ",",
    "period": ".",
    "oemperiod": ".",
    "slash": "/",
    "oemquestion": "/",
    "semicolon": ";",
    "oemsemicolon": ";",
    "apostrophe": "'",
    "quote": "'",
    "oemquotes": "'",
    "leftbracket": "[",
    "oemopenbrackets": "[",
    "rightbracket": "]",
    "oemclosebrackets": "]",
    "backslash": "\\",
    "oempipe": "\\",
    "oemtilde": "Tilde",
    "add": "NumPadAdd",
    "numpadadd": "NumPadAdd",
    "subtract": "NumPadSubtract",
    "numpadsubtract": "NumPadSubtract",
    "multiply": "NumPadMultiply",
    "numpadmultiply": "NumPadMultiply",
    "divide": "NumPadDivide",
    "numpaddivide": "NumPadDivide",
    "decimal": "NumPadDecimal",
    "numpaddecimal": "NumPadDecimal",
}

_UNITY_MOUSE_ALIASES = {
    "mouse0": "leftClick",
    "mouse1": "rightClick",
    "mouse2": "middleClick",
    "mouse3": "mouseButton4",
    "mouse4": "mouseButton5",
    "leftclick": "leftClick",
    "rightclick": "rightClick",
    "middleclick": "middleClick",
    "mousewheelup": "mouseWheelUp",
    "mousescrollup": "mouseWheelUp",
    "mousewheeldown": "mouseWheelDown",
    "mousescrolldown": "mouseWheelDown",
}

_TERRARIA_MOUSE_ALIASES = {
    "mouse1": "leftClick",
    "mouse2": "rightClick",
    "mouse3": "middleClick",
    "mouse4": "mouseButton4",
    "mouse5": "mouseButton5",
    "mouseleft": "leftClick",
    "mouseright": "rightClick",
    "mousemiddle": "middleClick",
    "mousexbutton1": "mouseButton4",
    "mousexbutton2": "mouseButton5",
    "scrollwheelup": "mouseWheelUp",
    "scrollwheeldown": "mouseWheelDown",
}

_GAMEPAD_ALIASES = {
    "a": "gamepadA",
    "b": "gamepadB",
    "x": "gamepadX",
    "y": "gamepadY",
    "gamepada": "gamepadA",
    "gamepadb": "gamepadB",
    "gamepadx": "gamepadX",
    "gamepady": "gamepadY",
    "controllerfacebuttonsouth": "gamepadA",
    "controllerfacebuttoneast": "gamepadB",
    "controllerfacebuttonwest": "gamepadX",
    "controllerfacebuttonnorth": "gamepadY",
    "buttonsouth": "gamepadA",
    "buttoneast": "gamepadB",
    "buttonwest": "gamepadX",
    "buttonnorth": "gamepadY",
    "leftshoulder": "gamepadLeftShoulder",
    "leftbumper": "gamepadLeftShoulder",
    "controllerleftbumper": "gamepadLeftShoulder",
    "rightshoulder": "gamepadRightShoulder",
    "rightbumper": "gamepadRightShoulder",
    "controllerrightbumper": "gamepadRightShoulder",
    "lefttrigger": "gamepadLeftTrigger",
    "controllerlefttrigger": "gamepadLeftTrigger",
    "righttrigger": "gamepadRightTrigger",
    "controllerrighttrigger": "gamepadRightTrigger",
    "leftstick": "gamepadLeftThumb",
    "leftstickpress": "gamepadLeftThumb",
    "controllerjoystickpress": "gamepadLeftThumb",
    "rightstick": "gamepadRightThumb",
    "rightstickpress": "gamepadRightThumb",
    "back": "gamepadBack",
    "select": "gamepadBack",
    "controllerselectbutton": "gamepadBack",
    "start": "gamepadStart",
    "controllerstartbutton": "gamepadStart",
    "dpadup": "gamepadDpadUp",
    "controllerdpadnorth": "gamepadDpadUp",
    "dpaddown": "gamepadDpadDown",
    "controllerdpadsouth": "gamepadDpadDown",
    "dpadleft": "gamepadDpadLeft",
    "controllerdpadwest": "gamepadDpadLeft",
    "dpadright": "gamepadDpadRight",
    "controllerdpadeast": "gamepadDpadRight",
    "leftthumbstickup": "gamepadLeftStickUp",
    "leftthumbstickdown": "gamepadLeftStickDown",
    "leftthumbstickleft": "gamepadLeftStickLeft",
    "leftthumbstickright": "gamepadLeftStickRight",
    "rightthumbstickup": "gamepadRightStickUp",
    "rightthumbstickdown": "gamepadRightStickDown",
    "rightthumbstickleft": "gamepadRightStickLeft",
    "rightthumbstickright": "gamepadRightStickRight",
}

_LITERAL_KEYS = {"-", "=", "'", ",", ".", "/", ";", "[", "]", "\\"}
_UNBOUND_INPUTS = {"", "none", "null", "unknown", "unbound", "disabled", "nobuttons", "numbuttons"}


def _compact(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _canonical_input(
    raw_input: object,
    *,
    device_hint: str | None = None,
    terraria_mouse_numbers: bool = False,
) -> tuple[str, str] | None:
    if not isinstance(raw_input, str):
        return None
    name = raw_input.strip().strip('"\'')
    if not name:
        return None

    unity_path = re.fullmatch(r"<([^>]+)>/(.+)", name)
    if unity_path:
        path_device = unity_path.group(1).casefold()
        device_hint = {
            "keyboard": "keyboard",
            "mouse": "mouse",
            "gamepad": "gamepad",
        }.get(path_device)
        if device_hint is None:
            return None
        name = unity_path.group(2)

    compact = _compact(name)
    if compact in _UNBOUND_INPUTS:
        return None

    if device_hint == "gamepad":
        mapped = _GAMEPAD_ALIASES.get(compact)
        return (mapped, "gamepad") if mapped else None

    mouse_aliases = _TERRARIA_MOUSE_ALIASES if terraria_mouse_numbers else _UNITY_MOUSE_ALIASES
    mapped_mouse = mouse_aliases.get(compact)
    if mapped_mouse:
        return mapped_mouse, "mouse"
    if device_hint == "mouse":
        return None

    if name in _LITERAL_KEYS:
        return name, "keyboard"
    mapped_key = _KEYBOARD_ALIASES.get(compact)
    if mapped_key:
        return mapped_key, "keyboard"
    if device_hint not in {None, "keyboard"}:
        return None

    # Godot uses Key1..Key0; XNA/MonoGame uses D1..D0.  Both describe the
    # regular number row, unlike NumPad1.
    digit_match = re.fullmatch(r"(?:key|digit|alpha|d)([0-9])", compact)
    if digit_match:
        return digit_match.group(1), "keyboard"
    if len(compact) == 1 and compact.isalnum():
        return compact.upper(), "keyboard"
    if re.fullmatch(r"f(?:[1-9]|1[0-9]|2[0-4])", compact):
        return compact.upper(), "keyboard"
    numpad_match = re.fullmatch(r"(?:numpad|num)([0-9])", compact)
    if numpad_match:
        return f"NumPad{numpad_match.group(1)}", "keyboard"
    return None


def _humanize_action(raw_action: object) -> str:
    if not isinstance(raw_action, str):
        return ""
    action = raw_action.strip()
    if not action:
        return ""
    # A slash is part of a tModLoader mod key's identity; use a colon so it
    # cannot be confused with the recorder's "multiple actions" separator.
    action = action.replace("/", ": ")
    action = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", action)
    action = re.sub(r"(?<=[A-Za-z])(?=[0-9])", " ", action)
    action = re.sub(r"[_\-.]+", " ", action)
    action = re.sub(r"\s+", " ", action).strip()
    words = action.split()
    if len(words) > 1 and words[0].casefold() in {"mega", "ui", "keyboard", "gameplay"}:
        words.pop(0)
    action = " ".join(words)
    return (action[:1].upper() + action[1:])[:120] if action else ""


def _movement_direction(raw_action: object, input_name: str) -> str | None:
    compact_action = _compact(raw_action)
    direction_actions = {
        "up": "W",
        "uiup": "W",
        "moveup": "W",
        "panup": "W",
        "navigateup": "W",
        "down": "B",
        "uidown": "B",
        "movedown": "B",
        "pandown": "B",
        "navigatedown": "B",
        "left": "L",
        "uileft": "L",
        "moveleft": "L",
        "panleft": "L",
        "navigateleft": "L",
        "right": "R",
        "uiright": "R",
        "moveright": "R",
        "panright": "R",
        "navigateright": "R",
    }
    direction = direction_actions.get(compact_action)
    if direction is None:
        return None
    return direction


def _add_mapping(
    target: Keymap,
    raw_input: object,
    raw_action: object,
    *,
    device_hint: str | None = None,
    terraria_mouse_numbers: bool = False,
    modifier: object = None,
) -> bool:
    canonical = _canonical_input(
        raw_input,
        device_hint=device_hint,
        terraria_mouse_numbers=terraria_mouse_numbers,
    )
    action = _humanize_action(raw_action)
    if canonical is None or not action:
        return False
    input_name, device_type = canonical
    normalized_modifier = _compact(modifier)
    if normalized_modifier not in _UNBOUND_INPUTS:
        modifier_name = _canonical_input(str(modifier), device_hint="keyboard")
        label = modifier_name[0] if modifier_name else str(modifier).strip()
        if label:
            input_name = f"{label}+{input_name}"
    direction = _movement_direction(raw_action, input_name)
    existing = target.get(input_name)
    if existing is None:
        mapping = {"type": device_type, "action": action}
        if direction:
            mapping["movement_direction"] = direction
        target[input_name] = mapping
        return True
    actions = existing["action"].split(" / ")
    if action not in actions:
        existing["action"] = " / ".join([*actions, action])[:240]
    if direction and "movement_direction" not in existing:
        existing["movement_direction"] = direction
    return True


def _iter_inputs(value: object) -> Iterator[object]:
    """Iterate input-shaped values without recursive descent.

    Parsed files already have a bounded nesting depth, but callers can also
    pass an in-memory object directly.  The explicit stack keeps that public
    path safe from deeply nested or cyclic containers as well.
    """
    fields = {
        "key",
        "keys",
        "input",
        "inputs",
        "binding",
        "bindings",
        "primary",
        "secondary",
        "value",
    }
    stack: list[tuple[object, int]] = [(value, 0)]
    visited: set[int] = set()
    visited_nodes = 0
    while stack:
        current, depth = stack.pop()
        visited_nodes += 1
        if visited_nodes > MAX_DOCUMENT_NODES or depth > MAX_JSON_NESTING:
            return
        if isinstance(current, (str, int)):
            yield current
            continue
        if isinstance(current, list):
            identity = id(current)
            if identity in visited:
                continue
            visited.add(identity)
            stack.extend((item, depth + 1) for item in reversed(current))
            continue
        if not isinstance(current, dict):
            continue
        identity = id(current)
        if identity in visited:
            continue
        visited.add(identity)
        for field, nested in reversed(list(current.items())):
            if _compact(field) in fields:
                stack.append((nested, depth + 1))


def _casefold_field(document: dict[object, object], *names: str) -> object | None:
    wanted = {_compact(name) for name in names}
    for key, value in document.items():
        if _compact(key) in wanted:
            return value
    return None


_NON_ACTION_SETTING_NAMES = {
    "antialiasing",
    "brightness",
    "display",
    "fullscreen",
    "graphics",
    "language",
    "layout",
    "monitor",
    "quality",
    "resolution",
    "theme",
    "volume",
    "vsync",
}

_ACTION_WORDS = {
    "accept",
    "aim",
    "attack",
    "back",
    "cancel",
    "cast",
    "dash",
    "down",
    "drop",
    "interact",
    "inventory",
    "jump",
    "left",
    "menu",
    "move",
    "navigate",
    "pause",
    "right",
    "run",
    "select",
    "shoot",
    "sprint",
    "up",
    "use",
}


def _looks_like_action_name(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    compact = _compact(value)
    if not compact or compact in _NON_ACTION_SETTING_NAMES:
        return False
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    words = {word.casefold() for word in re.findall(r"[A-Za-z0-9]+", separated)}
    if words & _ACTION_WORDS:
        return True
    # Namespaced identifiers are a strong action-map signature (for example
    # ui_accept or mega_select_card_1), unlike ordinary setting names.
    has_identifier_separator = bool(re.search(r"[_./:-]", value))
    has_camel_boundary = bool(re.search(r"(?<=[a-z0-9])(?=[A-Z])", value))
    return (has_identifier_separator or has_camel_boundary) and len(compact) >= 4


def _action_from_reverse_value(value: object) -> object | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    return _casefold_field(value, "action", "action_name", "command")


def _parse_one_action_map(
    mapping: dict[object, object],
    *,
    device_hint: str,
) -> tuple[Keymap, str | None, int]:
    """Parse and orient an action/input map using action-name evidence."""
    action_to_input: Keymap = {}
    forward_evidence = 0
    for raw_action, inputs in mapping.items():
        if not _looks_like_action_name(raw_action):
            continue
        added = False
        for raw_input in _iter_inputs(inputs):
            added = _add_mapping(
                action_to_input,
                raw_input,
                raw_action,
                device_hint=device_hint,
            ) or added
        forward_evidence += int(added)

    input_to_action: Keymap = {}
    reverse_evidence = 0
    for raw_input, action_value in mapping.items():
        raw_action = _action_from_reverse_value(action_value)
        if not _looks_like_action_name(raw_action):
            continue
        if _add_mapping(
            input_to_action,
            raw_input,
            raw_action,
            device_hint=device_hint,
        ):
            reverse_evidence += 1

    if forward_evidence == reverse_evidence:
        return {}, None, forward_evidence
    if forward_evidence > reverse_evidence:
        return action_to_input, "action_to_input", forward_evidence
    return input_to_action, "input_to_action", reverse_evidence


def _parse_action_input_maps(document: object) -> StructuredJsonParse:
    if not isinstance(document, dict):
        return StructuredJsonParse({}, False, False)
    keyboard = _casefold_field(document, "keyboard_mapping", "keyboardMapping")
    controller = _casefold_field(document, "controller_mapping", "controllerMapping")
    if not isinstance(keyboard, dict):
        return StructuredJsonParse({}, False, False)

    detected, orientation, evidence = _parse_one_action_map(
        keyboard,
        device_hint="keyboard",
    )
    # An empty map or a map containing only ordinary settings is not a keymap.
    # Requiring oriented action evidence avoids treating arbitrary
    # {"keyboard_mapping": {"theme": "A"}} documents as authoritative.
    if orientation is None or evidence < 1:
        return StructuredJsonParse({}, False, False)

    if isinstance(controller, dict):
        controller_detected, controller_orientation, _ = _parse_one_action_map(
            controller,
            device_hint="gamepad",
        )
        # Keyboard and controller maps in the same schema must use the same
        # orientation.  A contradictory section is ignored instead of being
        # guessed and merged into otherwise reliable keyboard results.
        if controller_orientation == orientation:
            for input_name, mapping in controller_detected.items():
                existing = detected.get(input_name)
                if existing is None:
                    detected[input_name] = dict(mapping)
                    continue
                for action in mapping["action"].split(" / "):
                    if action not in existing["action"].split(" / "):
                        existing["action"] = f'{existing["action"]} / {action}'[:240]
    return StructuredJsonParse(detected, True, True, "action_input_maps")


def _oni_entries(document: object) -> list[object] | None:
    if isinstance(document, list):
        return document
    if isinstance(document, dict):
        wrapped = _casefold_field(document, "bindings", "keybindings", "entries")
        if isinstance(wrapped, list):
            return wrapped
    return None


def _parse_oxygen_not_included(
    document: object,
    *,
    source_name: str,
) -> StructuredJsonParse:
    entries = _oni_entries(document)
    if entries is None:
        return StructuredJsonParse({}, False, False)
    shaped_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and _casefold_field(entry, "mAction") is not None
        and (
            _casefold_field(entry, "mKeyCode") is not None
            or _casefold_field(entry, "mButton") is not None
        )
    ]
    filename_identifies_schema = Path(source_name).name.casefold() == "keybindings.json"
    if not shaped_entries and not (not entries and filename_identifies_schema):
        return StructuredJsonParse({}, False, False)

    detected: Keymap = {}
    overridden_actions: set[str] = set()
    for entry in shaped_entries:
        action = _casefold_field(entry, "mAction")
        normalized_action = _humanize_action(action)
        if normalized_action:
            overridden_actions.add(normalized_action)
        key_code = _casefold_field(entry, "mKeyCode")
        modifier = _casefold_field(entry, "mModifier")
        added = _add_mapping(detected, key_code, action, modifier=modifier)
        if added:
            continue
        button = _casefold_field(entry, "mButton")
        _add_mapping(detected, button, action, device_hint="gamepad")
    # ONI's file is a sparse list of player overrides, not a complete default
    # profile.  It must be merged with any install/default mappings by callers.
    return StructuredJsonParse(
        detected,
        True,
        False,
        "oxygen_not_included",
        frozenset(overridden_actions),
    )


_TERRARIA_STRONG_KEYMAP_FIELDS = {
    "mouseandkeyboard",
    "keystatus",
}
_TERRARIA_CONTEXT_KEYMAP_FIELDS = {"keyboard", "keyboardgameplay", "keyboardui"}
_TERRARIA_GAMEPAD_FIELDS = {"gamepad", "gamepadgameplay", "gamepadui"}


def _mapping_containers(
    value: object,
    *,
    inside_configuration: bool = False,
) -> Iterator[tuple[dict[object, object], str | None]]:
    """Yield only explicitly named Terraria KeyConfiguration containers."""
    if not isinstance(value, dict):
        return
    field_names = {_compact(field) for field in value}
    has_strong_container = bool(field_names & _TERRARIA_STRONG_KEYMAP_FIELDS)
    for field, nested in value.items():
        normalized = _compact(field)
        if normalized in _TERRARIA_STRONG_KEYMAP_FIELDS and isinstance(nested, dict):
            yield nested, None
        elif (
            normalized in _TERRARIA_CONTEXT_KEYMAP_FIELDS
            and isinstance(nested, dict)
            and (inside_configuration or has_strong_container)
        ):
            yield nested, None
        elif (
            normalized in _TERRARIA_GAMEPAD_FIELDS
            and isinstance(nested, dict)
            and (inside_configuration or has_strong_container)
        ):
            yield nested, "gamepad"
        elif normalized in {"keyconfiguration", "inputmodes"} and isinstance(nested, dict):
            yield from _mapping_containers(nested, inside_configuration=True)


def _looks_like_terraria_profile(value: object) -> bool:
    return any(True for _mapping, _hint in _mapping_containers(value))


def _selected_terraria_profile(document: dict[object, object]) -> object | None:
    selected = _casefold_field(document, "Selected Profile", "SelectedProfile")
    profiles = _casefold_field(document, "Profiles")
    profile_root = profiles if isinstance(profiles, dict) else document
    if isinstance(selected, str) and isinstance(profile_root, dict):
        for name, value in profile_root.items():
            if str(name).casefold() == selected.casefold() and _looks_like_terraria_profile(value):
                return value
        # A declared but unavailable profile should not silently import a
        # different player's/default profile.
        return None
    if isinstance(profile_root, dict):
        for preferred in ("Custom", "Player", "Current"):
            for name, value in profile_root.items():
                if str(name).casefold() == preferred.casefold() and _looks_like_terraria_profile(value):
                    return value
        if _looks_like_terraria_profile(profile_root):
            return profile_root
        candidates = [value for value in profile_root.values() if _looks_like_terraria_profile(value)]
        if len(candidates) == 1:
            return candidates[0]
    return None


def _parse_terraria(document: object) -> StructuredJsonParse:
    if not isinstance(document, dict):
        return StructuredJsonParse({}, False, False)
    profile = _selected_terraria_profile(document)
    declared_selected = _casefold_field(document, "Selected Profile", "SelectedProfile")
    structurally_recognized = profile is not None or (
        isinstance(declared_selected, str)
        and any(_looks_like_terraria_profile(value) for value in document.values())
    )
    if not structurally_recognized:
        return StructuredJsonParse({}, False, False)
    if profile is None:
        return StructuredJsonParse({}, True, True, "terraria_key_configuration")

    detected: Keymap = {}
    for bindings, device_hint in _mapping_containers(profile):
        for action, inputs in bindings.items():
            for raw_input in _iter_inputs(inputs):
                _add_mapping(
                    detected,
                    raw_input,
                    action,
                    device_hint=device_hint,
                    terraria_mouse_numbers=device_hint is None,
                )
    return StructuredJsonParse(detected, True, True, "terraria_key_configuration")


def parse_structured_json_keymap(
    document: object,
    *,
    source_name: str = "",
) -> StructuredJsonParse:
    """Recognize and parse one already-decoded JSON document.

    Schema checks deliberately precede generic traversal to keep unrelated game
    settings JSON from being misidentified as a key map.
    """
    if not _document_shape_is_safe(document):
        return StructuredJsonParse({}, False, False)
    for parser in (
        _parse_action_input_maps,
        lambda value: _parse_oxygen_not_included(value, source_name=source_name),
        _parse_terraria,
    ):
        parsed = parser(document)
        if parsed.recognized_config:
            return parsed
    return StructuredJsonParse({}, False, False)


def parse_structured_json_file(path: Path) -> StructuredJsonParse:
    """Read and parse a small JSON key-binding file."""
    try:
        if path.is_symlink():
            return StructuredJsonParse({}, False, False)
        with path.open("rb") as stream:
            raw = stream.read(MAX_STRUCTURED_JSON_BYTES + 1)
        if len(raw) > MAX_STRUCTURED_JSON_BYTES:
            return StructuredJsonParse({}, False, False)
        text = raw.decode("utf-8-sig")
        if not _json_nesting_is_safe(text):
            return StructuredJsonParse({}, False, False)
        document = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        return StructuredJsonParse({}, False, False)
    return parse_structured_json_keymap(document, source_name=path.name)


def _json_nesting_is_safe(text: str) -> bool:
    """Bound JSON container nesting while ignoring brackets inside strings."""
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_NESTING:
                return False
        elif character in "]}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not in_string


def _document_shape_is_safe(document: object) -> bool:
    stack: list[tuple[object, int]] = [(document, 0)]
    visited: set[int] = set()
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_DOCUMENT_NODES or depth > MAX_JSON_NESTING:
            return False
        if isinstance(value, dict):
            identity = id(value)
            if identity in visited:
                return False
            visited.add(identity)
            stack.extend((nested, depth + 1) for nested in value.values())
        elif isinstance(value, list):
            identity = id(value)
            if identity in visited:
                return False
            visited.add(identity)
            stack.extend((nested, depth + 1) for nested in value)
    return True


_CANDIDATE_NAMES = {
    "settings.save",
    "keybindings.json",
    "input profiles.json",
    "inputprofiles.json",
}


def _walk_named_candidates(root: Path) -> tuple[list[Path], int, bool]:
    candidates: list[Path] = []
    entries = 0
    truncated = False
    if not root.is_dir():
        return candidates, entries, truncated
    for current_root, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current_root)
        safe_directories: list[str] = []
        for name in directory_names:
            path = current_path / name
            try:
                if path.is_symlink() or path.is_junction():
                    continue
            except OSError:
                continue
            safe_directories.append(name)
        directory_names[:] = safe_directories
        directory_names[:] = [
            name
            for name in directory_names
            if name.casefold() not in {"binaries", "paks", "movies", "crashes", "logs"}
        ]
        entries += len(directory_names) + len(file_names)
        if entries > MAX_DISCOVERY_ENTRIES:
            truncated = True
            break
        for filename in file_names:
            if filename.casefold() not in _CANDIDATE_NAMES:
                continue
            candidate = Path(current_root) / filename
            try:
                if candidate.is_symlink():
                    continue
            except OSError:
                continue
            candidates.append(candidate)
            if len(candidates) >= MAX_DISCOVERY_FILES:
                truncated = True
                break
        if truncated:
            break
    return candidates, entries, truncated


def _acf_value(text: str, field: str) -> str | None:
    match = re.search(
        rf'^\s*"{re.escape(field)}"\s+"([^"]*)"',
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return match.group(1) if match else None


def _steam_cloud_roots(game_directory: Path) -> list[Path]:
    common: Path | None = None
    for ancestor in (game_directory, *game_directory.parents):
        if ancestor.name.casefold() == "common" and ancestor.parent.name.casefold() == "steamapps":
            common = ancestor
            break
    if common is None:
        return []
    steamapps = common.parent
    try:
        relative = game_directory.relative_to(common)
    except ValueError:
        return []
    if not relative.parts:
        return []
    install_name = relative.parts[0]

    matches: list[tuple[str, str | None]] = []
    for manifest in steamapps.glob("appmanifest_*.acf"):
        try:
            text = manifest.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        if (_acf_value(text, "installdir") or "").casefold() != install_name.casefold():
            continue
        app_id = _acf_value(text, "appid") or manifest.stem.removeprefix("appmanifest_")
        matches.append((app_id, _acf_value(text, "LastOwner")))

    userdata = steamapps.parent / "userdata"
    roots: list[Path] = []
    for app_id, last_owner in matches:
        preferred_account: str | None = None
        if last_owner and last_owner.isdigit():
            steam_id = int(last_owner)
            account_id = (
                steam_id - 76561197960265728
                if steam_id >= 76561197960265728
                else steam_id
            )
            if 0 <= account_id <= 0xFFFFFFFF:
                preferred_account = str(account_id)
        if preferred_account:
            remote = userdata / preferred_account / app_id / "remote"
            if remote.is_dir():
                roots.append(remote)
            # A valid LastOwner is authoritative even when that account has no
            # Cloud copy.  Falling through here would import another local
            # Steam user's controls.
            continue
        remotes = [
            account / app_id / "remote"
            for account in userdata.iterdir()
            if account.is_dir() and account.name.isdigit() and (account / app_id / "remote").is_dir()
        ] if userdata.is_dir() else []
        remotes.sort(
            key=lambda path: max(
                (candidate.stat().st_mtime for candidate in path.iterdir() if candidate.is_file()),
                default=0.0,
            ),
            reverse=True,
        )
        # Without an owner identifier, only a single unambiguous local account
        # is safe.  Never select one of multiple users by modification time.
        if len(remotes) == 1:
            roots.append(remotes[0])
    return roots


def _windows_document_roots() -> list[Path]:
    roots: list[Path] = []
    user_profile = os.environ.get("USERPROFILE")
    one_drive = os.environ.get("OneDrive")
    for candidate in (
        Path(user_profile) / "Documents" if user_profile else None,
        Path(one_drive) / "Documents" if one_drive else None,
    ):
        if candidate is not None and candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    return roots


def _known_player_config_roots(game_directory: Path) -> list[Path]:
    identity = _compact(game_directory.name)
    roots: list[Path] = []
    for documents in _windows_document_roots():
        if "oxygennotincluded" in identity:
            candidate = documents / "Klei" / "OxygenNotIncluded"
            if candidate.is_dir():
                roots.append(candidate)
        if "terraria" in identity or "tmodloader" in identity:
            terraria = documents / "My Games" / "Terraria"
            variants = (
                terraria,
                terraria / "tModLoader",
                documents / "My Games" / "Terraria" / "tModLoader-1.4.3",
            )
            preferred = [path for path in variants if path.is_dir()]
            if "tmodloader" in identity:
                preferred.sort(key=lambda path: "tmodloader" not in path.name.casefold())
            roots.extend(path for path in preferred if path not in roots)
    return roots


def _deduplicate_paths(paths: Iterable[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            identity = str(path.resolve()).casefold()
        except OSError:
            identity = str(path.absolute()).casefold()
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(path)
    return unique


def discover_structured_json_keymaps(
    game_directory: Path,
    *,
    extra_roots: Iterable[Path] = (),
    include_player_roots: bool = False,
) -> StructuredJsonDiscovery:
    """Find recognized structured key maps in install/player-data locations.

    Player-data lookup is opt-in; hosts should normally provide already-vetted
    roots through ``extra_roots``.  When enabled, Steam Cloud is selected using
    the manifest's ``LastOwner`` and never falls back to a different account.
    ``extra_roots`` lets the host application add platform-specific save roots.
    """
    root = game_directory.resolve()
    if not root.is_dir():
        raise ValueError("game directory does not exist or is not accessible")

    player_roots = (
        [*_steam_cloud_roots(root), *_known_player_config_roots(root)]
        if include_player_roots
        else []
    )
    search_roots = _deduplicate_paths(
        [*player_roots, *(Path(path) for path in extra_roots), root]
    )
    candidates: list[Path] = []
    truncated = False
    for search_root in search_roots:
        discovered, _entries, partial = _walk_named_candidates(search_root)
        candidates.extend(discovered)
        truncated = truncated or partial
        if len(candidates) >= MAX_DISCOVERY_FILES:
            candidates = candidates[:MAX_DISCOVERY_FILES]
            truncated = True
            break
    candidates = _deduplicate_paths(candidates)

    detected: Keymap = {}
    sources: list[Path] = []
    schemas: list[str] = []
    recognized = False
    authoritative = False
    overridden_actions: set[str] = set()
    for path in candidates:
        parsed = parse_structured_json_file(path)
        if not parsed.recognized_config:
            continue
        recognized = True
        overridden_actions.update(parsed.overridden_actions)
        if parsed.authoritative and not authoritative:
            # The first player-data root has highest priority.  A complete
            # profile replaces any sparse defaults gathered before it.
            detected.clear()
            sources.clear()
            schemas.clear()
            authoritative = True
        if authoritative and not parsed.authoritative:
            continue
        for input_name, mapping in parsed.keymap.items():
            existing = detected.get(input_name)
            if existing is None:
                detected[input_name] = dict(mapping)
                continue
            for action in mapping["action"].split(" / "):
                if action not in existing["action"].split(" / "):
                    existing["action"] = f'{existing["action"]} / {action}'[:240]
            if mapping.get("movement_direction") and not existing.get("movement_direction"):
                existing["movement_direction"] = mapping["movement_direction"]
        sources.append(path)
        if parsed.schema and parsed.schema not in schemas:
            schemas.append(parsed.schema)
        # A complete player file has priority over install/default copies.
        if parsed.authoritative:
            break

    return StructuredJsonDiscovery(
        detected,
        tuple(sources),
        len(candidates),
        truncated,
        recognized,
        authoritative,
        tuple(schemas),
        frozenset(overridden_actions),
    )


__all__ = [
    "StructuredJsonDiscovery",
    "StructuredJsonParse",
    "discover_structured_json_keymaps",
    "parse_structured_json_file",
    "parse_structured_json_keymap",
]
