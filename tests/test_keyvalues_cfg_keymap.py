from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from keyvalues_cfg_keymap import (
    MAX_KEY_CONFIG_BYTES,
    MAX_KEY_CONFIG_CHARS,
    MAX_KEYVALUES_NESTING,
    is_keyvalues_cfg_candidate,
    parse_idtech_bind_cfg,
    parse_keyvalues_cfg,
    parse_keyvalues_cfg_file,
    parse_valve_keyvalues_bindings,
)


class CandidateTests(unittest.TestCase):
    def test_idtech_and_source2_names_are_candidates(self) -> None:
        for name in (
            "q3config.cfg",
            "DoomConfig.cfg",
            "wolfconfig_mp.cfg",
            "etconfig.cfg",
            "my_mod_controls.cfg",
            "cs2_user_keys_0_slot0.vcfg",
            "input_bindings.vdf",
            "keyboard_controls.txt",
        ):
            with self.subTest(name=name):
                self.assertTrue(is_keyvalues_cfg_candidate(name))

    def test_unrelated_generic_files_are_not_candidates(self) -> None:
        self.assertFalse(is_keyvalues_cfg_candidate("video.vdf"))
        self.assertFalse(is_keyvalues_cfg_candidate("characters.txt"))
        self.assertFalse(is_keyvalues_cfg_candidate("bindings.png"))


class IdTechCfgTests(unittest.TestCase):
    def test_quake_bindings_and_action_semantics(self) -> None:
        parsed = parse_idtech_bind_cfg(
            r'''
            // settings are deliberately ignored
            seta r_mode "-1"
            bind TAB "+scores"
            bind ENTER "+button2"
            bind ESCAPE "togglemenu"
            bind SPACE "+moveup"
            bind 1 "weapon 1"
            bind w "+forward"
            bind s "+back"
            bind a "+moveleft"
            bind d "+moveright"
            bind MOUSE1 "+attack"
            bind MWHEELUP "weapnext"
            bind KP_HOME "weapon 7"
            bind F12 "screenshotJPEG"
            '''
        )

        self.assertTrue(parsed.recognized_config)
        self.assertEqual(parsed.format_name, "idtech_cfg")
        self.assertEqual(parsed.keymap["W"]["action"], "Move forward")
        self.assertEqual(parsed.keymap["W"]["movement_direction"], "W")
        self.assertEqual(parsed.keymap["S"]["movement_direction"], "B")
        self.assertEqual(parsed.keymap["A"]["movement_direction"], "L")
        self.assertEqual(parsed.keymap["D"]["movement_direction"], "R")
        self.assertEqual(parsed.keymap["Space"]["action"], "Jump")
        self.assertNotIn("movement_direction", parsed.keymap["Space"])
        self.assertEqual(
            parsed.keymap["leftClick"], {"type": "mouse", "action": "Attack"}
        )
        self.assertEqual(parsed.keymap["mouseWheelUp"]["action"], "Next weapon")
        self.assertEqual(parsed.keymap["NumPad7"]["action"], "Weapon 7")
        self.assertEqual(parsed.keymap["F12"]["action"], "Screenshot")

    def test_doom_underscore_commands_and_controller_aliases(self) -> None:
        parsed = parse_idtech_bind_cfg(
            r'''
            bind "w" "_forward"
            bind "s" "_back"
            bind "MOUSE2" "_zoom"
            bind "JOY1" "_jump"
            bind "X_DPAD_UP" "_impulse 14"
            bind "KP_SLASH" "_impulse 10"
            '''
        )
        self.assertTrue(parsed.recognized_config)
        self.assertEqual(parsed.keymap["W"]["action"], "Move forward")
        self.assertEqual(parsed.keymap["rightClick"]["action"], "Zoom")
        self.assertEqual(parsed.keymap["gamepadA"]["action"], "Jump")
        self.assertEqual(parsed.keymap["gamepadDpadUp"]["action"], "Impulse 14")
        self.assertEqual(parsed.keymap["NumPadDivide"]["action"], "Impulse 10")

    def test_override_unbind_unbindall_and_empty_binding_order(self) -> None:
        parsed = parse_idtech_bind_cfg(
            r'''
            bind w "+forward"
            bind W "+back"
            unbind "w"
            bind w "+forward"
            bind mouse1 "+attack"
            bind mouse1 ""
            unbindall
            bind x "+moveright"
            '''
        )
        self.assertTrue(parsed.recognized_config)
        self.assertEqual(set(parsed.keymap), {"X"})
        self.assertEqual(parsed.keymap["X"]["movement_direction"], "R")

    def test_unquoted_statement_separator_and_quoted_macro(self) -> None:
        parsed = parse_idtech_bind_cfg(
            'bind w "+forward"; bind w "+back"\n'
            'bind mouse1 "+attack; _reload" // quoted macro\n'
            'bind "\'" "messagemode"\n'
        )
        self.assertEqual(parsed.keymap["W"]["movement_direction"], "B")
        self.assertEqual(parsed.keymap["leftClick"]["action"], "Attack / Reload")
        self.assertEqual(parsed.keymap["'"]["action"], "Chat")

    def test_unbindall_is_recognized_even_when_empty(self) -> None:
        parsed = parse_idtech_bind_cfg("unbindall\n")
        self.assertTrue(parsed.recognized_config)
        self.assertEqual(parsed.keymap, {})

    def test_settings_and_malformed_text_are_not_recognized(self) -> None:
        settings = parse_idtech_bind_cfg(
            'seta r_mode "-1"\nseta r_fullscreen "1"\n'
        )
        malformed = parse_idtech_bind_cfg('bind "w" "+forward\n')
        self.assertFalse(settings.recognized_config)
        self.assertFalse(malformed.recognized_config)
        self.assertEqual(settings.keymap, {})
        self.assertEqual(malformed.keymap, {})


