from __future__ import annotations

import unittest
import ctypes
import base64
import json
import os
import struct
import tempfile
import zipfile
import zlib
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
import zstandard
import screen_recorder as recorder
from foundation_registry_keymap import RegistryKeymapResult
from fromsoftware_numeric_ini_keymap import NumericIniDiscoveryResult
from player_config_paths import PlayerConfigDiscovery, PlayerConfigRoot

from screen_recorder import (
    InputEventTracker,
    KeymapDiscovery,
    QualityMonitor,
    SessionConfig,
    XInputState,
    capture_target_for_point,
    discover_indexed_xml_keymap,
    discover_pixpil_keymap,
    discover_klei_keymap,
    discover_keymap_from_game_directory,
    encoder_queue_capacity,
    minimum_required_bitrate,
    _decode_pixpil_safe_settings,
    _apply_keymap_discovery,
    _extract_pixpil_garchive_entry,
    _indexed_action_labels_from_dictionary,
    _parse_manual_control_text,
    _parse_indexed_xml_keymap,
    _parse_unity_serialized_input_manager,
    _parse_unity_serialized_input_actions,
    _parse_dotnet_input_xml,
    _read_pixpil_garchive,
    _steam_last_owner,
    _steam_userdata_account_id,
    parse_startup_settings,
    recover_interrupted_sessions,
    target_bitrate,
)


def tracker() -> InputEventTracker:
    return InputEventTracker(
        SessionConfig("测试游戏", {}),
        "session",
        datetime.now().astimezone(),
        60,
        0,
        0,
        1920,
        1080,
        100.0,
        "test",
        "monitor",
    )


def _uleb128(value: int) -> bytes:
    encoded = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            encoded.append(byte | 0x80)
        else:
            encoded.append(byte)
            return bytes(encoded)


def _luajit_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return _uleb128(len(encoded) + 5) + encoded


def _luajit_table(pairs: list[tuple[str, str]]) -> bytes:
    payload = bytearray(_uleb128(0))
    payload.extend(_uleb128(len(pairs)))
    for action, raw_input in pairs:
        payload.extend(_luajit_string(action))
        payload.extend(_luajit_string(raw_input))
    return _uleb128(1) + bytes(payload)


def _synthetic_pixpil_luajit(
    farm_tables: list[list[tuple[str, str]]] | None = None,
    *,
    mapping_configs: list[
        tuple[str, dict[str, list[tuple[str, str]]]]
    ]
    | None = None,
) -> bytes:
    if mapping_configs is None:
        farm_keyboard = (
            farm_tables[0]
            if farm_tables
            else [
                ("farm_interaction", "f1"),
                ("farm_run", "f2"),
                ("farm_menu", "f3"),
            ]
        )
        mapping_configs = [
            (
                "defaultInputMappingConfig",
                {
                    "keyboard": [
                        ("move_up", "w"),
                        ("move_down", "s"),
                        ("move_left", "a"),
                        ("move_right", "d"),
                        ("interaction", "space"),
                        ("running", "lshift"),
                        ("menu_map", "tab"),
                    ],
                    "mouse": [
                        ("fire_A", "left"),
                        ("fire_B", "right"),
                        ("toggle_isolated", "middle"),
                    ],
                    "joystick": [
                        ("confirm", "a"),
                        ("pause", "start"),
                        ("menu_back", "back"),
                    ],
                },
            ),
            (
                "farmDefaultInputMappingConfig",
                {"keyboard": farm_keyboard},
            ),
        ]

    constants: list[bytes] = []
    config_specs: list[tuple[list[tuple[int, int]], int, int]] = []
    for config_name, device_tables in mapping_configs:
        device_specs: list[tuple[int, int]] = []
        for device_name, pairs in device_tables.items():
            table_index = len(constants)
            constants.append(_luajit_table(pairs))
            device_index = len(constants)
            constants.append(_luajit_string(device_name))
            device_specs.append((table_index, device_index))
        mappings_index = len(constants)
        constants.append(_luajit_string("mappings"))
        config_name_index = len(constants)
        constants.append(_luajit_string(config_name))
        config_specs.append((device_specs, mappings_index, config_name_index))

    def raw_constant_index(logical_index: int) -> int:
        return len(constants) - logical_index - 1

    def instruction_ad(opcode: int, register: int, operand: int = 0) -> bytes:
        return bytes((opcode, register, operand & 0xFF, (operand >> 8) & 0xFF))

    def instruction_abc(
        opcode: int,
        value_register: int,
        target_register: int,
        constant_index: int,
    ) -> bytes:
        return bytes(
            (
                opcode,
                value_register,
                raw_constant_index(constant_index),
                target_register,
            )
        )

    instructions: list[bytes] = []
    for device_specs, mappings_index, config_name_index in config_specs:
        instructions.extend(
            (instruction_ad(0x34, 1), instruction_ad(0x34, 2))
        )
        for table_index, device_index in device_specs:
            instructions.append(
                instruction_ad(0x35, 3, raw_constant_index(table_index))
            )
            instructions.append(instruction_abc(0x3D, 3, 2, device_index))
        instructions.append(instruction_abc(0x3D, 2, 1, mappings_index))
        instructions.append(instruction_abc(0x3D, 1, 0, config_name_index))

    child_prototype = bytearray((0, 1, 4, 0))
    child_prototype.extend(_uleb128(len(constants)))
    child_prototype.extend(_uleb128(0))
    child_prototype.extend(_uleb128(len(instructions)))
    for instruction in instructions:
        child_prototype.extend(instruction)
    for constant in constants:
        child_prototype.extend(constant)

    root_constants = [
        _uleb128(0),  # KGC_CHILD: consumes the child prototype stack entry.
        _luajit_string("SyntheticGlobalManager"),
        _luajit_string("initBindings"),
    ]

    def root_constant_index(logical_index: int) -> int:
        return len(root_constants) - logical_index - 1

    root_instructions = [
        instruction_ad(0x36, 0, root_constant_index(1)),  # GGET manager table.
        instruction_ad(0x33, 1, root_constant_index(0)),  # FNEW child method.
        bytes((0x3D, 1, root_constant_index(2), 0)),  # manager.initBindings = fn.
    ]
    root_prototype = bytearray((0, 0, 2, 0))
    root_prototype.extend(_uleb128(len(root_constants)))
    root_prototype.extend(_uleb128(0))
    root_prototype.extend(_uleb128(len(root_instructions)))
    for instruction in root_instructions:
        root_prototype.extend(instruction)
    for constant in root_constants:
        root_prototype.extend(constant)

    return (
        b"\x1bLJ\x02"
        + _uleb128(2)
        + _uleb128(len(child_prototype))
        + bytes(child_prototype)
        + _uleb128(len(root_prototype))
        + bytes(root_prototype)
        + b"\0"
    )


def _pixpil_garchive(entries: dict[str, bytes]) -> bytes:
    compressed = [
        (name, payload, zstandard.ZstdCompressor().compress(payload))
        for name, payload in entries.items()
    ]
    header_size = 8 + sum(len(name.encode("utf-8")) + 1 + 16 for name in entries)
    header = bytearray(struct.pack("<II", 0x6A37, len(entries)))
    data = bytearray()
    offset = header_size
    for name, payload, stored in compressed:
        header.extend(name.encode("utf-8") + b"\0")
        header.extend(struct.pack("<IIII", offset, 2, len(payload), len(stored)))
        data.extend(stored)
        offset += len(stored)
    return bytes(header + data)


def _write_synthetic_pixpil_game(
    root: Path,
    *,
    farm_tables: list[list[tuple[str, str]]] | None = None,
) -> tuple[Path, Path, Path, Path]:
    game = root / "SyntheticPixpilGame"
    content = game / "content"
    archives = content / "game"
    archives.mkdir(parents=True)
    manifest = content / "packages.json"
    manifest.write_text(
        json.dumps(
            {
                "packages": {"script": {"mode": "packed"}},
                "project_info": {
                    "author": "SyntheticPixpil",
                    "name": game.name,
                },
            }
        ),
        encoding="utf-8",
    )
    script_name = "0123456789abcdef0123456789abcdef"
    config = archives / "config.g"
    config.write_bytes(
        _pixpil_garchive(
            {
                "script_library": json.dumps(
                    {
                        "export": {
                            "script.SyntheticGlobalManager": f"script/{script_name}"
                        }
                    }
                ).encode("utf-8")
            }
        )
    )
    script = archives / "script.g"
    script.write_bytes(
        _pixpil_garchive(
            {script_name: _synthetic_pixpil_luajit(farm_tables=farm_tables)}
        )
    )
    return game, manifest, config, script


def _pixpil_safe_settings(document: object) -> bytes:
    raw = json.dumps(document).encode("utf-8")
    compressor = zlib.compressobj(level=9, wbits=-zlib.MAX_WBITS)
    return bytes(range(64)) + compressor.compress(raw) + compressor.flush()


_INDEXED_MENU_KEY = bytes.fromhex(
    "68 20 78 c3 aa 5d 29 d7 bb 81 55 49 f3 e2 a8 d0"
)
_INDEXED_ACTIONS = {
    1: (200, "Go up"),
    2: (201, "Go down"),
    3: (202, "Go right"),
    4: (203, "Go left"),
    5: (204, "Jump"),
    6: (205, "Target"),
    7: (206, "Attack!"),
    8: (207, "Action"),
    9: (208, "Map"),
    10: (209, "Inventory"),
    11: (210, "Spells"),
    12: (211, "Recipes"),
    13: (212, "Character"),
    14: (213, "Quests"),
    15: (214, "Next Q."),
    16: (215, "Pause"),
    17: (216, "Chat"),
    18: (217, "Architecture"),
    19: (218, "Zoom"),
    20: (219, "-"),
    21: (260, "Menu"),
    22: (261, "Target Friend"),
    23: (262, "Lobby"),
}


