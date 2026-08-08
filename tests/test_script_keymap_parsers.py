from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from script_keymap_parsers import (
    MAX_SCRIPT_ACTIONS,
    MAX_SCRIPT_BYTES,
    MAX_SCRIPT_TOKENS,
    is_script_keymap_candidate,
    parse_script_keymap,
    parse_script_keymap_file,
)


class ScriptKeymapParserTests(unittest.TestCase):
    def test_renpy_config_keymap_is_parsed_without_executing_code(self) -> None:
        parsed = parse_script_keymap(
            r'''
init python:
    config.keymap = {
        "jump": ["K_SPACE"],
        "move_left": ["K_a", "K_LEFT"],
        "move_right": ["K_d"],
        "quick_save": ["ctrl_shift_K_s"],
        "rollback": ["mousedown_4"],
        "confirm": ["pad_a"],
    }
''',
            source_name="options.rpy",
        )

        self.assertTrue(parsed.recognized_config)
        self.assertFalse(parsed.authoritative)
        self.assertEqual(parsed.schema, "renpy_keymap")
        self.assertEqual(parsed.keymap["Space"]["action"], "Jump")
        self.assertEqual(parsed.keymap["A"]["movement_direction"], "L")
        self.assertEqual(parsed.keymap["Left"]["movement_direction"], "L")
        self.assertEqual(parsed.keymap["D"]["movement_direction"], "R")
        self.assertEqual(parsed.keymap["Ctrl+Shift+S"]["action"], "Quick save")
        self.assertEqual(parsed.keymap["mouseWheelUp"]["type"], "mouse")
        self.assertEqual(parsed.keymap["gamepadA"]["type"], "gamepad")

    def test_renpy_default_statement_survives_surrounding_dsl(self) -> None:
        parsed = parse_script_keymap(
            '''
label start:
    scene bg room
    "This is Ren'Py DSL and is not valid Python."

default keymap = {
    "move_up": ["K_w", "K_UP"],
    "move_down": ["K_s", "K_DOWN"],
    "interact": ["K_e"],
}
''',
            source_name="script.rpy",
        )

        self.assertTrue(parsed.recognized_config)
        self.assertEqual(parsed.keymap["W"]["movement_direction"], "W")
        self.assertEqual(parsed.keymap["Down"]["movement_direction"], "B")
        self.assertEqual(parsed.keymap["E"]["action"], "Interact")

    def test_renpy_computed_values_are_rejected_not_executed(self) -> None:
        parsed = parse_script_keymap(
            '''
config.keymap = {
    "jump": dangerous_call("space"),
    "left": ["K_a"],
    "right": ["K_d"],
    "menu": ["K_ESCAPE"],
}
''',
            source_name="options.rpy",
        )

        self.assertFalse(parsed.recognized_config)
        self.assertEqual(parsed.keymap, {})

    def test_love_lua_action_table_and_mouse_numbering(self) -> None:
        parsed = parse_script_keymap(
            '''
-- LÖVE controls; this table is read as data and never loaded as Lua.
local controls = {
    jump = "space",
    move_left = { "a", "left" },
    move_right = "d",
    attack = "mouse1",
    alternate_attack = "mouse2",
    pause = "escape",
}
''',
            source_name="controls.lua",
        )

        self.assertTrue(parsed.recognized_config)
        self.assertEqual(parsed.schema, "lua_action_table")
        self.assertEqual(parsed.keymap["Space"]["action"], "Jump")
        self.assertEqual(parsed.keymap["leftClick"]["action"], "Attack")
        self.assertEqual(parsed.keymap["rightClick"]["action"], "Alternate attack")
        self.assertEqual(parsed.keymap["A"]["movement_direction"], "L")
        self.assertEqual(parsed.keymap["D"]["movement_direction"], "R")

    def test_lua_grouped_devices_and_bracket_keys(self) -> None:
        parsed = parse_script_keymap(
            '''
keybindings = {
    keyboard = {
        ["jump"] = "space";
        ["menu"] = "escape";
        ["special"] = "ctrl-k";
    };
    gamepad = {
        jump = "a";
        menu = "start";
        special = "right_bumper";
    };
    fire = { mouse = 1 };
}
''',
            source_name="input.lua",
        )

        self.assertTrue(parsed.recognized_config)
        self.assertEqual(parsed.keymap["Ctrl+K"]["action"], "Special")
        self.assertEqual(parsed.keymap["gamepadA"]["action"], "Jump")
        self.assertEqual(parsed.keymap["gamepadStart"]["action"], "Menu")
        self.assertEqual(parsed.keymap["gamepadRightShoulder"]["action"], "Special")
        self.assertEqual(parsed.keymap["leftClick"]["action"], "Fire")

    def test_javascript_json_like_nested_bindings(self) -> None:
        parsed = parse_script_keymap(
            '''
const keybindings = {
  moveUp: { keys: ["KeyW", "ArrowUp"] },
  moveDown: { primary: "KeyS", secondary: "ArrowDown" },
  interact: { keyboard: "KeyE", gamepad: "buttonSouth" },
  inventory: "Ctrl+Shift+KeyI",
  fire: { mouse: "leftClick" },
};
''',
            source_name="controls.js",
        )

        self.assertTrue(parsed.recognized_config)
        self.assertEqual(parsed.schema, "javascript_action_object")
        self.assertEqual(parsed.keymap["W"]["movement_direction"], "W")
        self.assertEqual(parsed.keymap["Up"]["movement_direction"], "W")
        self.assertEqual(parsed.keymap["S"]["movement_direction"], "B")
        self.assertEqual(parsed.keymap["gamepadA"]["action"], "Interact")
        self.assertEqual(parsed.keymap["Ctrl+Shift+I"]["action"], "Inventory")
        self.assertEqual(parsed.keymap["leftClick"]["type"], "mouse")

    def test_javascript_nested_actions_wrapper_is_supported(self) -> None:
        parsed = parse_script_keymap(
            '''
export const inputMap = {
  actions: {
    accept: ["Enter", "gamepadA"],
    cancel: ["Escape", "gamepadB"],
    screenshot: "F12",
  },
};
''',
            source_name="input.mjs",
        )

        self.assertTrue(parsed.recognized_config)
        self.assertEqual(parsed.keymap["Enter"]["action"], "Accept")
        self.assertEqual(parsed.keymap["gamepadA"]["type"], "gamepad")
        self.assertEqual(parsed.keymap["gamepadB"]["type"], "gamepad")
        self.assertEqual(parsed.keymap["F12"]["action"], "Screenshot")

    def test_rpg_maker_input_keymapper_numeric_orientation(self) -> None:
        parsed = parse_script_keymap(
            '''
Input.keyMapper = {
    13: "ok",
    27: "escape",
    32: "jump",
    37: "left",
    38: "up",
    39: "right",
    40: "down",
    65: "left",
    68: "right",
};
''',
            source_name="rpg_core.js",
        )

        self.assertTrue(parsed.recognized_config)
        self.assertEqual(parsed.schema, "javascript_keycode_map")
        self.assertEqual(parsed.keymap["Enter"]["action"], "Ok")
        self.assertEqual(parsed.keymap["Esc"]["action"], "Escape")
        self.assertEqual(parsed.keymap["Space"]["action"], "Jump")
        self.assertEqual(parsed.keymap["Left"]["movement_direction"], "L")
        self.assertEqual(parsed.keymap["A"]["movement_direction"], "L")
        self.assertEqual(parsed.keymap["D"]["movement_direction"], "R")

    def test_json_like_wrapped_controls_object(self) -> None:
        parsed = parse_script_keymap(
            '''{
  "graphics": {"quality": "high"},
  "controls": {
    "move_up": "W",
    "move_down": "S",
    "interact": "E",
    "pause": "Escape"
  }
}''',
            source_name="preferences.json",
        )

        self.assertTrue(parsed.recognized_config)
        self.assertEqual(parsed.schema, "javascript_action_object")
        self.assertEqual(parsed.keymap["W"]["movement_direction"], "W")
        self.assertEqual(parsed.keymap["S"]["movement_direction"], "B")
        self.assertEqual(parsed.keymap["E"]["action"], "Interact")

    def test_gamemaker_known_vk_constants_are_static_literals(self) -> None:
        parsed = parse_script_keymap(
            '''
global.keybindings = {
    move_left: vk_left,
    move_right: vk_right,
    move_up: vk_up,
    move_down: vk_down,
    pause: vk_escape,
    debug: vk_f2,
};
''',
            source_name="controls.gml",
        )

        self.assertTrue(parsed.recognized_config)
        self.assertEqual(parsed.schema, "gamemaker_action_table")
        self.assertEqual(parsed.keymap["Left"]["movement_direction"], "L")
        self.assertEqual(parsed.keymap["Right"]["movement_direction"], "R")
        self.assertEqual(parsed.keymap["Esc"]["action"], "Pause")
        self.assertEqual(parsed.keymap["F2"]["action"], "Debug")

    def test_less_than_three_static_bindings_is_not_recognized(self) -> None:
        for source in (
            'const controls = { jump: "Space", menu: "Escape" };',
            'controls = { jump = "space", fire = dynamicKey, menu = "escape" }',
            'default keymap = {"jump": ["K_SPACE"], "menu": ["K_ESCAPE"]}',
        ):
            with self.subTest(source=source):
                parsed = parse_script_keymap(source, source_name="controls.js")
                self.assertFalse(parsed.recognized_config)
                self.assertEqual(parsed.keymap, {})

    def test_visual_settings_color_table_is_rejected(self) -> None:
        parsed = parse_script_keymap(
            '''
const controls = {
  red: "R",
  green: "G",
  blue: "B",
  alpha: "A",
  width: "W",
  height: "H",
};
''',
            source_name="theme.js",
        )

        self.assertFalse(parsed.recognized_config)

    def test_localization_and_unrelated_objects_are_rejected(self) -> None:
        documents = (
            '''const controls = { jump: "Jump", attack: "Attack", pause: "Pause" };''',
            '''const palette = { up: "W", down: "S", left: "A", right: "D" };''',
            '''local strings = { jump = "Space", attack = "F", menu = "Escape" }''',
        )
        for source in documents:
            with self.subTest(source=source):
                self.assertFalse(parse_script_keymap(source, source_name="data.js").recognized_config)

    def test_bare_identifiers_and_template_interpolation_are_not_static(self) -> None:
        for source in (
            '''const controls = { jump: SPACE, attack: KEY_F, menu: ESCAPE };''',
            '''const controls = { jump: `${prefix}Space`, attack: "F", menu: "Escape" };''',
        ):
            with self.subTest(source=source):
                self.assertFalse(parse_script_keymap(source, source_name="controls.js").recognized_config)

    def test_duplicate_inputs_merge_actions(self) -> None:
        parsed = parse_script_keymap(
            '''const controls = { accept: "Space", jump: "Space", advance: "Space" };''',
            source_name="controls.js",
        )

        self.assertTrue(parsed.recognized_config)
        self.assertEqual(
            set(parsed.keymap["Space"]["action"].split(" / ")),
            {"Accept", "Jump", "Advance"},
        )

    def test_size_nesting_and_malformed_input_limits(self) -> None:
        oversized = " " * (MAX_SCRIPT_BYTES + 1)
        self.assertFalse(parse_script_keymap(oversized, source_name="controls.lua").recognized_config)

        deeply_nested = "const controls = " + "{" * 40 + "}" * 40
        self.assertFalse(parse_script_keymap(deeply_nested, source_name="controls.js").recognized_config)

        malformed = 'const controls = { jump: "Space", attack: ["F", menu: "Esc" };'
        self.assertFalse(parse_script_keymap(malformed, source_name="controls.js").recognized_config)

        entries = ",".join(
            f'action_{index}: "A"' for index in range(MAX_SCRIPT_ACTIONS + 1)
        )
        too_many_entries = "const controls = {" + entries + "};"
        self.assertFalse(
            parse_script_keymap(too_many_entries, source_name="controls.js").recognized_config
        )

        too_many_tokens = "ignored " * (MAX_SCRIPT_TOKENS + 1)
        self.assertFalse(
            parse_script_keymap(too_many_tokens, source_name="controls.js").recognized_config
        )

    def test_bounded_file_api_and_candidate_suffixes(self) -> None:
        self.assertTrue(is_script_keymap_candidate("options.rpy"))
        self.assertTrue(is_script_keymap_candidate("controls.LUA"))
        self.assertTrue(is_script_keymap_candidate("input.js"))
        self.assertTrue(is_script_keymap_candidate("preferences.json"))
        self.assertTrue(is_script_keymap_candidate("controls.gml"))
        self.assertFalse(is_script_keymap_candidate("localization.csv"))
        self.assertFalse(is_script_keymap_candidate("game.exe"))

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            valid = root / "controls.lua"
            valid.write_text(
                'controls = { jump = "space", attack = "f", menu = "escape" }',
                encoding="utf-8",
            )
            oversized = root / "huge.js"
            oversized.write_bytes(b" " * (MAX_SCRIPT_BYTES + 1))

            self.assertTrue(parse_script_keymap_file(valid).recognized_config)
            self.assertFalse(parse_script_keymap_file(oversized).recognized_config)
            self.assertFalse(parse_script_keymap_file(root / "missing.lua").recognized_config)


if __name__ == "__main__":
    unittest.main()
