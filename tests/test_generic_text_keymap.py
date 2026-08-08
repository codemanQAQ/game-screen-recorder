from __future__ import annotations

import unittest

from screen_recorder import (
    _canonical_input_name,
    _godot_key_name,
    _movement_direction,
    _parse_godot_input_map,
    _parse_simple_text_keymap,
    _parse_simple_yaml_keymap,
)


class GenericTextKeymapTests(unittest.TestCase):
    def test_common_serialized_enum_names_are_canonicalized(self) -> None:
        self.assertEqual(_canonical_input_name("Key1"), ("1", "keyboard"))
        self.assertEqual(
            _canonical_input_name("SDL_SCANCODE_W"), ("W", "keyboard")
        )
        self.assertEqual(_canonical_input_name("D4"), ("4", "keyboard"))
        self.assertEqual(_canonical_input_name("OemComma"), (",", "keyboard"))
        self.assertEqual(
            _canonical_input_name("controller_face_button_south"),
            ("gamepadA", "gamepad"),
        )
        self.assertEqual(_godot_key_name(0x02000000 | ord("A")), "A")
        self.assertEqual(_godot_key_name(0x01000000 | 1), "Esc")
        self.assertEqual(_godot_key_name(0x0100002C, modern=False), "Win")
        self.assertEqual(
            _canonical_input_name("Shift+Ctrl+K"),
            ("Ctrl+Shift+K", "keyboard"),
        )

    def test_parses_godot_text_input_map_without_running_game_code(self) -> None:
        text = '''[application]
config/name="Example"

[input]
move_up={
"deadzone": 0.5,
"events": [Object(InputEventKey,"physical_keycode":87)]
}
pause={
"events": [Object(InputEventKey,"keycode":4194305)]
}
fire={
"events": [Object(InputEventMouseButton,"button_index":1)]
}
jump={
"events": [Object(InputEventJoypadButton,"button_index":0)]
}
move_left={
"events": [Object(InputEventJoypadMotion,"axis":0,"axis_value":-1.0)]
}

[rendering]
renderer/rendering_method="gl_compatibility"
'''
        parsed = _parse_godot_input_map(text)
        self.assertEqual(parsed["W"]["movement_direction"], "W")
        self.assertEqual(parsed["Esc"]["action"], "pause")
        self.assertEqual(parsed["leftClick"]["action"], "fire")
        self.assertEqual(parsed["gamepadA"]["action"], "jump")
        self.assertEqual(
            parsed["gamepadLeftStickLeft"]["movement_direction"], "L"
        )

    def test_godot_legacy_gamepad_modifiers_and_neutral_axes(self) -> None:
        text = '''config_version=4
[input]
command={
"events": [Object(InputEventKey,"scancode":75,"ctrl_pressed":true)]
}
shoulder={
"events": [Object(InputEventJoypadButton,"button_index":4)]
}
neutral={
"events": [Object(InputEventJoypadMotion,"axis":0,"axis_value":0.0)]
}
trigger={
"events": [Object(InputEventJoypadMotion,"axis":6,"axis_value":1.0)]
}
'''
        parsed = _parse_godot_input_map(text)
        self.assertEqual(parsed["Ctrl+K"]["action"], "command")
        self.assertEqual(parsed["gamepadLeftShoulder"]["action"], "shoulder")
        self.assertEqual(parsed["gamepadLeftTrigger"]["action"], "trigger")
        self.assertNotIn("gamepadLeftStickRight", parsed)

    def test_simple_text_parser_requires_a_control_section_and_multiple_keys(self) -> None:
        parsed = _parse_simple_text_keymap(
            "[Controls]\nMoveUp=W\nMoveDown=S\nJump=Space\nInventory=I\n"
        )
        self.assertEqual(parsed["W"]["movement_direction"], "W")
        self.assertEqual(parsed["Space"]["action"], "Jump")
        self.assertEqual(parsed["I"]["action"], "Inventory")

        self.assertEqual(
            _parse_simple_text_keymap(
                "[Video]\nWidth=1920\nHeight=1080\nFullscreen=true\n"
            ),
            {},
        )
        self.assertEqual(
            _parse_simple_text_keymap("[Monkey]\nRed=R\nGreen=G\nBlue=B\n"),
            {},
        )
        self.assertEqual(
            _parse_simple_text_keymap(
                "[InputAudio]\nDevice=A\nRate=R\nBuffer=B\n"
            ),
            {},
        )

    def test_yaml_nested_key_lists_are_attributed_to_the_parent_action(self) -> None:
        parsed = _parse_simple_yaml_keymap(
            "controls:\n"
            "  MoveUp:\n"
            "    keys:\n"
            "      - W\n"
            "  Jump:\n"
            "    primary: Space\n"
            "  Inventory: I\n"
        )
        self.assertEqual(parsed["W"]["action"], "MoveUp")
        self.assertEqual(parsed["Space"]["action"], "Jump")
        self.assertEqual(parsed["I"]["action"], "Inventory")
        self.assertEqual(
            _parse_simple_yaml_keymap(
                "controls:\n  visual_theme: R\n  opacity: T\n  color: B\n"
            ),
            {},
        )

    def test_movement_classifier_requires_movement_semantics(self) -> None:
        self.assertIsNone(_movement_direction("FastForward", "W"))
        self.assertIsNone(_movement_direction("VerticalSync", "Up"))
        self.assertIsNone(_movement_direction("RemoveUpgrade", "W"))
        self.assertIsNone(_movement_direction("left", "gamepadLeftShoulder"))
        self.assertEqual(_movement_direction("left", "A"), "L")
        self.assertEqual(_movement_direction("MoveUp", "K"), "W")


if __name__ == "__main__":
    unittest.main()