def _encoded_indexed_dictionary(entries: list[str]) -> bytes:
    payload = bytearray(struct.pack("<I", len(entries)))
    for entry in entries:
        raw = entry.encode("utf-16-le")
        encoded = bytes(
            value ^ _INDEXED_MENU_KEY[offset % len(_INDEXED_MENU_KEY)]
            for offset, value in enumerate(raw)
        )
        payload.extend(struct.pack("<I", len(raw) // 2))
        payload.extend(encoded)
    payload.extend(b"\0\0\0\0")
    return bytes(payload)


def _indexed_menu_dictionary() -> bytes:
    entries = ["-"] * 263
    entries[180] = "Controls"
    for _slot, (index, action) in _INDEXED_ACTIONS.items():
        entries[index] = action
    return _encoded_indexed_dictionary(entries)


def _localized_indexed_menu_dictionary() -> bytes:
    translations = {
        1: "往上",
        2: "往下",
        3: "往右",
        4: "往左",
        5: "跳",
        6: "选择目标",
        7: "攻击！",
        8: "行动",
        9: "地图",
        10: "背包",
        11: "法术",
        12: "配方",
        13: "角色",
        14: "任务",
        15: "下个任务",
        16: "暂停",
        17: "聊天",
        18: "建筑术",
        # Deliberately retain the four English labels found in a real
        # simplified-Chinese resource; the exact common-label fallback fills them.
        19: "Zoom",
        20: "-",
        21: "Menu",
        22: "Target Friend",
        23: "Lobby",
    }
    entries = ["-"] * 263
    entries[180] = "控制"
    for slot, (index, _english_action) in _INDEXED_ACTIONS.items():
        entries[index] = translations[slot]
    return _encoded_indexed_dictionary(entries)


def _indexed_dialog_dictionary() -> bytes:
    return _encoded_indexed_dictionary(
        [
            (
                "<NONE><END>Pressing F2 allows you to see all of the weapons "
                "you have and equip them.\\nPress F1 to return to the "
                "shortcut panel."
            ),
            (
                "Pressing F3 allows you to see and use all of the potions "
                "you have.\\nPress F1 to return to the shortcut panel."
            ),
            (
                "Pressing F4 allows you to see and use all of the scrolls "
                "you have.\\nPress F1 to return to the shortcut panel."
            ),
            (
                "Pressing F5 allows you to see all known spells.\\n"
                "Press F1 to return to the shortcut panel."
            ),
        ]
    )


def _localized_indexed_dialog_dictionary() -> bytes:
    return _encoded_indexed_dictionary(
        [
            "按[F2]键查看和装备所有的武器。\\n按[F1]键回到快捷键面板。",
            "按[F3]键查看和使用所有的药剂。\\n按[F1]键回到快捷键面板。",
            "按[F4]键查看和使用所有的卷轴。\\n按[F1]键回到快捷键面板。",
            "按[F5]键查看和使用所有的法术。\\n按[F1]键回到快捷键面板。",
            # This extra localized-only shortcut must not be added unless the
            # structurally verified reference dictionary also confirms it.
            "按[F9]键在两个角色间切换。",
        ]
    )


def _indexed_xml_document(
    keycodes: dict[int, int] | None = None,
    *,
    slots: int = 23,
) -> str:
    values = {slot: 0 for slot in range(1, slots + 1)}
    values.update(keycodes or {})
    lines = ['<?xml version="1.0" ?>', "<config>"]
    lines.extend(
        f"  <tecla_{slot}>{values[slot]}</tecla_{slot}>"
        for slot in range(1, slots + 1)
    )
    lines.extend(
        f"  <pad_{slot}>0</pad_{slot}>"
        for slot in range(1, slots + 1)
    )
    lines.extend(
        f"  <padMap_{slot}>1</padMap_{slot}>"
        for slot in range(1, max(32, slots) + 1)
    )
    lines.append("</config>")
    return "\n".join(lines)


def _write_indexed_config(path: Path, keycodes: dict[int, int] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_indexed_xml_document(keycodes), encoding="utf-8")
    return path


def _write_steam_manifest(
    steamapps: Path,
    game_name: str,
    *,
    app_id: int = 4242,
    last_owner: str | None = "76561197960265839",
    launcher_path: Path | None = None,
) -> Path:
    steamapps.mkdir(parents=True, exist_ok=True)
    path = steamapps / f"appmanifest_{app_id}.acf"
    lines = [
        '"AppState"',
        "{",
        f'    "appid"       "{app_id}"',
        f'    "installdir"  "{game_name}"',
    ]
    if launcher_path is not None:
        escaped_launcher = str(launcher_path).replace("\\", "\\\\")
        lines.append(f'    "LauncherPath" "{escaped_launcher}"')
    if last_owner is not None:
        lines.append(f'    "LastOwner"   "{last_owner}"')
    lines.append("}")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


class RecorderLogicTests(unittest.TestCase):
    def test_encoder_queue_capacity_is_bounded_by_frames_and_memory(self) -> None:
        self.assertEqual(encoder_queue_capacity(1920, 1080), 8)
        self.assertEqual(encoder_queue_capacity(2560, 1440), 8)
        self.assertEqual(encoder_queue_capacity(3840, 2160), 5)
        self.assertEqual(encoder_queue_capacity(7680, 4320), 1)

    def test_hardware_encoder_probe_uses_supported_resolution_and_options(
        self,
    ) -> None:
        commands: list[list[str]] = []

        def successful_probe(command: list[str], **_kwargs: object) -> object:
            commands.append(command)
            return recorder.subprocess.CompletedProcess(command, 0)

        previous_choice = recorder._ENCODER_CHOICE
        recorder._ENCODER_CHOICE = None
        try:
            with (
                patch.object(
                    recorder,
                    "media_tools",
                    return_value=("ffmpeg.exe", "ffprobe.exe"),
                ),
                patch.object(
                    recorder.subprocess,
                    "run",
                    side_effect=successful_probe,
                ) as run,
            ):
                selected = recorder.select_h264_encoder()
        finally:
            recorder._ENCODER_CHOICE = previous_choice

        self.assertEqual(selected, "h264_nvenc")
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertIn("color=size=1280x720:rate=30:duration=0.1", command)
        self.assertIn("yuv420p", command)
        self.assertIn("p6", command)
        self.assertIn("fullres", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 15)

    def test_segment_encoder_forces_exact_frame_boundary_idr(self) -> None:
        previous_choice = recorder._ENCODER_CHOICE
        recorder._ENCODER_CHOICE = "h264_nvenc"
        try:
            with (
                patch.object(
                    recorder,
                    "media_tools",
                    return_value=("ffmpeg.exe", "ffprobe.exe"),
                ),
                patch.object(recorder.subprocess, "Popen") as popen,
            ):
                encoder = recorder.H264Encoder(
                    Path("slice_%03d.mp4"),
                    1920,
                    1080,
                    1920,
                    1080,
                    60,
                )
                encoder.start()
        finally:
            recorder._ENCODER_CHOICE = previous_choice

        command = popen.call_args.args[0]
        force_index = command.index("-force_key_frames")
        self.assertEqual(
            command[force_index + 1],
            "expr:gte(n,n_forced*1800)",
        )
        keyint_index = command.index("-keyint_min")
        self.assertEqual(command[keyint_index + 1], "1")
        self.assertIn("-forced-idr", command)
        self.assertIn("-no-scenecut", command)
        self.assertIn("-strict_gop", command)

    def test_video_verification_reconciles_encoded_and_decoded_frames(
        self,
    ) -> None:
        metadata = {
            "streams": [
                {
                    "codec_name": "h264",
                    "profile": "High",
                    "width": 1920,
                    "height": 1080,
                    "pix_fmt": "yuv420p",
                    "r_frame_rate": "30/1",
                    "avg_frame_rate": "30/1",
                    "bit_rate": "24000000",
                    "duration": "60.000000",
                    "nb_frames": "1800",
                }
            ],
            "format": {"duration": "60.000000", "bit_rate": "24000000"},
        }
        probe = recorder.subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(metadata).encode("utf-8"),
            stderr=b"",
        )
        decode = recorder.subprocess.CompletedProcess(
            [],
            0,
            stdout=b"frame=1\nprogress=continue\nframe=1800\nprogress=end\n",
            stderr=b"",
        )
        with (
            patch.object(
                recorder,
                "media_tools",
                return_value=("ffmpeg.exe", "ffprobe.exe"),
            ),
            patch.object(
                recorder.subprocess,
                "run",
                side_effect=[probe, decode],
            ),
        ):
            report = recorder.verify_encoded_video(
                Path("slice.mp4"),
                60,
                1920,
                1080,
            )

        self.assertEqual(report["encoded_frame_count"], 1800)
        self.assertEqual(report["decoded_frame_count"], 1800)

    def test_video_verification_rejects_extra_encoded_frames(self) -> None:
        metadata = {
            "streams": [
                {
                    "codec_name": "h264",
                    "profile": "High",
                    "width": 1920,
                    "height": 1080,
                    "pix_fmt": "yuv420p",
                    "r_frame_rate": "30/1",
                    "avg_frame_rate": "30/1",
                    "bit_rate": "24000000",
                    "duration": "61.966667",
                    "nb_frames": "1859",
                }
            ],
            "format": {"duration": "61.966667", "bit_rate": "24000000"},
        }
        probe = recorder.subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(metadata).encode("utf-8"),
            stderr=b"",
        )
        with (
            patch.object(
                recorder,
                "media_tools",
                return_value=("ffmpeg.exe", "ffprobe.exe"),
            ),
            patch.object(
                recorder.subprocess,
                "run",
                return_value=probe,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "编码帧数为 1859，预期 1800 帧",
            ):
                recorder.verify_encoded_video(
                    Path("slice.mp4"),
                    60,
                    1920,
                    1080,
                )

    def test_action_semantic_chinese_issues_only_checks_action_column(self) -> None:
        issues = recorder._action_semantic_chinese_issues(
            {
                "W": {
                    "type": "keyboard",
                    "action": "前进",
                    "movement_direction": "W",
                },
                "F1": {"type": "keyboard", "action": "返回快捷面板"},
                "Space": {"type": "keyboard", "action": "技能栏1"},
            }
        )

        self.assertEqual(issues, [])

    def test_action_semantic_chinese_issues_rejects_latin_and_non_chinese(self) -> None:
        issues = recorder._action_semantic_chinese_issues(
            {
                "F1": {"type": "keyboard", "action": "Open Menu"},
                "F2": {"type": "keyboard", "action": "打开UI面板"},
                "F3": {"type": "keyboard", "action": "Ｆｉｒｅ"},
                "F4": {"type": "keyboard", "action": "123 / ?"},
            }
        )

        self.assertEqual(
            issues,
            [
                ("F1", "Open Menu", "含英文字母"),
                ("F2", "打开UI面板", "含英文字母"),
                ("F3", "Ｆｉｒｅ", "含英文字母"),
                ("F4", "123 / ?", "未包含中文"),
            ],
        )

    def test_canonical_input_name_accepts_modifier_mouse_chords(self) -> None:
        self.assertEqual(
            recorder._canonical_input_name("Shift+LeftMouseButton"),
            ("Shift+leftClick", "mouse"),
        )
        self.assertEqual(
            recorder._canonical_input_name("mouseWheelUp+Alt+Ctrl"),
            ("Ctrl+Alt+mouseWheelUp", "mouse"),
        )
        self.assertEqual(
            recorder._canonical_input_name("Shift+K"),
            ("Shift+K", "keyboard"),
        )
        self.assertIsNone(recorder._canonical_input_name("leftClick+rightClick"))

    def test_session_config_validation_blocks_english_action_semantics(self) -> None:
        class EntryStub:
            def get(self) -> str:
                return "测试游戏"

        class TreeStub:
            def __init__(self) -> None:
                self.selected: str | None = None
                self.focused: str | None = None
                self.visible: str | None = None

            def exists(self, item: str) -> bool:
                return item == "F1"

            def selection_set(self, item: str) -> None:
                self.selected = item

            def focus(self, item: str) -> None:
                self.focused = item

            def see(self, item: str) -> None:
                self.visible = item

        dialog = recorder.SessionConfigDialog.__new__(recorder.SessionConfigDialog)
        dialog.game_title_entry = EntryStub()
        dialog.keymap_tree = TreeStub()
        dialog.result = None
        dialog._keymap_document = lambda: {
            "F1": {"type": "keyboard", "action": "Open Menu"}
        }

        with patch.object(recorder.messagebox, "showerror") as showerror:
            accepted = dialog.validate()

        self.assertFalse(accepted)
        self.assertIsNone(dialog.result)
        self.assertEqual(dialog.keymap_tree.selected, "F1")
        self.assertEqual(dialog.keymap_tree.focused, "F1")
        self.assertEqual(dialog.keymap_tree.visible, "F1")
        showerror.assert_called_once()
        self.assertEqual(showerror.call_args.args[0], "动作语义必须使用中文")

    def test_session_config_validation_focuses_original_lowercase_input(self) -> None:
        class EntryStub:
            def get(self) -> str:
                return "测试游戏"

        class TreeStub:
            def __init__(self) -> None:
                self.selected: str | None = None

            def exists(self, item: str) -> bool:
                return item == "w"

            def selection_set(self, item: str) -> None:
                self.selected = item

            def focus(self, _item: str) -> None:
                return

            def see(self, _item: str) -> None:
                return

        dialog = recorder.SessionConfigDialog.__new__(recorder.SessionConfigDialog)
        dialog.game_title_entry = EntryStub()
        dialog.keymap_tree = TreeStub()
        dialog.result = None
        dialog._keymap_document = lambda: {
            "w": {"type": "keyboard", "action": "Move forward"}
        }

        with patch.object(recorder.messagebox, "showerror"):
            accepted = dialog.validate()

        self.assertFalse(accepted)
        self.assertEqual(dialog.keymap_tree.selected, "w")

    def test_session_config_validation_accepts_chinese_semantics_with_latin_keys(
        self,
    ) -> None:
        class EntryStub:
            def get(self) -> str:
                return "English Game Title"

        dialog = recorder.SessionConfigDialog.__new__(recorder.SessionConfigDialog)
        dialog.game_title_entry = EntryStub()
        dialog.result = None
        dialog._keymap_document = lambda: {
            "Ctrl+F1": {
                "type": "keyboard",
                "action": "打开快捷面板",
                "movement_direction": "W",
            }
        }

        with patch.object(recorder.messagebox, "showerror") as showerror:
            accepted = dialog.validate()

        self.assertTrue(accepted)
        self.assertEqual(
            dialog.result,
            SessionConfig(
                "English Game Title",
                {
                    "Ctrl+F1": {
                        "type": "keyboard",
                        "action": "打开快捷面板",
                        "movement_direction": "W",
                    }
                },
            ),
        )
        showerror.assert_not_called()

    def test_parses_dotnet_monogame_input_xml(self) -> None:
        document = """<Options>
  <moveUpButton><InputButton><key>W</key></InputButton></moveUpButton>
  <actionButton>
    <InputButton><key>X</key><mouseLeft>false</mouseLeft></InputButton>
    <InputButton><key>None</key><mouseLeft>true</mouseLeft></InputButton>
  </actionButton>
  <useToolButton><InputButton><key>C</key><mouseRight>true</mouseRight></InputButton></useToolButton>
</Options>"""

        result = _parse_dotnet_input_xml(document)
        self.assertEqual(result["W"]["movement_direction"], "W")
        self.assertEqual(result["X"]["action"], "actionButton")
        self.assertEqual(result["leftClick"]["action"], "actionButton")
        self.assertEqual(result["rightClick"]["action"], "useToolButton")

    def test_parses_indexed_xml_by_structure_and_windows_vk(self) -> None:
        labels = _indexed_action_labels_from_dictionary(_indexed_menu_dictionary())
        parsed = _parse_indexed_xml_keymap(
            _indexed_xml_document(
                {
                    1: 38,
                    2: 40,
                    3: 39,
                    4: 37,
                    5: 38,
                    6: 16,
                    7: 17,
                    8: 18,
                    9: 186,
                    10: 219,
                    11: 220,
                    12: 221,
                    13: 122,
                    14: 123,
                }
            ),
            labels,
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.slot_count, 23)
        self.assertEqual(parsed.keymap["Up"]["action"], "Go up / Jump")
        self.assertEqual(parsed.keymap["Up"]["movement_direction"], "W")
        self.assertEqual(parsed.keymap["Down"]["movement_direction"], "B")
        self.assertEqual(parsed.keymap["Right"]["movement_direction"], "R")
        self.assertEqual(parsed.keymap["Left"]["movement_direction"], "L")
        self.assertEqual(parsed.keymap["Shift"]["action"], "Target")
        self.assertEqual(parsed.keymap["Ctrl"]["action"], "Attack!")
        self.assertEqual(parsed.keymap["Alt"]["action"], "Action")
        self.assertEqual(parsed.keymap[";"]["action"], "Map")
        self.assertEqual(parsed.keymap["["]["action"], "Inventory")
        self.assertEqual(parsed.keymap["\\"]["action"], "Spells")
        self.assertEqual(parsed.keymap["]"]["action"], "Recipes")
        self.assertEqual(parsed.keymap["F11"]["action"], "Character")
        self.assertEqual(parsed.keymap["F12"]["action"], "Quests")

    def test_indexed_xml_rejects_weak_or_unsafe_signatures(self) -> None:
        document = _indexed_xml_document({1: 38})
        fallback = _parse_indexed_xml_keymap(document)
        self.assertIsNotNone(fallback)
        assert fallback is not None
        self.assertEqual(
            fallback.keymap["Up"]["action"],
            "game_action_1",
        )
        self.assertNotIn("movement_direction", fallback.keymap["Up"])

        missing_slot = document.replace(
            "<tecla_4>0</tecla_4>",
            "<unrelated>0</unrelated>",
        )
        self.assertIsNone(_parse_indexed_xml_keymap(missing_slot))
        self.assertIsNone(
            _parse_indexed_xml_keymap(
                '<!DOCTYPE config [<!ENTITY x "38">]>' + document
            )
        )
        self.assertIsNone(
            _parse_indexed_xml_keymap(
                document.replace("<tecla_23>0", "<tecla_65>0")
            )
        )
        displaced_pad_maps = document
        for source, target in zip(range(1, 9), range(57, 65)):
            displaced_pad_maps = displaced_pad_maps.replace(
                f"padMap_{source}",
                f"padMap_{target}",
            )
        self.assertIsNone(_parse_indexed_xml_keymap(displaced_pad_maps))

    def test_indexed_menu_dictionary_uses_non_contiguous_final_actions(self) -> None:
        dictionary = _indexed_menu_dictionary()
        labels = _indexed_action_labels_from_dictionary(dictionary)
        self.assertEqual(labels[1], "Go up")
        self.assertEqual(labels[19], "Zoom")
        self.assertNotIn(20, labels)
        self.assertEqual(labels[21], "Menu")
        self.assertEqual(labels[22], "Target Friend")
        self.assertEqual(labels[23], "Lobby")
        with self.assertRaises(ValueError):
            _indexed_action_labels_from_dictionary(dictionary + b"trailing")

    def test_indexed_menu_dictionary_uses_official_chinese_sibling(self) -> None:
        labels = _indexed_action_labels_from_dictionary(
            _indexed_menu_dictionary(),
            _localized_indexed_menu_dictionary(),
        )

        self.assertEqual(labels[1], "往上")
        self.assertEqual(labels[19], "缩放")
        self.assertEqual(labels[21], "菜单")
        self.assertEqual(labels[22], "选择友方目标")
        self.assertEqual(labels[23], "大厅")
        self.assertEqual(
            recorder._action_semantic_chinese_issues(
                {
                    str(slot): {"type": "keyboard", "action": action}
                    for slot, action in labels.items()
                }
            ),
            [],
        )

    def test_indexed_localization_allows_different_total_entry_counts(self) -> None:
        reference_entries = ["-"] * 270
        reference_entries[180] = "Controls"
        for _slot, (index, action) in _INDEXED_ACTIONS.items():
            reference_entries[index] = action

        labels = _indexed_action_labels_from_dictionary(
            _encoded_indexed_dictionary(reference_entries),
            _localized_indexed_menu_dictionary(),
        )

        self.assertEqual(labels[1], "往上")
        self.assertEqual(labels[23], "大厅")

    def test_indexed_dialog_dictionary_adds_strict_fixed_function_keys(self) -> None:
        keymap = recorder._indexed_function_key_labels_from_dictionary(
            _indexed_dialog_dictionary()
        )
        self.assertEqual(keymap["F1"]["action"], "Return to the shortcut panel")
        self.assertIn("weapons", keymap["F2"]["action"].casefold())
        self.assertIn("potions", keymap["F3"]["action"].casefold())
        self.assertIn("scrolls", keymap["F4"]["action"].casefold())
        self.assertIn("spells", keymap["F5"]["action"].casefold())

        weak = _encoded_indexed_dictionary(
            ["Pressing F2 allows you to open weapons. Press F1 to return."]
        )
        self.assertEqual(
            recorder._indexed_function_key_labels_from_dictionary(weak),
            {},
        )

    def test_discovers_indexed_xml_without_game_name_hardcoding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory) / "Arbitrary Native Game"
            config = _write_indexed_config(
                game / "settings" / "custom_controls.cfg",
                {1: 38, 5: 32, 21: 27, 22: 70, 23: 71},
            )
            dictionary = game / "resources" / "dictio_menu_en.bin"
            dictionary.parent.mkdir(parents=True)
            dictionary.write_bytes(_indexed_menu_dictionary())
            dialog = dictionary.with_name("dictio_dialog_en.bin")
            dialog.write_bytes(_indexed_dialog_dictionary())

            result = discover_indexed_xml_keymap(game)

            self.assertTrue(result.recognized_config)
            self.assertEqual(result.keymap["Up"]["action"], "Go up")
            self.assertEqual(result.keymap["Space"]["action"], "Jump")
            self.assertEqual(result.keymap["Esc"]["action"], "Menu")
            self.assertEqual(result.keymap["F"]["action"], "Target Friend")
            self.assertEqual(result.keymap["G"]["action"], "Lobby")
            self.assertIn("weapons", result.keymap["F2"]["action"].casefold())
            self.assertEqual(result.source_files, (config, dictionary, dialog))

    def test_discovers_official_chinese_indexed_resources_without_game_name(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory) / "Any Localized Native Game"
            config = _write_indexed_config(
                game / "settings" / "custom_controls.cfg",
                {1: 38, 5: 32, 19: 90, 21: 27, 22: 70, 23: 71},
            )
            english_menu = game / "resources" / "dictio_menu_en.bin"
            english_menu.parent.mkdir(parents=True)
            english_menu.write_bytes(_indexed_menu_dictionary())
            chinese_menu = english_menu.with_name("dictio_menu_ch.bin")
            chinese_menu.write_bytes(_localized_indexed_menu_dictionary())
            english_dialog = english_menu.with_name("dictio_dialog_en.bin")
            english_dialog.write_bytes(_indexed_dialog_dictionary())
            chinese_dialog = english_menu.with_name("dictio_dialog_ch.bin")
            chinese_dialog.write_bytes(_localized_indexed_dialog_dictionary())

            result = discover_indexed_xml_keymap(game)

            self.assertTrue(result.recognized_config)
            self.assertEqual(result.keymap["Up"]["action"], "往上")
            self.assertEqual(result.keymap["Space"]["action"], "跳")
            self.assertEqual(result.keymap["Z"]["action"], "缩放")
            self.assertEqual(result.keymap["Esc"]["action"], "菜单")
            self.assertEqual(result.keymap["F"]["action"], "选择友方目标")
            self.assertEqual(result.keymap["G"]["action"], "大厅")
            self.assertEqual(
                result.keymap["F1"]["action"],
                "回到快捷键面板",
            )
            self.assertEqual(
                result.keymap["F2"]["action"],
                "查看和装备所有的武器",
            )
            self.assertNotIn("F9", result.keymap)
            self.assertEqual(
                recorder._action_semantic_chinese_issues(result.keymap),
                [],
            )
            self.assertEqual(
                result.source_files,
                (config, chinese_menu, chinese_dialog),
            )

    def test_indexed_xml_steam_cloud_current_owner_precedes_other_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            steam = root / "steam"
            steamapps = steam / "steamapps"
            game = steamapps / "common" / "SyntheticIndexed"
            install = _write_indexed_config(game / "config.cfg", {11: 67})
            _write_steam_manifest(steamapps, game.name)
            owner = _write_indexed_config(
                steam / "userdata" / "111" / "4242" / "remote" / "config.cfg",
                {11: 83},
            )
            other = _write_indexed_config(
                steam / "userdata" / "222" / "4242" / "remote" / "config.cfg",
                {11: 88},
            )
            os.utime(other, (1_800_000_000, 1_800_000_000))
            appdata = root / "AppData" / "Roaming"
            local_appdata = root / "AppData" / "Local"
            appdata.mkdir(parents=True)
            local_appdata.mkdir(parents=True)

            with patch.dict(
                os.environ,
                {"APPDATA": str(appdata), "LOCALAPPDATA": str(local_appdata)},
            ):
                result = discover_indexed_xml_keymap(game)

            self.assertEqual(_steam_userdata_account_id("76561197960265839"), "111")
            self.assertIsNone(_steam_userdata_account_id("76561197960265728"))
            self.assertEqual(result.keymap["S"]["action"], "game_action_11")
            self.assertNotIn("C", result.keymap)
            self.assertNotIn("X", result.keymap)
            self.assertEqual(result.source_files, (owner,))
            self.assertNotEqual(result.source_files, (install,))

    def test_indexed_xml_steam_cloud_accepts_account32_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            steam = root / "steam"
            steamapps = steam / "steamapps"
            game = steamapps / "common" / "SyntheticIndexed"
            _write_indexed_config(game / "config.cfg", {11: 67})
            _write_steam_manifest(steamapps, game.name, last_owner="111")
            owner = _write_indexed_config(
                steam / "userdata" / "111" / "4242" / "remote" / "config.cfg",
                {11: 83},
            )

            result = discover_indexed_xml_keymap(game)

            self.assertEqual(_steam_userdata_account_id("111"), "111")
            self.assertIn("S", result.keymap)
            self.assertNotIn("C", result.keymap)
            self.assertEqual(result.source_files, (owner,))

    def test_indexed_xml_secondary_steam_library_uses_main_userdata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main_steam = root / "MainSteam"
            launcher = main_steam / "steam.exe"
            launcher.parent.mkdir(parents=True)
            launcher.touch()
            (main_steam / "steamapps").mkdir()
            library_steamapps = root / "SecondaryLibrary" / "steamapps"
            game = library_steamapps / "common" / "SyntheticIndexed"
            _write_indexed_config(game / "config.cfg", {11: 67})
            _write_steam_manifest(
                library_steamapps,
                game.name,
                last_owner="111",
                launcher_path=launcher,
            )
            owner = _write_indexed_config(
                main_steam
                / "userdata"
                / "111"
                / "4242"
                / "remote"
                / "config.cfg",
                {11: 83},
            )

            with patch.object(
                recorder,
                "_windows_steam_registry_roots",
                return_value=(),
            ):
                result = discover_indexed_xml_keymap(game)

            self.assertIn("S", result.keymap)
            self.assertNotIn("C", result.keymap)
            self.assertEqual(result.source_files, (owner,))

    def test_indexed_xml_missing_owner_uses_newest_valid_userdata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            steam = root / "steam"
            steamapps = steam / "steamapps"
            game = steamapps / "common" / "SyntheticIndexed"
            _write_indexed_config(game / "config.cfg", {11: 67})
            _write_steam_manifest(steamapps, game.name, last_owner=None)
            older = _write_indexed_config(
                steam / "userdata" / "111" / "4242" / "remote" / "config.cfg",
                {11: 83},
            )
            newest = steam / "userdata" / "222" / "4242" / "remote" / "config.cfg"
            newest.parent.mkdir(parents=True)
            newest.write_text("not XML", encoding="utf-8")
            os.utime(older, (1_700_000_000, 1_700_000_000))
            os.utime(newest, (1_800_000_000, 1_800_000_000))

            with patch.object(
                recorder,
                "_windows_steam_registry_roots",
                return_value=(),
            ):
                valid_fallback = discover_indexed_xml_keymap(game)

            self.assertIn("S", valid_fallback.keymap)
            self.assertNotIn("C", valid_fallback.keymap)
            self.assertEqual(valid_fallback.source_files, (older,))

            _write_indexed_config(newest)
            os.utime(newest, (1_800_000_000, 1_800_000_000))
            with patch.object(
                recorder,
                "_windows_steam_registry_roots",
                return_value=(),
            ):
                empty_newest = discover_indexed_xml_keymap(game)

            self.assertTrue(empty_newest.recognized_config)
            self.assertEqual(empty_newest.keymap, {})
            self.assertEqual(empty_newest.source_files, (newest,))

    def test_indexed_xml_corrupt_owner_falls_back_without_cross_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            steam = root / "steam"
            steamapps = steam / "steamapps"
            game = steamapps / "common" / "SyntheticIndexed"
            install = _write_indexed_config(game / "config.cfg", {11: 67})
            _write_steam_manifest(steamapps, game.name)
            owner = steam / "userdata" / "111" / "4242" / "remote" / "config.cfg"
            owner.parent.mkdir(parents=True)
            owner.write_text("not XML", encoding="utf-8")
            _write_indexed_config(
                steam / "userdata" / "222" / "4242" / "remote" / "config.cfg",
                {11: 88},
            )

            result = discover_indexed_xml_keymap(game)

            self.assertEqual(result.keymap["C"]["action"], "game_action_11")
            self.assertNotIn("X", result.keymap)
            self.assertEqual(result.source_files, (install,))

    def test_indexed_xml_invalid_owner_never_crosses_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            steam = root / "steam"
            steamapps = steam / "steamapps"
            game = steamapps / "common" / "SyntheticIndexed"
            install = _write_indexed_config(game / "config.cfg", {11: 67})
            _write_steam_manifest(
                steamapps,
                game.name,
                last_owner="invalid-owner",
            )
            _write_indexed_config(
                steam / "userdata" / "222" / "4242" / "remote" / "config.cfg",
                {11: 88},
            )

            with patch.object(
                recorder,
                "_windows_steam_registry_roots",
                return_value=(),
            ):
                result = discover_indexed_xml_keymap(game)

            self.assertIn("C", result.keymap)
            self.assertNotIn("X", result.keymap)
            self.assertEqual(result.source_files, (install,))

    def test_indexed_xml_valid_empty_owner_stops_default_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            steam = root / "steam"
            steamapps = steam / "steamapps"
            game = steamapps / "common" / "SyntheticIndexed"
            _write_indexed_config(game / "config.cfg", {1: 87})
            _write_steam_manifest(steamapps, game.name)
            owner = _write_indexed_config(
                steam / "userdata" / "111" / "4242" / "remote" / "config.cfg"
            )
            (game / "autoexec.cfg").write_text(
                'bind "w" "+forward"',
                encoding="utf-8",
            )

            result = discover_keymap_from_game_directory(game)

            self.assertTrue(result.recognized_config)
            self.assertEqual(result.keymap, {})
            self.assertEqual(result.source_files, (owner,))

    def test_authoritative_keymap_replaces_template_and_partial_scan_merges(self) -> None:
        current = {
            "W": {
                "type": "keyboard",
                "action": "前进",
                "movement_direction": "W",
            }
        }
        authoritative = KeymapDiscovery(
            {"S": {"type": "keyboard", "action": "Spells"}},
            (),
            1,
            False,
            True,
        )
        authoritative_empty = KeymapDiscovery({}, (), 1, False, True)
        partial = KeymapDiscovery(
            {"E": {"type": "keyboard", "action": "Use"}},
            (),
            1,
            False,
        )

        self.assertEqual(
            _apply_keymap_discovery(current, authoritative),
            authoritative.keymap,
        )
        self.assertEqual(_apply_keymap_discovery(current, authoritative_empty), {})
        self.assertEqual(
            set(_apply_keymap_discovery(current, partial)),
            {"W", "E"},
        )

    def test_foundation_live_registry_is_highest_priority_even_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory)
            executable = game / "game.exe"
            live = RegistryKeymapResult(
                {},
                r"Software\Vendor\Game\Controls",
                True,
                False,
                "已读取实际键位",
                True,
                executable,
                1,
            )
            with (
                patch(
                    "screen_recorder.discover_foundation_registry_keymap",
                    return_value=live,
                ),
                patch(
                    "screen_recorder.discover_player_config_files",
                    side_effect=AssertionError("不应继续扫描其他玩家配置"),
                ),
            ):
                result = discover_keymap_from_game_directory(game)

        self.assertTrue(result.recognized_config)
        self.assertFalse(result.requires_game_launch)
        self.assertTrue(result.has_verified_player_config)
        self.assertEqual(result.keymap, {})
        self.assertEqual(_apply_keymap_discovery(recorder.DEFAULT_KEYMAP, result), {})

    def test_verified_numeric_ini_is_authoritative_player_keymap(self) -> None:
        live_map = {
            "W": {
                "type": "keyboard",
                "action": "前进",
                "movement_direction": "W",
            },
            "Shift+leftClick": {
                "type": "mouse",
                "action": "右手武器重攻击",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory)
            executable = game / "program.exe"
            player_ini = game.parent / "profile" / "PlayerKeys.ini"
            live = NumericIniDiscoveryResult(
                live_map,
                True,
                False,
                "已读取玩家数值 INI 键位。",
                source_file=player_ini,
                executable_path=executable,
                scanned_executables=1,
                has_verified_player_config=True,
            )
            empty_foundation = RegistryKeymapResult(
                {}, None, False, False, "", False
            )
            with (
                patch(
                    "screen_recorder.discover_foundation_registry_keymap",
                    return_value=empty_foundation,
                ),
                patch(
                    "screen_recorder.discover_fromsoftware_numeric_ini_keymap",
                    return_value=live,
                ),
                patch(
                    "screen_recorder.discover_player_config_files",
                    side_effect=AssertionError("权威玩家 INI 后不应继续泛型扫描"),
                ),
            ):
                result = discover_keymap_from_game_directory(game)

        self.assertTrue(result.recognized_config)
        self.assertTrue(result.has_verified_player_config)
        self.assertFalse(result.requires_game_launch)
        self.assertEqual(result.keymap, live_map)
        self.assertEqual(result.source_files, (player_ini, executable))
        self.assertEqual(
            _apply_keymap_discovery(recorder.DEFAULT_KEYMAP, result), live_map
        )

    def test_declared_numeric_ini_missing_preserves_first_launch_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory)
            executable = game / "program.exe"
            notice = (
                "未找到可执行文件声明的玩家键位 INI。游戏可能尚未首次启动；"
                "请启动游戏、进入按键设置并正常退出后重新选择游戏目录。"
            )
            missing = NumericIniDiscoveryResult(
                {},
                True,
                True,
                notice,
                executable_path=executable,
                declared_paths=(game.parent / "profile" / "PlayerKeys.ini",),
                scanned_executables=1,
            )
            empty_foundation = RegistryKeymapResult(
                {}, None, False, False, "", False
            )
            with (
                patch(
                    "screen_recorder.discover_foundation_registry_keymap",
                    return_value=empty_foundation,
                ),
                patch(
                    "screen_recorder.discover_fromsoftware_numeric_ini_keymap",
                    return_value=missing,
                ),
                patch(
                    "screen_recorder.discover_player_config_files",
                    side_effect=AssertionError("缺失声明文件后不应误扫安装默认配置"),
                ),
            ):
                result = discover_keymap_from_game_directory(game)

        self.assertFalse(result.recognized_config)
        self.assertFalse(result.has_verified_player_config)
        self.assertTrue(result.requires_game_launch)
        self.assertEqual(result.notice, notice)
        self.assertEqual(result.source_files, (executable,))
        self.assertEqual(
            _apply_keymap_discovery(
                {"E": {"type": "keyboard", "action": "互动"}}, result
            ),
            {"E": {"type": "keyboard", "action": "互动"}},
        )

    def test_foundation_defaults_replace_template_and_are_chinese(self) -> None:
        default_map = {
            "W": {
                "type": "keyboard",
                "action": "前进",
                "movement_direction": "W",
            },
            "A": {
                "type": "keyboard",
                "action": "向左移动",
                "movement_direction": "L",
            },
            "S": {
                "type": "keyboard",
                "action": "后退",
                "movement_direction": "B",
            },
            "D": {
                "type": "keyboard",
                "action": "向右移动",
                "movement_direction": "R",
            },
        }
        fallback = RegistryKeymapResult(
            default_map,
            r"Software\Vendor\Game\Controls",
            False,
            True,
            "未读取当前玩家改键，已载入默认键位",
            True,
            Path("game.exe"),
            1,
        )
        empty_player_files = PlayerConfigDiscovery((), (), (), 0, False)
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "screen_recorder.discover_foundation_registry_keymap",
                    return_value=fallback,
                ),
                patch(
                    "screen_recorder.discover_player_config_files",
                    return_value=empty_player_files,
                ),
                patch(
                    "screen_recorder.discover_external_player_keymap",
                    return_value=KeymapDiscovery({}, (), 0, False),
                ),
            ):
                result = discover_keymap_from_game_directory(Path(directory))

        applied = _apply_keymap_discovery(recorder.DEFAULT_KEYMAP, result)
        self.assertTrue(result.recognized_config)
        self.assertTrue(result.requires_game_launch)
        self.assertEqual(applied, default_map)
        self.assertNotIn("gamepadA", applied)
        self.assertEqual(recorder._action_semantic_chinese_issues(applied), [])

    def test_game_directory_defaults_require_explicit_confirmation(self) -> None:
        class EntryStub:
            def get(self) -> str:
                return ""

            def insert(self, _index: int, _value: str) -> None:
                raise AssertionError("取消后不应填写游戏名称")

        with tempfile.TemporaryDirectory() as directory:
            discovery = KeymapDiscovery(
                {"W": {"type": "keyboard", "action": "前进"}},
                (Path(directory) / "game.exe",),
                1,
                False,
                True,
                notice="未找到玩家注册表；当前载入默认键位。",
                requires_game_launch=True,
            )
            dialog = recorder.SessionConfigDialog.__new__(
                recorder.SessionConfigDialog
            )
            dialog.configure = lambda **_kwargs: None
            dialog.update_idletasks = lambda: None
            dialog.game_title_entry = EntryStub()
            dialog._keymap_document = lambda: self.fail("取消后不应读取当前键位")
            dialog._set_keymap = lambda _keymap: self.fail("取消后不应应用默认键位")

            with (
                patch.object(recorder.filedialog, "askdirectory", return_value=directory),
                patch.object(
                    recorder,
                    "discover_keymap_from_game_directory",
                    return_value=discovery,
                ),
                patch.object(
                    recorder.messagebox,
                    "askyesno",
                    return_value=False,
                ) as askyesno,
                patch.object(recorder.messagebox, "showinfo") as showinfo,
            ):
                dialog._discover_from_game_directory()

        askyesno.assert_called_once()
        self.assertEqual(askyesno.call_args.args[0], "未找到玩家实际键位")
        self.assertIn("安装目录或程序内置的默认键位", askyesno.call_args.args[1])
        self.assertIn("JSON 动作与实际按键不对应", askyesno.call_args.args[1])
        self.assertEqual(askyesno.call_args.kwargs["default"], recorder.messagebox.NO)
        showinfo.assert_not_called()

    def test_game_directory_defaults_apply_after_confirmation(self) -> None:
        class EntryStub:
            def __init__(self) -> None:
                self.value = ""

            def get(self) -> str:
                return self.value

            def insert(self, _index: int, value: str) -> None:
                self.value = value

        with tempfile.TemporaryDirectory() as directory:
            default_map = {"W": {"type": "keyboard", "action": "前进"}}
            discovery = KeymapDiscovery(
                default_map,
                (Path(directory) / "game.exe",),
                1,
                False,
                True,
                notice="未找到玩家注册表；当前载入默认键位。",
                requires_game_launch=True,
            )
            applied: list[dict[str, dict[str, str]]] = []
            dialog = recorder.SessionConfigDialog.__new__(
                recorder.SessionConfigDialog
            )
            dialog.configure = lambda **_kwargs: None
            dialog.update_idletasks = lambda: None
            dialog.game_title_entry = EntryStub()
            dialog._keymap_document = lambda: {
                "S": {"type": "keyboard", "action": "后退"}
            }
            dialog._set_keymap = applied.append

            with (
                patch.object(recorder.filedialog, "askdirectory", return_value=directory),
                patch.object(
                    recorder,
                    "discover_keymap_from_game_directory",
                    return_value=discovery,
                ),
                patch.object(
                    recorder.messagebox,
                    "askyesno",
                    return_value=True,
                ) as askyesno,
                patch.object(recorder.messagebox, "showinfo") as showinfo,
            ):
                dialog._discover_from_game_directory()

        askyesno.assert_called_once()
        self.assertEqual(applied, [default_map])
        self.assertEqual(dialog.game_title_entry.value, Path(directory).name)
        showinfo.assert_called_once()

    def test_game_directory_live_keymap_skips_default_warning(self) -> None:
        class EntryStub:
            def __init__(self) -> None:
                self.value = "已有名称"

            def get(self) -> str:
                return self.value

            def insert(self, _index: int, value: str) -> None:
                self.value = value

        with tempfile.TemporaryDirectory() as directory:
            live_map = {"F1": {"type": "keyboard", "action": "打开帮助"}}
            discovery = KeymapDiscovery(
                live_map,
                (Path(directory) / "game.exe",),
                1,
                False,
                True,
                notice="已读取当前玩家实际键位。",
                requires_game_launch=False,
                has_verified_player_config=True,
            )
            applied: list[dict[str, dict[str, str]]] = []
            dialog = recorder.SessionConfigDialog.__new__(
                recorder.SessionConfigDialog
            )
            dialog.configure = lambda **_kwargs: None
            dialog.update_idletasks = lambda: None
            dialog.game_title_entry = EntryStub()
            dialog._keymap_document = lambda: {}
            dialog._set_keymap = applied.append

            with (
                patch.object(recorder.filedialog, "askdirectory", return_value=directory),
                patch.object(
                    recorder,
                    "discover_keymap_from_game_directory",
                    return_value=discovery,
                ),
                patch.object(recorder.messagebox, "askyesno") as askyesno,
                patch.object(recorder.messagebox, "showinfo") as showinfo,
            ):
                dialog._discover_from_game_directory()

        askyesno.assert_not_called()
        self.assertEqual(applied, [live_map])
        self.assertEqual(dialog.game_title_entry.value, "已有名称")
        showinfo.assert_called_once()

    def test_install_default_without_launch_flag_still_requires_confirmation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "game"
            game.mkdir()
            mapping = {"W": {"type": "keyboard", "action": "前进"}}
            install_default = KeymapDiscovery(
                mapping,
                (game / "DefaultInput.ini",),
                1,
                False,
            )
            external_player = KeymapDiscovery(
                mapping,
                (root / "profile" / "Input.ini",),
                1,
                False,
            )
            explicitly_verified = KeymapDiscovery(
                mapping,
                (game / "portable.ini",),
                1,
                False,
                has_verified_player_config=True,
            )
            empty_install_default = KeymapDiscovery(
                {},
                (game / "empty-default.xml",),
                1,
                False,
                recognized_config=True,
            )

            self.assertTrue(
                recorder._keymap_requires_player_config_confirmation(
                    game,
                    install_default,
                )
            )
            self.assertFalse(
                recorder._keymap_requires_player_config_confirmation(
                    game,
                    external_player,
                )
            )
            self.assertFalse(
                recorder._keymap_requires_player_config_confirmation(
                    game,
                    explicitly_verified,
                )
            )
            self.assertTrue(
                recorder._keymap_requires_player_config_confirmation(
                    game,
                    empty_install_default,
                )
            )

    def test_missing_player_keymap_shows_launch_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            discovery = KeymapDiscovery(
                {},
                (),
                1,
                False,
                notice=(
                    "未找到玩家实际键位配置。请先启动游戏、进入按键设置并正常退出。"
                ),
                requires_game_launch=True,
            )
            dialog = recorder.SessionConfigDialog.__new__(
                recorder.SessionConfigDialog
            )
            dialog.configure = lambda **_kwargs: None
            dialog.update_idletasks = lambda: None
            dialog._keymap_document = lambda: self.fail("无映射时不应读取当前键位")
            dialog._set_keymap = lambda _keymap: self.fail("无映射时不应修改表格")

            with (
                patch.object(recorder.filedialog, "askdirectory", return_value=directory),
                patch.object(
                    recorder,
                    "discover_keymap_from_game_directory",
                    return_value=discovery,
                ),
                patch.object(recorder.messagebox, "showwarning") as showwarning,
                patch.object(recorder.messagebox, "askyesno") as askyesno,
            ):
                dialog._discover_from_game_directory()

        showwarning.assert_called_once()
        self.assertEqual(showwarning.call_args.args[0], "未找到玩家键位配置")
        self.assertIn("请先启动游戏", showwarning.call_args.args[1])
        askyesno.assert_not_called()

    def test_specific_player_config_notice_is_shown_without_generic_claims(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            discovery = KeymapDiscovery(
                {},
                (Path(directory) / "Input.ini",),
                1,
                False,
                notice=(
                    "已找到并解析玩家设置文件，但其中没有已保存的自定义键位。"
                    "若一直使用游戏内置默认键位，这是正常状态。"
                ),
                requires_game_launch=False,
                has_recognized_player_file=True,
            )
            dialog = recorder.SessionConfigDialog.__new__(
                recorder.SessionConfigDialog
            )
            dialog.configure = lambda **_kwargs: None
            dialog.update_idletasks = lambda: None

            with (
                patch.object(recorder.filedialog, "askdirectory", return_value=directory),
                patch.object(
                    recorder,
                    "discover_keymap_from_game_directory",
                    return_value=discovery,
                ),
                patch.object(recorder.messagebox, "showwarning") as showwarning,
            ):
                dialog._discover_from_game_directory()

        showwarning.assert_called_once()
        self.assertEqual(
            showwarning.call_args.args[0],
            "玩家配置中没有已保存的自定义键位",
        )
        warning = showwarning.call_args.args[1]
        self.assertIn("已找到并解析玩家设置文件", warning)
        self.assertIn("使用游戏内置默认键位", warning)
        self.assertNotIn("二进制/加密格式", warning)
        self.assertIn("已解析玩家配置文件", warning)
        self.assertIn("Input.ini", warning)
        self.assertNotIn("候选文件或可执行文件", warning)
        self.assertIn("本次检测没有修改当前键位表", warning)
        self.assertIn("可能仍是通用模板", warning)
        self.assertTrue(dialog._keymap_needs_manual_confirmation)

    def test_failed_detection_requires_manual_confirmation_before_validate(
        self,
    ) -> None:
        class EntryStub:
            def get(self) -> str:
                return "测试游戏"

        dialog = recorder.SessionConfigDialog.__new__(
            recorder.SessionConfigDialog
        )
        dialog.game_title_entry = EntryStub()
        dialog.result = None
        dialog._keymap_needs_manual_confirmation = True
        dialog._keymap_document = lambda: {
            "W": {
                "type": "keyboard",
                "action": "前进",
                "movement_direction": "W",
            }
        }

        with patch.object(
            recorder.messagebox,
            "askyesno",
            side_effect=(False, True),
        ) as askyesno:
            self.assertFalse(dialog.validate())
            self.assertIsNone(dialog.result)
            self.assertTrue(dialog._keymap_needs_manual_confirmation)

            first_prompt = askyesno.call_args_list[0]
            self.assertEqual(first_prompt.args[0], "确认已人工核对键位")
            self.assertIn("当前表格可能仍是通用模板", first_prompt.args[1])
            self.assertIn("逐项核对或修正", first_prompt.args[1])
            self.assertEqual(
                first_prompt.kwargs["default"],
                recorder.messagebox.NO,
            )

            self.assertTrue(dialog.validate())

        self.assertEqual(askyesno.call_count, 2)
        self.assertFalse(dialog._keymap_needs_manual_confirmation)
        self.assertEqual(dialog.result.game_title, "测试游戏")
        self.assertEqual(dialog.result.keymap["W"]["action"], "前进")

    def test_complete_external_player_config_outranks_foundation_defaults(self) -> None:
        fallback = RegistryKeymapResult(
            {"W": {"type": "keyboard", "action": "前进"}},
            r"Software\Vendor\Game\Controls",
            False,
            True,
            "默认键位",
            True,
            Path("game.exe"),
            1,
        )
        external = KeymapDiscovery(
            {"F1": {"type": "keyboard", "action": "帮助"}},
            (Path("player.cfg"),),
            1,
            False,
            True,
        )
        empty_player_files = PlayerConfigDiscovery((), (), (), 0, False)
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "screen_recorder.discover_foundation_registry_keymap",
                    return_value=fallback,
                ),
                patch(
                    "screen_recorder.discover_player_config_files",
                    return_value=empty_player_files,
                ),
                patch(
                    "screen_recorder.discover_external_player_keymap",
                    return_value=external,
                ),
            ):
                result = discover_keymap_from_game_directory(Path(directory))

        self.assertIs(result, external)

    def test_foundation_schema_without_safe_map_preserves_template_and_guidance(
        self,
    ) -> None:
        unresolved = RegistryKeymapResult(
            {},
            r"Software\Vendor\Game\Controls",
            False,
            True,
            "请先启动游戏并正常退出",
            True,
            Path("game.exe"),
            1,
        )
        empty_player_files = PlayerConfigDiscovery((), (), (), 0, False)
        current = {"W": {"type": "keyboard", "action": "前进"}}
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "screen_recorder.discover_foundation_registry_keymap",
                    return_value=unresolved,
                ),
                patch(
                    "screen_recorder.discover_player_config_files",
                    return_value=empty_player_files,
                ),
                patch(
                    "screen_recorder.discover_external_player_keymap",
                    return_value=KeymapDiscovery({}, (), 0, False),
                ),
            ):
                result = discover_keymap_from_game_directory(Path(directory))

        self.assertFalse(result.recognized_config)
        self.assertTrue(result.requires_game_launch)
        self.assertIn("先启动游戏", result.notice)
        self.assertEqual(_apply_keymap_discovery(current, result), current)

    def test_raw_keyboard_names_match_extended_directinput_mappings(self) -> None:
        self.assertEqual(
            InputEventTracker._key_name(
                0x0D,
                0x1C,
                InputEventTracker.RI_KEY_E0,
            ),
            "NumPadEnter",
        )
        self.assertEqual(InputEventTracker._key_name(0x2C, 0x37, 0), "PrintScreen")
        self.assertEqual(InputEventTracker._key_name(0x5D, 0x5D, 0), "Apps")

    def test_sparse_player_override_removes_old_action_binding_and_unbound(self) -> None:
        keymap = {
            "W": {
                "type": "keyboard",
                "action": "PanUp / Attack",
                "movement_direction": "W",
            },
            "Space": {"type": "keyboard", "action": "CycleSpeed"},
            "E": {"type": "keyboard", "action": "Use"},
        }
        recorder._remove_overridden_actions(
            keymap,
            frozenset({"Pan Up", "Cycle Speed"}),
        )
        recorder._merge_keymap_records(
            keymap,
            {
                "Up": {
                    "type": "keyboard",
                    "action": "Pan Up",
                    "movement_direction": "W",
                }
            },
        )

        self.assertEqual(keymap["W"]["action"], "Attack")
        self.assertNotIn("movement_direction", keymap["W"])
        self.assertNotIn("Space", keymap)
        self.assertEqual(keymap["Up"]["action"], "Pan Up")
        self.assertIn("E", keymap)

    def test_external_player_config_prefers_live_file_and_downgrades_fuzzy_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            game = base / "Exact Game"
            game.mkdir()
            player_root = base / "AppData" / "Exact Game"
            live = player_root / "settings.save"
            backup = player_root / "backup" / "settings.save"
            for path, key in ((live, "J"), (backup, "B")):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps({"keyboard_mapping": {"Jump": key}}),
                    encoding="utf-8",
                )
            discovery = PlayerConfigDiscovery(
                (backup, live),
                (PlayerConfigRoot(player_root, "appdata"),),
                ("exactgame",),
                2,
                False,
            )

            result = recorder.discover_external_player_keymap(game, discovery)

            self.assertTrue(result.recognized_config)
            self.assertIn("J", result.keymap)
            self.assertNotIn("B", result.keymap)
            self.assertEqual(result.source_files, (live,))

            fuzzy_root = base / "AppData" / "Exact Game Mod Manager"
            fuzzy_config = fuzzy_root / "controls.xml"
            fuzzy_config.parent.mkdir(parents=True)
            fuzzy_config.write_text(
                '<controls><binding action="MoveForward" key="W" />'
                '<binding action="Jump" key="Space" />'
                '<binding action="Attack" key="F" />'
                '<binding action="Pause" key="Escape" /></controls>',
                encoding="utf-8",
            )
            fuzzy_discovery = PlayerConfigDiscovery(
                (fuzzy_config,),
                (PlayerConfigRoot(fuzzy_root, "appdata"),),
                ("exactgame",),
                1,
                False,
            )
            fuzzy_result = recorder.discover_external_player_keymap(
                game,
                fuzzy_discovery,
            )
            self.assertFalse(fuzzy_result.recognized_config)
            self.assertEqual(fuzzy_result.keymap, {})
            self.assertEqual(fuzzy_result.source_files, ())

    def test_sparse_player_actions_overlay_complete_install_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            game = base / "Overlay Game"
            game.mkdir()
            (game / "Overlay Game.exe").write_bytes(b"")
            (game / "settings.save").write_text(
                json.dumps(
                    {
                        "keyboard_mapping": {
                            "PanUp": "W",
                            "CycleSpeed": "Space",
                            "Attack": "F",
                        }
                    }
                ),
                encoding="utf-8",
            )
            profile = base / "Profile"
            player = (
                profile
                / "AppData"
                / "Roaming"
                / "Overlay Game"
                / "keybindings.json"
            )
            player.parent.mkdir(parents=True)
            player.write_text(
                json.dumps(
                    [
                        {
                            "mButton": "NumButtons",
                            "mKeyCode": "UpArrow",
                            "mAction": "PanUp",
                            "mModifier": "None",
                        },
                        {
                            "mButton": "NumButtons",
                            "mKeyCode": "None",
                            "mAction": "CycleSpeed",
                            "mModifier": "None",
                        },
                    ]
                ),
                encoding="utf-8",
            )
            environment = {
                "USERPROFILE": str(profile),
                "APPDATA": str(profile / "AppData" / "Roaming"),
                "LOCALAPPDATA": str(profile / "AppData" / "Local"),
            }
            (profile / "AppData" / "Local").mkdir(parents=True)

            with patch.dict(os.environ, environment, clear=True):
                result = discover_keymap_from_game_directory(game)

            self.assertNotIn("W", result.keymap)
            self.assertNotIn("Space", result.keymap)
            self.assertEqual(result.keymap["Up"]["action"], "Pan Up")
            self.assertEqual(result.keymap["F"]["action"], "Attack")
            self.assertIn(player, result.source_files)

    def test_indexed_xml_appdata_scan_reports_resource_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "SyntheticIndexed"
            _write_indexed_config(game / "config.cfg", {11: 67})
            appdata = root / "AppData" / "Roaming"
            local_appdata = root / "AppData" / "Local"
            for name in ("one", "two", "three"):
                (appdata / name).mkdir(parents=True)
            local_appdata.mkdir(parents=True)

            with (
                patch.dict(
                    os.environ,
                    {
                        "APPDATA": str(appdata),
                        "LOCALAPPDATA": str(local_appdata),
                    },
                ),
                patch.object(recorder, "MAX_GAME_DIRECTORY_ENTRIES", 2),
                patch.object(
                    recorder,
                    "_windows_steam_registry_roots",
                    return_value=(),
                ),
            ):
                result = discover_indexed_xml_keymap(game)

            self.assertTrue(result.recognized_config)
            self.assertTrue(result.truncated)
            self.assertIn("C", result.keymap)

    def test_parses_binary_unity_input_manager_document(self) -> None:
        document = {
            "m_Axes": [
                {
                    "m_Name": "Horizontal",
                    "negativeButton": "a",
                    "positiveButton": "d",
                    "altNegativeButton": "left",
                    "altPositiveButton": "right",
                },
                {
                    "m_Name": "Fire1",
                    "negativeButton": "",
                    "positiveButton": "left ctrl",
                    "altNegativeButton": "",
                    "altPositiveButton": "mouse 0",
                },
            ]
        }

        result = _parse_unity_serialized_input_manager(document)
        self.assertEqual(result["A"]["movement_direction"], "L")
        self.assertEqual(result["D"]["movement_direction"], "R")
        self.assertEqual(result["Ctrl"]["action"], "Fire1")
        self.assertEqual(result["leftClick"]["action"], "Fire1")

    def test_serialized_unity_input_actions_limit_movement_classification(self) -> None:
        document = {
            "m_ActionMaps": [
                {
                    "m_Bindings": [
                        {
                            "m_Path": "<Keyboard>/w",
                            "m_Action": "Move",
                            "m_Name": "up",
                        },
                        {
                            "m_Path": "<Keyboard>/q",
                            "m_Action": "CocktailMoveLeft",
                            "m_Name": "",
                        },
                        {
                            "m_Path": "<Mouse>/leftButton",
                            "m_Action": "Fire",
                            "m_Name": "",
                        },
                    ]
                }
            ]
        }

        result = _parse_unity_serialized_input_actions(document)
        self.assertEqual(result["W"]["movement_direction"], "W")
        self.assertNotIn("movement_direction", result["Q"])
        self.assertEqual(result["leftClick"]["action"], "Fire")

    def test_discovers_klei_profile_by_format_not_game_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "Example Klei Game"
            archive_path = game / "data" / "databundles" / "scripts.zip"
            archive_path.parent.mkdir(parents=True)
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "scripts/constants.lua",
                    "\n".join(
                        (
                            "CONTROL_MOVE_UP = 5",
                            "CONTROL_ATTACK = 2",
                            "CONTROL_ACTION = 4",
                            "CONTROL_ROTATE_LEFT = 11",
                            "CONTROL_MAP = 14",
                            "MOUSEBUTTON_LEFT = 1000",
                        )
                    ),
                )

            controls: list[list[tuple[int, int]]] = [[] for _ in range(90)]
            controls[4] = [(82, 0)]  # Custom rotate-left key: R.
            controls[7] = [(84, 0)]  # Custom rotate-right key: T.
            controls[28] = [(1000, 0)]
            controls[36] = [(71, 0)]  # Custom attack key: G.
            controls[37] = [(32, 0)]
            movement = [[(74, 0)], [(76, 0)], [(75, 0)], [(73, 0)]]
            values = [10, 7, len(controls)]
            for mapping in controls:
                values.append(len(mapping))
                for code, modifier in mapping:
                    values.extend((code, modifier))
            values.append(len(movement))
            for mapping in movement:
                values.append(len(mapping))
                for code, modifier in mapping:
                    values.extend((code, modifier))
            control_blob = struct.pack(f"<{len(values)}I", *values)
            payload = json.dumps(
                {
                    "controls": [
                        {
                            "enabled": True,
                            "guid": 1,
                            "data": base64.b64encode(control_blob).decode("ascii"),
                        }
                    ]
                }
            ).encode("utf-8")
            compressed = zlib.compress(payload, level=9)
            envelope = struct.pack(
                "<IIII",
                1,
                16,
                len(payload),
                len(compressed),
            ) + compressed
            profile_root = root / "Documents" / "Klei" / "ExampleKleiGame"
            profile = profile_root / "123" / "client_save" / "profile"
            profile.parent.mkdir(parents=True)
            profile.write_text(
                "KLEI     1D" + base64.b64encode(envelope).decode("ascii"),
                encoding="ascii",
            )

            result = discover_klei_keymap(game, [profile_root])
            self.assertEqual(result.keymap["R"]["action"], "视角左转")
            self.assertEqual(result.keymap["T"]["action"], "视角右转")
            self.assertEqual(result.keymap["G"]["action"], "自动攻击")
            self.assertEqual(result.keymap["J"]["movement_direction"], "L")
            self.assertEqual(result.keymap["L"]["movement_direction"], "R")
            self.assertEqual(result.keymap["K"]["movement_direction"], "B")
            self.assertEqual(result.keymap["I"]["movement_direction"], "W")
            self.assertFalse(any(key.startswith("gamepad") for key in result.keymap))
            self.assertEqual(len(result.source_files), 2)

    def test_klei_archive_without_profile_does_not_invent_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory)
            archive_path = game / "data" / "databundles" / "scripts.zip"
            archive_path.parent.mkdir(parents=True)
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "scripts/constants.lua",
                    "\n".join(
                        (
                            "CONTROL_MOVE_UP = 5",
                            "CONTROL_ATTACK = 2",
                            "CONTROL_ACTION = 4",
                            "CONTROL_ROTATE_LEFT = 11",
                            "CONTROL_MAP = 14",
                            "MOUSEBUTTON_LEFT = 1000",
                        )
                    ),
                )
            result = discover_klei_keymap(game, [])
            self.assertEqual(result.keymap, {})
            self.assertEqual(result.source_files, (archive_path,))

    def test_discovers_pixpil_defaults_from_synthetic_garchives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game, manifest, config, script = _write_synthetic_pixpil_game(root)
            appdata = root / "AppData" / "Roaming"
            local_appdata = root / "AppData" / "Local"
            appdata.mkdir(parents=True)
            local_appdata.mkdir(parents=True)

            with patch.dict(
                os.environ,
                {
                    "APPDATA": str(appdata),
                    "LOCALAPPDATA": str(local_appdata),
                },
            ):
                result = discover_pixpil_keymap(game)

            self.assertEqual(result.keymap["W"]["movement_direction"], "W")
            self.assertEqual(result.keymap["S"]["movement_direction"], "B")
            self.assertEqual(result.keymap["A"]["movement_direction"], "L")
            self.assertEqual(result.keymap["D"]["movement_direction"], "R")
            self.assertEqual(result.keymap["Space"]["action"], "interaction")
            self.assertEqual(result.keymap["Shift"]["action"], "running")
            self.assertEqual(result.keymap["leftClick"]["action"], "fire_A")
            self.assertEqual(result.keymap["rightClick"]["action"], "fire_B")
            self.assertEqual(result.keymap["middleClick"]["action"], "toggle_isolated")
            self.assertEqual(result.source_files, (manifest, config, script))
            self.assertEqual(result.scanned_files, 3)
            self.assertFalse(result.truncated)

    def test_pixpil_global_manager_precedes_many_controller_exports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game, _manifest, config, script = _write_synthetic_pixpil_game(root)
            script_name = "0123456789abcdef0123456789abcdef"
            controller_entries = {
                f"controller_{index:03d}": b"not-a-mapping-module"
                for index in range(65)
            }
            exports = {
                f"script.Scene{index:03d}Controller": f"script/controller_{index:03d}"
                for index in range(65)
            }
            # Keep the explicit target last so an insertion-order cap would miss it.
            exports["script.SyntheticGlobalManager"] = f"script/{script_name}"
            config.write_bytes(
                _pixpil_garchive(
                    {
                        "script_library": json.dumps(
                            {"export": exports}
                        ).encode("utf-8")
                    }
                )
            )
            script.write_bytes(
                _pixpil_garchive(
                    {
                        **controller_entries,
                        script_name: _synthetic_pixpil_luajit(),
                    }
                )
            )
            appdata = root / "AppData" / "Roaming"
            local_appdata = root / "AppData" / "Local"
            appdata.mkdir(parents=True)
            local_appdata.mkdir(parents=True)

            with patch.dict(
                os.environ,
                {
                    "APPDATA": str(appdata),
                    "LOCALAPPDATA": str(local_appdata),
                },
            ):
                result = discover_pixpil_keymap(game)

            self.assertEqual(result.keymap["Space"]["action"], "interaction")
            self.assertFalse(result.truncated)

    def test_pixpil_safe_settings_override_archive_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game, manifest, config, script = _write_synthetic_pixpil_game(root)
            appdata = root / "AppData" / "Roaming"
            local_appdata = root / "AppData" / "Local"
            local_appdata.mkdir(parents=True)
            player_document = {
                "mappings": {
                    "keyboard": {
                        "interaction": "e",
                        "running": {"key": "lctrl"},
                    },
                    "mouse": {"fire_A": "right"},
                }
            }
            player_settings = (
                appdata
                / "SyntheticPixpil"
                / game.name
                / "steam_123"
                / "input_settings"
            )
            player_settings.parent.mkdir(parents=True)
            player_settings.write_bytes(_pixpil_safe_settings(player_document))

            self.assertEqual(
                _decode_pixpil_safe_settings(player_settings),
                player_document,
            )
            with patch.dict(
                os.environ,
                {
                    "APPDATA": str(appdata),
                    "LOCALAPPDATA": str(local_appdata),
                },
            ):
                result = discover_pixpil_keymap(game)

            self.assertNotIn("Space", result.keymap)
            self.assertNotIn("Shift", result.keymap)
            self.assertNotIn("leftClick", result.keymap)
            self.assertEqual(result.keymap["E"]["action"], "interaction")
            self.assertEqual(result.keymap["Ctrl"]["action"], "running")
            self.assertEqual(
                set(result.keymap["rightClick"]["action"].split(" / ")),
                {"fire_A", "fire_B"},
            )
            self.assertEqual(result.keymap["W"]["movement_direction"], "W")
            self.assertEqual(
                result.source_files,
                (manifest, config, script, player_settings),
            )
            self.assertEqual(result.scanned_files, 4)

    def test_pixpil_player_mapping_accepts_left_bracket_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game, _manifest, _config, _script = _write_synthetic_pixpil_game(root)
            appdata = root / "AppData" / "Roaming"
            local_appdata = root / "AppData" / "Local"
            local_appdata.mkdir(parents=True)
            player_settings = (
                appdata
                / "SyntheticPixpil"
                / game.name
                / "steam_123"
                / "input_settings"
            )
            player_settings.parent.mkdir(parents=True)
            player_settings.write_bytes(
                _pixpil_safe_settings(
                    {"mappings": {"keyboard": {"menu_map": "["}}}
                )
            )

            with patch.dict(
                os.environ,
                {
                    "APPDATA": str(appdata),
                    "LOCALAPPDATA": str(local_appdata),
                },
            ):
                result = discover_pixpil_keymap(game)

            self.assertNotIn("Tab", result.keymap)
            self.assertEqual(result.keymap["["]["action"], "menu_map")

    def test_pixpil_false_and_empty_list_unbind_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game, _manifest, _config, _script = _write_synthetic_pixpil_game(root)
            appdata = root / "AppData" / "Roaming"
            local_appdata = root / "AppData" / "Local"
            local_appdata.mkdir(parents=True)
            player_settings = (
                appdata
                / "SyntheticPixpil"
                / game.name
                / "steam_123"
                / "input_settings"
            )
            player_settings.parent.mkdir(parents=True)
            player_settings.write_bytes(
                _pixpil_safe_settings(
                    {
                        "mappings": {
                            "keyboard": {
                                "interaction": False,
                                "running": [],
                            }
                        }
                    }
                )
            )

            with patch.dict(
                os.environ,
                {
                    "APPDATA": str(appdata),
                    "LOCALAPPDATA": str(local_appdata),
                },
            ):
                result = discover_pixpil_keymap(game)

            self.assertNotIn("Space", result.keymap)
            self.assertNotIn("Shift", result.keymap)
            self.assertEqual(result.keymap["W"]["movement_direction"], "W")
            self.assertEqual(result.keymap["leftClick"]["action"], "fire_A")

    def test_pixpil_uses_latest_parseable_single_steam_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game, manifest, config, script = _write_synthetic_pixpil_game(root)
            appdata = root / "AppData" / "Roaming"
            local_appdata = root / "AppData" / "Local"
            local_appdata.mkdir(parents=True)
            profiles = appdata / "SyntheticPixpil" / game.name

            old_regular = profiles / "steam_old" / "input_settings"
            old_farm = profiles / "steam_old" / "input_settings_farm"
            current_regular = profiles / "steam_current" / "input_settings"
            current_farm = profiles / "steam_current" / "input_settings_farm"
            broken_regular = profiles / "steam_broken" / "input_settings"
            for path in (
                old_regular,
                old_farm,
                current_regular,
                current_farm,
                broken_regular,
            ):
                path.parent.mkdir(parents=True, exist_ok=True)

            old_regular.write_bytes(
                _pixpil_safe_settings(
                    {"mappings": {"keyboard": {"interaction": "q"}}}
                )
            )
            old_farm.write_bytes(
                _pixpil_safe_settings(
                    {"mappings": {"keyboard": {"farm_only": "h"}}}
                )
            )
            current_regular.write_bytes(
                _pixpil_safe_settings(
                    {"mappings": {"keyboard": {"interaction": "e"}}}
                )
            )
            current_farm.write_bytes(
                _pixpil_safe_settings(
                    {"mappings": {"keyboard": {"farm_only": "j"}}}
                )
            )
            broken_regular.write_bytes(bytes(64) + b"not-a-deflate-stream")
            for path in (old_regular, old_farm):
                os.utime(path, (1_700_000_000, 1_700_000_000))
            for path in (current_regular, current_farm):
                os.utime(path, (1_700_000_100, 1_700_000_100))
            os.utime(broken_regular, (1_700_000_200, 1_700_000_200))

            with patch.dict(
                os.environ,
                {
                    "APPDATA": str(appdata),
                    "LOCALAPPDATA": str(local_appdata),
                },
            ):
                result = discover_pixpil_keymap(game)

            self.assertEqual(result.keymap["E"]["action"], "interaction")
            self.assertEqual(result.keymap["J"]["action"], "farm_only")
            self.assertNotIn("Q", result.keymap)
            self.assertNotIn("H", result.keymap)
            self.assertEqual(
                result.source_files,
                (manifest, config, script, current_regular, current_farm),
            )

    def test_pixpil_latest_valid_empty_profile_stops_account_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game, manifest, config, script = _write_synthetic_pixpil_game(root)
            appdata = root / "AppData" / "Roaming"
            local_appdata = root / "AppData" / "Local"
            local_appdata.mkdir(parents=True)
            profiles = appdata / "SyntheticPixpil" / game.name
            old_settings = profiles / "steam_old" / "input_settings"
            latest_empty = profiles / "steam_latest" / "input_settings"
            old_settings.parent.mkdir(parents=True)
            latest_empty.parent.mkdir(parents=True)
            old_settings.write_bytes(
                _pixpil_safe_settings(
                    {"mappings": {"keyboard": {"interaction": "q"}}}
                )
            )
            latest_empty.write_bytes(_pixpil_safe_settings({"mappings": {}}))
            os.utime(old_settings, (1_700_000_000, 1_700_000_000))
            os.utime(latest_empty, (1_700_000_500, 1_700_000_500))

            with patch.dict(
                os.environ,
                {
                    "APPDATA": str(appdata),
                    "LOCALAPPDATA": str(local_appdata),
                },
            ):
                result = discover_pixpil_keymap(game)

            self.assertEqual(result.keymap["Space"]["action"], "interaction")
            self.assertNotIn("Q", result.keymap)
            self.assertEqual(
                result.source_files,
                (manifest, config, script, latest_empty),
            )

    def test_pixpil_steam_last_owner_precedes_newer_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            steamapps = root / "steamapps"
            game, manifest, config, script = _write_synthetic_pixpil_game(
                steamapps / "common"
            )
            (steamapps / "appmanifest_123.acf").write_text(
                '\n'.join(
                    (
                        '"AppState"',
                        "{",
                        '    "appid"        "123"',
                        f'    "installdir"  "{game.name}"',
                        '    "LastOwner"    "111"',
                        "}",
                    )
                ),
                encoding="utf-8",
            )
            appdata = root / "AppData" / "Roaming"
            local_appdata = root / "AppData" / "Local"
            local_appdata.mkdir(parents=True)
            profiles = appdata / "SyntheticPixpil" / game.name
            owner_settings = profiles / "steam_111" / "input_settings"
            newer_settings = profiles / "steam_222" / "input_settings"
            owner_settings.parent.mkdir(parents=True)
            newer_settings.parent.mkdir(parents=True)
            owner_settings.write_bytes(
                _pixpil_safe_settings(
                    {"mappings": {"keyboard": {"interaction": "o"}}}
                )
            )
            newer_settings.write_bytes(
                _pixpil_safe_settings(
                    {"mappings": {"keyboard": {"interaction": "n"}}}
                )
            )
            os.utime(owner_settings, (1_700_000_000, 1_700_000_000))
            os.utime(newer_settings, (1_700_000_500, 1_700_000_500))

            with patch.dict(
                os.environ,
                {
                    "APPDATA": str(appdata),
                    "LOCALAPPDATA": str(local_appdata),
                },
            ):
                result = discover_pixpil_keymap(game)

            self.assertEqual(result.keymap["O"]["action"], "interaction")
            self.assertNotIn("N", result.keymap)
            self.assertEqual(
                result.source_files,
                (manifest, config, script, owner_settings),
            )

    def test_pixpil_steam_install_dir_match_is_case_insensitive_but_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            steamapps = Path(directory) / "steamapps"
            game = steamapps / "common" / "Game A"
            game.mkdir(parents=True)
            (steamapps / "appmanifest_001.acf").write_text(
                '\n'.join(
                    (
                        '"AppState"',
                        "{",
                        '    "installdir"  "Game-A"',
                        '    "LastOwner"   "111"',
                        "}",
                    )
                ),
                encoding="utf-8",
            )

            self.assertIsNone(_steam_last_owner(game))

            (steamapps / "appmanifest_002.acf").write_text(
                '\n'.join(
                    (
                        '"AppState"',
                        "{",
                        '    "installdir"  "gAmE a"',
                        '    "LastOwner"   "222"',
                        "}",
                    )
                ),
                encoding="utf-8",
            )

            self.assertEqual(_steam_last_owner(game), "222")

    def test_pixpil_corrupt_last_owner_does_not_import_another_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            steamapps = root / "steamapps"
            game, manifest, config, script = _write_synthetic_pixpil_game(
                steamapps / "common"
            )
            (steamapps / "appmanifest_123.acf").write_text(
                '\n'.join(
                    (
                        '"AppState"',
                        "{",
                        '    "appid"        "123"',
                        f'    "installdir"  "{game.name}"',
                        '    "LastOwner"    "111"',
                        "}",
                    )
                ),
                encoding="utf-8",
            )
            appdata = root / "AppData" / "Roaming"
            local_appdata = root / "AppData" / "Local"
            local_appdata.mkdir(parents=True)
            profiles = appdata / "SyntheticPixpil" / game.name
            corrupt_owner = profiles / "steam_111" / "input_settings"
            other_settings = profiles / "steam_222" / "input_settings"
            corrupt_owner.parent.mkdir(parents=True)
            other_settings.parent.mkdir(parents=True)
            corrupt_owner.write_bytes(bytes(64) + b"not-a-deflate-stream")
            other_settings.write_bytes(
                _pixpil_safe_settings(
                    {"mappings": {"keyboard": {"interaction": "n"}}}
                )
            )
            os.utime(corrupt_owner, (1_700_000_000, 1_700_000_000))
            os.utime(other_settings, (1_700_000_500, 1_700_000_500))

            with patch.dict(
                os.environ,
                {
                    "APPDATA": str(appdata),
                    "LOCALAPPDATA": str(local_appdata),
                },
            ):
                result = discover_pixpil_keymap(game)

            self.assertEqual(result.keymap["Space"]["action"], "interaction")
            self.assertNotIn("N", result.keymap)
            self.assertEqual(result.source_files, (manifest, config, script))

    def test_pixpil_regular_and_farm_overrides_are_scope_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game, _manifest, _config, _script = _write_synthetic_pixpil_game(
                root,
                farm_tables=[
                    [
                        ("interaction", "q"),
                        ("farm_only", "h"),
                        ("farm_aux", "j"),
                    ]
                ],
            )
            appdata = root / "AppData" / "Roaming"
            local_appdata = root / "AppData" / "Local"
            local_appdata.mkdir(parents=True)
            profile = appdata / "SyntheticPixpil" / game.name / "steam_123"
            regular_settings = profile / "input_settings"
            farm_settings = profile / "input_settings_farm"
            profile.mkdir(parents=True)
            regular_settings.write_bytes(
                _pixpil_safe_settings(
                    {"mappings": {"keyboard": {"interaction": "e"}}}
                )
            )

            environment = {
                "APPDATA": str(appdata),
                "LOCALAPPDATA": str(local_appdata),
            }
            with self.subTest("regular override preserves farm default"):
                with patch.dict(os.environ, environment):
                    regular_result = discover_pixpil_keymap(game)
                self.assertNotIn("Space", regular_result.keymap)
                self.assertEqual(regular_result.keymap["E"]["action"], "interaction")
                self.assertEqual(regular_result.keymap["Q"]["action"], "interaction")

            farm_settings.write_bytes(
                _pixpil_safe_settings(
                    {"mappings": {"keyboard": {"interaction": "f"}}}
                )
            )
            with self.subTest("regular and farm overrides retain the same action"):
                with patch.dict(os.environ, environment):
                    combined_result = discover_pixpil_keymap(game)
                self.assertNotIn("Space", combined_result.keymap)
                self.assertNotIn("Q", combined_result.keymap)
                self.assertEqual(combined_result.keymap["E"]["action"], "interaction")
                self.assertEqual(combined_result.keymap["F"]["action"], "interaction")

    def test_pixpil_multiscene_left_right_only_marks_directional_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game, _manifest, _config, script = _write_synthetic_pixpil_game(root)
            script_name = "0123456789abcdef0123456789abcdef"
            script.write_bytes(
                _pixpil_garchive(
                    {
                        script_name: _synthetic_pixpil_luajit(
                            mapping_configs=[
                                (
                                    "defaultInputMappingConfig",
                                    {
                                        "keyboard": [
                                            ("left", "a"),
                                            ("right", "d"),
                                        ],
                                        "joystick": [
                                            ("left", "lb"),
                                            ("right", "rb"),
                                        ],
                                    },
                                ),
                                (
                                    "farmDefaultInputMappingConfig",
                                    {
                                        "joystick": [
                                            ("left", "left"),
                                            ("right", "right"),
                                        ]
                                    },
                                ),
                            ]
                        )
                    }
                )
            )
            appdata = root / "AppData" / "Roaming"
            local_appdata = root / "AppData" / "Local"
            appdata.mkdir(parents=True)
            local_appdata.mkdir(parents=True)

            with patch.dict(
                os.environ,
                {
                    "APPDATA": str(appdata),
                    "LOCALAPPDATA": str(local_appdata),
                },
            ):
                result = discover_pixpil_keymap(game)

            self.assertEqual(result.keymap["gamepadLeftShoulder"]["action"], "left")
            self.assertEqual(result.keymap["gamepadRightShoulder"]["action"], "right")
            self.assertNotIn(
                "movement_direction", result.keymap["gamepadLeftShoulder"]
            )
            self.assertNotIn(
                "movement_direction", result.keymap["gamepadRightShoulder"]
            )
            self.assertEqual(result.keymap["A"]["movement_direction"], "L")
            self.assertEqual(result.keymap["D"]["movement_direction"], "R")
            self.assertEqual(
                result.keymap["gamepadDpadLeft"]["movement_direction"], "L"
            )
            self.assertEqual(
                result.keymap["gamepadDpadRight"]["movement_direction"], "R"
            )

    def test_pixpil_corrupt_player_settings_fall_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game, _manifest, _config, _script = _write_synthetic_pixpil_game(root)
            appdata = root / "AppData" / "Roaming"
            local_appdata = root / "AppData" / "Local"
            local_appdata.mkdir(parents=True)
            player_settings = (
                appdata
                / "SyntheticPixpil"
                / game.name
                / "steam_123"
                / "input_settings"
            )
            player_settings.parent.mkdir(parents=True)
            player_settings.write_bytes(bytes(64) + b"not-a-deflate-stream")

            with patch.dict(
                os.environ,
                {
                    "APPDATA": str(appdata),
                    "LOCALAPPDATA": str(local_appdata),
                },
            ):
                result = discover_pixpil_keymap(game)

            self.assertEqual(result.keymap["Space"]["action"], "interaction")
            self.assertEqual(result.keymap["Shift"]["action"], "running")
            self.assertEqual(result.keymap["leftClick"]["action"], "fire_A")
            self.assertNotIn("E", result.keymap)
            self.assertNotIn(player_settings, result.source_files)

    def test_pixpil_rejects_corrupt_archives_zstd_and_safe_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "test.g"
            archive.write_bytes(bytes(8))
            with self.assertRaises(ValueError):
                _read_pixpil_garchive(archive)

            archive.write_bytes(_pixpil_garchive({"entry": b"payload"}))
            archive_data, entries = _read_pixpil_garchive(archive)
            corrupt_data = bytearray(archive_data)
            entry = entries["entry"]
            corrupt_data[entry.offset : entry.offset + 4] = bytes(4)
            with self.assertRaises(ValueError):
                _extract_pixpil_garchive_entry(bytes(corrupt_data), entry)

            settings = root / "input_settings"
            settings.write_bytes(bytes(64) + b"not-a-deflate-stream")
            with self.assertRaises((ValueError, zlib.error)):
                _decode_pixpil_safe_settings(settings)

            game, _manifest, _config, script = _write_synthetic_pixpil_game(root)
            script.write_bytes(bytes(8))
            appdata = root / "EmptyAppData"
            local_appdata = root / "EmptyLocalAppData"
            appdata.mkdir()
            local_appdata.mkdir()
            with patch.dict(
                os.environ,
                {
                    "APPDATA": str(appdata),
                    "LOCALAPPDATA": str(local_appdata),
                },
            ):
                result = discover_pixpil_keymap(game)
            self.assertEqual(result.keymap, {})

    def test_pixpil_rejects_zstd_frame_with_trailing_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "trailing.g"
            name = b"entry"
            payload = b"valid-pixpil-entry"
            stored = zstandard.ZstdCompressor().compress(payload) + b"junk"
            offset = 8 + len(name) + 1 + 16
            archive.write_bytes(
                struct.pack("<II", 0x6A37, 1)
                + name
                + b"\0"
                + struct.pack("<IIII", offset, 2, len(payload), len(stored))
                + stored
            )
            archive_data, entries = _read_pixpil_garchive(archive)

            with self.assertRaises(ValueError):
                _extract_pixpil_garchive_entry(archive_data, entries["entry"])

    def test_pixpil_safe_settings_reject_deep_json_and_trailing_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deep_json = root / "deep_input_settings"
            deep_json.write_bytes(b"[" * 2_000 + b"0" + b"]" * 2_000)
            with self.subTest("deep JSON"):
                with self.assertRaises(ValueError):
                    _decode_pixpil_safe_settings(deep_json)

            trailing_data = root / "trailing_input_settings"
            trailing_data.write_bytes(
                _pixpil_safe_settings(
                    {"mappings": {"keyboard": {"interaction": "e"}}}
                )
                + b"trailing-data"
            )
            with self.subTest("raw-DEFLATE trailing data"):
                with self.assertRaises(ValueError):
                    _decode_pixpil_safe_settings(trailing_data)

    def test_discovers_valve_bind_cfg_with_action_descriptions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory)
            config = game / "examplemod" / "config.cfg"
            action_list = game / "examplemod" / "gfx" / "shell" / "kb_act.lst"
            config.parent.mkdir(parents=True)
            action_list.parent.mkdir(parents=True)
            config.write_text(
                '\n'.join(
                    (
                        'unbindall',
                        'bind "w" "+forward"',
                        'bind "s" "+back"',
                        'bind "MOUSE1" "+attack"',
                        'bind "INS" "+klook"',
                        'bind "KP_END" "slot1"',
                    )
                ),
                encoding="utf-8",
            )
            action_list.write_text(
                '\n'.join(
                    (
                        '"+forward" "Move forward"',
                        '"+back" "Move back"',
                        '"+attack" "Primary Attack"',
                    )
                ),
                encoding="utf-8",
            )

            result = discover_keymap_from_game_directory(game)
            self.assertEqual(result.keymap["W"]["action"], "Move forward")
            self.assertEqual(result.keymap["W"]["movement_direction"], "W")
            self.assertEqual(result.keymap["S"]["movement_direction"], "B")
            self.assertEqual(result.keymap["leftClick"]["action"], "Primary Attack")
            self.assertEqual(result.keymap["Insert"]["action"], "+klook")
            self.assertEqual(result.keymap["NumPad1"]["action"], "slot1")
            self.assertIn(config, result.source_files)
            self.assertIn(action_list, result.source_files)

    def test_discovers_valve_config_default_without_live_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory)
            config = game / "examplemod" / "config_default.cfg"
            config.parent.mkdir(parents=True)
            config.write_text(
                'bind "w" "+forward"\nbind "SPACE" "+jump"',
                encoding="utf-8",
            )

            result = discover_keymap_from_game_directory(game)

            self.assertEqual(result.keymap["W"]["movement_direction"], "W")
            self.assertEqual(result.keymap["Space"]["action"], "+jump")
            self.assertFalse(result.recognized_config)
            self.assertEqual(result.source_files, (config,))

    def test_valve_steam_cloud_cfg_replaces_install_and_applies_autoexec(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            steam = root / "steam"
            steamapps = steam / "steamapps"
            game = steamapps / "common" / "SyntheticSource"
            _write_steam_manifest(steamapps, game.name)
            install = game / "synthetic" / "config.cfg"
            labels = game / "synthetic" / "gfx" / "shell" / "kb_act.lst"
            install.parent.mkdir(parents=True)
            labels.parent.mkdir(parents=True)
            install.write_text(
                'unbindall\nbind "w" "+forward"',
                encoding="utf-8",
            )
            labels.write_text(
                '"+use" "Use"\n"+attack" "Attack"',
                encoding="utf-8",
            )
            cloud = (
                steam
                / "userdata"
                / "111"
                / "4242"
                / "remote"
                / "cfg"
                / "config.cfg"
            )
            autoexec = cloud.with_name("autoexec.cfg")
            cloud.parent.mkdir(parents=True)
            cloud.write_text(
                'unbindall\nbind "e" "+use"\nbind "MOUSE1" "+attack"',
                encoding="utf-8",
            )
            autoexec.write_text(
                'unbind "e"\nbind "f" "+use"',
                encoding="utf-8",
            )

            result = discover_keymap_from_game_directory(game)

            self.assertTrue(result.recognized_config)
            self.assertNotIn("W", result.keymap)
            self.assertNotIn("E", result.keymap)
            self.assertEqual(result.keymap["F"]["action"], "Use")
            self.assertEqual(result.keymap["leftClick"]["action"], "Attack")
            self.assertIn(cloud, result.source_files)
            self.assertIn(autoexec, result.source_files)
            self.assertNotIn(install, result.source_files)
            self.assertIn(labels, result.source_files)

    def test_steam_remote_nested_path_stays_inside_remote_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            steam = Path(directory) / "steam"
            config = (
                steam
                / "userdata"
                / "111"
                / "4242"
                / "remote"
                / "cfg"
                / "config.cfg"
            )
            config.parent.mkdir(parents=True)
            config.write_text('bind "w" "+forward"', encoding="utf-8")

            self.assertEqual(
                recorder._steam_remote_candidate(
                    steam,
                    "111",
                    "4242",
                    "cfg/config.cfg",
                ),
                config.resolve(),
            )
            self.assertIsNone(
                recorder._steam_remote_candidate(
                    steam,
                    "111",
                    "4242",
                    "../config.cfg",
                )
            )
            self.assertIsNone(
                recorder._steam_remote_candidate(
                    steam,
                    "111",
                    "4242",
                    str(config.resolve()),
                )
            )

    def test_discovers_default_keys_from_recursive_pdf_manual(self) -> None:
        manual_text = """GAME CONTROL
Left Alt
• Dog view
ESC
• Pause Menu
• Exit
↑ ↓ ← →
• Move
Space
• Interaction
F1
• Hint
F2
• Diaries
Left Shift
• Throw
D
• Melee Attack
1,2,3,4
• Dog order
Tab
• Wiki Page
"""

        class FakePage:
            def extract_text(self) -> str:
                return manual_text

        class FakeReader:
            is_encrypted = False
            pages = [FakePage()]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manual = root / "Support" / "Manual" / "English" / "Manual.pdf"
            manual.parent.mkdir(parents=True)
            manual.write_bytes(b"%PDF-test")
            with patch("pypdf.PdfReader", return_value=FakeReader()):
                result = discover_keymap_from_game_directory(root)

            self.assertEqual(result.keymap["Alt"]["action"], "Dog view")
            self.assertEqual(result.keymap["Esc"]["action"], "Pause Menu / Exit")
            self.assertEqual(result.keymap["Up"]["movement_direction"], "W")
            self.assertEqual(result.keymap["Down"]["movement_direction"], "B")
            self.assertEqual(result.keymap["Left"]["movement_direction"], "L")
            self.assertEqual(result.keymap["Right"]["movement_direction"], "R")
            self.assertEqual(result.keymap["D"]["action"], "Melee Attack")
            self.assertEqual(result.keymap["4"]["action"], "Dog order")
            self.assertEqual(len(result.keymap), 16)
            self.assertEqual(result.source_files, (manual,))

    def test_manual_table_requires_and_respects_column_headers(self) -> None:
        ambiguous = _parse_manual_control_text(
            "GAME CONTROL\nPause    Esc\nStart    Enter\nSelect    Tab\n"
        )
        self.assertEqual(ambiguous, {})

        action_first = _parse_manual_control_text(
            "GAME CONTROL\nAction    Key\nPause    Esc\nJump    Space\nMap    Tab\n"
        )
        self.assertEqual(action_first["Esc"]["action"], "Pause")
        self.assertEqual(action_first["Space"]["action"], "Jump")
        self.assertEqual(action_first["Tab"]["action"], "Map")

    def test_combined_startup_settings_accepts_path_and_duration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            minimum = parse_startup_settings(f'"{root}"', "60")
            maximum = parse_startup_settings(str(root), "900")
            self.assertEqual(minimum.save_directory, root)
            self.assertEqual(minimum.segment_seconds, 60)
            self.assertEqual(maximum.segment_seconds, 900)

    def test_combined_startup_settings_rejects_invalid_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for duration in ("", "59", "901", "60.5"):
                with self.subTest(duration=duration):
                    with self.assertRaises(ValueError):
                        parse_startup_settings(str(root), duration)
            with self.assertRaises(ValueError):
                parse_startup_settings(str(root / "missing"), "60")
            with self.assertRaises(ValueError):
                parse_startup_settings("", "60")

    def test_current_monitor_is_selected_from_cursor_position(self) -> None:
        monitors = [
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": -2560, "top": 0, "width": 2560, "height": 1440},
        ]
        target = capture_target_for_point(monitors, (-100, 500))
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.monitor_index, 2)
        self.assertEqual((target.left, target.width, target.height), (-2560, 2560, 1440))

    def test_current_monitor_falls_back_to_first_display(self) -> None:
        monitors = [{"left": 0, "top": 0, "width": 1920, "height": 1080}]
        target = capture_target_for_point(monitors, None)
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.monitor_index, 1)
        self.assertIsNone(capture_target_for_point([], (0, 0)))

    def test_clean_window_gate_checks_width_and_height_independently(self) -> None:
        target = recorder.CaptureTarget("显示器", 1, 0, 0, 1920, 1080)

        width_ratio, height_ratio = recorder.rectangle_dimension_coverage(
            (0, 0, 1344, 756),
            target,
        )
        self.assertAlmostEqual(width_ratio, 0.70)
        self.assertAlmostEqual(height_ratio, 0.70)

        with patch.object(recorder, "window_process_id", return_value=200):
            with patch.object(
                recorder,
                "window_client_rect",
                return_value=(0, 0, 1344, 756),
            ):
                self.assertTrue(
                    recorder.window_meets_capture_dimensions(
                        123,
                        target,
                        excluded_process_id=100,
                    )
                )
            with patch.object(
                recorder,
                "window_client_rect",
                return_value=(0, 0, 1343, 756),
            ):
                self.assertFalse(
                    recorder.window_meets_capture_dimensions(123, target)
                )
            with patch.object(
                recorder,
                "window_client_rect",
                return_value=(0, 0, 1344, 755),
            ):
                self.assertFalse(
                    recorder.window_meets_capture_dimensions(123, target)
                )

        with patch.object(recorder, "window_process_id", return_value=100):
            self.assertFalse(
                recorder.window_meets_capture_dimensions(
                    123,
                    target,
                    excluded_process_id=100,
                )
            )

    def test_monitor_recording_arms_once_then_starts_after_game_returns(self) -> None:
        class FakeRoot:
            def __init__(self) -> None:
                self.callbacks: list[object] = []

            def after(self, _delay: int, callback: object) -> None:
                self.callbacks.append(callback)

        floating = recorder.FloatingRecorder.__new__(recorder.FloatingRecorder)
        floating.root = FakeRoot()
        floating.capture_target = recorder.CaptureTarget(
            "显示器",
            1,
            0,
            0,
            1920,
            1080,
        )
        floating.state = "idle"
        floating.status_detail = ""
        floating.arming_deadline = 0.0
        floating.arming_generation = 0
        floating._draw = lambda: None
        notices: list[str] = []
        starts: list[bool] = []
        floating._show_discarded_notice = notices.append

        def begin_recording() -> None:
            starts.append(True)
            floating.state = "recording"

        floating._begin_recording = begin_recording

        with (
            patch.object(recorder, "foreground_window", return_value=10),
            patch.object(
                recorder,
                "window_meets_capture_dimensions",
                return_value=False,
            ),
            patch.object(recorder.time, "monotonic", return_value=100.0),
            patch.object(recorder.messagebox, "showinfo") as showinfo,
        ):
            floating._start_recording()
            showinfo.assert_not_called()

        self.assertEqual(floating.state, "arming")
        self.assertEqual(len(floating.root.callbacks), 1)
        callback = floating.root.callbacks.pop()
        self.assertTrue(callable(callback))
        with (
            patch.object(recorder, "foreground_window", return_value=20),
            patch.object(
                recorder,
                "window_meets_capture_dimensions",
                return_value=True,
            ),
        ):
            callback()

        self.assertEqual(floating.state, "recording")
        self.assertEqual(starts, [True])
        self.assertEqual(notices, [])

    def test_monitor_recording_arming_times_out_without_blocking_popup(self) -> None:
        class FakeRoot:
            def __init__(self) -> None:
                self.callbacks: list[object] = []

            def after(self, _delay: int, callback: object) -> None:
                self.callbacks.append(callback)

        floating = recorder.FloatingRecorder.__new__(recorder.FloatingRecorder)
        floating.root = FakeRoot()
        floating.capture_target = recorder.CaptureTarget(
            "显示器",
            1,
            0,
            0,
            1920,
            1080,
        )
        floating.state = "idle"
        floating.status_detail = ""
        floating.arming_deadline = 0.0
        floating.arming_generation = 0
        floating._draw = lambda: None
        notices: list[str] = []
        floating._show_discarded_notice = notices.append

        with (
            patch.object(recorder, "foreground_window", return_value=None),
            patch.object(
                recorder,
                "window_meets_capture_dimensions",
                return_value=False,
            ),
            patch.object(recorder.time, "monotonic", return_value=100.0),
        ):
            floating._start_recording()

        callback = floating.root.callbacks.pop()
        with (
            patch.object(recorder, "foreground_window", return_value=None),
            patch.object(
                recorder,
                "window_meets_capture_dimensions",
                return_value=False,
            ),
            patch.object(recorder.time, "monotonic", return_value=111.0),
            patch.object(recorder.messagebox, "showinfo") as showinfo,
        ):
            callback()
            showinfo.assert_not_called()

        self.assertEqual(floating.state, "idle")
        self.assertEqual(len(notices), 1)

    def test_native_top_level_handle_uses_win32_root_ancestor(self) -> None:
        class FakeCall:
            def __init__(self, result: int) -> None:
                self.result = result
                self.argtypes: object = None
                self.restype: object = None

            def __call__(self, *_args: object) -> int:
                return self.result

        fake_user32 = type(
            "FakeUser32",
            (),
            {"GetAncestor": FakeCall(456)},
        )()
        fake_windll = type("FakeWindll", (), {"user32": fake_user32})()
        with (
            patch.object(recorder.sys, "platform", "win32"),
            patch.object(recorder.ctypes, "windll", fake_windll),
        ):
            self.assertEqual(recorder.native_top_level_window_handle(123), 456)

    def test_discovers_unreal_engine_input_ini(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "Config"
            config.mkdir()
            (config / "DefaultInput.ini").write_text(
                "\n".join(
                    [
                        '+AxisMappings=(AxisName="MoveForward",Scale=1.000000,Key=W)',
                        '+AxisMappings=(AxisName="MoveForward",Scale=-1.000000,Key=S)',
                        '+AxisMappings=(AxisName="MoveRight",Scale=-1.000000,Key=A)',
                        '+ActionMappings=(ActionName="Jump",Key=SpaceBar)',
                        '+ActionMappings=(ActionName="Fire",Key=LeftMouseButton)',
                    ]
                ),
                encoding="utf-8",
            )
            result = discover_keymap_from_game_directory(root)
            self.assertEqual(result.keymap["W"]["movement_direction"], "W")
            self.assertEqual(result.keymap["S"]["movement_direction"], "B")
            self.assertEqual(result.keymap["A"]["movement_direction"], "L")
            self.assertEqual(result.keymap["Space"]["action"], "Jump")
            self.assertEqual(result.keymap["leftClick"]["action"], "Fire")
            self.assertEqual(len(result.source_files), 1)

    def test_unreal_localappdata_profile_overrides_defaults_and_keeps_chords(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "Installed Game"
            project = game / "GameProject"
            executable = (
                project
                / "Binaries"
                / "Win64"
                / "SquadGame-Win64-Shipping.exe"
            )
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"")
            default_input = project / "Config" / "DefaultInput.ini"
            default_input.parent.mkdir(parents=True)
            default_input.write_text(
                "\n".join(
                    (
                        '+ActionMappings=(ActionName="Jump",Key=SpaceBar)',
                        '+ActionMappings=(ActionName="Interact",Key=E)',
                        '+ActionMappings=(ActionName="QuickSlot",Key=One)',
                    )
                ),
                encoding="utf-8",
            )
            local_appdata = root / "LocalAppData"
            player_input = (
                local_appdata
                / "SquadGame"
                / "Saved"
                / "Config"
                / "WindowsNoEditor"
                / "Input.ini"
            )
            player_input.parent.mkdir(parents=True)
            player_input.write_text(
                "\n".join(
                    (
                        '-ActionMappings=(ActionName="Jump",Key=SpaceBar)',
                        '+ActionMappings=(ActionName="Jump",Key=J)',
                        '+ActionMappings=(ActionName="Command",bShift=True,bCtrl=True,bAlt=False,bCmd=False,Key=K)',
                        '+ActionMappings=(ActionName="System",bShift=False,bCtrl=False,bAlt=False,bCmd=True,Key=P)',
                    )
                ),
                encoding="utf-8",
            )
            unrelated = (
                local_appdata
                / "UnrelatedGame"
                / "Saved"
                / "Config"
                / "Windows"
                / "Input.ini"
            )
            unrelated.parent.mkdir(parents=True)
            unrelated.write_text(
                '+ActionMappings=(ActionName="WrongGame",Key=H)',
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {"LOCALAPPDATA": str(local_appdata)},
            ):
                result = discover_keymap_from_game_directory(game)

            self.assertTrue(result.recognized_config)
            self.assertNotIn("Space", result.keymap)
            self.assertEqual(result.keymap["J"]["action"], "Jump")
            self.assertEqual(
                result.keymap["Ctrl+Shift+K"]["action"],
                "Command",
            )
            self.assertEqual(result.keymap["Win+P"]["action"], "System")
            self.assertEqual(result.keymap["E"]["action"], "Interact")
            self.assertEqual(result.keymap["1"]["action"], "QuickSlot")
            self.assertNotIn("H", result.keymap)
            self.assertIn(default_input, result.source_files)
            self.assertIn(player_input.resolve(), result.source_files)
            self.assertNotIn(unrelated, result.source_files)

    def test_unreal_player_input_without_mappings_reports_saved_state(self) -> None:
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
            player_input = (
                local_appdata
                / "SyntheticProject"
                / "Saved"
                / "Config"
                / "Windows"
                / "Input.ini"
            )
            player_input.parent.mkdir(parents=True)
            player_input.write_text(
                "[/Script/Engine.InputSettings]\r\n; created on first run\r\n",
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {"LOCALAPPDATA": str(local_appdata)},
            ):
                unreal = recorder.discover_unreal_keymap(game)
                result = discover_keymap_from_game_directory(game)

            self.assertEqual(unreal.keymap, {})
            self.assertFalse(unreal.recognized_config)
            self.assertEqual(unreal.source_files, (player_input.resolve(),))
            self.assertIn("仅启动并退出游戏不一定会保存默认键位", unreal.notice)
            self.assertEqual(result.keymap, {})
            self.assertFalse(result.recognized_config)
            self.assertFalse(result.requires_game_launch)
            self.assertEqual(result.notice, unreal.notice)
            self.assertIn(player_input.resolve(), result.source_files)

    def test_discovers_unity_input_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = {
                "maps": [
                    {
                        "name": "Gameplay",
                        "actions": [
                            {"name": "Move", "id": "move-id"},
                            {"name": "Fire", "id": "fire-id"},
                            {"name": "Jump", "id": "jump-id"},
                        ],
                        "bindings": [
                            {
                                "name": "up",
                                "path": "<Keyboard>/w",
                                "action": "move-id",
                            },
                            {
                                "path": "<Mouse>/leftButton",
                                "action": "fire-id",
                            },
                            {
                                "path": "<Gamepad>/buttonSouth",
                                "action": "jump-id",
                            },
                            {
                                "name": "up",
                                "path": "<Gamepad>/dpad/up",
                                "action": "move-id",
                            },
                            {
                                "path": "<Mouse>/scroll/y",
                                "action": "fire-id",
                            },
                        ],
                    }
                ]
            }
            (root / "PlayerControls.inputactions").write_text(
                json.dumps(document), encoding="utf-8"
            )
            result = discover_keymap_from_game_directory(root)
            self.assertEqual(result.keymap["W"]["movement_direction"], "W")
            self.assertEqual(result.keymap["leftClick"]["action"], "Fire")
            self.assertEqual(result.keymap["gamepadA"]["action"], "Jump")
            self.assertEqual(
                result.keymap["gamepadDpadUp"]["movement_direction"], "W"
            )
            self.assertNotIn("Y", result.keymap)

    def test_discovers_legacy_unity_input_manager(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "ProjectSettings"
            settings.mkdir()
            (settings / "InputManager.asset").write_text(
                """InputManager:
  m_Axes:
  - serializedVersion: 3
    m_Name: Horizontal
    negativeButton: a
    positiveButton: d
    altNegativeButton: left
    altPositiveButton: right
  - serializedVersion: 3
    m_Name: Fire
    negativeButton:
    positiveButton: mouse 0
    altNegativeButton:
    altPositiveButton:
""",
                encoding="utf-8",
            )
            result = discover_keymap_from_game_directory(root)
            self.assertEqual(result.keymap["A"]["movement_direction"], "L")
            self.assertEqual(result.keymap["D"]["movement_direction"], "R")
            self.assertEqual(result.keymap["leftClick"]["action"], "Fire")

    def test_discovers_idtech_source2_and_static_script_keymaps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            idtech = root / "IdTechGame"
            idtech.mkdir()
            (idtech / "q3config.cfg").write_text(
                'bind w "+forward"\nbind s "+back"\nbind mouse1 "+attack"\n',
                encoding="utf-8",
            )
            idtech_result = discover_keymap_from_game_directory(idtech)
            self.assertTrue(idtech_result.recognized_config)
            self.assertEqual(idtech_result.keymap["W"]["movement_direction"], "W")
            self.assertEqual(idtech_result.keymap["leftClick"]["action"], "Attack")

            source2 = root / "Source2Game"
            source2.mkdir()
            (source2 / "cs2_user_keys_0_slot0.vcfg").write_text(
                '"config" { "bindings" { "W" "+forward" "SPACE" "+jump" "MOUSE1" "+attack" } }',
                encoding="utf-8",
            )
            source2_result = discover_keymap_from_game_directory(source2)
            self.assertTrue(source2_result.recognized_config)
            self.assertEqual(source2_result.keymap["Space"]["action"], "Jump")

            scripted = root / "ScriptedGame"
            scripted.mkdir()
            (scripted / "controls.lua").write_text(
                'controls = { move_up = "w", jump = "space", attack = "mouse1" }',
                encoding="utf-8",
            )
            scripted_result = discover_keymap_from_game_directory(scripted)
            self.assertEqual(scripted_result.keymap["W"]["movement_direction"], "W")
            self.assertEqual(scripted_result.keymap["Space"]["action"], "Jump")
            self.assertEqual(scripted_result.keymap["leftClick"]["action"], "Attack")

    def test_empty_portable_player_xml_remains_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "Config" / "KeyPrefs.xml"
            config.parent.mkdir()
            config.write_text("<KeyPrefs />", encoding="utf-8")

            result = discover_keymap_from_game_directory(root)

            self.assertTrue(result.recognized_config)
            self.assertEqual(result.keymap, {})
            self.assertEqual(result.source_files, (config,))

    def test_game_directory_without_input_config_returns_empty_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "readme.txt").write_text("no input settings", encoding="utf-8")
            result = discover_keymap_from_game_directory(root)
            self.assertEqual(result.keymap, {})
            self.assertEqual(result.scanned_files, 0)
            self.assertFalse(result.truncated)
            self.assertTrue(result.requires_game_launch)
            self.assertIn("请先启动游戏", result.notice)

    def test_bitrate_is_above_document_floor(self) -> None:
        self.assertEqual(target_bitrate(1920, 1080), 24_000_000)
        self.assertEqual(target_bitrate(2560, 1440), 36_000_000)
        self.assertEqual(target_bitrate(3840, 2160), 60_000_000)
        self.assertEqual(minimum_required_bitrate(1920, 1080), 8_000_000)
        self.assertEqual(minimum_required_bitrate(2560, 1440), 24_000_000)
        self.assertEqual(minimum_required_bitrate(3840, 2160), 35_000_000)

    def test_long_movement_is_split_at_30_seconds(self) -> None:
        item = tracker()
        intervals = [{"direction": "W", "start_time": 0.0, "end_time": 60.0}]
        result = item._movement_for_segment(intervals, 0)
        self.assertEqual(len(result), 2)
        self.assertTrue(
            all(event["end_time"] - event["start_time"] <= 30 for event in result)
        )

    def test_gamepad_sticks_feed_movement_and_looking(self) -> None:
        item = tracker()
        item.gamepad_frames[0] = {
            "left_x": 0.8,
            "left_y": 0.9,
            "right_x": -0.5,
            "right_y": 0.25,
        }
        self.assertEqual(item._gamepad_movement_direction(item.gamepad_frames[0]), "WR")
        looking = item._looking_for_segment(0)[0]
        self.assertEqual(looking["source"], "gamepad_right_stick")
        self.assertLess(looking["dx"], 0)
        self.assertLess(looking["dy"], 0)

    def test_keyboard_chords_resolve_actions_and_movement_from_held_modifiers(self) -> None:
        item = InputEventTracker(
            SessionConfig(
                "测试游戏",
                {
                    "K": {"type": "keyboard", "action": "Plain"},
                    "Ctrl+K": {"type": "keyboard", "action": "Command"},
                    "Shift+I": {
                        "type": "keyboard",
                        "action": "Move up",
                        "movement_direction": "W",
                    },
                },
            ),
            "session",
            datetime.now().astimezone(),
            60,
            0,
            0,
            1920,
            1080,
            100.0,
            "test",
            "monitor",
        )
        item.keyboard_events.extend(
            [
                {"global_time": 0.1, "key": "K", "pressed": True},
                {"global_time": 0.15, "key": "K", "pressed": False},
                {"global_time": 0.2, "key": "Ctrl", "pressed": True},
                {"global_time": 0.25, "key": "K", "pressed": True},
                {"global_time": 0.3, "key": "K", "pressed": False},
                {"global_time": 0.35, "key": "Ctrl", "pressed": False},
                {"global_time": 0.4, "key": "Shift", "pressed": True},
                {"global_time": 0.45, "key": "I", "pressed": True},
                {"global_time": 0.55, "key": "I", "pressed": False},
                {"global_time": 0.6, "key": "Shift", "pressed": False},
            ]
        )

        complete_keymap = item._complete_keymap()
        actions = item._action_for_segment(0, complete_keymap)
        self.assertEqual([event["action"] for event in actions], ["Plain", "Command"])
        self.assertEqual(actions[1]["key_chord"], "Ctrl+K")
        self.assertEqual(actions[1]["keyboard_event"], "Ctrl+K")
        self.assertEqual(actions[1]["physical_key"], "K")
        self.assertEqual(set(complete_keymap), {"K", "Ctrl+K", "Shift+I"})
        movement = item._build_global_movement_events(1.0)
        self.assertEqual(movement[0]["direction"], "W")
        self.assertAlmostEqual(movement[0]["start_time"], 0.4666666667)
        self.assertAlmostEqual(movement[0]["end_time"], 0.5666666667)
        ambiguous = {
            "K": {"type": "keyboard", "action": "Plain"},
            "Ctrl+K": {"type": "keyboard", "action": "Ctrl action"},
            "Alt+K": {"type": "keyboard", "action": "Alt action"},
        }
        self.assertIsNone(
            item._keyboard_mapping_key("K", {"Ctrl", "Alt", "K"}, ambiguous)
        )
        ambiguous["Ctrl+Alt+K"] = {
            "type": "keyboard",
            "action": "Exact action",
        }
        self.assertEqual(
            item._keyboard_mapping_key("K", {"Ctrl", "Alt", "K"}, ambiguous),
            "Ctrl+Alt+K",
        )

    def test_mouse_chords_use_modifiers_held_at_each_event_time(self) -> None:
        item = InputEventTracker(
            SessionConfig(
                "test game",
                {
                    "leftClick": {"type": "mouse", "action": "Plain attack"},
                    "Shift+leftClick": {
                        "type": "mouse",
                        "action": "Heavy attack",
                    },
                    "mouseWheelUp": {
                        "type": "mouse",
                        "action": "Previous item",
                    },
                    "Ctrl+Alt+mouseWheelUp": {
                        "type": "mouse",
                        "action": "Special item",
                    },
                },
            ),
            "session",
            datetime.now().astimezone(),
            60,
            0,
            0,
            1920,
            1080,
            100.0,
            "test",
            "monitor",
        )
        item.keyboard_events.extend(
            [
                {"global_time": 0.1, "key": "Shift", "pressed": True},
                {"global_time": 0.3, "key": "Shift", "pressed": False},
                {"global_time": 0.5, "key": "Ctrl", "pressed": True},
                {"global_time": 0.6, "key": "Alt", "pressed": True},
                {"global_time": 0.8, "key": "Alt", "pressed": False},
                # Leave Shift held at the end to prove that matching does not
                # use the final keyboard state for earlier mouse events.
                {"global_time": 1.0, "key": "Shift", "pressed": True},
            ]
        )
        item.mouse_button_events.extend(
            [
                {"global_time": 0.2, "button": "leftClick", "pressed": True},
                {"global_time": 0.4, "button": "leftClick", "pressed": True},
            ]
        )
        item.mouse_scroll_events.extend(
            [
                {"global_time": 0.7, "dx": 0, "dy": 120, "x": 0, "y": 0},
                {"global_time": 0.9, "dx": 0, "dy": 120, "x": 0, "y": 0},
            ]
        )

        actions = item._action_for_segment(0, item._complete_keymap())

        self.assertEqual(
            [event["action"] for event in actions],
            ["Heavy attack", "Plain attack", "Special item", "Previous item"],
        )
        self.assertEqual(
            [event["key_chord"] for event in actions],
            ["Shift+leftClick", "", "Ctrl+Alt+mouseWheelUp", ""],
        )
        self.assertEqual(
            [event["mouse_event"] for event in actions],
            ["leftClick", "leftClick", "mouseWheelUp", "mouseWheelUp"],
        )

    def test_mouse_chord_physical_parts_are_retained_in_raw_input(self) -> None:
        item = InputEventTracker(
            SessionConfig(
                "test game",
                {
                    "Ctrl+mouseButton4": {
                        "type": "mouse",
                        "action": "Ping",
                    },
                    "Win+mouseWheelDown": {
                        "type": "mouse",
                        "action": "Zoom out",
                    },
                },
            ),
            "session",
            datetime.now().astimezone(),
            60,
            0,
            0,
            1920,
            1080,
            100.0,
            "test",
            "monitor",
        )
        item.keyboard_events.extend(
            [
                {"global_time": 0.1, "key": "Ctrl", "pressed": True},
                {"global_time": 0.3, "key": "Ctrl", "pressed": False},
                {"global_time": 0.4, "key": "Win", "pressed": True},
            ]
        )
        item.mouse_button_events.append(
            {"global_time": 0.2, "button": "mouseButton4", "pressed": True}
        )
        item.mouse_scroll_events.append(
            {"global_time": 0.5, "dx": 0, "dy": -120, "x": 0, "y": 0}
        )

        self.assertEqual(
            item._configured_physical_inputs(),
            {"Ctrl", "mouseButton4", "Win", "mouseWheelDown"},
        )
        raw = item._raw_for_segment(0)
        self.assertEqual(
            {event["key"] for event in raw["keyboard_events"]},
            {"Ctrl", "Win"},
        )
        self.assertEqual(
            [event["button"] for event in raw["mouse_button_events"]],
            ["mouseButton4"],
        )
        self.assertEqual(len(raw["mouse_scroll_events"]), 1)

    def test_unconfigured_inputs_do_not_pollute_raw_actions_or_keymap(self) -> None:
        item = InputEventTracker(
            SessionConfig(
                "测试游戏",
                {
                    "F1": {"type": "keyboard", "action": "Shortcut panel"},
                    "Shift": {"type": "keyboard", "action": "Sprint"},
                    "leftClick": {"type": "mouse", "action": "Attack"},
                    "mouseWheelUp": {"type": "mouse", "action": "Previous item"},
                    "gamepadA": {"type": "gamepad", "action": "Jump"},
                },
            ),
            "session",
            datetime.now().astimezone(),
            60,
            0,
            0,
            1920,
            1080,
            100.0,
            "test",
            "monitor",
        )
        item.keyboard_events.extend(
            [
                {"global_time": 1.0, "key": "F1", "pressed": True},
                {"global_time": 1.1, "key": "F1", "pressed": False},
                {"global_time": 1.2, "key": "Win", "pressed": True},
                {"global_time": 1.3, "key": "Win", "pressed": False},
                {"global_time": 1.4, "key": "Shift", "pressed": True},
                {"global_time": 1.5, "key": "Shift", "pressed": False},
            ]
        )
        item.mouse_button_events.extend(
            [
                {"global_time": 2.0, "button": "leftClick", "pressed": True},
                {"global_time": 2.1, "button": "rightClick", "pressed": True},
            ]
        )
        item.mouse_scroll_events.extend(
            [
                {"global_time": 3.0, "dx": 0, "dy": 120, "x": 0, "y": 0},
                {"global_time": 3.1, "dx": 0, "dy": -120, "x": 0, "y": 0},
            ]
        )
        item.gamepad_button_events.extend(
            [
                {
                    "global_time": 4.0,
                    "controller": 0,
                    "button": "gamepadA",
                    "pressed": True,
                },
                {
                    "global_time": 4.1,
                    "controller": 0,
                    "button": "gamepadB",
                    "pressed": True,
                },
            ]
        )

        completed = item._complete_keymap()
        self.assertEqual(set(completed), set(item.session.keymap))
        self.assertNotIn("Win", completed)
        actions = item._action_for_segment(0, completed)
        self.assertEqual(
            {event["action"] for event in actions},
            {"Shortcut panel", "Sprint", "Attack", "Previous item", "Jump"},
        )
        raw = item._raw_for_segment(0)
        self.assertEqual(
            {event["key"] for event in raw["keyboard_events"]},
            {"F1", "Shift"},
        )
        self.assertEqual(
            {event["button"] for event in raw["mouse_button_events"]},
            {"leftClick"},
        )
        self.assertEqual(len(raw["mouse_scroll_events"]), 1)
        self.assertEqual(
            {event["button"] for event in raw["gamepad_button_events"]},
            {"gamepadA"},
        )

    def test_gamepad_triggers_generate_button_events(self) -> None:
        item = tracker()
        trigger_value = {"left": 100}

        def fake_get_state(index: int, pointer: object) -> int:
            if index:
                return 1167
            state = ctypes.cast(pointer, ctypes.POINTER(XInputState)).contents
            state.gamepad.left_trigger = trigger_value["left"]
            return 0

        item.xinput_get_state = fake_get_state
        item._poll_gamepads(0, 0.0)
        trigger_value["left"] = 0
        item._poll_gamepads(1, 1 / 30)
        events = [
            event
            for event in item.gamepad_button_events
            if event["button"] == "gamepadLeftTrigger"
        ]
        self.assertEqual([event["pressed"] for event in events], [True, False])

    def test_compaction_preserves_held_key_state(self) -> None:
        item = tracker()
        item.keyboard_events = [
            {"global_time": 1.0, "key": "W", "pressed": True},
            {"global_time": 3.0, "key": "E", "pressed": True},
        ]
        item.compact_before(2.0)
        self.assertEqual(item.keyboard_events[0]["key"], "W")
        self.assertTrue(item.keyboard_events[0]["synthetic"])
        self.assertEqual(item.keyboard_events[0]["global_time"], 2.0)
        self.assertEqual(item.keyboard_events[1]["key"], "E")

    def test_compacted_modifier_state_applies_to_next_segment_mouse_chord(self) -> None:
        item = tracker()
        item.session = SessionConfig(
            "test game",
            {
                "Shift+leftClick": {
                    "type": "mouse",
                    "action": "Heavy attack",
                }
            },
        )
        item.keyboard_events = [
            {"global_time": 59.0, "key": "Shift", "pressed": True}
        ]

        item.compact_before(60.0)
        item.mouse_button_events.append(
            {"global_time": 60.2, "button": "leftClick", "pressed": True}
        )

        actions = item._action_for_segment(1, item._complete_keymap())
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action"], "Heavy attack")
        self.assertEqual(actions[0]["key_chord"], "Shift+leftClick")

    def test_quality_monitor_accepts_well_paced_changing_frames(self) -> None:
        monitor = QualityMonitor()
        for index in range(30):
            frame = np.full((64, 64, 4), 20 + index, dtype=np.uint8)
            monitor.observe(frame, index / 30)
        report = monitor.reports(1, 30)[0]
        self.assertTrue(report["passed"])
        self.assertGreaterEqual(report["actual_capture_fps"], 29.9)
        self.assertFalse(hasattr(monitor, "frames"))

    def test_quality_monitor_warns_but_accepts_isolated_134ms_gap(self) -> None:
        monitor = QualityMonitor()
        frames_per_segment = 300
        gap_index = 150
        schedule_shift = 0.1341 - 1.0 / 30.0
        for index in range(frames_per_segment):
            captured_at = index / 30.0
            if index >= gap_index:
                captured_at += schedule_shift
            frame = np.full((64, 64, 4), 20 + index % 200, dtype=np.uint8)
            monitor.observe(frame, captured_at, frames_per_segment)

        report = monitor.report_for_segment(0)
        self.assertTrue(report["passed"])
        self.assertAlmostEqual(report["max_frame_gap_ms"], 134.1, places=2)
        self.assertEqual(report["capture_stall_failure_threshold_ms"], 700.0)
        self.assertEqual(len(report["warnings"]), 1)
        self.assertEqual(report["failures"], [])

    def test_quality_monitor_rejects_700ms_capture_stall(self) -> None:
        monitor = QualityMonitor()
        frames_per_segment = 300
        gap_index = 150
        schedule_shift = 0.7 - 1.0 / 30.0
        for index in range(frames_per_segment):
            captured_at = index / 30.0
            if index >= gap_index:
                captured_at += schedule_shift
            frame = np.full((64, 64, 4), 20 + index % 200, dtype=np.uint8)
            monitor.observe(frame, captured_at, frames_per_segment)

        report = monitor.report_for_segment(0)
        self.assertFalse(report["passed"])
        self.assertAlmostEqual(report["max_frame_gap_ms"], 700.0, places=2)
        self.assertTrue(
            any("700 ms 卡顿门限" in failure for failure in report["failures"])
        )

    def test_quality_monitor_warns_for_dynamic_3_77s_black_transition(self) -> None:
        monitor = QualityMonitor()
        frames_per_segment = 600
        black_start = 120
        black_frames = 113
        for index in range(frames_per_segment):
            if black_start <= index < black_start + black_frames:
                frame = np.full((64, 64, 4), 2, dtype=np.uint8)
                frame[0, 0, 0] = index % 4
            else:
                frame = np.full((64, 64, 4), 40 + index % 100, dtype=np.uint8)
            monitor.observe(frame, index / 30.0, frames_per_segment)

        report = monitor.report_for_segment(0)
        self.assertTrue(report["passed"])
        self.assertAlmostEqual(report["longest_black_run_seconds"], 3.767, places=3)
        self.assertEqual(report["black_frame_count"], 113)
        self.assertAlmostEqual(report["black_frame_ratio"], 113 / 600, places=6)
        self.assertTrue(
            any("游戏转场或加载页" in warning for warning in report["warnings"])
        )
        self.assertEqual(report["failures"], [])

    def test_quality_monitor_rejects_10s_dynamic_black_run(self) -> None:
        monitor = QualityMonitor()
        frames_per_segment = 600
        for index in range(frames_per_segment):
            if 120 <= index < 420:
                frame = np.full((64, 64, 4), 2, dtype=np.uint8)
                frame[0, 0, 0] = index % 4
            else:
                frame = np.full((64, 64, 4), 40 + index % 100, dtype=np.uint8)
            monitor.observe(frame, index / 30.0, frames_per_segment)

        report = monitor.report_for_segment(0)
        self.assertFalse(report["passed"])
        self.assertEqual(report["longest_black_run_seconds"], 10.0)
        self.assertTrue(
            any("达到 10 秒门限" in failure for failure in report["failures"])
        )

    def test_quality_monitor_accepts_6s_constant_black_transition_with_warnings(self) -> None:
        monitor = QualityMonitor()
        frames_per_segment = 600
        for index in range(frames_per_segment):
            if 120 <= index < 300:
                frame = np.zeros((64, 64, 4), dtype=np.uint8)
            else:
                frame = np.full((64, 64, 4), 40 + index % 100, dtype=np.uint8)
            monitor.observe(frame, index / 30.0, frames_per_segment)

        report = monitor.report_for_segment(0)
        self.assertTrue(report["passed"])
        self.assertAlmostEqual(report["longest_black_run_seconds"], 6.0)
        self.assertAlmostEqual(report["longest_frozen_run_seconds"], 5.967)
        self.assertEqual(len(report["warnings"]), 2)
        self.assertEqual(report["failures"], [])

    def test_quality_monitor_rejects_10s_constant_bright_frozen_run(self) -> None:
        monitor = QualityMonitor()
        frames_per_segment = 600
        frozen_frame = np.full((64, 64, 4), 80, dtype=np.uint8)
        for index in range(frames_per_segment):
            if 120 <= index < 421:
                frame = frozen_frame
            else:
                frame = np.full((64, 64, 4), 40 + index % 100, dtype=np.uint8)
            monitor.observe(frame, index / 30.0, frames_per_segment)

        report = monitor.report_for_segment(0)
        self.assertFalse(report["passed"])
        self.assertEqual(report["longest_frozen_run_seconds"], 10.0)
        self.assertTrue(
            any("达到 10 秒门限" in failure for failure in report["failures"])
        )

    def test_input_alignment_threshold_remains_100ms(self) -> None:
        self.assertEqual(recorder.MAX_INPUT_ALIGNMENT_ERROR_SECONDS, 0.1)

    def test_crash_recovery_keeps_committed_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = "game_session"
            committed = root / f"{prefix}_001.mp4"
            uncommitted = root / f"{prefix}_002.mp4"
            committed.write_bytes(b"ok")
            uncommitted.write_bytes(b"partial")
            manifest = root / f"{prefix}_manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "status": "recording",
                        "segments": [{"video": committed.name}],
                    }
                ),
                encoding="utf-8",
            )
            sessions, files = recover_interrupted_sessions(root)
            self.assertEqual((sessions, files), (1, 1))
            self.assertTrue(committed.exists())
            self.assertFalse(uncommitted.exists())
            self.assertTrue((root / "rejected" / uncommitted.name).exists())


if __name__ == "__main__":
    unittest.main()
