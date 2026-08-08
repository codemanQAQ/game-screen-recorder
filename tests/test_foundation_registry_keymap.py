from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from foundation_registry_keymap import (
    RegistryKeymapLimits,
    decode_crystal_binding,
    discover_registry_keymap,
    inspect_pe_for_registry_schema,
)


ACTION_IDS = {
    "ButtonAction_Melee": 0,
    "ButtonAction_Interact": 1,
    "ButtonAction_Roll": 2,
    "ButtonAction_Jump": 3,
    "ButtonAction_Weapon_Aim": 4,
    "ButtonAction_Weapon_Primary": 5,
    "ButtonAction_Instinct": 6,
    "ButtonAction_Weapon_Alternate": 7,
    "ButtonAction_ShoulderSwap": 8,
    "ButtonAction_Weapon_Zoom": 9,
    "ButtonAction_Map": 11,
    "ButtonAction_Weapon_Bow": 12,
    "ButtonAction_Weapon_Pistol": 13,
    "ButtonAction_Weapon_Shotgun": 14,
    "ButtonAction_Weapon_SMG": 15,
    "ButtonAction_Move_Right": 16,
    "ButtonAction_Move_Left": 17,
    "ButtonAction_Move_Back": 18,
    "ButtonAction_Move_Forward": 19,
    "ButtonAction_Walk": 20,
    "ButtonAction_Weapon_Next": 21,
    "ButtonAction_Weapon_Prev": 22,
    "ButtonAction_Reload": 23,
    "ButtonAction_TextChat": 24,
    "ButtonAction_TextChatTeam": 25,
    "ButtonAction_VoicechatSpeak": 26,
}

DEFAULT_VALUES = {
    0: "161",
    1: "146",
    2: "170",
    3: "185",
    4: "98",
    5: "97",
    6: "144",
    7: "100,185",
    8: "174",
    9: "172",
    10: "129",
    11: "143",
    12: "130",
    13: "131",
    14: "132",
    15: "133",
    16: "333,160",
    17: "331,158",
    18: "336,159",
    19: "328,145",
    20: "157",
    21: "103",
    22: "104",
    23: "147",
    24: "148",
    25: "149",
    26: "",
}


def synthetic_foundation_pe(
    *,
    registry_path: str = r"SOFTWARE\Synthetic Vendor\Synthetic Game\Controls",
    corrupt_action: str | None = None,
) -> bytes:
    data = bytearray(0x8000)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x84, 0x014C)
    struct.pack_into("<H", data, 0x86, 2)
    struct.pack_into("<H", data, 0x94, 0xE0)
    optional = 0x98
    struct.pack_into("<H", data, optional, 0x010B)
    struct.pack_into("<I", data, optional + 28, 0x00400000)
    sections = optional + 0xE0
    data[sections : sections + 8] = b".text\0\0\0"
    struct.pack_into("<I", data, sections + 8, 0x3000)
    struct.pack_into("<I", data, sections + 12, 0x1000)
    struct.pack_into("<I", data, sections + 16, 0x3000)
    struct.pack_into("<I", data, sections + 20, 0x400)
    struct.pack_into("<I", data, sections + 36, 0x60000020)
    rdata = sections + 40
    data[rdata : rdata + 8] = b".rdata\0\0"
    struct.pack_into("<I", data, rdata + 8, 0x3000)
    struct.pack_into("<I", data, rdata + 12, 0x5000)
    struct.pack_into("<I", data, rdata + 16, 0x3000)
    struct.pack_into("<I", data, rdata + 20, 0x3400)
    struct.pack_into("<I", data, rdata + 36, 0x40000040)

    string_offset = 0x3400
    encoded_path = registry_path.encode("ascii") + b"\0"
    data[string_offset : string_offset + len(encoded_path)] = encoded_path
    string_offset += len(encoded_path)
    for token in (
        "Input_LMouseButton",
        "Input_RMouseButton",
        "Input_MMouseButton",
        "Input_MouseButton4",
        "Input_MouseButton5",
        "Input_MouseWheelUp",
        "Input_MouseWheelDown",
        "Input_MouseWheelLeft",
        "Input_MouseWheelRight",
    ):
        encoded = token.encode("ascii") + b"\0"
        data[string_offset : string_offset + len(encoded)] = encoded
        string_offset += len(encoded)

    code_offset = 0x400
    for token, action_id in ACTION_IDS.items():
        encoded = token.encode("ascii") + b"\0"
        token_offset = string_offset
        data[token_offset : token_offset + len(encoded)] = encoded
        string_offset += len(encoded)
        token_va = 0x00400000 + 0x5000 + token_offset - 0x3400
        stored_id = action_id + 1 if token == corrupt_action else action_id
        block = b"".join(
            (
                b"\x68" + struct.pack("<I", token_va),
                b"\xB9" + struct.pack("<I", 0x00408000),
                b"\xA3" + struct.pack("<I", 0x00409000),
                b"\xE8\0\0\0\0",
                b"\x68" + struct.pack("<I", 0x00405000),
                b"\xB9" + struct.pack("<I", 0x00408000),
                b"\xA3" + struct.pack("<I", 0x00409000),
                b"\xC7\x05" + struct.pack("<II", 0x00409004, stored_id),
                b"\xE8\0\0\0\0",
            )
        )
        data[code_offset : code_offset + len(block)] = block
        code_offset += len(block) + 4
    return bytes(data)


