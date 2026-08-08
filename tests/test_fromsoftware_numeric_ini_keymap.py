from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fromsoftware_numeric_ini_keymap as numeric_adapter
from fromsoftware_numeric_ini_keymap import (
    NumericIniLimits,
    discover_numeric_ini_keymap,
    inspect_pe_for_numeric_ini_declaration,
    parse_numeric_keymap_ini,
    parse_numeric_keymap_ini_text,
)


ACTIONS = (
    "ACTION_Move_Forward",
    "ACTION_Move_Back",
    "ACTION_Move_Right",
    "ACTION_Move_Left",
    "ACTION_Camera_Up",
    "ACTION_Camera_Lockon",
    "ACTION_Equip_ChangeRightWep",
    "ACTION_Action_RightAction",
    "ACTION_Action_RightSubAction",
    "ACTION_Action_LeftAction",
    "ACTION_MenuCtrl_Up",
    "ACTION_MenuCtrl_Down",
    "ACTION_MenuCtrl_SpecFunc1",
    "ACTION_MenuCtrl_SpecFunc2",
)


def numeric_ini(
    *,
    primary_overrides: dict[str, tuple[int, int]] | None = None,
    alternate_overrides: dict[str, tuple[int, int]] | None = None,
    omit_modifier: str | None = None,
    omit_alternate: tuple[str, ...] = (),
) -> str:
    primary = {action: (300, 300) for action in ACTIONS}
    primary.update(
        {
            "ACTION_Move_Forward": (17, 300),
            "ACTION_Move_Back": (31, 300),
            "ACTION_Move_Right": (32, 300),
            "ACTION_Move_Left": (30, 300),
            "ACTION_Camera_Up": (264, 300),
            "ACTION_Camera_Lockon": (16, 300),
            "ACTION_Equip_ChangeRightWep": (268, 42),
            "ACTION_Action_RightAction": (256, 300),
            "ACTION_Action_RightSubAction": (256, 42),
            "ACTION_MenuCtrl_Up": (200, 300),
            "ACTION_MenuCtrl_Down": (208, 300),
        }
    )
    if primary_overrides:
        primary.update(primary_overrides)
    alternate = {
        action: (300, 300)
        for action in ACTIONS
        if not action.startswith("ACTION_MenuCtrl_SpecFunc")
        and action not in omit_alternate
    }
    alternate["ACTION_Camera_Lockon"] = (258, 300)
    if alternate_overrides:
        alternate.update(alternate_overrides)

    lines = ["[META]", "FILE_VERSION = 18", "", "[KEYBOARD_AND_MOUSE]"]
    for action, (key, modifier) in primary.items():
        lines.append(f"{action} = {key}")
        if action != omit_modifier:
            lines.append(f"{action}_mod = {modifier}")
    lines.extend(("", "[KEYBOARD_AND_MOUSE_ALT]"))
    for action, (key, modifier) in alternate.items():
        lines.append(f"{action} = {key}")
        lines.append(f"{action}_mod = {modifier}")
    return "\n".join(lines) + "\n"


def synthetic_pe(
    *,
    paths: tuple[str, ...] = (r"Vendor\Product\ProductKeys.ini",),
    actions: tuple[str, ...] = ACTIONS,
    include_alt_anchor: bool = True,
    include_meta_anchor: bool = True,
    include_mod_anchor: bool = True,
    action_gap: int = 3,
    strings_in_readable_section: bool = True,
) -> bytes:
    data = bytearray(0x6000)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x84, 0x8664)
    struct.pack_into("<H", data, 0x86, 1)
    struct.pack_into("<H", data, 0x94, 0xF0)
    struct.pack_into("<H", data, 0x98, 0x020B)
    section = 0x188
    data[section : section + 8] = b".rdata\0\0"
    struct.pack_into(
        "<I", data, section + 16, 0x5E00 if strings_in_readable_section else 0x100
    )
    struct.pack_into("<I", data, section + 20, 0x200)
    struct.pack_into("<I", data, section + 36, 0x40000040)
    cursor = 0x300 if strings_in_readable_section else 0x1000
    literals = [b"KEYBOARD_AND_MOUSE\0", b"FILE_VERSION\0"]
    if include_alt_anchor:
        literals.append(b"KEYBOARD_AND_MOUSE_ALT\0")
    if include_meta_anchor:
        literals.append(b"META\0")
    if include_mod_anchor:
        literals.append(b"_mod\0")
    for literal in literals:
        data[cursor : cursor + len(literal)] = literal
        cursor += len(literal) + 3
    for action in actions:
        literal = action.encode("ascii") + b"\0"
        data[cursor : cursor + len(literal)] = literal
        cursor += len(literal) + action_gap
    for path in paths:
        literal = path.encode("ascii") + b"\0"
        data[cursor : cursor + len(literal)] = literal
        cursor += len(literal) + 3
    return bytes(data)