class ValveKeyvaluesTests(unittest.TestCase):
    def test_keyvalues1_direct_bindings(self) -> None:
        parsed = parse_valve_keyvalues_bindings(
            r'''
            "config"
            {
                "bindings"
                {
                    "W" "+forward"
                    "S" "+back"
                    "MOUSE1" "+attack"
                    "SPACE" "+jump"
                    "F10" ""
                }
                "video" { "setting.cpu_level" "2" }
            }
            ''',
            file_name="cs2_user_keys_0_slot0.vcfg",
        )
        self.assertTrue(parsed.recognized_config)
        self.assertEqual(parsed.format_name, "source2_vcfg")
        self.assertEqual(parsed.keymap["W"]["movement_direction"], "W")
        self.assertEqual(parsed.keymap["S"]["movement_direction"], "B")
        self.assertEqual(parsed.keymap["leftClick"]["action"], "Attack")
        self.assertEqual(parsed.keymap["Space"]["action"], "Jump")
        self.assertNotIn("F10", parsed.keymap)

    def test_kv3_text_and_later_pair_overrides(self) -> None:
        parsed = parse_valve_keyvalues_bindings(
            r'''
            <!-- kv3 encoding:text:version{00000000-0000-0000-0000-000000000000}
                 format:generic:version{00000000-0000-0000-0000-000000000000} -->
            {
                bindings =
                {
                    A = "+moveleft",
                    D = "+moveright",
                    A = "+attack",
                    X_BUTTON_A = "+jump",
                    X_BUTTON_LEFT_SHOULDER = "+speed",
                    X_BUTTON_UP = "slot1"
                }
            }
            ''',
            file_name="user_keys.vcfg",
        )
        self.assertTrue(parsed.recognized_config)
        self.assertEqual(parsed.keymap["A"]["action"], "Attack")
        self.assertNotIn("movement_direction", parsed.keymap["A"])
        self.assertEqual(parsed.keymap["D"]["movement_direction"], "R")
        self.assertEqual(parsed.keymap["gamepadA"]["action"], "Jump")
        self.assertEqual(parsed.keymap["gamepadLeftShoulder"]["action"], "Run")
        self.assertEqual(parsed.keymap["gamepadDpadUp"]["action"], "Weapon slot 1")

    def test_keyvalues_platform_conditions_do_not_shift_following_pairs(self) -> None:
        parsed = parse_valve_keyvalues_bindings(
            r'''
            "bindings" {
                "W" "+forward" [$WINDOWS]
                "S" "+back"
            }
            ''',
            file_name="input_bindings.vdf",
        )
        self.assertTrue(parsed.recognized_config)
        self.assertEqual(parsed.keymap["W"]["movement_direction"], "W")
        self.assertEqual(parsed.keymap["S"]["movement_direction"], "B")

    def test_multiple_binding_objects_apply_in_document_order(self) -> None:
        parsed = parse_valve_keyvalues_bindings(
            r'''
            "defaults" { "bindings" { "W" "+forward" "E" "+use" } }
            "user" { "bindings" { "W" "" "E" "+reload" } }
            ''',
            file_name="input_bindings.vdf",
        )
        self.assertTrue(parsed.recognized_config)
        self.assertNotIn("W", parsed.keymap)
        self.assertEqual(parsed.keymap["E"]["action"], "Reload")

    def test_nested_records_are_not_guessed_as_direct_pairs(self) -> None:
        parsed = parse_valve_keyvalues_bindings(
            r'''
            "config" {
                "bindings" {
                    "0" { "key" "W" "command" "+forward" }
                }
            }
            ''',
            file_name="video.vdf",
        )
        self.assertFalse(parsed.recognized_config)
        self.assertEqual(parsed.keymap, {})

    def test_graphics_vdf_and_pairs_outside_bindings_are_rejected(self) -> None:
        graphics = parse_valve_keyvalues_bindings(
            r'''
            "VideoConfig" {
                "setting.cpu_level" "2"
                "bindings" { "shadow_quality" "high" "texture_detail" "low" }
            }
            ''',
            file_name="video.vdf",
        )
        outside = parse_valve_keyvalues_bindings(
            '"config" { "W" "+forward" "MOUSE1" "+attack" }',
            file_name="config.vdf",
        )
        not_an_object = parse_valve_keyvalues_bindings(
            '"bindings" "W" "+forward"', file_name="input_bindings.vdf"
        )
        self.assertFalse(graphics.recognized_config)
        self.assertFalse(outside.recognized_config)
        self.assertFalse(not_an_object.recognized_config)

    def test_empty_binding_object_requires_strong_filename(self) -> None:
        strong = parse_valve_keyvalues_bindings(
            '"config" { "bindings" { } }',
            file_name="cs2_user_keys_0_slot0.vcfg",
        )
        weak = parse_valve_keyvalues_bindings(
            '"config" { "bindings" { } }', file_name="video.vdf"
        )
        self.assertTrue(strong.recognized_config)
        self.assertEqual(strong.keymap, {})
        self.assertFalse(weak.recognized_config)

    def test_malformed_binary_and_excessive_nesting_are_rejected(self) -> None:
        malformed = parse_valve_keyvalues_bindings(
            '"config" { "bindings" { "W" "+forward" }',
            file_name="user_keys.vcfg",
        )
        binary = parse_valve_keyvalues_bindings(
            '"bindings" {\x00"W" "+forward"}', file_name="user_keys.vcfg"
        )
        deep = parse_valve_keyvalues_bindings(
            "{" * (MAX_KEYVALUES_NESTING + 1)
            + '"bindings" { "W" "+forward" }'
            + "}" * (MAX_KEYVALUES_NESTING + 1),
            file_name="user_keys.vcfg",
        )
        self.assertFalse(malformed.recognized_config)
        self.assertFalse(binary.recognized_config)
        self.assertFalse(deep.recognized_config)


