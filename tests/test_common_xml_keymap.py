from __future__ import annotations

import unittest

from common_xml_keymap import (
    CommonXmlKeymapResult,
    is_common_xml_keymap_candidate,
    parse_common_xml_keymap,
)


class CommonXmlKeymapTests(unittest.TestCase):
    def test_barotrauma_player_config_attributes_and_inventory(self) -> None:
        result = parse_common_xml_keymap(
            """<?xml version="1.0"?>
            <config>
              <keymapping
                Run="LeftShift" Attack="Keys.R" Aim="SecondaryMouse"
                Up="W" Down="S" Select="PrimaryMouse"
                Shoot="PrimaryMouse" ToggleRun="None" />
              <inventorykeymapping slot0="D1" slot1="D2" slot2="None" />
            </config>
            """,
            file_name=r"C:\games\Barotrauma\config_player.xml",
        )

        self.assertTrue(result.recognized)
        self.assertTrue(result.recognized_config)
        self.assertEqual(result.format_name, "barotrauma")
        self.assertEqual(result.keymap["Shift"], {"type": "keyboard", "action": "Run"})
        self.assertEqual(result.keymap["R"]["action"], "Attack")
        self.assertEqual(result.keymap["rightClick"]["action"], "Aim")
        self.assertEqual(result.keymap["leftClick"]["action"], "Select / Shoot")
        self.assertEqual(result.keymap["W"]["movement_direction"], "W")
        self.assertEqual(result.keymap["S"]["movement_direction"], "B")
        self.assertEqual(result.keymap["1"]["action"], "Inventory slot 1")
        self.assertEqual(result.keymap["2"]["action"], "Inventory slot 2")

    def test_barotrauma_structure_is_recognized_without_filename(self) -> None:
        result = parse_common_xml_keymap(
            '<config><keymapping Run="LeftShift" Attack="R" Use="E" /></config>'
        )

        self.assertTrue(result.recognized)
        self.assertEqual(set(result.keymap), {"Shift", "R", "E"})

    def test_barotrauma_like_unrelated_xml_does_not_match(self) -> None:
        result = parse_common_xml_keymap(
            '<config><keymapping color="Red" layout="Wide" /></config>'
        )

        self.assertFalse(result.recognized)
        self.assertEqual(result.keymap, {})

    def test_celeste_nested_keyboard_and_controller_with_multiple_keys(self) -> None:
        result = parse_common_xml_keymap(
            """<Settings>
              <Left><Keyboard><Keys>Left</Keys><Keys>A</Keys></Keyboard></Left>
              <Right><Keyboard><Keys>Right</Keys><Keys>D</Keys></Keyboard></Right>
              <Up><Keyboard><Keys>Up</Keys><Keys>W</Keys></Keyboard></Up>
              <Down><Keyboard><Keys>Down</Keys><Keys>S</Keys></Keyboard></Down>
              <Jump>
                <Keyboard><Keys>C</Keys><Keys>Space</Keys></Keyboard>
                <Controller><Buttons>A</Buttons></Controller>
              </Jump>
              <Dash>
                <Keyboard><Keys>X</Keys></Keyboard>
                <Controller><Buttons>X</Buttons></Controller>
              </Dash>
            </Settings>""",
            file_name="settings.celeste",
        )

        self.assertTrue(result.recognized)
        self.assertEqual(result.format_name, "celeste")
        self.assertEqual(result.keymap["Space"]["action"], "Jump")
        self.assertEqual(result.keymap["gamepadA"], {"type": "gamepad", "action": "Jump"})
        self.assertEqual(result.keymap["gamepadX"]["action"], "Dash")
        self.assertEqual(result.keymap["A"]["movement_direction"], "L")
        self.assertEqual(result.keymap["D"]["movement_direction"], "R")
        self.assertEqual(result.keymap["W"]["movement_direction"], "W")
        self.assertEqual(result.keymap["S"]["movement_direction"], "B")

    def test_celeste_legacy_direct_keys_and_empty_binding_are_recognized(self) -> None:
        direct = parse_common_xml_keymap(
            """<Settings>
              <Jump><Keys>Space</Keys></Jump>
              <Dash><Keys>X</Keys></Dash>
              <Grab><Keys>Z</Keys></Grab>
            </Settings>"""
        )
        empty = parse_common_xml_keymap(
            "<Settings><Jump><Keyboard /></Jump></Settings>",
            file_name="settings.celeste",
        )

        self.assertTrue(direct.recognized)
        self.assertEqual(direct.keymap["Space"]["action"], "Jump")
        self.assertTrue(empty.recognized)
        self.assertEqual(empty.keymap, {})

    def test_rimworld_keyprefs_dictionary_uses_player_overrides(self) -> None:
        result = parse_common_xml_keymap(
            """<KeyPrefs><keys>
              <li><key>MapDolly_Up</key><value>
                <keyBindingA>W</keyBindingA><keyBindingB>UpArrow</keyBindingB>
              </value></li>
              <li><key>MapDolly_Down</key><value>
                <keyBindingA>S</keyBindingA><keyBindingB>None</keyBindingB>
              </value></li>
              <li><key>OpenThingTab</key><value>
                <keyBindingA>Mouse2</keyBindingA><keyBindingB>None</keyBindingB>
              </value></li>
            </keys></KeyPrefs>""",
            file_name="KeyPrefs.xml",
        )

        self.assertTrue(result.recognized)
        self.assertEqual(result.format_name, "rimworld")
        self.assertEqual(result.keymap["W"]["movement_direction"], "W")
        self.assertEqual(result.keymap["Up"]["movement_direction"], "W")
        self.assertEqual(result.keymap["S"]["movement_direction"], "B")
        self.assertEqual(result.keymap["middleClick"]["action"], "OpenThingTab")

    def test_rimworld_key_binding_def_defaults(self) -> None:
        result = parse_common_xml_keymap(
            """<Defs>
              <KeyBindingDef>
                <defName>MapDolly_Left</defName>
                <defaultKeyCodeA>A</defaultKeyCodeA>
                <defaultKeyCodeB>LeftArrow</defaultKeyCodeB>
              </KeyBindingDef>
              <KeyBindingDef>
                <defName>MapDolly_Right</defName>
                <defaultKeyCodeA>D</defaultKeyCodeA>
              </KeyBindingDef>
            </Defs>"""
        )

        self.assertTrue(result.recognized)
        self.assertEqual(result.keymap["A"]["movement_direction"], "L")
        self.assertEqual(result.keymap["Left"]["movement_direction"], "L")
        self.assertEqual(result.keymap["D"]["movement_direction"], "R")

    def test_rimworld_empty_authoritative_file_is_recognized(self) -> None:
        result = parse_common_xml_keymap(
            "<KeyPrefs />",
            file_name=r"C:\Users\Player\Config\KeyPrefs.xml",
        )

        self.assertTrue(result.recognized)
        self.assertEqual(result.keymap, {})

    def test_generic_repeated_binding_records_require_strong_signature(self) -> None:
        result = parse_common_xml_keymap(
            """<controls>
              <binding action="MoveForward" key="W" />
              <binding action="MoveBackward" key="S" />
              <binding action="PrimaryAttack" mouse="Left" />
              <binding action="Pause" key="Escape" />
            </controls>""",
            file_name="controls.xml",
        )

        self.assertTrue(result.recognized)
        self.assertEqual(result.format_name, "generic_xml")
        self.assertEqual(result.keymap["W"]["movement_direction"], "W")
        self.assertEqual(result.keymap["S"]["movement_direction"], "B")
        self.assertEqual(result.keymap["leftClick"]["type"], "mouse")
        self.assertEqual(result.keymap["Esc"]["action"], "Pause")

    def test_generic_child_records_and_gamepad_device(self) -> None:
        result = parse_common_xml_keymap(
            """<inputMappings>
              <bind device="gamepad"><action>Jump</action><button>A</button></bind>
              <bind device="gamepad"><action>Cancel</action><button>B</button></bind>
              <bind device="keyboard"><action>Pause</action><key>Escape</key></bind>
            </inputMappings>"""
        )

        self.assertTrue(result.recognized)
        self.assertEqual(result.keymap["gamepadA"]["action"], "Jump")
        self.assertEqual(result.keymap["gamepadB"]["action"], "Cancel")
        self.assertEqual(result.keymap["Esc"]["action"], "Pause")

    def test_enum_prefix_determines_gamepad_and_mouse_device(self) -> None:
        result = parse_common_xml_keymap(
            """<controls>
              <binding action="Jump" input="Buttons.A" />
              <binding action="Cancel" input="Buttons.B" />
              <binding action="Shoot" input="MouseButton.Left" />
              <binding action="Aim" input="MouseButton.Right" />
            </controls>""",
            file_name="controls.xml",
        )

        self.assertTrue(result.recognized)
        self.assertEqual(result.keymap["gamepadA"], {"type": "gamepad", "action": "Jump"})
        self.assertEqual(result.keymap["gamepadB"], {"type": "gamepad", "action": "Cancel"})
        self.assertEqual(result.keymap["leftClick"], {"type": "mouse", "action": "Shoot"})
        self.assertEqual(result.keymap["rightClick"], {"type": "mouse", "action": "Aim"})

    def test_conflicting_explicit_device_and_enum_prefix_is_rejected(self) -> None:
        result = parse_common_xml_keymap(
            """<controls>
              <binding action="Jump" keyboard="Buttons.A" />
              <binding action="Cancel" keyboard="Buttons.B" />
              <binding action="Pause" keyboard="Keys.Escape" />
            </controls>""",
            file_name="controls.xml",
        )

        self.assertFalse(result.recognized)
        self.assertEqual(result.keymap, {})

    def test_unrelated_malformed_and_entity_xml_are_not_recognized(self) -> None:
        unrelated = parse_common_xml_keymap(
            """<Settings>
              <Graphics><Width>1920</Width><Height>1080</Height></Graphics>
              <Audio><Volume>80</Volume></Audio>
            </Settings>"""
        )
        malformed = parse_common_xml_keymap("<controls><binding></controls>")
        entity = parse_common_xml_keymap(
            '<!DOCTYPE x [<!ENTITY key "W">]><controls><bind key="&key;" /></controls>'
        )

        self.assertEqual(unrelated, CommonXmlKeymapResult({}, False, None))
        self.assertFalse(malformed.recognized)
        self.assertFalse(entity.recognized)

    def test_candidate_filter_includes_special_extension_without_all_xml_assets(self) -> None:
        self.assertTrue(is_common_xml_keymap_candidate("settings.celeste"))
        self.assertTrue(is_common_xml_keymap_candidate("KeyPrefs.xml"))
        self.assertTrue(is_common_xml_keymap_candidate("custom_controls.xml"))
        self.assertFalse(is_common_xml_keymap_candidate("characters.xml"))
        self.assertFalse(is_common_xml_keymap_candidate("texture.png"))


if __name__ == "__main__":
    unittest.main()