class NumericIniParserTests(unittest.TestCase):
    def test_decodes_dik_mouse_wheel_chords_and_chinese_actions(self) -> None:
        result = parse_numeric_keymap_ini_text(numeric_ini())

        self.assertTrue(result.recognized_config)
        self.assertEqual(result.action_count, len(ACTIONS))
        self.assertEqual(result.keymap["W"]["movement_direction"], "W")
        self.assertEqual(result.keymap["S"]["movement_direction"], "B")
        self.assertEqual(result.keymap["A"]["movement_direction"], "L")
        self.assertEqual(result.keymap["D"]["movement_direction"], "R")
        self.assertEqual(result.keymap["leftClick"]["action"], "右手武器普通攻击")
        self.assertEqual(result.keymap["Shift+leftClick"]["type"], "mouse")
        self.assertEqual(result.keymap["Shift+leftClick"]["action"], "右手武器重攻击")
        self.assertEqual(result.keymap["Shift+mouseWheelUp"]["action"], "切换右手武器")
        self.assertEqual(result.keymap["middleClick"]["action"], "锁定目标或重置镜头")
        self.assertNotIn("mouseMoveLeft", result.keymap)
        self.assertFalse(any(character.isascii() and character.isalpha()
                             for mapping in result.keymap.values()
                             for character in mapping["action"]))

    def test_unbound_300_and_mouse_motion_are_skipped(self) -> None:
        result = parse_numeric_keymap_ini_text(numeric_ini())

        self.assertTrue(result.recognized_config)
        self.assertFalse(any(mapping["action"] == "镜头向上" for mapping in result.keymap.values()))
        self.assertFalse(any(mapping["action"] == "左手武器动作" for mapping in result.keymap.values()))

    def test_unbound_or_mouse_motion_primary_cannot_keep_modifier(self) -> None:
        for action, primary in (
            ("ACTION_Action_LeftAction", 300),
            ("ACTION_Camera_Up", 264),
        ):
            with self.subTest(primary=primary):
                result = parse_numeric_keymap_ini_text(
                    numeric_ini(primary_overrides={action: (primary, 42)})
                )
                self.assertFalse(result.recognized_config)
                self.assertEqual(result.keymap, {})
                self.assertIn("修饰键并非 300", result.diagnostic)

    def test_unknown_active_code_rejects_entire_map(self) -> None:
        result = parse_numeric_keymap_ini_text(
            numeric_ini(primary_overrides={"ACTION_Move_Forward": (261, 300)})
        )

        self.assertFalse(result.recognized_config)
        self.assertEqual(result.keymap, {})
        self.assertIn("未知输入码 261", result.diagnostic)

    def test_verified_mouse_side_button_codes_are_supported(self) -> None:
        result = parse_numeric_keymap_ini_text(
            numeric_ini(
                primary_overrides={
                    "ACTION_Action_LeftAction": (259, 300),
                    "ACTION_MenuCtrl_SpecFunc1": (260, 300),
                }
            )
        )

        self.assertTrue(result.recognized_config)
        self.assertEqual(result.keymap["mouseButton4"]["action"], "左手武器动作")
        self.assertEqual(result.keymap["mouseButton5"]["action"], "菜单特殊功能一")

    def test_non_modifier_in_mod_slot_rejects_entire_map(self) -> None:
        result = parse_numeric_keymap_ini_text(
            numeric_ini(primary_overrides={"ACTION_Action_RightAction": (256, 17)})
        )

        self.assertFalse(result.recognized_config)
        self.assertIn("不支持的修饰键码 17", result.diagnostic)

    def test_every_action_requires_a_mod_pair(self) -> None:
        result = parse_numeric_keymap_ini_text(
            numeric_ini(omit_modifier="ACTION_Move_Forward")
        )

        self.assertFalse(result.recognized_config)
        self.assertIn("未成对", result.diagnostic)

    def test_expected_executable_schema_must_match_primary_table(self) -> None:
        result = parse_numeric_keymap_ini_text(
            numeric_ini(), expected_actions=(*ACTIONS, "ACTION_Action_Jump")
        )

        self.assertFalse(result.recognized_config)
        self.assertIn("可执行文件声明不一致", result.diagnostic)

    def test_alternate_table_obeys_exact_schema_relationship(self) -> None:
        valid = parse_numeric_keymap_ini_text(numeric_ini())
        arbitrary_subset = parse_numeric_keymap_ini_text(
            numeric_ini(omit_alternate=("ACTION_Move_Back",))
        )

        self.assertTrue(valid.recognized_config)
        self.assertFalse(arbitrary_subset.recognized_config)
        self.assertIn("精确 schema 关系", arbitrary_subset.diagnostic)

    def test_action_merge_length_overflow_rejects_instead_of_truncating(self) -> None:
        actions = tuple(f"ACTION_Custom_{index:02d}" for index in range(30))
        lines = ["[META]", "FILE_VERSION=18", "", "[KEYBOARD_AND_MOUSE]"]
        for action in actions:
            lines.extend((f"{action}=18", f"{action}_mod=300"))
        lines.extend(("", "[KEYBOARD_AND_MOUSE_ALT]"))
        for action in actions:
            lines.extend((f"{action}=300", f"{action}_mod=300"))
        details = {
            action: (f"非常长的测试动作语义{chr(0x4E00 + index)}", "")
            for index, action in enumerate(actions)
        }
        with patch.dict(numeric_adapter._ACTION_DETAILS, details):
            result = parse_numeric_keymap_ini_text("\n".join(lines) + "\n")

        self.assertFalse(result.recognized_config)
        self.assertEqual(result.keymap, {})
        self.assertIn("动作语义总长度超过安全上限", result.diagnostic)

    def test_missing_format_section_is_not_recognized(self) -> None:
        text = numeric_ini().replace("[KEYBOARD_AND_MOUSE_ALT]", "[OTHER]")
        result = parse_numeric_keymap_ini_text(text)

        self.assertFalse(result.recognized_config)
        self.assertEqual(result.keymap, {})

    def test_path_reader_supports_utf16_bom_and_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "PlayerKeys.ini"
            source.write_bytes(b"\xff\xfe" + numeric_ini().encode("utf-16-le"))
            parsed = parse_numeric_keymap_ini(source)
            too_small = parse_numeric_keymap_ini(
                source, limits=NumericIniLimits(max_ini_bytes=10)
            )

        self.assertTrue(parsed.recognized_config)
        self.assertFalse(too_small.recognized_config)
        self.assertIn("安全", too_small.diagnostic)


