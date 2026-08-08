"""Conservative key-map extraction for common XML game configuration formats.

The module is intentionally independent from ``screen_recorder.py`` so it can
be imported by the recorder without creating an import cycle.  A successful
parse uses the recorder's native shape::

    {"Space": {"type": "keyboard", "action": "Jump"}}

``recognized`` is separate from the key-map contents because an authoritative
player configuration can be valid while containing only unbound controls.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable
from xml.etree import ElementTree


MAX_XML_BYTES = 4 * 1024 * 1024
MAX_XML_ELEMENTS = 50_000

COMMON_XML_EXACT_FILENAMES = frozenset(
    {"config_player.xml", "keyprefs.xml", "settings.celeste"}
)


@dataclass(frozen=True)
class CommonXmlKeymapResult:
    """Result returned by :func:`parse_common_xml_keymap`."""

    keymap: dict[str, dict[str, str]]
    recognized: bool
    format_name: str | None = None

    @property
    def recognized_config(self) -> bool:
        """Alias matching ``screen_recorder.KeymapDiscovery`` terminology."""

        return self.recognized


def is_common_xml_keymap_candidate(file_name: str | Path) -> bool:
    """Return whether a file name is worth passing to the XML parser.

    This deliberately does not select every XML asset in a game installation.
    XML-heavy games routinely ship tens of thousands of unrelated content files.
    """

    name = _base_name(file_name)
    if name in COMMON_XML_EXACT_FILENAMES:
        return True
    suffix = Path(name).suffix.casefold()
    if suffix not in {".xml", ".celeste"}:
        return False
    stem = Path(name).stem.casefold()
    return any(word in stem for word in ("bind", "control", "input", "key", "pref"))


def parse_common_xml_keymap(
    xml_data: str | bytes,
    *,
    file_name: str | Path = "",
) -> CommonXmlKeymapResult:
    """Parse supported XML key-binding formats without guessing on loose text.

    Supported structures are Barotrauma ``config_player.xml``, Celeste
    ``settings.celeste``, RimWorld ``KeyPrefs.xml`` / ``KeyBindingDef`` files,
    and a strict repeated-record fallback for other XML control files.
    """

    if not isinstance(xml_data, (str, bytes)):
        return _unrecognized()
    size = len(xml_data.encode("utf-8", errors="ignore")) if isinstance(xml_data, str) else len(xml_data)
    if size == 0 or size > MAX_XML_BYTES:
        return _unrecognized()
    lowered = xml_data.casefold() if isinstance(xml_data, str) else xml_data.lower()
    if isinstance(lowered, str):
        contains_declaration = "<!doctype" in lowered or "<!entity" in lowered
    else:
        contains_declaration = b"<!doctype" in lowered or b"<!entity" in lowered
    if contains_declaration:
        return _unrecognized()
    try:
        root = ElementTree.fromstring(xml_data)
    except (ElementTree.ParseError, ValueError, TypeError):
        return _unrecognized()
    for index, _element in enumerate(root.iter(), start=1):
        if index > MAX_XML_ELEMENTS:
            return _unrecognized()

    normalized_name = _base_name(file_name)
    parsers = (
        _parse_barotrauma,
        _parse_celeste,
        _parse_rimworld,
        _parse_generic_records,
    )
    for parser in parsers:
        parsed = parser(root, normalized_name)
        if parsed is not None:
            return parsed
    return _unrecognized()


def _unrecognized() -> CommonXmlKeymapResult:
    return CommonXmlKeymapResult({}, False, None)


def _recognized(
    keymap: dict[str, dict[str, str]], format_name: str
) -> CommonXmlKeymapResult:
    return CommonXmlKeymapResult(keymap, True, format_name)


def _base_name(file_name: str | Path) -> str:
    # ``Path`` on Windows handles native paths, while the split also handles a
    # Windows path passed on another platform during tests or reuse.
    value = str(file_name or "").strip()
    return re.split(r"[\\/]", value)[-1].casefold()


def _local_name(tag: object) -> str:
    value = str(tag or "")
    return value.rsplit("}", 1)[-1] if "}" in value else value


def _direct_children(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    expected = name.casefold()
    return [child for child in element if _local_name(child.tag).casefold() == expected]


def _first_direct_child(
    element: ElementTree.Element, names: Iterable[str]
) -> ElementTree.Element | None:
    expected = {name.casefold() for name in names}
    return next(
        (child for child in element if _local_name(child.tag).casefold() in expected),
        None,
    )


def _clean_text(element: ElementTree.Element | None) -> str:
    return "" if element is None else (element.text or "").strip()


_INPUT_ALIASES: dict[str, tuple[str, str]] = {
    "space": ("Space", "keyboard"),
    "spacebar": ("Space", "keyboard"),
    "return": ("Enter", "keyboard"),
    "enter": ("Enter", "keyboard"),
    "backspace": ("Backspace", "keyboard"),
    "tab": ("Tab", "keyboard"),
    "pause": ("Pause", "keyboard"),
    "capslock": ("CapsLock", "keyboard"),
    "numlock": ("NumLock", "keyboard"),
    "scrolllock": ("ScrollLock", "keyboard"),
    "escape": ("Esc", "keyboard"),
    "esc": ("Esc", "keyboard"),
    "leftshift": ("Shift", "keyboard"),
    "rightshift": ("Shift", "keyboard"),
    "shift": ("Shift", "keyboard"),
    "leftctrl": ("Ctrl", "keyboard"),
    "rightctrl": ("Ctrl", "keyboard"),
    "leftcontrol": ("Ctrl", "keyboard"),
    "rightcontrol": ("Ctrl", "keyboard"),
    "control": ("Ctrl", "keyboard"),
    "ctrl": ("Ctrl", "keyboard"),
    "leftalt": ("Alt", "keyboard"),
    "rightalt": ("Alt", "keyboard"),
    "alt": ("Alt", "keyboard"),
    "leftwindows": ("Win", "keyboard"),
    "rightwindows": ("Win", "keyboard"),
    "leftwin": ("Win", "keyboard"),
    "rightwin": ("Win", "keyboard"),
    "leftsuper": ("Win", "keyboard"),
    "rightsuper": ("Win", "keyboard"),
    "win": ("Win", "keyboard"),
    "uparrow": ("Up", "keyboard"),
    "up": ("Up", "keyboard"),
    "downarrow": ("Down", "keyboard"),
    "down": ("Down", "keyboard"),
    "leftarrow": ("Left", "keyboard"),
    "left": ("Left", "keyboard"),
    "rightarrow": ("Right", "keyboard"),
    "right": ("Right", "keyboard"),
    "pageup": ("PageUp", "keyboard"),
    "pagedown": ("PageDown", "keyboard"),
    "home": ("Home", "keyboard"),
    "end": ("End", "keyboard"),
    "insert": ("Insert", "keyboard"),
    "delete": ("Delete", "keyboard"),
    "backquote": ("Tilde", "keyboard"),
    "grave": ("Tilde", "keyboard"),
    "tilde": ("Tilde", "keyboard"),
    "minus": ("-", "keyboard"),
    "oemminus": ("-", "keyboard"),
    "equals": ("=", "keyboard"),
    "oemplus": ("=", "keyboard"),
    "comma": (",", "keyboard"),
    "oemcomma": (",", "keyboard"),
    "period": (".", "keyboard"),
    "oemperiod": (".", "keyboard"),
    "slash": ("/", "keyboard"),
    "oemquestion": ("/", "keyboard"),
    "semicolon": (";", "keyboard"),
    "oemsemicolon": (";", "keyboard"),
    "apostrophe": ("'", "keyboard"),
    "quote": ("'", "keyboard"),
    "oemquotes": ("'", "keyboard"),
    "leftbracket": ("[", "keyboard"),
    "oemopenbrackets": ("[", "keyboard"),
    "rightbracket": ("]", "keyboard"),
    "oemclosebrackets": ("]", "keyboard"),
    "backslash": ("\\", "keyboard"),
    "oempipe": ("\\", "keyboard"),
    "add": ("NumPadAdd", "keyboard"),
    "numpadadd": ("NumPadAdd", "keyboard"),
    "subtract": ("NumPadSubtract", "keyboard"),
    "numpadsubtract": ("NumPadSubtract", "keyboard"),
    "multiply": ("NumPadMultiply", "keyboard"),
    "numpadmultiply": ("NumPadMultiply", "keyboard"),
    "divide": ("NumPadDivide", "keyboard"),
    "numpaddivide": ("NumPadDivide", "keyboard"),
    "decimal": ("NumPadDecimal", "keyboard"),
    "numpaddecimal": ("NumPadDecimal", "keyboard"),
    "primarymouse": ("leftClick", "mouse"),
    "leftmousebutton": ("leftClick", "mouse"),
    "leftclick": ("leftClick", "mouse"),
    "mouseleft": ("leftClick", "mouse"),
    "leftbutton": ("leftClick", "mouse"),
    "mouse0": ("leftClick", "mouse"),
    "secondarymouse": ("rightClick", "mouse"),
    "rightmousebutton": ("rightClick", "mouse"),
    "rightclick": ("rightClick", "mouse"),
    "mouseright": ("rightClick", "mouse"),
    "rightbutton": ("rightClick", "mouse"),
    "mouse1": ("rightClick", "mouse"),
    "middlemouse": ("middleClick", "mouse"),
    "middlemousebutton": ("middleClick", "mouse"),
    "middleclick": ("middleClick", "mouse"),
    "mousemiddle": ("middleClick", "mouse"),
    "middlebutton": ("middleClick", "mouse"),
    "mouse2": ("middleClick", "mouse"),
    "mouse3": ("mouseButton4", "mouse"),
    "mousebutton4": ("mouseButton4", "mouse"),
    "thumbmousebutton": ("mouseButton4", "mouse"),
    "mouse4": ("mouseButton5", "mouse"),
    "mousebutton5": ("mouseButton5", "mouse"),
    "thumbmousebutton2": ("mouseButton5", "mouse"),
    "mousewheelup": ("mouseWheelUp", "mouse"),
    "mousescrollup": ("mouseWheelUp", "mouse"),
    "mousewheeldown": ("mouseWheelDown", "mouse"),
    "mousescrolldown": ("mouseWheelDown", "mouse"),
}

_GAMEPAD_ALIASES: dict[str, str] = {
    "a": "gamepadA",
    "gamepada": "gamepadA",
    "buttonsouth": "gamepadA",
    "b": "gamepadB",
    "gamepadb": "gamepadB",
    "buttoneast": "gamepadB",
    "x": "gamepadX",
    "gamepadx": "gamepadX",
    "buttonwest": "gamepadX",
    "y": "gamepadY",
    "gamepady": "gamepadY",
    "buttonnorth": "gamepadY",
    "leftshoulder": "gamepadLeftShoulder",
    "gamepadleftshoulder": "gamepadLeftShoulder",
    "rightshoulder": "gamepadRightShoulder",
    "gamepadrightshoulder": "gamepadRightShoulder",
    "lefttrigger": "gamepadLeftTrigger",
    "gamepadlefttrigger": "gamepadLeftTrigger",
    "righttrigger": "gamepadRightTrigger",
    "gamepadrighttrigger": "gamepadRightTrigger",
    "leftstick": "gamepadLeftThumb",
    "leftthumbstick": "gamepadLeftThumb",
    "gamepadleftthumb": "gamepadLeftThumb",
    "rightstick": "gamepadRightThumb",
    "rightthumbstick": "gamepadRightThumb",
    "gamepadrightthumb": "gamepadRightThumb",
    "back": "gamepadBack",
    "select": "gamepadBack",
    "gamepadback": "gamepadBack",
    "start": "gamepadStart",
    "gamepadstart": "gamepadStart",
    "dpadup": "gamepadDpadUp",
    "gamepaddpadup": "gamepadDpadUp",
    "dpaddown": "gamepadDpadDown",
    "gamepaddpaddown": "gamepadDpadDown",
    "dpadleft": "gamepadDpadLeft",
    "gamepaddpadleft": "gamepadDpadLeft",
    "dpadright": "gamepadDpadRight",
    "gamepaddpadright": "gamepadDpadRight",
    "leftthumbstickup": "gamepadLeftStickUp",
    "leftthumbstickdown": "gamepadLeftStickDown",
    "leftthumbstickleft": "gamepadLeftStickLeft",
    "leftthumbstickright": "gamepadLeftStickRight",
    "rightthumbstickup": "gamepadRightStickUp",
    "rightthumbstickdown": "gamepadRightStickDown",
    "rightthumbstickleft": "gamepadRightStickLeft",
    "rightthumbstickright": "gamepadRightStickRight",
}

_JOYSTICK_BUTTONS = {
    0: "gamepadA",
    1: "gamepadB",
    2: "gamepadX",
    3: "gamepadY",
    4: "gamepadLeftShoulder",
    5: "gamepadRightShoulder",
    6: "gamepadBack",
    7: "gamepadStart",
    8: "gamepadLeftThumb",
    9: "gamepadRightThumb",
}

_LITERAL_KEYS = {
    "-": "-",
    "=": "=",
    "'": "'",
    ",": ",",
    ".": ".",
    "/": "/",
    ";": ";",
    "[": "[",
    "]": "]",
    "\\": "\\",
}


def _canonical_input(
    raw_input: object,
    expected_device: str | None = None,
) -> tuple[str, str] | None:
    if not isinstance(raw_input, str):
        return None
    value = raw_input.strip().strip('"\'')
    if not value or value.casefold() in {"none", "null", "unbound", "disabled", "unknown"}:
        return None
    if value in _LITERAL_KEYS and expected_device in {None, "keyboard"}:
        return _LITERAL_KEYS[value], "keyboard"

    # Enum serialization commonly emits Keys.W, KeyCode.W, Buttons.A, or
    # MouseButton.Left.  Preserve the enum type as device context before
    # stripping it; otherwise Buttons.A and MouseButton.Left look like
    # ordinary keyboard keys.
    enum_parts = value.split(".")
    if len(enum_parts) > 1 and re.fullmatch(
        r"(?:keys?|keycode|keyboard|keyboardkey|buttons?|mousebutton|unityengine)+",
        "".join(enum_parts[:-1]),
        flags=re.I,
    ):
        enum_prefix = re.sub(r"[^a-z]", "", "".join(enum_parts[:-1]).casefold())
        enum_device: str | None = None
        if "mousebutton" in enum_prefix:
            enum_device = "mouse"
        elif "button" in enum_prefix:
            enum_device = "gamepad"
        elif any(token in enum_prefix for token in ("key", "keyboard")):
            enum_device = "keyboard"
        if enum_device is not None:
            if expected_device is not None and expected_device != enum_device:
                return None
            expected_device = enum_device
        value = enum_parts[-1]
    value = re.sub(r"^(?:vk|keycode|keyboardkey)[_:-]", "", value, flags=re.I)
    compact = re.sub(r"[^a-z0-9]+", "", value.casefold())
    if not compact:
        return None

    joystick = re.fullmatch(r"joystickbutton(\d{1,2})", compact)
    if joystick:
        name = _JOYSTICK_BUTTONS.get(int(joystick.group(1)))
        return (name, "gamepad") if name and expected_device in {None, "gamepad"} else None

    if expected_device == "gamepad":
        gamepad_name = _GAMEPAD_ALIASES.get(compact)
        return (gamepad_name, "gamepad") if gamepad_name else None

    if expected_device == "mouse":
        contextual_mouse = {
            "left": "leftClick",
            "primary": "leftClick",
            "button0": "leftClick",
            "button1": "leftClick",
            "right": "rightClick",
            "secondary": "rightClick",
            "button2": "rightClick",
            "middle": "middleClick",
            "button3": "middleClick",
            "button4": "mouseButton4",
            "button5": "mouseButton5",
        }.get(compact)
        if contextual_mouse:
            return contextual_mouse, "mouse"

    alias = _INPUT_ALIASES.get(compact)
    if alias is not None:
        return alias if expected_device in {None, alias[1]} else None

    digit = re.fullmatch(r"(?:alpha|digit|key|d)([0-9])", compact)
    if digit and expected_device in {None, "keyboard"}:
        return digit.group(1), "keyboard"
    numpad = re.fullmatch(r"(?:numpad|keypad)([0-9])", compact)
    if numpad and expected_device in {None, "keyboard"}:
        return f"NumPad{numpad.group(1)}", "keyboard"
    if len(compact) == 1 and compact.isalnum() and expected_device in {None, "keyboard"}:
        return compact.upper(), "keyboard"
    function_key = re.fullmatch(r"f(?:[1-9]|1[0-9]|2[0-4])", compact)
    if function_key and expected_device in {None, "keyboard"}:
        return compact.upper(), "keyboard"
    return None


def _action_words(action: str) -> list[str]:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", action)
    return [word.casefold() for word in re.findall(r"[A-Za-z0-9]+", separated)]


def _movement_direction(action: str) -> str | None:
    compact = re.sub(r"[^a-z0-9]+", "", action.casefold())
    exact = {
        "up": "W",
        "down": "B",
        "left": "L",
        "right": "R",
        "forward": "W",
        "backward": "B",
        "moveforward": "W",
        "movebackward": "B",
        "moveback": "B",
        "moveup": "W",
        "movedown": "B",
        "moveleft": "L",
        "moveright": "R",
        "mapdollyup": "W",
        "mapdollydown": "B",
        "mapdollyleft": "L",
        "mapdollyright": "R",
    }
    if compact in exact:
        return exact[compact]
    words = _action_words(action)
    contexts = {"camera", "dolly", "map", "menu", "move", "movement", "navigate", "scroll", "ui"}
    if words and contexts.intersection(words):
        return {"up": "W", "down": "B", "left": "L", "right": "R"}.get(words[-1])
    return None


def _add_mapping(
    keymap: dict[str, dict[str, str]],
    raw_input: object,
    action: object,
    *,
    expected_device: str | None = None,
) -> bool:
    canonical = _canonical_input(raw_input, expected_device)
    if canonical is None or not isinstance(action, str) or not action.strip():
        return False
    input_name, device_type = canonical
    action_name = action.strip()[:120]
    direction = _movement_direction(action_name)
    current = keymap.get(input_name)
    if current is None:
        current = {"type": device_type, "action": action_name}
        if direction:
            current["movement_direction"] = direction
        keymap[input_name] = current
        return True
    actions = [item.strip() for item in current.get("action", "").split(" / ") if item.strip()]
    if action_name not in actions:
        current["action"] = " / ".join([*actions, action_name])[:240]
    if direction and "movement_direction" not in current:
        current["movement_direction"] = direction
    return True


_BAROTRAUMA_ACTIONS = frozenset(
    action.casefold()
    for action in (
        "Run ToggleRun Attack Crouch Grab Health Ragdoll Aim DropItem InfoTab "
        "Chat RadioChat ActiveChat CrewOrders ChatBox Voice RadioVoice LocalVoice "
        "ToggleChatMode Command ContextualCommand PreviousFireMode NextFireMode "
        "TakeHalfFromInventorySlot TakeOneFromInventorySlot Up Down Left Right "
        "ToggleInventory SelectNextCharacter SelectPreviousCharacter Use Select "
        "Deselect Shoot ShowInteractionLabels"
    ).split()
)


def _parse_barotrauma(
    root: ElementTree.Element,
    file_name: str,
) -> CommonXmlKeymapResult | None:
    if _local_name(root.tag).casefold() != "config":
        return None
    key_elements = _direct_children(root, "keymapping")
    inventory_elements = _direct_children(root, "inventorykeymapping")
    if not key_elements and not inventory_elements:
        return None
    attributes = [
        (_local_name(name), value)
        for element in key_elements
        for name, value in element.attrib.items()
    ]
    known_actions = sum(name.casefold() in _BAROTRAUMA_ACTIONS for name, _ in attributes)
    inventory_slots = sum(
        bool(re.fullmatch(r"slot\d+", _local_name(name), flags=re.I))
        for element in inventory_elements
        for name in element.attrib
    )
    if not (
        file_name == "config_player.xml"
        or known_actions >= 3
        or inventory_slots >= 3
    ):
        return None

    detected: dict[str, dict[str, str]] = {}
    for action, raw_input in attributes:
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,119}", action):
            _add_mapping(detected, raw_input, action)
    for element in inventory_elements:
        slots: list[tuple[int, str]] = []
        for raw_name, value in element.attrib.items():
            match = re.fullmatch(r"slot(\d+)", _local_name(raw_name), flags=re.I)
            if match:
                slots.append((int(match.group(1)), value))
        for slot, raw_input in sorted(slots):
            _add_mapping(detected, raw_input, f"Inventory slot {slot + 1}")
    return _recognized(detected, "barotrauma")


_CELESTE_KNOWN_ACTIONS = frozenset(
    action.casefold()
    for action in (
        "Left Right Up Down Jump Dash Grab Talk Pause Confirm Cancel Journal "
        "QuickRestart CrouchDash DemoDash"
    ).split()
)


def _celeste_bindings(
    action_element: ElementTree.Element,
) -> list[tuple[str, str]]:
    bindings: list[tuple[str, str]] = []
    for child in action_element:
        child_name = _local_name(child.tag).casefold()
        if child_name in {"keyboard", "keys", "key", "keycode"}:
            expected = "keyboard"
            value_tags = {"keys", "key", "keycode"}
        elif child_name in {"controller", "gamepad", "buttons", "button"}:
            expected = "gamepad"
            value_tags = {"buttons", "button"}
        elif child_name in {"mouse", "mousebuttons", "mousebutton"}:
            expected = "mouse"
            value_tags = {"mousebuttons", "mousebutton", "button"}
        else:
            continue
        nested = [
            value
            for value in child.iter()
            if value is not child and _local_name(value.tag).casefold() in value_tags
        ]
        if nested:
            bindings.extend((expected, _clean_text(value)) for value in nested)
        elif _clean_text(child):
            bindings.append((expected, _clean_text(child)))
    return bindings


def _parse_celeste(
    root: ElementTree.Element,
    file_name: str,
) -> CommonXmlKeymapResult | None:
    if _local_name(root.tag).casefold() != "settings":
        return None
    candidates: list[tuple[str, list[tuple[str, str]]]] = []
    for element in root.iter():
        if element is root:
            continue
        action = _local_name(element.tag)
        if action.casefold() in {
            "keyboard", "keys", "key", "keycode", "controller", "gamepad",
            "buttons", "button", "mouse", "mousebuttons", "mousebutton",
        }:
            continue
        bindings = _celeste_bindings(element)
        has_device_container = any(
            _local_name(child.tag).casefold()
            in {
                "keyboard", "keys", "key", "keycode", "controller", "gamepad",
                "buttons", "button", "mouse", "mousebuttons", "mousebutton",
            }
            for child in element
        )
        if bindings or has_device_container and action.casefold() in _CELESTE_KNOWN_ACTIONS:
            candidates.append((action, bindings))

    known_count = sum(action.casefold() in _CELESTE_KNOWN_ACTIONS for action, _ in candidates)
    if not candidates:
        return None
    if not (
        file_name == "settings.celeste"
        or known_count >= 3
    ):
        return None

    detected: dict[str, dict[str, str]] = {}
    for action, bindings in candidates:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_. -]{0,119}", action):
            continue
        for device, raw_input in bindings:
            _add_mapping(detected, raw_input, action, expected_device=device)
    return _recognized(detected, "celeste")


_RIMWORLD_BINDING_TAGS = frozenset(
    {"keybindinga", "keybindingb", "defaultkeycodea", "defaultkeycodeb"}
)


def _rimworld_dictionary_entries(
    root: ElementTree.Element,
) -> list[tuple[str, list[str]]]:
    entries: list[tuple[str, list[str]]] = []
    for item in root.iter():
        if _local_name(item.tag).casefold() != "li":
            continue
        key_element = _first_direct_child(item, ("key", "defName"))
        value_element = _first_direct_child(item, ("value",))
        action = _clean_text(key_element)
        if not action or value_element is None:
            continue
        bindings = [
            _clean_text(element)
            for element in value_element.iter()
            if element is not value_element
            and _local_name(element.tag).casefold() in _RIMWORLD_BINDING_TAGS
        ]
        # Some older dictionary serializers store a single KeyCode directly.
        if not bindings and _clean_text(value_element):
            bindings.append(_clean_text(value_element))
        entries.append((action, bindings))
    return entries


def _rimworld_definition_entries(
    root: ElementTree.Element,
) -> list[tuple[str, list[str]]]:
    entries: list[tuple[str, list[str]]] = []
    for definition in root.iter():
        if _local_name(definition.tag).casefold() != "keybindingdef":
            continue
        action = _clean_text(_first_direct_child(definition, ("defName",)))
        if not action:
            continue
        bindings = [
            _clean_text(child)
            for child in definition
            if _local_name(child.tag).casefold() in _RIMWORLD_BINDING_TAGS
        ]
        entries.append((action, bindings))
    return entries


def _parse_rimworld(
    root: ElementTree.Element,
    file_name: str,
) -> CommonXmlKeymapResult | None:
    root_name = _local_name(root.tag).casefold()
    keyprefs_roots = [
        element for element in root.iter() if _local_name(element.tag).casefold() == "keyprefs"
    ]
    dictionary_entries = _rimworld_dictionary_entries(root)
    definition_entries = _rimworld_definition_entries(root)
    keyprefs_signature = root_name == "keyprefs" or bool(keyprefs_roots)
    if not (
        definition_entries
        or keyprefs_signature
        and (file_name == "keyprefs.xml" or dictionary_entries)
    ):
        return None

    detected: dict[str, dict[str, str]] = {}
    for action, bindings in [*dictionary_entries, *definition_entries]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:+ -]{0,119}", action):
            continue
        for raw_input in bindings:
            _add_mapping(detected, raw_input, action)
    return _recognized(detected, "rimworld")


_GENERIC_ACTION_FIELDS = ("action", "actionname", "command", "control", "id", "name")
_GENERIC_INPUT_FIELDS = (
    "key", "keycode", "input", "binding", "keyboard", "mouse", "gamepad", "controller", "button"
)


def _casefold_attributes(element: ElementTree.Element) -> dict[str, str]:
    return {_local_name(name).casefold(): value for name, value in element.attrib.items()}


def _generic_expected_device(field_name: str, attributes: dict[str, str]) -> str | None:
    if field_name == "keyboard":
        return "keyboard"
    if field_name == "mouse":
        return "mouse"
    if field_name in {"gamepad", "controller"}:
        return "gamepad"
    device = attributes.get("device", "").casefold()
    if "keyboard" in device:
        return "keyboard"
    if "mouse" in device:
        return "mouse"
    if "gamepad" in device or "controller" in device or "joystick" in device:
        return "gamepad"
    return None


def _parse_generic_records(
    root: ElementTree.Element,
    file_name: str,
) -> CommonXmlKeymapResult | None:
    root_name = _local_name(root.tag).casefold()
    hint = f"{root_name} {Path(file_name).stem.casefold()}"
    if not any(word in hint for word in ("binding", "bind", "control", "inputmap", "keymap", "keypref")):
        return None

    records: list[tuple[str, str, str | None]] = []
    for element in root.iter():
        attributes = _casefold_attributes(element)
        action_field = next((name for name in _GENERIC_ACTION_FIELDS if attributes.get(name, "").strip()), None)
        input_field = next((name for name in _GENERIC_INPUT_FIELDS if attributes.get(name, "").strip()), None)
        if action_field and input_field:
            records.append(
                (
                    attributes[action_field].strip(),
                    attributes[input_field].strip(),
                    _generic_expected_device(input_field, attributes),
                )
            )
            continue

        children = {_local_name(child.tag).casefold(): _clean_text(child) for child in element}
        action_field = next((name for name in _GENERIC_ACTION_FIELDS if children.get(name, "")), None)
        input_field = next((name for name in _GENERIC_INPUT_FIELDS if children.get(name, "")), None)
        if action_field and input_field:
            records.append(
                (
                    children[action_field],
                    children[input_field],
                    _generic_expected_device(input_field, attributes),
                )
            )

    if len(records) < 3:
        return None
    detected: dict[str, dict[str, str]] = {}
    accepted = 0
    for action, raw_input, device in records:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:+ /-]{0,119}", action):
            continue
        accepted += int(_add_mapping(detected, raw_input, action, expected_device=device))
    if accepted < 3 or accepted * 4 < len(records) * 3:
        return None
    return _recognized(detected, "generic_xml")


__all__ = [
    "COMMON_XML_EXACT_FILENAMES",
    "CommonXmlKeymapResult",
    "is_common_xml_keymap_candidate",
    "parse_common_xml_keymap",
]