class FakeWinreg:
    HKEY_CURRENT_USER = object()
    KEY_READ = 0x20019
    KEY_WOW64_32KEY = 0x0200
    KEY_WOW64_64KEY = 0x0100
    REG_SZ = 1
    REG_EXPAND_SZ = 2

    def __init__(
        self,
        values: dict[int, str] | None,
        *,
        available_view: int = KEY_WOW64_32KEY,
    ) -> None:
        self.values = values
        self.available_view = available_view
        self.opened_views: list[int] = []
        self.closed = 0

    def OpenKey(self, _root, _path, _reserved, access):
        view = access & (self.KEY_WOW64_32KEY | self.KEY_WOW64_64KEY)
        self.opened_views.append(view)
        if self.values is None or view != self.available_view:
            raise FileNotFoundError
        return view

    def EnumValue(self, _key, index):
        assert self.values is not None
        items = sorted(self.values.items())
        if index >= len(items):
            raise OSError
        action_id, value = items[index]
        return str(action_id), value, self.REG_SZ

    def CloseKey(self, _key):
        self.closed += 1


class FailingEnumerationWinreg(FakeWinreg):
    def QueryInfoKey(self, _key):
        assert self.values is not None
        return 0, len(self.values), 0

    def EnumValue(self, key, index):
        if index == 24:
            error = OSError("registry read failure")
            error.winerror = 5
            raise error
        return super().EnumValue(key, index)