class NumericIniExecutableDiscoveryTests(unittest.TestCase):
    def test_pe_requires_all_format_anchors_actions_and_safe_relative_path(self) -> None:
        declaration = inspect_pe_for_numeric_ini_declaration(synthetic_pe())

        self.assertIsNotNone(declaration)
        assert declaration is not None
        self.assertEqual(declaration.relative_paths, (r"Vendor\Product\ProductKeys.ini",))
        self.assertEqual(declaration.action_names, frozenset(ACTIONS))
        self.assertIsNone(
            inspect_pe_for_numeric_ini_declaration(
                synthetic_pe(include_alt_anchor=False)
            )
        )
        self.assertIsNone(
            inspect_pe_for_numeric_ini_declaration(
                synthetic_pe(include_meta_anchor=False)
            )
        )
        self.assertIsNone(
            inspect_pe_for_numeric_ini_declaration(
                synthetic_pe(include_mod_anchor=False)
            )
        )
        self.assertIsNone(
            inspect_pe_for_numeric_ini_declaration(
                synthetic_pe(paths=(r"..\Outside\BadKeys.ini",))
            )
        )

    def test_pe_rejects_overlay_anchors_and_scattered_action_strings(self) -> None:
        self.assertIsNone(
            inspect_pe_for_numeric_ini_declaration(
                synthetic_pe(strings_in_readable_section=False)
            )
        )
        self.assertIsNone(
            inspect_pe_for_numeric_ini_declaration(
                synthetic_pe(action_gap=128)
            )
        )

    def test_missing_declared_player_ini_requires_game_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as local:
            game = Path(directory)
            (game / "program.exe").write_bytes(synthetic_pe())

            result = discover_numeric_ini_keymap(game, local_appdata=local)

        self.assertTrue(result.recognized)
        self.assertTrue(result.requires_game_launch)
        self.assertFalse(result.has_verified_player_config)
        self.assertEqual(result.keymap, {})
        self.assertIn("启动游戏", result.diagnostic)
        self.assertEqual(result.scanned_executables, 1)

    def test_existing_declared_player_ini_is_strictly_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as local:
            game = Path(directory)
            executable = game / "program.exe"
            executable.write_bytes(
                synthetic_pe(
                    paths=(
                        r"RegionA\Product\ProductKeys.ini",
                        r"RegionB\Product\ProductKeys.ini",
                    )
                )
            )
            source = Path(local) / "RegionB" / "Product" / "ProductKeys.ini"
            source.parent.mkdir(parents=True)
            source.write_text(numeric_ini(), encoding="utf-8")

            result = discover_numeric_ini_keymap(game, local_appdata=local)

        self.assertTrue(result.recognized)
        self.assertFalse(result.requires_game_launch)
        self.assertTrue(result.has_verified_player_config)
        self.assertEqual(result.executable_path, executable)
        self.assertEqual(result.source_file, source)
        self.assertEqual(result.keymap["W"]["movement_direction"], "W")
        self.assertIn("Shift+leftClick", result.keymap)

    def test_invalid_existing_ini_is_not_misreported_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as local:
            game = Path(directory)
            (game / "program.exe").write_bytes(synthetic_pe())
            source = Path(local) / "Vendor" / "Product" / "ProductKeys.ini"
            source.parent.mkdir(parents=True)
            source.write_text("[META]\nFILE_VERSION=18\n", encoding="utf-8")

            result = discover_numeric_ini_keymap(game, local_appdata=local)

        self.assertTrue(result.recognized)
        self.assertFalse(result.requires_game_launch)
        self.assertFalse(result.has_verified_player_config)
        self.assertIn("严格校验失败", result.diagnostic)


if __name__ == "__main__":
    unittest.main()
