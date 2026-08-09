from __future__ import annotations

import struct
import tempfile
import unittest
import os
import json
from pathlib import Path
from unittest.mock import patch

import screen_recorder
import unreal_gvas_keymap as gvas

from unreal_gvas_keymap import (
    GvasKeymapLimits,
    discover_gvas_player_keymap,
    parse_gvas_player_keymap,
)


def _fstring(value: str) -> bytes:
    raw = value.encode("utf-8") + b"\0"
    return struct.pack("<i", len(raw)) + raw


def _tag(
    name: str,
    type_name: str,
    payload: bytes,
    *,
    struct_name: str = "",
    enum_name: str = "",
    inner_type: str = "",
    key_type: str = "",
    value_type: str = "",
) -> bytes:
    result = _fstring(name) + _fstring(type_name)
    result += struct.pack("<II", len(payload), 0)
    if type_name == "StructProperty":
        result += _fstring(struct_name) + (b"\0" * 16)
    elif type_name == "BoolProperty":
        result += b"\0"
    elif type_name in {"ByteProperty", "EnumProperty"}:
        result += _fstring(enum_name)
    elif type_name in {"ArrayProperty", "SetProperty"}:
        result += _fstring(inner_type)
    elif type_name == "MapProperty":
        result += _fstring(key_type) + _fstring(value_type)
    result += b"\0"  # no property GUID
    return result + payload


def _stream(*tags: bytes) -> bytes:
    return b"".join(tags) + _fstring("None")


def _options_gvas(*option_fields: bytes, save_class: str = "/Script/Demo.DemoOptionSaveGame") -> bytes:
    option_payload = _stream(*option_fields)
    root = _stream(
        _tag(
            "OptionSaveData",
            "StructProperty",
            option_payload,
            struct_name="DemoOptionSaveData",
        )
    )
    header = b"GVAS" + struct.pack("<III", 3, 522, 1008)
    header += struct.pack("<HHHI", 5, 1, 1, 0)
    header += _fstring("++UE5+Release-5.1")
    header += struct.pack("<II", 3, 0)
    header += _fstring(save_class)
    return header + root + b"\0\0\0\0"


def _common_settings() -> bytes:
    common = _stream(
        _tag("bEnableMotionBlur", "BoolProperty", b""),
        _tag("ScreenPercentage", "IntProperty", struct.pack("<i", 50)),
        _tag(
            "MapObjectDrawDistanceType",
            "EnumProperty",
            _fstring("EDrawDistance::VeryLong"),
            enum_name="EDrawDistance",
        ),
    )
    return _tag(
        "CommonSettings",
        "StructProperty",
        common,
        struct_name="DemoOptionCommonSettings",
    )


def _supported_key_config() -> bytes:
    binding = _stream(
        _tag(
            "MainKey",
            "StructProperty",
            _fstring("SpaceBar"),
            struct_name="Key",
        ),
        _tag("SecondaryKey", "NameProperty", _fstring("None")),
    )
    mappings = struct.pack("<II", 0, 1) + _fstring("Jump") + binding
    key_config = _stream(
        _tag(
            "MouseAndKeyboardActionMappings",
            "MapProperty",
            mappings,
            key_type="NameProperty",
            value_type="StructProperty",
        )
    )
    return _tag(
        "KeyConfigSettings",
        "StructProperty",
        key_config,
        struct_name="DemoKeyConfigSettings",
    )


def _axis_entry(action: str, filter_member: str, key: str) -> bytes:
    return _stream(
        _tag("AxisName", "NameProperty", _fstring(action)),
        _tag(
            "FilterType",
            "EnumProperty",
            _fstring(f"DemoAxisFilterType::{filter_member}"),
            enum_name="DemoAxisFilterType",
        ),
        _tag(
            "MainKey",
            "StructProperty",
            _fstring(key),
            struct_name="Key",
        ),
        _tag(
            "SecondaryKey",
            "StructProperty",
            _fstring("None"),
            struct_name="Key",
        ),
    )


def _supported_axis_key_config(*, invalid_filter: str = "") -> bytes:
    filter_value = invalid_filter or "Plus"
    entries = (
        _axis_entry("MoveForward", filter_value, "W")
        + _axis_entry("MoveForward", "Minus", "S")
        + _axis_entry("MoveRight", "Minus", "A")
        + _axis_entry("MoveRight", "Plus", "D")
    )
    inner = _tag(
        "MouseAndKeyboardAxisMappings",
        "StructProperty",
        entries,
        struct_name="DemoAxisKeyConfigKeys",
    )
    axis_array = _tag(
        "MouseAndKeyboardAxisMappings",
        "ArrayProperty",
        struct.pack("<I", 4) + inner,
        inner_type="StructProperty",
    )
    return _tag(
        "KeyConfigSettings",
        "StructProperty",
        _stream(axis_array),
        struct_name="DemoKeyConfigSettings",
    )