class AutoDetectAndFileTests(unittest.TestCase):
    def test_auto_detects_both_formats(self) -> None:
        cfg = parse_keyvalues_cfg('bind "w" "+forward"', file_name="q3config.cfg")
        kv = parse_keyvalues_cfg(
            '"config" { "bindings" { "W" "+forward" } }',
            file_name="user_keys.vcfg",
        )
        self.assertEqual(cfg.format_name, "idtech_cfg")
        self.assertEqual(kv.format_name, "source2_vcfg")

    def test_file_reader_handles_utf16_and_byte_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            utf16 = root / "DoomConfig.cfg"
            oversized = root / "q3config.cfg"
            utf16.write_bytes('bind "w" "_forward"'.encode("utf-16"))
            oversized.write_bytes(b"x" * (MAX_KEY_CONFIG_BYTES + 1))

            parsed = parse_keyvalues_cfg_file(utf16)
            too_big = parse_keyvalues_cfg_file(oversized)

        self.assertTrue(parsed.recognized_config)
        self.assertEqual(parsed.keymap["W"]["movement_direction"], "W")
        self.assertFalse(too_big.recognized_config)
        self.assertTrue(too_big.truncated)

    def test_text_character_limit_is_all_or_nothing(self) -> None:
        parsed = parse_keyvalues_cfg("x" * (MAX_KEY_CONFIG_CHARS + 1))
        self.assertFalse(parsed.recognized_config)
        self.assertEqual(parsed.keymap, {})
        self.assertTrue(parsed.truncated)


if __name__ == "__main__":
    unittest.main()
