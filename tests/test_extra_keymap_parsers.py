from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from extra_keymap_parsers import (
    discover_structured_json_keymaps,
    parse_structured_json_file,
    parse_structured_json_keymap,
)


SLAY_THE_SPIRE_2_DIRECTORY = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2"
)
SLAY_THE_SPIRE_2_SETTINGS = Path(
    r"C:\Program Files (x86)\Steam\userdata\939738312\2868840\remote\settings.save"
)


class StructuredJsonParserTests(unittest.TestCase):
    def test_action_input_maps_parse_keyboard_controller_and_duplicates(self) -> None:
        parsed = parse_structured_json_keymap(
            {
                "keyboard_mapping": {
                    "ui_accept": "E",
                    "ui_cancel": "Escape",
                    "mega_pause_and_back": "Escape",
                    "ui_down": "Down",
                    "mega_release_card": "Down",
                    "mega_select_card_1": "Key1",
                    "mega_view_deck_and_tab_left": "D",
                },
                "controller_mapping": {
                    "ui_accept": "controller_face_button_north",
                    "ui_select": "controller_face_button_south",
                    "ui_cancel": "controller_face_button_east",
                    "mega_top_panel": "controller_face_button_west",
                    "ui_up": "controller_d_pad_north",
                },
            },
            source_name="settings.save",
        )

        self.assertTrue(parsed.recognized_config)
        self.assertTrue(parsed.authoritative)
        self.assertEqual(parsed.schema, "action_input_maps")
        self.assertEqual(parsed.keymap["E"]["action"], "Accept")
        self.assertEqual(
            set(parsed.keymap["Esc"]["action"].split(" / ")),
            {"Cancel", "Pause and back"},
        )
        self.assertEqual(parsed.keymap["1"]["action"], "Select card 1")
        self.assertEqual(parsed.keymap["Down"]["movement_direction"], "B")
        self.assertNotIn("movement_direction", parsed.keymap["D"])
        self.assertEqual(parsed.keymap["gamepadY"]["action"], "Accept")
        self.assertEqual(parsed.keymap["gamepadA"]["action"], "Select")
        self.assertEqual(parsed.keymap["gamepadB"]["action"], "Cancel")
        self.assertEqual(parsed.keymap["gamepadX"]["action"], "Top panel")
        self.assertEqual(parsed.keymap["gamepadDpadUp"]["movement_direction"], "W")

    def test_action_input_map_orientation_can_be_input_to_action(self) -> None:
        parsed = parse_structured_json_keymap(
            {
                "keyboard_mapping": {
                    "Space": "Jump",
                    "W": "MoveUp",
                    "Escape": "ui_cancel",
                },
                "controller_mapping": {
                    "controller_face_button_south": "Jump",
                    "controller_d_pad_north": "MoveUp",
                },
            },
            source_name="settings.save",
        )

        self.assertTrue(parsed.recognized_config)
        self.assertTrue(parsed.authoritative)
        self.assertEqual(parsed.keymap["Space"]["action"], "Jump")
        self.assertEqual(parsed.keymap["W"]["movement_direction"], "W")
        self.assertEqual(parsed.keymap["gamepadA"]["action"], "Jump")

    def test_arbitrary_or_empty_keyboard_mapping_is_not_authoritative(self) -> None:
        for document in (
            {"keyboard_mapping": {}},
            {"keyboard_mapping": {"theme": "A"}},
            {"keyboard_mapping": {"layout": "W", "volume": "V"}},
        ):
            with self.subTest(document=document):
                parsed = parse_structured_json_keymap(
                    document,
                    source_name="settings.save",
                )
                self.assertFalse(parsed.recognized_config)
                self.assertFalse(parsed.authoritative)
                self.assertEqual(parsed.keymap, {})

    def test_movement_depends_on_action_semantics_not_physical_key(self) -> None:
        parsed = parse_structured_json_keymap(
            {
                "keyboard_mapping": {
                    "Attack": "W",
                    "MoveUp": "J",
                    "RemoveUpgrade": "Up",
                    "FastForward": "D",
                }
            }
        )

        self.assertTrue(parsed.recognized_config)
        self.assertNotIn("movement_direction", parsed.keymap["W"])
        self.assertEqual(parsed.keymap["J"]["movement_direction"], "W")
        self.assertNotIn("movement_direction", parsed.keymap["Up"])
        self.assertNotIn("movement_direction", parsed.keymap["D"])

    def test_oxygen_not_included_entry_array_is_sparse(self) -> None:
        parsed = parse_structured_json_keymap(
            [
                {
                    "mButton": "NumButtons",
                    "mKeyCode": "W",
                    "mAction": "PanUp",
                    "mModifier": "None",
                },
                {
                    "mButton": "NumButtons",
                    "mKeyCode": "Mouse4",
                    "mAction": "Dig",
                    "mModifier": "None",
                },
                {
                    "mButton": "NumButtons",
                    "mKeyCode": "Q",
                    "mAction": "Prioritize",
                    "mModifier": "LeftShift",
                },
                {
                    "mButton": "NumButtons",
                    "mKeyCode": "None",
                    "mAction": "CycleSpeed",
                    "mModifier": "None",
                },
            ],
            source_name="keybindings.json",
        )

        self.assertTrue(parsed.recognized_config)
        self.assertFalse(parsed.authoritative)
        self.assertEqual(parsed.schema, "oxygen_not_included")
        self.assertEqual(parsed.keymap["W"]["movement_direction"], "W")
        self.assertEqual(parsed.keymap["mouseButton5"]["action"], "Dig")
        self.assertEqual(parsed.keymap["Shift+Q"]["action"], "Prioritize")
        self.assertNotIn("Q", parsed.keymap)
        self.assertNotIn("None", parsed.keymap)
        self.assertEqual(
            parsed.overridden_actions,
            frozenset({"Pan Up", "Dig", "Prioritize", "Cycle Speed"}),
        )

    def test_empty_oni_file_is_recognized_by_exact_filename(self) -> None:
        parsed = parse_structured_json_keymap([], source_name="keybindings.json")
        self.assertTrue(parsed.recognized_config)
        self.assertFalse(parsed.authoritative)
        self.assertEqual(parsed.keymap, {})

    def test_terraria_uses_selected_profile_and_xna_mouse_numbering(self) -> None:
        parsed = parse_structured_json_keymap(
            {
                "Selected Profile": "Custom",
                "Redigit's Pick": {
                    "Mouse And Keyboard": {"Jump": ["J"]},
                },
                "Custom": {
                    "Settings": {"Edittable": True},
                    "Mouse And Keyboard": {
                        "MouseLeft": ["Mouse1"],
                        "MouseRight": ["Mouse2"],
                        "Up": ["W"],
                        "Down": ["S"],
                        "Left": ["A"],
                        "Right": ["D"],
                        "Jump": ["Space"],
                        "Hotbar1": ["D1"],
                        "ExampleMod/RandomBuff": ["Q"],
                    },
                    "Gamepad": {
                        "Jump": ["A"],
                        "Inventory": ["Y"],
                    },
                },
            },
            source_name="input profiles.json",
        )

        self.assertTrue(parsed.recognized_config)
        self.assertTrue(parsed.authoritative)
        self.assertEqual(parsed.schema, "terraria_key_configuration")
        self.assertNotIn("J", parsed.keymap)
        self.assertEqual(parsed.keymap["leftClick"]["action"], "Mouse Left")
        self.assertEqual(parsed.keymap["rightClick"]["action"], "Mouse Right")
        self.assertEqual(parsed.keymap["W"]["movement_direction"], "W")
        self.assertEqual(parsed.keymap["S"]["movement_direction"], "B")
        self.assertEqual(parsed.keymap["A"]["movement_direction"], "L")
        self.assertEqual(parsed.keymap["D"]["movement_direction"], "R")
        self.assertEqual(parsed.keymap["1"]["action"], "Hotbar 1")
        self.assertEqual(parsed.keymap["Q"]["action"], "Example Mod: Random Buff")
        self.assertEqual(parsed.keymap["gamepadA"]["action"], "Jump")
        self.assertEqual(parsed.keymap["gamepadY"]["action"], "Inventory")

    def test_exported_key_configuration_key_status_is_supported(self) -> None:
        parsed = parse_structured_json_keymap(
            {
                "KeyConfiguration": {
                    "KeyStatus": {
                        "Jump": ["Space"],
                        "QuickHeal": ["H"],
                        "MapZoomIn": ["OemPlus"],
                    }
                }
            }
        )
        self.assertTrue(parsed.recognized_config)
        self.assertEqual(parsed.keymap["Space"]["action"], "Jump")
        self.assertEqual(parsed.keymap["H"]["action"], "Quick Heal")
        self.assertEqual(parsed.keymap["="]["action"], "Map Zoom In")

    def test_unrelated_json_is_not_recognized(self) -> None:
        parsed = parse_structured_json_keymap(
            {"keyboard": {"layout": "qwerty"}, "graphics": {"fps": 60}}
        )
        self.assertFalse(parsed.recognized_config)
        self.assertEqual(parsed.keymap, {})

    def test_file_parser_accepts_utf8_bom_and_rejects_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            valid = root / "settings.save"
            valid.write_text(
                json.dumps({"keyboard_mapping": {"ui_accept": "Enter"}}),
                encoding="utf-8-sig",
            )
            invalid = root / "keybindings.json"
            invalid.write_text("not json", encoding="utf-8")
            self.assertEqual(parse_structured_json_file(valid).keymap["Enter"]["action"], "Accept")
            self.assertFalse(parse_structured_json_file(invalid).recognized_config)

    def test_file_parser_rejects_excessive_nesting_without_recursion_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested = root / "settings.save"
            nested.write_text(
                "[" * 2_000 + "0" + "]" * 2_000,
                encoding="utf-8",
            )
            self.assertFalse(parse_structured_json_file(nested).recognized_config)

            quoted_brackets = root / "quoted-settings.save"
            quoted_brackets.write_text(
                json.dumps(
                    {
                        "note": "[" * 1_000 + "]" * 1_000,
                        "keyboard_mapping": {"ui_accept": "Enter"},
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(parse_structured_json_file(quoted_brackets).recognized_config)

    def test_direct_parser_rejects_deep_or_cyclic_container(self) -> None:
        deep: object = {"keyboard_mapping": {"ui_accept": "Enter"}}
        for _ in range(200):
            deep = {"wrapper": deep}
        self.assertFalse(parse_structured_json_keymap(deep).recognized_config)

        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic
        self.assertFalse(parse_structured_json_keymap(cyclic).recognized_config)

    def test_discovery_finds_install_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested = root / "config"
            nested.mkdir()
            path = nested / "keybindings.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "mButton": "NumButtons",
                            "mKeyCode": "R",
                            "mAction": "RotateBuilding",
                            "mModifier": "None",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            discovery = discover_structured_json_keymaps(root)
            self.assertTrue(discovery.recognized_config)
            self.assertFalse(discovery.authoritative)
            self.assertEqual(discovery.source_files, (path,))
            self.assertEqual(discovery.keymap["R"]["action"], "Rotate Building")

    def test_player_root_discovery_is_opt_in_and_never_crosses_last_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            steam = Path(temp_dir) / "Steam"
            steamapps = steam / "steamapps"
            game = steamapps / "common" / "Example Game"
            game.mkdir(parents=True)
            (steamapps / "appmanifest_123.acf").write_text(
                '"AppState"\n{\n"appid" "123"\n"installdir" "Example Game"\n'
                '"LastOwner" "111"\n}',
                encoding="utf-8",
            )
            wrong_remote = steam / "userdata" / "222" / "123" / "remote"
            wrong_remote.mkdir(parents=True)
            (wrong_remote / "settings.save").write_text(
                json.dumps({"keyboard_mapping": {"ui_accept": "E"}}),
                encoding="utf-8",
            )

            default_discovery = discover_structured_json_keymaps(game)
            opted_in = discover_structured_json_keymaps(
                game,
                include_player_roots=True,
            )
            self.assertFalse(default_discovery.recognized_config)
            self.assertFalse(opted_in.recognized_config)

    @unittest.skipUnless(
        SLAY_THE_SPIRE_2_DIRECTORY.is_dir() and SLAY_THE_SPIRE_2_SETTINGS.is_file(),
        "local Slay the Spire 2 Steam Cloud sample is unavailable",
    )
    def test_real_slay_the_spire_2_player_settings_and_steam_discovery(self) -> None:
        direct = parse_structured_json_file(SLAY_THE_SPIRE_2_SETTINGS)
        self.assertTrue(direct.recognized_config)
        self.assertEqual(direct.schema, "action_input_maps")
        self.assertEqual(direct.keymap["E"]["action"], "Accept")
        self.assertEqual(direct.keymap["1"]["action"], "Select card 1")

        discovery = discover_structured_json_keymaps(
            SLAY_THE_SPIRE_2_DIRECTORY,
            include_player_roots=True,
        )
        self.assertTrue(discovery.recognized_config)
        self.assertTrue(discovery.authoritative)
        self.assertEqual(discovery.source_files, (SLAY_THE_SPIRE_2_SETTINGS,))
        self.assertEqual(discovery.schemas, ("action_input_maps",))
        self.assertEqual(len(direct.keymap), 38)
        self.assertEqual(discovery.keymap, direct.keymap)


if __name__ == "__main__":
    unittest.main()