class UnrealGvasKeymapTests(unittest.TestCase):
    def test_common_unreal_fkey_names_are_canonicalized(self) -> None:
        expected = {
            "Zero": ("0", "keyboard"),
            "One": ("1", "keyboard"),
            "Nine": ("9", "keyboard"),
            "NumPadOne": ("NumPad1", "keyboard"),
            "NumPadEnter": ("NumPadEnter", "keyboard"),
            "PrintScreen": ("PrintScreen", "keyboard"),
            "Apps": ("Apps", "keyboard"),
            "MouseScrollLeft": ("mouseWheelLeft", "mouse"),
            "MouseX": None,
        }
        for raw_key, canonical in expected.items():
            with self.subTest(raw_key=raw_key):
                self.assertEqual(canonical, gvas._normalize_key(raw_key))

        with self.assertRaises(ValueError):
            gvas._normalize_key("UnverifiedFutureKey")

    def test_valid_player_gvas_without_key_config_has_precise_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "UserOption.sav"
            path.write_bytes(_options_gvas(_common_settings()))

            result = parse_gvas_player_keymap(path)

        self.assertTrue(result.recognized)
        self.assertTrue(result.has_player_file)
        self.assertTrue(result.needs_key_change)
        self.assertFalse(result.has_verified_player_config)
        self.assertFalse(result.unsupported_schema)
        self.assertEqual({}, result.keymap)
        self.assertIn("已经生成玩家设置", result.diagnostic)
        self.assertIn("没有已序列化的键位段", result.diagnostic)
        self.assertIn("使用游戏内置默认键位", result.diagnostic)
        self.assertIn("单纯启动、实际游玩或正常退出", result.diagnostic)
        self.assertIn("真正改动并保存一个键位", result.diagnostic)
        self.assertNotIn("从未启动", result.diagnostic)

    def test_key_config_with_unknown_layout_is_rejected_as_a_whole(self) -> None:
        unsupported_key_config = _tag(
            "KeyConfigSettings",
            "StructProperty",
            _stream(_tag("FutureMappings", "IntProperty", struct.pack("<i", 1))),
            struct_name="DemoKeyConfigSettings",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "UserOption.sav"
            path.write_bytes(_options_gvas(_common_settings(), unsupported_key_config))

            result = parse_gvas_player_keymap(path)

        self.assertTrue(result.recognized)
        self.assertTrue(result.has_player_file)
        self.assertTrue(result.unsupported_schema)
        self.assertFalse(result.needs_key_change)
        self.assertFalse(result.has_verified_player_config)
        self.assertEqual({}, result.keymap)
        self.assertIn("拒绝整份映射", result.diagnostic)

    def test_fully_tagged_supported_key_config_is_imported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "UserOption.sav"
            path.write_bytes(
                _options_gvas(_common_settings(), _supported_key_config())
            )

            result = parse_gvas_player_keymap(path)

        self.assertTrue(result.recognized)
        self.assertTrue(result.has_verified_player_config)
        self.assertFalse(result.needs_key_change)
        self.assertFalse(result.unsupported_schema)
        self.assertEqual(
            {
                "Space": {
                    "type": "keyboard",
                    "action": "Jump",
                    "movement_direction": "",
                }
            },
            result.keymap,
        )

    def test_axis_filter_type_is_validated_and_preserves_movement_direction(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "UserOption.sav"
            path.write_bytes(
                _options_gvas(
                    _common_settings(),
                    _supported_axis_key_config(),
                )
            )

            result = parse_gvas_player_keymap(path)

        self.assertTrue(result.has_verified_player_config)
        self.assertFalse(result.unsupported_schema)
        self.assertEqual("W", result.keymap["W"]["movement_direction"])
        self.assertEqual("B", result.keymap["S"]["movement_direction"])
        self.assertEqual("L", result.keymap["A"]["movement_direction"])
        self.assertEqual("R", result.keymap["D"]["movement_direction"])

    def test_unknown_axis_filter_type_skips_only_affected_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "UserOption.sav"
            path.write_bytes(
                _options_gvas(
                    _common_settings(),
                    _supported_axis_key_config(invalid_filter="Future"),
                )
            )

            result = parse_gvas_player_keymap(path)

        self.assertTrue(result.recognized)
        self.assertFalse(result.unsupported_schema)
        self.assertTrue(result.has_verified_player_config)
        self.assertNotIn("W", result.keymap)
        self.assertEqual("B", result.keymap["S"]["movement_direction"])
        self.assertEqual("L", result.keymap["A"]["movement_direction"])
        self.assertEqual("R", result.keymap["D"]["movement_direction"])
        self.assertEqual(1, result.skipped_mappings)
        self.assertIn("1 项无法确认并已跳过", result.diagnostic)

    def test_newer_recognized_without_key_config_blocks_older_verified_save(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            newer = root / "current_UserOption.sav"
            older = root / "backup_UserOption.sav"
            newer.write_bytes(_options_gvas(_common_settings()))
            older.write_bytes(
                _options_gvas(_common_settings(), _supported_key_config())
            )
            os.utime(older, (100, 100))
            os.utime(newer, (200, 200))

            result = discover_gvas_player_keymap([newer, older])

        self.assertTrue(result.recognized)
        self.assertTrue(result.has_player_file)
        self.assertTrue(result.needs_key_change)
        self.assertFalse(result.has_verified_player_config)
        self.assertFalse(result.unsupported_schema)
        self.assertEqual({}, result.keymap)
        self.assertEqual(newer, result.source_file)
        self.assertEqual(1, result.scanned_files)

    def test_newer_unsupported_key_config_blocks_older_verified_save(self) -> None:
        unsupported_key_config = _tag(
            "KeyConfigSettings",
            "StructProperty",
            _stream(_tag("FutureMappings", "IntProperty", struct.pack("<i", 1))),
            struct_name="DemoKeyConfigSettings",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            newer = root / "current_UserOption.sav"
            older = root / "backup_UserOption.sav"
            newer.write_bytes(
                _options_gvas(_common_settings(), unsupported_key_config)
            )
            older.write_bytes(
                _options_gvas(_common_settings(), _supported_key_config())
            )
            os.utime(older, (100, 100))
            os.utime(newer, (200, 200))

            result = discover_gvas_player_keymap([newer, older])

        self.assertTrue(result.recognized)
        self.assertTrue(result.has_player_file)
        self.assertFalse(result.needs_key_change)
        self.assertFalse(result.has_verified_player_config)
        self.assertTrue(result.unsupported_schema)
        self.assertTrue(result.has_key_config)
        self.assertEqual({}, result.keymap)
        self.assertEqual(newer, result.source_file)
        self.assertEqual(1, result.scanned_files)

    def test_non_option_and_truncated_files_are_not_player_configs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            world = root / "world.sav"
            world.write_bytes(_options_gvas(_common_settings(), save_class="/Script/Demo.WorldSaveGame"))
            broken = root / "broken.sav"
            broken.write_bytes(_options_gvas(_common_settings())[:-20])

            world_result = parse_gvas_player_keymap(world)
            broken_result = parse_gvas_player_keymap(broken)

        self.assertFalse(world_result.recognized)
        self.assertFalse(world_result.has_player_file)
        self.assertFalse(broken_result.recognized)
        self.assertFalse(broken_result.has_player_file)

    def test_discovery_skips_nonmatching_and_corrupt_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unrelated = root / "newer_world.sav"
            broken = root / "broken_options.sav"
            current = root / "current_UserOption.sav"
            unrelated.write_bytes(
                _options_gvas(
                    _common_settings(),
                    save_class="/Script/Demo.WorldSaveGame",
                )
            )
            broken.write_bytes(_options_gvas(_common_settings())[:-20])
            current.write_bytes(
                _options_gvas(_common_settings(), _supported_key_config())
            )

            result = discover_gvas_player_keymap(
                [unrelated, broken, current]
            )

        self.assertTrue(result.recognized)
        self.assertTrue(result.has_verified_player_config)
        self.assertEqual(current, result.source_file)
        self.assertEqual(3, result.scanned_files)

    def test_discovery_uses_fixed_unreal_location_without_recursive_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)
            player = local / "Project" / "Saved" / "SaveGames" / "UserOption.sav"
            player.parent.mkdir(parents=True)
            player.write_bytes(_options_gvas(_common_settings()))

            result = discover_gvas_player_keymap(
                [local], limits=GvasKeymapLimits(max_directory_entries=8)
            )

        self.assertTrue(result.recognized)
        self.assertEqual(player, result.source_file)
        # The first recognized current player file decides discovery state.
        self.assertEqual(1, result.scanned_files)
        self.assertFalse(result.truncated)

    def test_recorder_reports_existing_option_save_without_key_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "Installed Game"
            executable = (
                game
                / "SyntheticProject"
                / "Binaries"
                / "Win64"
                / "SyntheticProject-Win64-Shipping.exe"
            )
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"")
            local_appdata = root / "LocalAppData"
            player = (
                local_appdata
                / "SyntheticProject"
                / "Saved"
                / "SaveGames"
                / "UserOption.sav"
            )
            player.parent.mkdir(parents=True)
            player.write_bytes(_options_gvas(_common_settings()))

            with patch.dict(os.environ, {"LOCALAPPDATA": str(local_appdata)}):
                result = screen_recorder.discover_keymap_from_game_directory(game)

        self.assertEqual({}, result.keymap)
        self.assertFalse(result.recognized_config)
        self.assertTrue(result.has_recognized_player_file)
        self.assertFalse(result.has_verified_player_config)
        self.assertFalse(result.requires_game_launch)
        self.assertFalse(result.truncated)
        self.assertEqual((player.resolve(),), result.source_files)
        # One GVAS player file plus the bounded packaged-default probe.
        self.assertEqual(2, result.scanned_files)
        self.assertIn("成功解析玩家配置文件", result.notice)
        self.assertIn("已经生成玩家设置", result.notice)
        self.assertIn("使用游戏内置默认键位", result.notice)
        self.assertIn("真正改动并保存一个键位", result.notice)
        self.assertNotIn("尚未首次启动", result.notice)

    def test_real_input_ini_outranks_option_save_without_key_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "Installed Game"
            executable = (
                game
                / "SyntheticProject"
                / "Binaries"
                / "Win64"
                / "SyntheticProject-Win64-Shipping.exe"
            )
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"")
            local_appdata = root / "LocalAppData"
            saved = local_appdata / "SyntheticProject" / "Saved"
            player = saved / "SaveGames" / "UserOption.sav"
            player.parent.mkdir(parents=True)
            player.write_bytes(_options_gvas(_common_settings()))
            player_input = saved / "Config" / "Windows" / "Input.ini"
            player_input.parent.mkdir(parents=True)
            player_input.write_text(
                '+ActionMappings=(ActionName="Jump",Key=J)',
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"LOCALAPPDATA": str(local_appdata)}):
                result = screen_recorder.discover_keymap_from_game_directory(game)

        self.assertFalse(result.recognized_config)
        self.assertFalse(result.has_verified_player_config)
        self.assertTrue(result.has_recognized_player_file)
        self.assertEqual("Jump", result.keymap["J"]["action"])
        self.assertEqual(
            (player.resolve(), player_input.resolve()),
            result.source_files,
        )
        self.assertEqual("mixed_unverified", result.binding_authority)
        self.assertEqual("merge", result.apply_mode)
        self.assertIn("增量操作", result.notice)

    def test_verified_gvas_outranks_authoritative_install_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "Installed Game"
            executable = (
                game
                / "SyntheticProject"
                / "Binaries"
                / "Win64"
                / "SyntheticProject-Win64-Shipping.exe"
            )
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"")
            (game / "settings.save").write_text(
                json.dumps(
                    {
                        "keyboard_mapping": {
                            "InstallJump": "J",
                            "InstallUse": "E",
                            "InstallPause": "Escape",
                        }
                    }
                ),
                encoding="utf-8",
            )
            local_appdata = root / "LocalAppData"
            player = (
                local_appdata
                / "SyntheticProject"
                / "Saved"
                / "SaveGames"
                / "UserOption.sav"
            )
            player.parent.mkdir(parents=True)
            player.write_bytes(
                _options_gvas(_common_settings(), _supported_key_config())
            )

            with patch.dict(os.environ, {"LOCALAPPDATA": str(local_appdata)}):
                result = screen_recorder.discover_keymap_from_game_directory(game)

        self.assertTrue(result.recognized_config)
        self.assertTrue(result.has_verified_player_config)
        self.assertEqual((player.resolve(),), result.source_files)
        self.assertEqual("Jump", result.keymap["Space"]["action"])
        self.assertNotIn("J", result.keymap)


if __name__ == "__main__":
    unittest.main()