class FoundationRegistryKeymapTests(unittest.TestCase):
    def test_inspects_complete_pe32_action_schema(self) -> None:
        schema = inspect_pe_for_registry_schema(synthetic_foundation_pe())

        self.assertIsNotNone(schema)
        assert schema is not None
        self.assertEqual(
            schema.registry_path,
            r"Software\Synthetic Vendor\Synthetic Game\Controls",
        )
        self.assertEqual(set(schema.action_ids), set(ACTION_IDS.values()))
        self.assertEqual(schema.action_ids[19], "ButtonAction_Move_Forward")

    def test_rejects_corrupted_action_id_relationship(self) -> None:
        self.assertIsNone(
            inspect_pe_for_registry_schema(
                synthetic_foundation_pe(corrupt_action="ButtonAction_Jump")
            )
        )

    def test_rejects_schema_with_unrecognized_extra_action(self) -> None:
        data = bytearray(synthetic_foundation_pe())
        extra = b"ButtonAction_UnexpectedFutureAction\0"
        data[0x7000 : 0x7000 + len(extra)] = extra

        self.assertIsNone(inspect_pe_for_registry_schema(bytes(data)))

    def test_rejects_keywords_without_valid_pe_structure(self) -> None:
        fake = (
            b"MZ"
            + b"\0" * 100
            + rb"SOFTWARE\Vendor\Game\Controls"
            + b"\0ButtonAction_Jump\0Input_LMouseButton\0"
        )
        self.assertIsNone(inspect_pe_for_registry_schema(fake))

    def test_decodes_verified_mouse_and_directinput_codes(self) -> None:
        self.assertEqual(decode_crystal_binding(97), ("leftClick", "mouse"))
        self.assertEqual(decode_crystal_binding(101), ("mouseButton4", "mouse"))
        self.assertEqual(decode_crystal_binding(102), ("mouseButton5", "mouse"))
        self.assertEqual(decode_crystal_binding(105), ("mouseWheelLeft", "mouse"))
        self.assertEqual(decode_crystal_binding(106), ("mouseWheelRight", "mouse"))
        self.assertIsNone(decode_crystal_binding(99))
        self.assertEqual(decode_crystal_binding("145"), ("W", "keyboard"))
        self.assertEqual(decode_crystal_binding(187), ("F1", "keyboard"))
        self.assertEqual(decode_crystal_binding(333), ("Right", "keyboard"))
        self.assertIsNone(decode_crystal_binding(999))
        self.assertIsNone(decode_crystal_binding("not-a-code"))

    def test_reads_actual_registry_from_both_windows_views(self) -> None:
        values = dict(DEFAULT_VALUES)
        values[0] = "187"
        registry = FakeWinreg(
            values,
            available_view=FakeWinreg.KEY_WOW64_64KEY,
        )
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "synthetic.exe"
            executable.write_bytes(synthetic_foundation_pe())

            result = discover_registry_keymap(
                Path(directory),
                winreg_module=registry,
            )

        self.assertTrue(result.recognized)
        self.assertTrue(result.used_registry)
        self.assertFalse(result.requires_game_launch)
        self.assertEqual(result.keymap["F1"]["action"], "近战攻击")
        self.assertNotIn("F", result.keymap)
        self.assertEqual(
            registry.opened_views,
            [FakeWinreg.KEY_WOW64_32KEY, FakeWinreg.KEY_WOW64_64KEY],
        )
        self.assertEqual(registry.closed, 1)

    def test_missing_registry_uses_strict_defaults_with_notice(self) -> None:
        registry = FakeWinreg(None)
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "synthetic.exe"
            executable.write_bytes(synthetic_foundation_pe())

            result = discover_registry_keymap(
                Path(directory),
                winreg_module=registry,
            )

        self.assertTrue(result.recognized)
        self.assertFalse(result.used_registry)
        self.assertTrue(result.requires_game_launch)
        self.assertEqual(len(result.keymap), 30)
        self.assertEqual(result.keymap["Space"]["action"], "跳跃 / 次要攻击")
        self.assertEqual(result.keymap["W"]["movement_direction"], "W")
        self.assertIn("默认键位", result.diagnostic)
        self.assertIn("不是玩家自定义改键", result.diagnostic)

    def test_invalid_registry_does_not_hide_custom_state_with_defaults(self) -> None:
        registry = FakeWinreg({0: "161", 1: "not-a-binding"})
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "synthetic.exe"
            executable.write_bytes(synthetic_foundation_pe())

            result = discover_registry_keymap(
                Path(directory),
                winreg_module=registry,
            )

        self.assertTrue(result.recognized)
        self.assertFalse(result.used_registry)
        self.assertTrue(result.requires_game_launch)
        self.assertEqual(result.keymap, {})
        self.assertIn("结构不完整", result.diagnostic)

    def test_unknown_actual_binding_does_not_import_incomplete_keymap(self) -> None:
        values = dict(DEFAULT_VALUES)
        values[0] = "999"
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "synthetic.exe"
            executable.write_bytes(synthetic_foundation_pe())

            result = discover_registry_keymap(
                Path(directory),
                winreg_module=FakeWinreg(values),
            )

        self.assertTrue(result.recognized)
        self.assertFalse(result.used_registry)
        self.assertTrue(result.requires_game_launch)
        self.assertEqual(result.keymap, {})
        self.assertIn("输入码999", result.diagnostic)
        self.assertIn("未被替换", result.diagnostic)

    def test_real_registry_enumeration_error_is_not_treated_as_end(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "synthetic.exe"
            executable.write_bytes(synthetic_foundation_pe())

            result = discover_registry_keymap(
                Path(directory),
                winreg_module=FailingEnumerationWinreg(dict(DEFAULT_VALUES)),
            )

        self.assertTrue(result.recognized)
        self.assertFalse(result.used_registry)
        self.assertEqual(result.keymap, {})
        self.assertIn("当前无法读取", result.diagnostic)

    def test_scan_limits_bound_executable_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(4):
                (root / f"candidate{index}.exe").write_bytes(b"MZ" + b"\0" * 4094)

            result = discover_registry_keymap(
                root,
                winreg_module=FakeWinreg(None),
                limits=RegistryKeymapLimits(max_executables=2),
            )

        self.assertFalse(result.recognized)
        self.assertEqual(result.scanned_executables, 2)
        self.assertTrue(result.truncated)

    def test_depth_scope_is_not_reported_as_resource_exhaustion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "candidate.exe").write_bytes(b"MZ" + b"\0" * 4094)
            nested = root
            for index in range(4):
                nested /= f"engine_level_{index}"
                nested.mkdir()
            (nested / "deeper_plugins").mkdir()

            result = discover_registry_keymap(
                root,
                winreg_module=FakeWinreg(None),
                limits=RegistryKeymapLimits(max_depth=3),
            )

        self.assertFalse(result.recognized)
        self.assertEqual(result.scanned_executables, 1)
        self.assertFalse(result.truncated)


if __name__ == "__main__":
    unittest.main()
