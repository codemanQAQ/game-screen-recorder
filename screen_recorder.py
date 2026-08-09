from __future__ import annotations

import ctypes
import bisect
import base64
import binascii
import hashlib
import itertools
import json
import math
import os
import queue
import re
import shutil
import subprocess
import struct
import sys
import threading
import time
import unicodedata
import uuid
import zipfile
import zlib
import xml.etree.ElementTree as ElementTree
from ctypes import wintypes
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import Menu, Tk, filedialog, messagebox, simpledialog
import tkinter as tk
from tkinter import ttk

import dxcam
import mss
import numpy as np
from common_xml_keymap import (
    is_common_xml_keymap_candidate,
    parse_common_xml_keymap,
)
from extra_keymap_parsers import (
    discover_structured_json_keymaps,
    parse_structured_json_keymap,
)
from foundation_registry_keymap import (
    discover_registry_keymap as discover_foundation_registry_keymap,
)
from fromsoftware_numeric_ini_keymap import (
    discover_numeric_ini_keymap as discover_fromsoftware_numeric_ini_keymap,
)
from keyvalues_cfg_keymap import (
    is_keyvalues_cfg_candidate,
    parse_keyvalues_cfg,
)
from player_config_paths import (
    PlayerConfigDiscovery,
    discover_player_config_files,
)
from pixpil_mapping import extract_mapping_configs
from script_keymap_parsers import (
    is_script_keymap_candidate,
    parse_script_keymap,
)
from static_ffmpeg import run as static_ffmpeg_run
from unreal_gvas_keymap import discover_gvas_player_keymap
from unreal_pak_keymap import (
    KrakenHelperConfig,
    discover_default_input_from_game_directory,
)
import zstandard


APP_NAME = "悬浮录屏"
APP_VERSION = "2.22.0"
SCHEMA_VERSION = "2.1"
FPS = 30.0
BUTTON_SIZE = 84
DRAG_THRESHOLD = 5
TRANSPARENT_COLOR = "#010203"
MIN_CLEAN_DIMENSION_RATIO = 0.70
ARMING_TIMEOUT_SECONDS = 10.0
ARMING_POLL_MILLISECONDS = 100
MAX_ENCODER_QUEUE_FRAMES = 8
ENCODER_QUEUE_MEMORY_BUDGET_BYTES = 160 * 1024**2
DEFAULT_SEGMENT_SECONDS = 60
MIN_SEGMENT_SECONDS = 60
MAX_SEGMENT_SECONDS = 900
MIN_WIDTH = 1920
MIN_HEIGHT = 1080
MIN_ACCEPTABLE_FPS = 24.0
MAX_INPUT_ALIGNMENT_ERROR_SECONDS = 0.1
FRAME_GAP_WARNING_SECONDS = 0.1
MAX_SINGLE_CAPTURE_STALL_SECONDS = 0.7
BLACK_RUN_WARNING_SECONDS = 3.0
BLACK_RUN_FAILURE_SECONDS = 10.0
FROZEN_RUN_WARNING_SECONDS = 3.0
FROZEN_RUN_FAILURE_SECONDS = 10.0
MIN_FREE_SPACE_BYTES = 5 * 1024**3
DISK_CHECK_INTERVAL_SECONDS = 5.0
GAMEPAD_TRIGGER_THRESHOLD = 30
FINALIZER_QUEUE_SIZE = 4
MAX_GAME_CONFIG_FILES = 500
MAX_GAME_CONFIG_FILE_BYTES = 8 * 1024**2
MAX_GAME_DIRECTORY_ENTRIES = 25_000
MAX_UNITY_CATALOG_BYTES = 64 * 1024**2
MAX_UNITY_INPUT_BUNDLE_BYTES = 512 * 1024**2
MAX_DOTNET_ASSEMBLY_BYTES = 64 * 1024**2
MAX_MANUAL_PDF_FILES = 16
MAX_MANUAL_PDF_CANDIDATES = 128
MAX_MANUAL_PDF_BYTES = 32 * 1024**2
MAX_MANUAL_PAGES = 40
MAX_MANUAL_TEXT_CHARS = 1_000_000
MAX_MANUAL_PAGE_STREAM_BYTES = 8 * 1024**2
MAX_MANUAL_SCAN_SECONDS = 12.0
MAX_PIXPIL_ARCHIVE_BYTES = 64 * 1024**2
MAX_PIXPIL_ARCHIVE_ENTRIES = 10_000
MAX_PIXPIL_ENTRY_BYTES = 32 * 1024**2
MAX_PIXPIL_LUA_BYTES = 2 * 1024**2
MAX_PIXPIL_LIBRARY_BYTES = 4 * 1024**2
MAX_PIXPIL_SCRIPT_CANDIDATES = 64
MAX_PIXPIL_SCAN_SECONDS = 8.0
MAX_PIXPIL_PROFILE_GROUPS = 8
MAX_PIXPIL_PROFILE_BYTES = 1024**2
MAX_PIXPIL_PROFILE_JSON_BYTES = 4 * 1024**2
MAX_PIXPIL_PROFILE_JSON_DEPTH = 64
MAX_LUAJIT_PROTOTYPES = 2_048
MAX_LUAJIT_CONSTANTS = 100_000
MAX_LUAJIT_TABLE_ITEMS = 10_000
MAX_INDEXED_XML_CONFIG_FILES = 64
MAX_INDEXED_XML_ELEMENTS = 512
MAX_INDEXED_XML_ACTION_SLOTS = 64
MAX_INDEXED_MENU_DICTIONARIES = 32
MAX_INDEXED_MENU_DICTIONARY_BYTES = 2 * 1024**2
MAX_INDEXED_MENU_DICTIONARY_ENTRIES = 4_096
MAX_INDEXED_MENU_STRING_CHARS = 4_096
MAX_INDEXED_MENU_TEXT_CHARS = 1_000_000
MAX_INDEXED_IDENTITY_SCAN_SECONDS = 4.0
MAX_STEAM_USERDATA_ACCOUNTS = 256
MAX_UNREAL_IDENTITY_NAMES = 64
MAX_UNREAL_LOCALAPPDATA_ENTRIES = 2_048
MAX_UNREAL_CONFIG_PLATFORMS = 32
STEAM_ID64_ACCOUNT_BASE = 76_561_197_960_265_728
DEFAULT_KEYMAP: dict[str, dict[str, str]] = {
    "W": {"type": "keyboard", "action": "前进", "movement_direction": "W"},
    "A": {"type": "keyboard", "action": "左移", "movement_direction": "L"},
    "S": {"type": "keyboard", "action": "后退", "movement_direction": "B"},
    "D": {"type": "keyboard", "action": "右移", "movement_direction": "R"},
    "Space": {"type": "keyboard", "action": "跳跃"},
    "E": {"type": "keyboard", "action": "交互"},
    "F": {"type": "keyboard", "action": "交互"},
    "R": {"type": "keyboard", "action": "装填或使用"},
    "Shift": {"type": "keyboard", "action": "冲刺"},
    "Ctrl": {"type": "keyboard", "action": "蹲伏"},
    "leftClick": {"type": "mouse", "action": "攻击"},
    "rightClick": {"type": "mouse", "action": "瞄准或次要操作"},
    "middleClick": {"type": "mouse", "action": "中键操作"},
    "mouseButton4": {"type": "mouse", "action": "鼠标侧键4"},
    "mouseButton5": {"type": "mouse", "action": "鼠标侧键5"},
    "mouseWheelUp": {"type": "mouse", "action": "滚轮向上"},
    "mouseWheelDown": {"type": "mouse", "action": "滚轮向下"},
    "gamepadA": {"type": "gamepad", "action": "确认或跳跃"},
    "gamepadB": {"type": "gamepad", "action": "取消或蹲伏"},
    "gamepadX": {"type": "gamepad", "action": "交互或装填"},
    "gamepadY": {"type": "gamepad", "action": "切换或使用"},
    "gamepadLeftShoulder": {"type": "gamepad", "action": "左肩键"},
    "gamepadRightShoulder": {"type": "gamepad", "action": "右肩键"},
    "gamepadLeftThumb": {"type": "gamepad", "action": "按下左摇杆"},
    "gamepadRightThumb": {"type": "gamepad", "action": "按下右摇杆"},
    "gamepadStart": {"type": "gamepad", "action": "开始或菜单"},
    "gamepadBack": {"type": "gamepad", "action": "返回或视图"},
    "gamepadDpadUp": {"type": "gamepad", "action": "方向键上"},
    "gamepadDpadDown": {"type": "gamepad", "action": "方向键下"},
    "gamepadDpadLeft": {"type": "gamepad", "action": "方向键左"},
    "gamepadDpadRight": {"type": "gamepad", "action": "方向键右"},
    "gamepadLeftTrigger": {"type": "gamepad", "action": "左扳机"},
    "gamepadRightTrigger": {"type": "gamepad", "action": "右扳机"},
}


def _is_link_or_junction_path(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction is not None and is_junction())
    except OSError:
        return True


def _prune_walk_links(
    current_root: str | os.PathLike[str],
    directory_names: list[str],
    file_names: list[str],
) -> None:
    base = Path(current_root)
    directory_names[:] = [
        name
        for name in directory_names
        if not _is_link_or_junction_path(base / name)
    ]
ARROW_KEY_KEYMAP: dict[str, dict[str, str]] = {
    **{
        key: dict(value)
        for key, value in DEFAULT_KEYMAP.items()
        if key not in {"W", "A", "S", "D"}
    },
    "Up": {"type": "keyboard", "action": "前进", "movement_direction": "W"},
    "Left": {"type": "keyboard", "action": "左移", "movement_direction": "L"},
    "Down": {"type": "keyboard", "action": "后退", "movement_direction": "B"},
    "Right": {"type": "keyboard", "action": "右移", "movement_direction": "R"},
}

_MEDIA_TOOLS: tuple[str, str] | None = None
_ENCODER_CHOICE: str | None = None


def media_tools() -> tuple[str, str]:
    global _MEDIA_TOOLS
    if _MEDIA_TOOLS is None:
        ffmpeg_path, ffprobe_path = (
            static_ffmpeg_run.get_or_fetch_platform_executables_else_raise()
        )
        _MEDIA_TOOLS = str(ffmpeg_path), str(ffprobe_path)
    return _MEDIA_TOOLS


def target_bitrate(width: int, height: int) -> int:
    """Return a visually lossless-oriented CBR target above the acceptance floor."""
    pixels = width * height
    if pixels <= 1920 * 1080:
        return 24_000_000
    if pixels <= 2560 * 1440:
        return 36_000_000
    return 60_000_000


def minimum_required_bitrate(width: int, height: int) -> int:
    pixels = width * height
    if pixels <= 1920 * 1080:
        return 8_000_000
    if pixels <= 2560 * 1440:
        return 24_000_000
    return 35_000_000


def encoder_queue_capacity(width: int, height: int) -> int:
    """Buffer short encoder stalls without unbounded 4K frame memory."""
    frame_bytes = max(1, width * height * 4)
    memory_limited_frames = max(
        1,
        ENCODER_QUEUE_MEMORY_BUDGET_BYTES // frame_bytes,
    )
    return min(MAX_ENCODER_QUEUE_FRAMES, memory_limited_frames)


def select_h264_encoder() -> str:
    """Probe hardware encoders once and fall back to libx264."""
    global _ENCODER_CHOICE
    if _ENCODER_CHOICE is not None:
        return _ENCODER_CHOICE
    ffmpeg_path, _ffprobe_path = media_tools()
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    probe_options = {
        "h264_nvenc": [
            "-preset",
            "p6",
            "-tune",
            "hq",
            "-rc",
            "cbr",
            "-multipass",
            "fullres",
            "-profile:v",
            "high",
        ],
        "h264_qsv": ["-preset", "medium", "-profile:v", "high"],
        "h264_amf": [
            "-quality",
            "quality",
            "-rc",
            "cbr",
            "-profile:v",
            "high",
        ],
    }
    for encoder_name in ("h264_nvenc", "h264_qsv", "h264_amf"):
        command = [
            ffmpeg_path,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=1280x720:rate=30:duration=0.1",
            "-frames:v",
            "1",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            encoder_name,
            *probe_options[encoder_name],
            "-b:v",
            "8000000",
            "-f",
            "null",
            "-",
        ]
        try:
            process = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if process.returncode == 0:
            _ENCODER_CHOICE = encoder_name
            return encoder_name
    _ENCODER_CHOICE = "libx264"
    return _ENCODER_CHOICE


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, document: object) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(document, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def recover_interrupted_sessions(save_directory: Path) -> tuple[int, int]:
    """Quarantine uncommitted files left by a crash and preserve committed triples."""
    recovered_sessions = 0
    quarantined_files = 0
    for manifest_path in save_directory.glob("*_manifest.json"):
        try:
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if document.get("status") != "recording":
            continue
        prefix = manifest_path.name.removesuffix("_manifest.json")
        committed_names: set[str] = set()
        for record in document.get("segments", []):
            if not isinstance(record, dict):
                continue
            for field in ("video", "operation_json", "keymap_json"):
                value = record.get(field)
                if isinstance(value, str):
                    committed_names.add(value)
        rejected_directory = save_directory / "rejected"
        rejected_directory.mkdir(exist_ok=True)
        moved: list[str] = []
        for path in save_directory.glob(f"{prefix}_*"):
            if (
                not path.is_file()
                or path == manifest_path
                or path.name in committed_names
            ):
                continue
            if path.suffix == ".tmp":
                path.unlink()
                continue
            if path.suffix not in {".mp4", ".json"}:
                continue
            destination = rejected_directory / path.name
            os.replace(path, destination)
            moved.append(destination.name)
            quarantined_files += 1
        document["status"] = "recovered_after_crash"
        document["error"] = "检测到上次进程未正常结束；未提交文件已移入rejected"
        document["recovered_files"] = moved
        document["updated_at"] = datetime.now().astimezone().isoformat(
            timespec="milliseconds"
        )
        atomic_write_json(manifest_path, document)
        recovered_sessions += 1
    return recovered_sessions, quarantined_files


def safe_file_component(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned[:60] or "未命名游戏"


def enable_high_dpi() -> None:
    """Keep Tk and captured screen coordinates in the same coordinate system."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


@dataclass(frozen=True)
class RecordingResult:
    kind: str
    path: Path | None = None
    message: str = ""


@dataclass(frozen=True)
class SessionConfig:
    game_title: str
    keymap: dict[str, dict[str, str]]


@dataclass(frozen=True)
class CapturedMapping:
    input_name: str
    device_type: str
    action: str


def _contains_latin_letter(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text)
    return any(
        character.isalpha()
        and "LATIN" in unicodedata.name(character, "")
        for character in normalized
    )


def _contains_chinese_character(text: str) -> bool:
    return any(
        "CJK UNIFIED IDEOGRAPH" in unicodedata.name(character, "")
        or "CJK COMPATIBILITY IDEOGRAPH" in unicodedata.name(character, "")
        for character in text
    )


def _action_semantic_chinese_issues(
    keymap: dict[str, dict[str, str]],
) -> list[tuple[str, str, str]]:
    """Return action fields that are not completely written in Chinese."""
    issues: list[tuple[str, str, str]] = []
    for input_name, mapping in keymap.items():
        action = mapping.get("action")
        if not isinstance(action, str):
            continue
        if _contains_latin_letter(action):
            issues.append((input_name, action, "含英文字母"))
        elif not _contains_chinese_character(action):
            issues.append((input_name, action, "未包含中文"))
    return issues


@dataclass(frozen=True)
class StartupSettings:
    save_directory: Path
    segment_seconds: int


def parse_startup_settings(
    save_directory_text: str,
    segment_seconds_text: str,
) -> StartupSettings:
    raw_directory = save_directory_text.strip().strip('"')
    if not raw_directory:
        raise ValueError("请选择录屏保存路径")
    directory = Path(os.path.expandvars(raw_directory)).expanduser()
    try:
        directory = directory.resolve()
    except OSError as exc:
        raise ValueError(f"无法解析保存路径：{exc}") from exc
    if not directory.exists():
        raise ValueError("保存路径不存在，请先选择已有文件夹")
    if not directory.is_dir():
        raise ValueError("保存路径必须是文件夹")
    try:
        segment_seconds = int(segment_seconds_text.strip())
    except ValueError as exc:
        raise ValueError("切片时长必须是整数秒") from exc
    if not MIN_SEGMENT_SECONDS <= segment_seconds <= MAX_SEGMENT_SECONDS:
        raise ValueError(
            f"切片时长必须在 {MIN_SEGMENT_SECONDS}–{MAX_SEGMENT_SECONDS} 秒之间"
        )
    return StartupSettings(directory, segment_seconds)


@dataclass(frozen=True)
class KeymapSource:
    """Structured provenance for one physical player/default source."""

    physical_path: Path
    kind: str
    virtual_path: str = ""
    contributes_bindings: bool = True


KEYMAP_AUTHORITY_UNKNOWN = "unknown"
KEYMAP_AUTHORITY_VERIFIED_PLAYER = "verified_player"
KEYMAP_AUTHORITY_DEFAULT_ONLY = "default_only"
KEYMAP_AUTHORITY_MIXED_UNVERIFIED = "mixed_unverified"
KEYMAP_APPLY_INFER = "infer"
KEYMAP_APPLY_REPLACE = "replace"
KEYMAP_APPLY_MERGE = "merge"
KEYMAP_APPLY_NONE = "none"


@dataclass(frozen=True)
class KeymapDiscovery:
    keymap: dict[str, dict[str, str]]
    source_files: tuple[Path, ...]
    scanned_files: int
    truncated: bool
    recognized_config: bool = False
    overridden_actions: frozenset[str] = frozenset()
    notice: str = ""
    requires_game_launch: bool = False
    has_verified_player_config: bool = False
    has_recognized_player_file: bool = False
    blocks_heuristic_fallback: bool = False
    source_records: tuple[KeymapSource, ...] = ()
    binding_authority: str = KEYMAP_AUTHORITY_UNKNOWN
    apply_mode: str = KEYMAP_APPLY_INFER


@dataclass(frozen=True)
class _SteamGameManifest:
    path: Path
    steam_roots: tuple[Path, ...]
    app_id: str | None
    last_owner: str | None
    last_owner_present: bool


@dataclass(frozen=True)
class _IndexedXmlMapping:
    keymap: dict[str, dict[str, str]]
    slot_count: int


@dataclass(frozen=True)
class _PixpilArchiveEntry:
    name: str
    offset: int
    codec: int
    uncompressed_size: int
    stored_size: int


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
    "tilde": ("Tilde", "keyboard"),
    "backquote": ("Tilde", "keyboard"),
    "grave": ("Tilde", "keyboard"),
    "minus": ("-", "keyboard"),
    "equals": ("=", "keyboard"),
    "comma": (",", "keyboard"),
    "period": (".", "keyboard"),
    "slash": ("/", "keyboard"),
    "semicolon": (";", "keyboard"),
    "apostrophe": ("'", "keyboard"),
    "quote": ("'", "keyboard"),
    "leftbracket": ("[", "keyboard"),
    "rightbracket": ("]", "keyboard"),
    "backslash": ("\\", "keyboard"),
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
    "leftcommand": ("Win", "keyboard"),
    "rightcommand": ("Win", "keyboard"),
    "command": ("Win", "keyboard"),
    "cmd": ("Win", "keyboard"),
    "leftwindows": ("Win", "keyboard"),
    "rightwindows": ("Win", "keyboard"),
    "lwin": ("Win", "keyboard"),
    "rwin": ("Win", "keyboard"),
    "windows": ("Win", "keyboard"),
    "win": ("Win", "keyboard"),
    "leftsuper": ("Win", "keyboard"),
    "rightsuper": ("Win", "keyboard"),
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
    "digit0": ("0", "keyboard"),
    "digit1": ("1", "keyboard"),
    "digit2": ("2", "keyboard"),
    "digit3": ("3", "keyboard"),
    "digit4": ("4", "keyboard"),
    "digit5": ("5", "keyboard"),
    "digit6": ("6", "keyboard"),
    "digit7": ("7", "keyboard"),
    "digit8": ("8", "keyboard"),
    "digit9": ("9", "keyboard"),
    "alpha0": ("0", "keyboard"),
    "alpha1": ("1", "keyboard"),
    "alpha2": ("2", "keyboard"),
    "alpha3": ("3", "keyboard"),
    "alpha4": ("4", "keyboard"),
    "alpha5": ("5", "keyboard"),
    "alpha6": ("6", "keyboard"),
    "alpha7": ("7", "keyboard"),
    "alpha8": ("8", "keyboard"),
    "alpha9": ("9", "keyboard"),
    "oemcomma": (",", "keyboard"),
    "oemperiod": (".", "keyboard"),
    "oemquestion": ("/", "keyboard"),
    "oemsemicolon": (";", "keyboard"),
    "oemquotes": ("'", "keyboard"),
    "oemopenbrackets": ("[", "keyboard"),
    "oemclosebrackets": ("]", "keyboard"),
    "oempipe": ("\\", "keyboard"),
    "oemminus": ("-", "keyboard"),
    "oemplus": ("=", "keyboard"),
    "oemtilde": ("Tilde", "keyboard"),
    "numpadmultiply": ("NumPadMultiply", "keyboard"),
    "multiply": ("NumPadMultiply", "keyboard"),
    "numpadadd": ("NumPadAdd", "keyboard"),
    "add": ("NumPadAdd", "keyboard"),
    "numpadsubtract": ("NumPadSubtract", "keyboard"),
    "subtract": ("NumPadSubtract", "keyboard"),
    "numpaddecimal": ("NumPadDecimal", "keyboard"),
    "decimal": ("NumPadDecimal", "keyboard"),
    "numpaddivide": ("NumPadDivide", "keyboard"),
    "divide": ("NumPadDivide", "keyboard"),
    "numpadenter": ("NumPadEnter", "keyboard"),
    "printscreen": ("PrintScreen", "keyboard"),
    "snapshot": ("PrintScreen", "keyboard"),
    "apps": ("Apps", "keyboard"),
    "applicationkey": ("Apps", "keyboard"),
    "leftmousebutton": ("leftClick", "mouse"),
    "leftclick": ("leftClick", "mouse"),
    "mouseleft": ("leftClick", "mouse"),
    "leftbutton": ("leftClick", "mouse"),
    "mouse0": ("leftClick", "mouse"),
    "rightmousebutton": ("rightClick", "mouse"),
    "rightclick": ("rightClick", "mouse"),
    "mouseright": ("rightClick", "mouse"),
    "rightbutton": ("rightClick", "mouse"),
    "mouse1": ("rightClick", "mouse"),
    "middlemousebutton": ("middleClick", "mouse"),
    "middleclick": ("middleClick", "mouse"),
    "mousemiddle": ("middleClick", "mouse"),
    "middlebutton": ("middleClick", "mouse"),
    "mouse2": ("middleClick", "mouse"),
    "thumbmousebutton": ("mouseButton4", "mouse"),
    "mousebutton4": ("mouseButton4", "mouse"),
    "backbutton": ("mouseButton4", "mouse"),
    "mouse3": ("mouseButton4", "mouse"),
    "thumbmousebutton2": ("mouseButton5", "mouse"),
    "mousebutton5": ("mouseButton5", "mouse"),
    "forwardbutton": ("mouseButton5", "mouse"),
    "mouse4": ("mouseButton5", "mouse"),
    "mousescrollup": ("mouseWheelUp", "mouse"),
    "mousewheelup": ("mouseWheelUp", "mouse"),
    "mousescrolldown": ("mouseWheelDown", "mouse"),
    "mousewheeldown": ("mouseWheelDown", "mouse"),
    "mousescrollleft": ("mouseWheelLeft", "mouse"),
    "mousewheelleft": ("mouseWheelLeft", "mouse"),
    "mousescrollright": ("mouseWheelRight", "mouse"),
    "mousewheelright": ("mouseWheelRight", "mouse"),
    "gamepadfacebuttonbottom": ("gamepadA", "gamepad"),
    "gamepada": ("gamepadA", "gamepad"),
    "buttonsouth": ("gamepadA", "gamepad"),
    "joystickbutton0": ("gamepadA", "gamepad"),
    "gamepadfacebuttonright": ("gamepadB", "gamepad"),
    "gamepadb": ("gamepadB", "gamepad"),
    "buttoneast": ("gamepadB", "gamepad"),
    "joystickbutton1": ("gamepadB", "gamepad"),
    "gamepadfacebuttonleft": ("gamepadX", "gamepad"),
    "gamepadx": ("gamepadX", "gamepad"),
    "buttonwest": ("gamepadX", "gamepad"),
    "joystickbutton2": ("gamepadX", "gamepad"),
    "gamepadfacebuttontop": ("gamepadY", "gamepad"),
    "gamepady": ("gamepadY", "gamepad"),
    "buttonnorth": ("gamepadY", "gamepad"),
    "joystickbutton3": ("gamepadY", "gamepad"),
    "gamepadleftshoulder": ("gamepadLeftShoulder", "gamepad"),
    "leftshoulder": ("gamepadLeftShoulder", "gamepad"),
    "gamepadrightshoulder": ("gamepadRightShoulder", "gamepad"),
    "rightshoulder": ("gamepadRightShoulder", "gamepad"),
    "gamepadleftthumb": ("gamepadLeftThumb", "gamepad"),
    "gamepadleftthumbstick": ("gamepadLeftThumb", "gamepad"),
    "leftstickpress": ("gamepadLeftThumb", "gamepad"),
    "gamepadrightthumb": ("gamepadRightThumb", "gamepad"),
    "gamepadrightthumbstick": ("gamepadRightThumb", "gamepad"),
    "rightstickpress": ("gamepadRightThumb", "gamepad"),
    "gamepadlefttrigger": ("gamepadLeftTrigger", "gamepad"),
    "lefttrigger": ("gamepadLeftTrigger", "gamepad"),
    "gamepadrighttrigger": ("gamepadRightTrigger", "gamepad"),
    "righttrigger": ("gamepadRightTrigger", "gamepad"),
    "gamepadspecialleft": ("gamepadBack", "gamepad"),
    "gamepadback": ("gamepadBack", "gamepad"),
    "select": ("gamepadBack", "gamepad"),
    "gamepadspecialright": ("gamepadStart", "gamepad"),
    "gamepadstart": ("gamepadStart", "gamepad"),
    "start": ("gamepadStart", "gamepad"),
    "dpadup": ("gamepadDpadUp", "gamepad"),
    "gamepaddpadup": ("gamepadDpadUp", "gamepad"),
    "dpaddown": ("gamepadDpadDown", "gamepad"),
    "gamepaddpaddown": ("gamepadDpadDown", "gamepad"),
    "dpadleft": ("gamepadDpadLeft", "gamepad"),
    "gamepaddpadleft": ("gamepadDpadLeft", "gamepad"),
    "dpadright": ("gamepadDpadRight", "gamepad"),
    "gamepaddpadright": ("gamepadDpadRight", "gamepad"),
    "gamepadleftstickup": ("gamepadLeftStickUp", "gamepad"),
    "gamepadleftstickdown": ("gamepadLeftStickDown", "gamepad"),
    "gamepadleftstickleft": ("gamepadLeftStickLeft", "gamepad"),
    "gamepadleftstickright": ("gamepadLeftStickRight", "gamepad"),
    "gamepadrightstickup": ("gamepadRightStickUp", "gamepad"),
    "gamepadrightstickdown": ("gamepadRightStickDown", "gamepad"),
    "gamepadrightstickleft": ("gamepadRightStickLeft", "gamepad"),
    "gamepadrightstickright": ("gamepadRightStickRight", "gamepad"),
    "controllerfacebuttonsouth": ("gamepadA", "gamepad"),
    "controllerfacebuttoneast": ("gamepadB", "gamepad"),
    "controllerfacebuttonwest": ("gamepadX", "gamepad"),
    "controllerfacebuttonnorth": ("gamepadY", "gamepad"),
    "controllerleftbumper": ("gamepadLeftShoulder", "gamepad"),
    "controllerrightbumper": ("gamepadRightShoulder", "gamepad"),
    "controllerlefttrigger": ("gamepadLeftTrigger", "gamepad"),
    "controllerrighttrigger": ("gamepadRightTrigger", "gamepad"),
    "controllerselectbutton": ("gamepadBack", "gamepad"),
    "controllerstartbutton": ("gamepadStart", "gamepad"),
    "controllerjoystickpress": ("gamepadLeftThumb", "gamepad"),
    "controllerdpadnorth": ("gamepadDpadUp", "gamepad"),
    "controllerdpadsouth": ("gamepadDpadDown", "gamepad"),
    "controllerdpadwest": ("gamepadDpadLeft", "gamepad"),
    "controllerdpadeast": ("gamepadDpadRight", "gamepad"),
}


def _canonical_input_name(raw_name: object) -> tuple[str, str] | None:
    if not isinstance(raw_name, str):
        return None
    name = raw_name.strip().strip('"\'')
    if not name:
        return None
    if "+" in name:
        modifier_order = ("Ctrl", "Alt", "Shift", "Win")
        modifiers: set[str] = set()
        primary: tuple[str, str] | None = None
        for token in (part.strip() for part in name.split("+")):
            if not token:
                return None
            canonical_part = _canonical_input_name(token)
            if canonical_part is None:
                return None
            part_name, part_type = canonical_part
            if part_name in modifier_order:
                if part_type != "keyboard":
                    return None
                modifiers.add(part_name)
            elif primary is None:
                if part_type not in {"keyboard", "mouse"}:
                    return None
                primary = (part_name, part_type)
            else:
                return None
        if primary is None or not modifiers:
            return None
        return "+".join(
            (*[item for item in modifier_order if item in modifiers], primary[0])
        ), primary[1]
    literal_aliases = {
        "-": ("-", "keyboard"),
        "=": ("=", "keyboard"),
        "'": ("'", "keyboard"),
        ",": (",", "keyboard"),
        ".": (".", "keyboard"),
        "/": ("/", "keyboard"),
        ";": (";", "keyboard"),
        "[": ("[", "keyboard"),
        "]": ("]", "keyboard"),
        "\\": ("\\", "keyboard"),
    }
    if name in literal_aliases:
        return literal_aliases[name]
    # Unity paths look like <Keyboard>/w; keep nested device controls together so
    # <Gamepad>/dpad/up is recognized while analog paths such as mouse scroll/y
    # cannot be mistaken for the keyboard Y key.
    unity_path = re.fullmatch(r"<([^>]+)>/(.+)", name)
    expected_type: str | None = None
    if unity_path:
        device = unity_path.group(1).casefold()
        expected_type = {
            "keyboard": "keyboard",
            "mouse": "mouse",
            "gamepad": "gamepad",
        }.get(device)
        if expected_type is None:
            return None
        name = unity_path.group(2)
    compact = re.sub(r"[^a-z0-9]+", "", name.casefold())
    # Common serialized enum spellings used by Godot, SDL, XNA/MonoGame and
    # Unity.  Prefix removal is deliberately limited to a single key token so
    # arbitrary setting names cannot accidentally become keyboard mappings.
    prefixed_key = re.fullmatch(
        r"(?:key|keys|keyboardkey|sdlscancode|scancode)([a-z0-9])",
        compact,
    )
    if expected_type in {None, "keyboard"} and prefixed_key:
        return prefixed_key.group(1).upper(), "keyboard"
    xna_digit = re.fullmatch(r"d([0-9])", compact)
    if expected_type in {None, "keyboard"} and xna_digit:
        return xna_digit.group(1), "keyboard"
    if compact in _INPUT_ALIASES:
        canonical = _INPUT_ALIASES[compact]
        return canonical if expected_type in {None, canonical[1]} else None
    if expected_type in {None, "keyboard"} and len(compact) == 1 and compact.isalnum():
        return compact.upper(), "keyboard"
    if expected_type in {None, "keyboard"} and re.fullmatch(r"f(?:[1-9]|1[0-9]|2[0-4])", compact):
        return compact.upper(), "keyboard"
    if expected_type in {None, "keyboard"} and compact.startswith("numpad") and compact[6:].isdigit():
        return f"NumPad{compact[6:]}", "keyboard"
    return None


def _movement_direction(
    action: str,
    input_name: str,
    part: str = "",
    scale: float | None = None,
) -> str | None:
    normalized_action = re.sub(r"[^a-z0-9]+", "", action.casefold())
    normalized_part = re.sub(r"[^a-z0-9]+", "", part.casefold())
    separated_action = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", action)
    action_words = [
        word.casefold()
        for word in re.findall(r"[A-Za-z0-9]+", separated_action)
    ]
    action_word_set = set(action_words)
    base_input = input_name.rsplit("+", 1)[-1]
    part_directions = {"up": "W", "down": "B", "left": "L", "right": "R"}
    if normalized_part in part_directions:
        return part_directions[normalized_part]
    if normalized_action in part_directions:
        physical_directions = {
            "W": "W",
            "S": "B",
            "A": "L",
            "D": "R",
            "Up": "W",
            "Down": "B",
            "Left": "L",
            "Right": "R",
            "gamepadDpadUp": "W",
            "gamepadDpadDown": "B",
            "gamepadDpadLeft": "L",
            "gamepadDpadRight": "R",
            "gamepadLeftStickUp": "W",
            "gamepadLeftStickDown": "B",
            "gamepadLeftStickLeft": "L",
            "gamepadLeftStickRight": "R",
        }
        return physical_directions.get(base_input)
    movement_context = bool(action_word_set.intersection({"move", "movement", "locomotion"}))
    if normalized_action in {"moveforward", "forward", "vertical"} or (
        movement_context and bool(action_word_set.intersection({"forward", "vertical"}))
    ):
        return "B" if scale is not None and scale < 0 else "W"
    if normalized_action in {"moveright", "strafe", "horizontal"} or (
        movement_context and bool(action_word_set.intersection({"right", "strafe", "horizontal"}))
    ):
        return "L" if scale is not None and scale < 0 else "R"
    if normalized_action in {"backward", "moveback", "movebackward"} or (
        movement_context and bool(action_word_set.intersection({"back", "backward"}))
    ):
        return "B"
    if normalized_action == "moveup" or movement_context and "up" in action_word_set:
        return "W"
    if normalized_action == "movedown" or movement_context and "down" in action_word_set:
        return "B"
    if normalized_action == "moveleft" or movement_context and "left" in action_word_set:
        return "L"
    if normalized_action in {"move", "movement", "locomotion"}:
        return {
            "W": "W",
            "S": "B",
            "A": "L",
            "D": "R",
            "Up": "W",
            "Down": "B",
            "Left": "L",
            "Right": "R",
            "gamepadDpadUp": "W",
            "gamepadDpadDown": "B",
            "gamepadDpadLeft": "L",
            "gamepadDpadRight": "R",
            "gamepadLeftStickUp": "W",
            "gamepadLeftStickDown": "B",
            "gamepadLeftStickLeft": "L",
            "gamepadLeftStickRight": "R",
        }.get(base_input)
    return None


def _add_detected_mapping(
    keymap: dict[str, dict[str, str]],
    raw_input: object,
    action: object,
    *,
    part: str = "",
    scale: float | None = None,
) -> bool:
    canonical = _canonical_input_name(raw_input)
    if canonical is None or not isinstance(action, str) or not action.strip():
        return False
    input_name, device_type = canonical
    action_name = action.strip()[:120]
    direction = _movement_direction(action_name, input_name, part, scale)
    existing = keymap.get(input_name)
    if existing is None:
        mapping = {"type": device_type, "action": action_name}
        if direction:
            mapping["movement_direction"] = direction
        keymap[input_name] = mapping
        return True
    actions = [item.strip() for item in existing["action"].split(" / ")]
    if action_name not in actions:
        existing["action"] = " / ".join([*actions, action_name])[:240]
    if direction and "movement_direction" not in existing:
        existing["movement_direction"] = direction
    return True


def _parse_native_keymap(document: object) -> dict[str, dict[str, str]]:
    if not isinstance(document, dict):
        return {}
    detected: dict[str, dict[str, str]] = {}
    for raw_input, value in document.items():
        if not isinstance(value, dict):
            continue
        device_type = value.get("type")
        action = value.get("action")
        if (
            not isinstance(raw_input, str)
            or device_type not in {"keyboard", "mouse", "gamepad"}
            or not isinstance(action, str)
            or not action.strip()
        ):
            continue
        key = raw_input.upper() if device_type == "keyboard" and len(raw_input) == 1 else raw_input
        mapping = {"type": str(device_type), "action": action.strip()[:240]}
        direction = value.get("movement_direction")
        if direction in {"W", "B", "L", "R"}:
            mapping["movement_direction"] = str(direction)
        detected[key] = mapping
    return detected


def _parse_unity_input_actions(document: object) -> dict[str, dict[str, str]]:
    if not isinstance(document, dict) or not isinstance(document.get("maps"), list):
        return {}
    detected: dict[str, dict[str, str]] = {}
    for input_map in document["maps"]:
        if not isinstance(input_map, dict):
            continue
        action_names: dict[str, str] = {}
        for action in input_map.get("actions", []):
            if not isinstance(action, dict) or not isinstance(action.get("name"), str):
                continue
            name = action["name"].strip()
            action_names[name] = name
            if isinstance(action.get("id"), str):
                action_names[action["id"]] = name
        for binding in input_map.get("bindings", []):
            if not isinstance(binding, dict):
                continue
            action_ref = binding.get("action")
            action_name = action_names.get(str(action_ref), str(action_ref or "").strip())
            _add_detected_mapping(
                detected,
                binding.get("path"),
                action_name,
                part=str(binding.get("name") or ""),
            )
    return detected


def _parse_unity_serialized_input_actions(
    document: object,
) -> dict[str, dict[str, str]]:
    if not isinstance(document, dict) or not isinstance(document.get("m_ActionMaps"), list):
        return {}
    detected: dict[str, dict[str, str]] = {}
    movement_inputs: set[str] = set()
    for input_map in document["m_ActionMaps"]:
        if not isinstance(input_map, dict):
            continue
        bindings = input_map.get("m_Bindings")
        if not isinstance(bindings, list):
            continue
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            action = binding.get("m_Action")
            part = str(binding.get("m_Name") or "")
            _add_detected_mapping(
                detected,
                binding.get("m_Path"),
                action,
                part=part,
            )
            normalized_action = re.sub(
                r"[^a-z0-9]+",
                "",
                str(action or "").casefold(),
            )
            canonical = _canonical_input_name(binding.get("m_Path"))
            if (
                canonical is not None
                and normalized_action
                in {"move", "movement", "navigate", "navigation", "locomotion"}
                and _movement_direction(str(action), canonical[0], part)
            ):
                movement_inputs.add(canonical[0])
    for input_name, mapping in detected.items():
        if input_name not in movement_inputs:
            mapping.pop("movement_direction", None)
    return detected


@dataclass(frozen=True)
class _UnrealInputBinding:
    collection: str
    action: str
    raw_key: str
    scale: float | None
    modifiers: tuple[str, ...]


_UNREAL_RAW_KEY_ALIASES = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "numpadzero": "NumPad0",
    "numpadone": "NumPad1",
    "numpadtwo": "NumPad2",
    "numpadthree": "NumPad3",
    "numpadfour": "NumPad4",
    "numpadfive": "NumPad5",
    "numpadsix": "NumPad6",
    "numpadseven": "NumPad7",
    "numpadeight": "NumPad8",
    "numpadnine": "NumPad9",
    "hyphen": "-",
}


def _canonical_unreal_input_name(raw_key: str) -> tuple[str, str] | None:
    compact = re.sub(r"[^a-z0-9]+", "", raw_key.casefold())
    return _canonical_input_name(_UNREAL_RAW_KEY_ALIASES.get(compact, raw_key))


def _unreal_input_operations(
    text: str,
) -> tuple[list[tuple[str, str, _UnrealInputBinding | None]], bool, bool]:
    """Parse legacy Unreal array operations without assuming a particular game."""
    operations: list[tuple[str, str, _UnrealInputBinding | None]] = []
    saw_assignment = False
    malformed = False
    pattern = re.compile(
        r"(?im)^\s*([+\-.!]?)\s*(ActionMappings|AxisMappings)\s*=\s*"
        r"(?:\((.*?)\)|([^\r\n;]*))",
    )
    for match in pattern.finditer(text):
        saw_assignment = True
        operator = match.group(1) or "+"
        collection = match.group(2).casefold()
        scalar_value = (match.group(4) or "").strip()
        if operator == "!":
            if match.group(3) is None and scalar_value.casefold() == "cleararray":
                operations.append(("clear", collection, None))
            else:
                malformed = True
            continue
        body = match.group(3)
        if body is None:
            malformed = True
            continue
        fields: dict[str, str] = {}
        for field in re.finditer(
            r"(\w+)\s*=\s*(?:\"([^\"]*)\"|([^,)]*))",
            body,
        ):
            fields[field.group(1).casefold()] = (
                field.group(2) or field.group(3)
            ).strip()
        action = fields.get("actionname") or fields.get("axisname")
        raw_key = fields.get("key")
        if not action or not raw_key:
            malformed = True
            continue
        scale: float | None = None
        if "scale" in fields:
            try:
                scale = float(fields["scale"])
            except ValueError:
                malformed = True
                continue
            if not math.isfinite(scale):
                malformed = True
                continue
        invalid_modifier = any(
            fields[field_name].casefold()
            not in {"0", "1", "false", "true", "no", "yes"}
            for field_name in ("bctrl", "balt", "bshift", "bcmd")
            if field_name in fields
        )
        if invalid_modifier:
            malformed = True
            continue
        modifiers = tuple(
            name
            for field_name, name in (
                ("bctrl", "Ctrl"),
                ("balt", "Alt"),
                ("bshift", "Shift"),
                ("bcmd", "Win"),
            )
            if fields.get(field_name, "false").casefold() in {"1", "true", "yes"}
        )
        operations.append(
            (
                "remove" if operator == "-" else "add",
                collection,
                _UnrealInputBinding(
                    collection,
                    action.strip(),
                    raw_key.strip(),
                    scale,
                    modifiers,
                ),
            )
        )
    return operations, bool(operations), bool(saw_assignment and malformed)


def _unreal_binding_identity(binding: _UnrealInputBinding) -> tuple[object, ...]:
    canonical = _canonical_unreal_input_name(binding.raw_key)
    normalized_key = (
        canonical[0].casefold()
        if canonical is not None
        else binding.raw_key.casefold()
    )
    return (
        binding.collection,
        binding.action.casefold(),
        normalized_key,
        binding.scale,
        binding.modifiers,
    )


def _apply_unreal_input_operations(
    bindings: list[_UnrealInputBinding],
    operations: list[tuple[str, str, _UnrealInputBinding | None]],
) -> None:
    for operation, collection, binding in operations:
        if operation == "clear":
            bindings[:] = [
                existing
                for existing in bindings
                if existing.collection != collection
            ]
            continue
        if binding is None:
            continue
        identity = _unreal_binding_identity(binding)
        if operation == "remove":
            bindings[:] = [
                existing
                for existing in bindings
                if _unreal_binding_identity(existing) != identity
            ]
        elif not any(
            _unreal_binding_identity(existing) == identity
            for existing in bindings
        ):
            bindings.append(binding)


_UNREAL_DIAGNOSTIC_ACTION_TOKENS = frozenset(
    {
        "cheat",
        "console",
        "debug",
        "developer",
        "diagnostic",
        "editor",
        "profiler",
        "test",
    }
)


def _is_unreal_diagnostic_action(action: str) -> bool:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", action)
    separated = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", separated)
    tokens = set(re.findall(r"[a-z0-9]+", separated.casefold()))
    return bool(tokens & _UNREAL_DIAGNOSTIC_ACTION_TOKENS)


def _unreal_bindings_keymap(
    bindings: list[_UnrealInputBinding],
) -> dict[str, dict[str, str]]:
    detected: dict[str, dict[str, str]] = {}
    for binding in bindings:
        if _is_unreal_diagnostic_action(binding.action):
            continue
        canonical = _canonical_unreal_input_name(binding.raw_key)
        if canonical is None:
            continue
        input_name = "+".join((*binding.modifiers, canonical[0]))
        _add_detected_mapping(
            detected,
            input_name,
            binding.action,
            scale=binding.scale,
        )
    return detected


def _parse_unreal_input_ini(text: str) -> dict[str, dict[str, str]]:
    bindings: list[_UnrealInputBinding] = []
    operations, _recognized, malformed = _unreal_input_operations(text)
    if malformed:
        return {}
    _apply_unreal_input_operations(bindings, operations)
    return _unreal_bindings_keymap(bindings)


_GODOT_SPECIAL_KEYS = {
    1: "Esc",
    2: "Tab",
    4: "Backspace",
    5: "Enter",
    6: "Enter",
    7: "Insert",
    8: "Delete",
    9: "Pause",
    13: "Home",
    14: "End",
    15: "Left",
    16: "Up",
    17: "Right",
    18: "Down",
    19: "PageUp",
    20: "PageDown",
    21: "Shift",
    22: "Ctrl",
    23: "Win",
    24: "Alt",
}
_GODOT3_EXTRA_SPECIAL_KEYS = {
    44: "Win",
    45: "Win",
}
_GODOT_MOUSE_BUTTONS = {
    1: "leftClick",
    2: "rightClick",
    3: "middleClick",
    4: "mouseWheelUp",
    5: "mouseWheelDown",
    8: "mouseButton4",
    9: "mouseButton5",
}
_GODOT_GAMEPAD_BUTTONS = {
    0: "gamepadA",
    1: "gamepadB",
    2: "gamepadX",
    3: "gamepadY",
    4: "gamepadBack",
    6: "gamepadStart",
    7: "gamepadLeftThumb",
    8: "gamepadRightThumb",
    9: "gamepadLeftShoulder",
    10: "gamepadRightShoulder",
    11: "gamepadDpadUp",
    12: "gamepadDpadDown",
    13: "gamepadDpadLeft",
    14: "gamepadDpadRight",
}
_GODOT3_GAMEPAD_BUTTONS = {
    0: "gamepadA",
    1: "gamepadB",
    2: "gamepadX",
    3: "gamepadY",
    4: "gamepadLeftShoulder",
    5: "gamepadRightShoulder",
    6: "gamepadLeftTrigger",
    7: "gamepadRightTrigger",
    8: "gamepadLeftThumb",
    9: "gamepadRightThumb",
    10: "gamepadDpadUp",
    11: "gamepadDpadDown",
    12: "gamepadDpadLeft",
    13: "gamepadDpadRight",
    14: "gamepadBack",
    15: "gamepadStart",
}


def _godot_key_name(value: int, modern: bool | None = None) -> str | None:
    """Convert the common Godot 3/4 Key constants without loading the engine."""
    # Godot 4 uses a 23-bit key-code mask and a 0x00400000 special-key bit;
    # Godot 3 used 0x01000000.  Trying the modern mask first also strips
    # modifiers from printable Godot 4 keycodes without confusing legacy
    # special keys.
    modern_value = value & 0x007FFFFF
    candidates: tuple[tuple[int, int, int], ...]
    if modern is True:
        candidates = ((modern_value, 0x00400000, 51),)
    elif modern is False:
        candidates = ((value & 0x01FFFFFF, 0x01000000, 43),)
    else:
        candidates = (
            (modern_value, 0x00400000, 51),
            (value & 0x01FFFFFF, 0x01000000, 43),
        )
    for key_value, special_mask, maximum_function_offset in candidates:
        if key_value & special_mask:
            offset = key_value - special_mask
            if 28 <= offset <= maximum_function_offset:
                return f"F{offset - 27}"
            if 134 <= offset <= 143:
                return f"NumPad{offset - 134}"
            special = _GODOT_SPECIAL_KEYS.get(offset)
            if special is None and special_mask == 0x01000000:
                special = _GODOT3_EXTRA_SPECIAL_KEYS.get(offset)
            return special
    printable_value = modern_value if modern is not False else value & 0x00FFFFFF
    if 32 <= printable_value <= 126:
        canonical = _canonical_input_name(chr(printable_value))
        return canonical[0] if canonical else None
    return None


def _parse_godot_input_map(text: str) -> dict[str, dict[str, str]]:
    """Parse textual Godot InputMap resources/project.godot sections."""
    input_section = re.search(
        r"(?ms)^\s*\[input\]\s*(.*?)(?=^\s*\[[^\]]+\]|\Z)",
        text,
    )
    if input_section is None:
        return {}
    section = input_section.group(1)
    version_match = re.search(r"(?m)^\s*config_version\s*=\s*([0-9]+)", text)
    explicit_version = int(version_match.group(1)) if version_match else None
    modern = (
        explicit_version >= 5
        if explicit_version is not None
        else bool(re.search(r'"?(?:physical_keycode|keycode)"?\s*[:=]', section))
    )
    detected: dict[str, dict[str, str]] = {}
    blocks = re.finditer(
        r"(?ms)^\s*([A-Za-z0-9_.:/-]+)\s*=\s*\{(.*?)^\s*\}\s*$",
        section,
    )
    for block in blocks:
        action = block.group(1).strip()
        body = block.group(2)
        for event in re.finditer(r"(?s)Object\(InputEventKey,(.*?)\)", body):
            fields = {
                item.group(1).casefold(): int(item.group(2))
                for item in re.finditer(
                    r'\"?(physical_keycode|physical_scancode|keycode|scancode|unicode)\"?\s*[:=]\s*(-?[0-9]+)',
                    event.group(1),
                    re.I,
                )
            }
            selected_value: int | None = None
            raw_input: str | None = None
            for name in (
                "physical_keycode",
                "physical_scancode",
                "keycode",
                "scancode",
                "unicode",
            ):
                value = fields.get(name, 0)
                candidate = _godot_key_name(value, modern=modern) if value else None
                if candidate:
                    selected_value = value
                    raw_input = candidate
                    break
            event_text = event.group(1)
            modifiers: set[str] = set()
            for field_name, label in (
                ("ctrl_pressed", "Ctrl"),
                ("alt_pressed", "Alt"),
                ("shift_pressed", "Shift"),
                ("meta_pressed", "Win"),
                ("command_pressed", "Win"),
            ):
                if re.search(
                    rf'"?{field_name}"?\s*[:=]\s*(?:true|1)\b',
                    event_text,
                    re.I,
                ):
                    modifiers.add(label)
            if selected_value is not None:
                for bit, label in (
                    (1 << 28, "Ctrl"),
                    (1 << 26, "Alt"),
                    (1 << 25, "Shift"),
                    (1 << 27, "Win"),
                ):
                    if selected_value & bit:
                        modifiers.add(label)
            if raw_input and raw_input not in {"Ctrl", "Alt", "Shift", "Win"} and modifiers:
                raw_input = "+".join(
                    (*[name for name in ("Ctrl", "Alt", "Shift", "Win") if name in modifiers], raw_input)
                )
            _add_detected_mapping(detected, raw_input, action)
        for event in re.finditer(
            r"(?s)Object\(InputEventMouseButton,(.*?)\)", body
        ):
            button = re.search(
                r'\"?button_index\"?\s*[:=]\s*([0-9]+)', event.group(1), re.I
            )
            raw_input = (
                _GODOT_MOUSE_BUTTONS.get(int(button.group(1))) if button else None
            )
            _add_detected_mapping(detected, raw_input, action)
        for event in re.finditer(
            r"(?s)Object\(InputEventJoypadButton,(.*?)\)", body
        ):
            button = re.search(
                r'\"?button_index\"?\s*[:=]\s*([0-9]+)', event.group(1), re.I
            )
            button_map = _GODOT_GAMEPAD_BUTTONS if modern else _GODOT3_GAMEPAD_BUTTONS
            raw_input = button_map.get(int(button.group(1))) if button else None
            _add_detected_mapping(detected, raw_input, action)
        for event in re.finditer(
            r"(?s)Object\(InputEventJoypadMotion,(.*?)\)", body
        ):
            axis_match = re.search(
                r'\"?axis\"?\s*[:=]\s*([0-9]+)', event.group(1), re.I
            )
            value_match = re.search(
                r'\"?axis_value\"?\s*[:=]\s*(-?[0-9.]+)',
                event.group(1),
                re.I,
            )
            if not axis_match or not value_match:
                continue
            axis = int(axis_match.group(1))
            try:
                axis_value = float(value_match.group(1))
            except ValueError:
                continue
            if abs(axis_value) < 1e-9:
                continue
            axis_inputs = {
                (0, -1): "gamepadLeftStickLeft",
                (0, 1): "gamepadLeftStickRight",
                (1, -1): "gamepadLeftStickUp",
                (1, 1): "gamepadLeftStickDown",
                (2, -1): "gamepadRightStickLeft",
                (2, 1): "gamepadRightStickRight",
                (3, -1): "gamepadRightStickUp",
                (3, 1): "gamepadRightStickDown",
            }
            if modern:
                axis_inputs.update(
                    {
                        (4, 1): "gamepadLeftTrigger",
                        (5, 1): "gamepadRightTrigger",
                    }
                )
            else:
                axis_inputs.update(
                    {
                        (6, 1): "gamepadLeftTrigger",
                        (7, 1): "gamepadRightTrigger",
                    }
                )
            raw_input = axis_inputs.get((axis, -1 if axis_value < 0 else 1))
            _add_detected_mapping(detected, raw_input, action)
    return detected


def _text_config_input_name(value: str) -> str | None:
    raw = value.strip().strip('"\'')
    canonical = _canonical_input_name(raw)
    if canonical is not None:
        return canonical[0]
    if re.fullmatch(r"[0-9]{1,3}", raw):
        numeric = int(raw)
        if numeric > 2:
            return _windows_virtual_key_name(numeric)
    return None


_CONTROL_SECTION_NAMES = {
    "binding",
    "bindings",
    "control",
    "controls",
    "input",
    "inputs",
    "keybinding",
    "keybindings",
    "keyboardcontrols",
    "keymap",
}


def _is_control_section_name(value: str) -> bool:
    compact = re.sub(r"[^a-z0-9]+", "", value.casefold())
    return compact in _CONTROL_SECTION_NAMES


def _parse_simple_text_keymap(text: str) -> dict[str, dict[str, str]]:
    """Parse conservative action=key INI/Lua-style control dictionaries."""
    detected: dict[str, dict[str, str]] = {}
    section = ""
    strong_structure = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        section_match = re.fullmatch(r"\[([^\]]+)\]", line)
        if section_match:
            section = section_match.group(1).strip()
            continue
        if not line or line.startswith(("#", ";", "//", "--")):
            continue
        assignment = re.match(
            r'''["']?([A-Za-z_][A-Za-z0-9_.:/ -]{0,119})["']?\s*(?:=|:)\s*(.+?)\s*[,;]?$''',
            line,
        )
        if assignment is None:
            continue
        left = assignment.group(1).strip()
        right = assignment.group(2).strip().rstrip(",").strip()
        if not right or right.casefold().strip('"\'') in {"none", "null", "unbound", "false", "true"}:
            continue
        section_is_controls = _is_control_section_name(section)
        input_name = _text_config_input_name(right)
        action = left
        if input_name is None:
            input_name = _text_config_input_name(left)
            action = right.strip('"\'')
        if input_name is None or not action or len(action) > 120:
            continue
        if not section_is_controls and not _looks_like_action_field(action):
            continue
        if section_is_controls:
            strong_structure = True
        clean_action = re.sub(r"[_-]+", " ", action).strip()
        _add_detected_mapping(detected, input_name, clean_action)
    return detected if strong_structure and len(detected) >= 3 else {}


def _parse_simple_yaml_keymap(text: str) -> dict[str, dict[str, str]]:
    """Parse the small action-to-key subset used by human-readable YAML configs."""
    detected: dict[str, dict[str, str]] = {}
    control_indent: int | None = None
    action_indent: int | None = None
    current_action = ""
    strong_structure = False
    action_evidence = 0
    nested_input_fields = {
        "binding",
        "bindings",
        "input",
        "inputs",
        "key",
        "keys",
        "primary",
        "secondary",
    }
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        field = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_. /-]{0,119}):\s*(.*?)\s*", line)
        if field:
            name, scalar = field.group(1).strip(), field.group(2).strip()
            normalized = re.sub(r"[^a-z0-9]+", "", name.casefold())
            if normalized in _CONTROL_SECTION_NAMES and (
                control_indent is None or indent <= control_indent
            ):
                control_indent = indent
                strong_structure = True
                action_indent = None
                current_action = ""
                continue
            if control_indent is None or indent <= control_indent:
                if indent <= (control_indent or 0):
                    control_indent = None
                continue
            if action_indent is None or indent <= action_indent:
                current_action = name
                action_indent = indent
                if _looks_like_action_field(name):
                    action_evidence += 1
            elif normalized not in nested_input_fields:
                continue
            if scalar and current_action:
                raw_values = re.findall(r"[A-Za-z0-9_+ -]+", scalar.strip("[]"))
                for raw_value in raw_values:
                    input_name = _text_config_input_name(raw_value.strip())
                    _add_detected_mapping(detected, input_name, current_action)
            continue
        item = re.fullmatch(r"-\s*[\"']?([^\"']+?)[\"']?\s*", line)
        if (
            item
            and control_indent is not None
            and action_indent is not None
            and indent > action_indent
            and current_action
        ):
            input_name = _text_config_input_name(item.group(1))
            _add_detected_mapping(detected, input_name, current_action)
    return (
        detected
        if strong_structure and action_evidence >= 1 and len(detected) >= 3
        else {}
    )


def _parse_unity_input_manager(text: str) -> dict[str, dict[str, str]]:
    if "m_Axes:" not in text or "positiveButton:" not in text:
        return {}
    detected: dict[str, dict[str, str]] = {}
    blocks = re.split(r"(?m)^\s*-\s+serializedVersion:\s*", text)[1:]
    for block in blocks:
        fields = {
            match.group(1): match.group(2).strip()
            for match in re.finditer(
                r"(?m)^[ \t]*(m_Name|negativeButton|positiveButton|altNegativeButton|altPositiveButton):[ \t]*(.*?)[ \t]*$",
                block,
            )
        }
        action = fields.get("m_Name", "").strip()
        for field_name, scale in (
            ("positiveButton", 1.0),
            ("altPositiveButton", 1.0),
            ("negativeButton", -1.0),
            ("altNegativeButton", -1.0),
        ):
            _add_detected_mapping(detected, fields.get(field_name), action, scale=scale)
    return detected


def _parse_unity_serialized_input_manager(
    document: object,
) -> dict[str, dict[str, str]]:
    """Parse Unity's binary globalgamemanagers InputManager object."""
    if not isinstance(document, dict) or not isinstance(document.get("m_Axes"), list):
        return {}
    detected: dict[str, dict[str, str]] = {}
    for axis in document["m_Axes"]:
        if not isinstance(axis, dict):
            continue
        action = axis.get("m_Name")
        normalized_action = re.sub(r"[^a-z0-9]+", "", str(action or "").casefold())
        if "debug" in normalized_action or normalized_action in {
            "speedaxis",
            "xaxis",
            "yaxis",
        }:
            continue
        for field_name, scale in (
            ("positiveButton", 1.0),
            ("altPositiveButton", 1.0),
            ("negativeButton", -1.0),
            ("altNegativeButton", -1.0),
        ):
            _add_detected_mapping(
                detected,
                axis.get(field_name),
                action,
                scale=scale,
            )
    return detected


_ACTION_FIELD_WORDS = (
    "action",
    "attack",
    "button",
    "cancel",
    "chat",
    "confirm",
    "control",
    "crouch",
    "dash",
    "emote",
    "fire",
    "grab",
    "input",
    "interact",
    "inventory",
    "journal",
    "jump",
    "key",
    "map",
    "menu",
    "move",
    "pause",
    "reload",
    "run",
    "slot",
    "sprint",
    "tool",
    "toolbar",
    "use",
)


def _looks_like_action_field(name: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", str(name or "").casefold())
    return bool(normalized) and any(word in normalized for word in _ACTION_FIELD_WORDS)


def _clean_action_field(name: object) -> str:
    action = str(name or "").strip().lstrip("_")
    action = re.sub(r"^(?:input|keybind|binding)[_-]+", "", action, flags=re.I)
    return action[:120]


def _xna_key_name(value: int) -> str | None:
    """Convert common XNA/MonoGame Keys enum values without loading game code."""
    if 48 <= value <= 57 or 65 <= value <= 90:
        return chr(value)
    if 96 <= value <= 105:
        return f"NumPad{value - 96}"
    if 112 <= value <= 123:
        return f"F{value - 111}"
    aliases = {
        8: "Backspace",
        9: "Tab",
        13: "Enter",
        19: "Pause",
        27: "Esc",
        32: "Space",
        33: "PageUp",
        34: "PageDown",
        35: "End",
        36: "Home",
        37: "Left",
        38: "Up",
        39: "Right",
        40: "Down",
        45: "Insert",
        46: "Delete",
        160: "Shift",
        161: "Shift",
        162: "Ctrl",
        163: "Ctrl",
        164: "Alt",
        165: "Alt",
        186: ";",
        187: "=",
        188: ",",
        189: "-",
        190: ".",
        191: "/",
        192: "Tilde",
        219: "[",
        220: "\\",
        221: "]",
    }
    return aliases.get(value)


_INDEXED_MENU_XOR_KEY = bytes.fromhex(
    "68 20 78 c3 aa 5d 29 d7 bb 81 55 49 f3 e2 a8 d0"
)
_INDEXED_MENU_ACTION_OFFSETS = (*range(20), 60, 61, 62)
_INDEXED_CHINESE_LANGUAGE_CODES = (
    "zh_cn",
    "zh-hans",
    "zh_hans",
    "chs",
    "schinese",
    "cn",
    "ch",
    "zh",
    "zh_tw",
    "zh-hant",
    "zh_hant",
    "cht",
    "tchinese",
    "tw",
)
_COMMON_ACTION_CHINESE_TRANSLATIONS = {
    "zoom": "缩放",
    "menu": "菜单",
    "target friend": "选择友方目标",
    "lobby": "大厅",
}


def _windows_virtual_key_name(value: int) -> str | None:
    """Convert a bounded Windows virtual-key value to the recorder key name."""
    generic_modifiers = {
        16: "Shift",
        17: "Ctrl",
        18: "Alt",
    }
    return generic_modifiers.get(value) or _xna_key_name(value)


def _decode_indexed_menu_dictionary(data: bytes) -> tuple[str, ...]:
    """Decode a bounded XOR/UTF-16 menu dictionary without executing game code."""
    if not 8 <= len(data) <= MAX_INDEXED_MENU_DICTIONARY_BYTES:
        raise ValueError("Indexed menu dictionary size is outside the safety limit")
    count = struct.unpack_from("<I", data, 0)[0]
    if not 1 <= count <= MAX_INDEXED_MENU_DICTIONARY_ENTRIES:
        raise ValueError("Invalid indexed menu dictionary entry count")
    position = 4
    total_chars = 0
    entries: list[str] = []
    for _index in range(count):
        if position + 4 > len(data):
            raise ValueError("Truncated indexed menu dictionary")
        char_count = struct.unpack_from("<I", data, position)[0]
        position += 4
        if char_count > MAX_INDEXED_MENU_STRING_CHARS:
            raise ValueError("Indexed menu dictionary string is too long")
        byte_count = char_count * 2
        if byte_count > len(data) - position:
            raise ValueError("Truncated indexed menu dictionary string")
        encoded = data[position : position + byte_count]
        position += byte_count
        decoded_bytes = bytes(
            value ^ _INDEXED_MENU_XOR_KEY[offset % len(_INDEXED_MENU_XOR_KEY)]
            for offset, value in enumerate(encoded)
        )
        try:
            decoded = decoded_bytes.decode("utf-16-le")
        except UnicodeDecodeError as exc:
            raise ValueError("Invalid indexed menu dictionary text") from exc
        if "\x00" in decoded:
            raise ValueError("Invalid indexed menu dictionary NUL character")
        total_chars += len(decoded)
        if total_chars > MAX_INDEXED_MENU_TEXT_CHARS:
            raise ValueError("Indexed menu dictionary text exceeds the safety limit")
        entries.append(decoded)
    if data[position:] not in {b"", b"\0\0\0\0"}:
        raise ValueError("Indexed menu dictionary has trailing data")
    return tuple(entries)


def _indexed_action_labels_from_dictionary(
    data: bytes,
    localized_data: bytes | None = None,
) -> dict[int, str]:
    entries = _decode_indexed_menu_dictionary(data)
    localized_entries = (
        _decode_indexed_menu_dictionary(localized_data)
        if localized_data is not None
        else ()
    )
    if len(entries) < 263:
        return {}
    anchors = ("go up", "go down", "go right", "go left")
    last_offset = max(_INDEXED_MENU_ACTION_OFFSETS)
    for start in range(len(entries) - last_offset):
        if tuple(entries[start + offset].strip().casefold() for offset in range(4)) != anchors:
            continue
        semantic_anchors = {
            -20: "controls",
            4: "jump",
            15: "pause",
            60: "menu",
            61: "target friend",
            62: "lobby",
        }
        if start < 20 or any(
            entries[start + offset].strip().casefold() != expected
            for offset, expected in semantic_anchors.items()
        ):
            continue
        labels: dict[int, str] = {}
        localized_labels: dict[int, str] = {}
        for slot, offset in enumerate(_INDEXED_MENU_ACTION_OFFSETS, start=1):
            value = entries[start + offset]
            clean = re.sub(r"[\x00-\x1f\x7f]+", " ", value).strip()
            if clean and clean != "-":
                labels[slot] = clean[:120]
                localized_clean = ""
                localized_index = start + offset
                if localized_index < len(localized_entries):
                    localized_clean = re.sub(
                        r"[\x00-\x1f\x7f]+",
                        " ",
                        localized_entries[localized_index],
                    ).strip()
                if not (
                    localized_clean
                    and localized_clean != "-"
                    and _contains_chinese_character(localized_clean)
                    and not _contains_latin_letter(localized_clean)
                ):
                    normalized_reference = re.sub(
                        r"\s+",
                        " ",
                        unicodedata.normalize("NFKC", clean),
                    ).strip().casefold()
                    localized_clean = _COMMON_ACTION_CHINESE_TRANSLATIONS.get(
                        normalized_reference,
                        "",
                    )
                if localized_clean:
                    localized_labels[slot] = localized_clean[:120]
        if all(slot in labels for slot in range(1, 20)):
            if localized_data is not None and all(
                slot in localized_labels for slot in labels
            ):
                return localized_labels
            return labels
        return {}
    return {}


_INDEXED_FUNCTION_KEY_INSTRUCTION = re.compile(
    r"\bPress(?:ing)?\s+"
    r"(F(?:[1-9]|1[0-9]|2[0-4]))\s+"
    r"(?:allows?\s+you\s+to|to)\s+"
    r"([^.!?\r\n]{3,180})(?=[.!?]|$)",
    re.IGNORECASE,
)
_INDEXED_CHINESE_FUNCTION_KEY_INSTRUCTION = re.compile(
    r"按\s*[\[【（(]?\s*"
    r"(F(?:[1-9]|1[0-9]|2[0-4]))\s*"
    r"[\]】）)]?\s*键\s*"
    r"([^。！？\r\n]{2,180})(?=[。！？\r\n]|$)",
    re.IGNORECASE,
)


def _indexed_function_key_labels_from_dictionary(
    data: bytes,
) -> dict[str, dict[str, str]]:
    """Extract explicit fixed F-key tutorials from a bounded game dictionary."""
    detected: dict[str, dict[str, str]] = {}
    for entry in _decode_indexed_menu_dictionary(data):
        text = entry.replace("\\n", "\n")
        matches = itertools.chain(
            _INDEXED_FUNCTION_KEY_INSTRUCTION.finditer(text.replace("\n", " ")),
            _INDEXED_CHINESE_FUNCTION_KEY_INSTRUCTION.finditer(text),
        )
        for match in matches:
            action = re.sub(r"\s+", " ", match.group(2)).strip(" <>-")
            if not action:
                continue
            if _contains_latin_letter(action):
                action = action[0].upper() + action[1:]
            _add_detected_mapping(
                detected,
                match.group(1).upper(),
                action,
            )
    # Isolated prose mentions are too weak.  Require F1 plus a consecutive
    # tutorial family of at least three other function keys.
    numbers = sorted(
        int(input_name[1:])
        for input_name in detected
        if input_name != "F1"
    )
    longest_run = 0
    current_run = 0
    previous = -1
    for number in numbers:
        current_run = current_run + 1 if number == previous + 1 else 1
        longest_run = max(longest_run, current_run)
        previous = number
    return detected if "F1" in detected and longest_run >= 3 else {}


def _parse_indexed_xml_keymap(
    text: str,
    action_labels: dict[int, str] | None = None,
) -> _IndexedXmlMapping | None:
    """Parse flat ``tecla_N`` XML mappings identified by their structure."""
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
        return None
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return None
    if root.tag.rsplit("}", 1)[-1].casefold() != "config":
        return None
    elements = list(root.iter())
    if len(elements) > MAX_INDEXED_XML_ELEMENTS:
        return None

    tecla_values: dict[int, int] = {}
    pad_indexes: set[int] = set()
    pad_map_indexes: set[int] = set()
    for element in list(root):
        if list(element):
            continue
        tag = element.tag.rsplit("}", 1)[-1].casefold()
        tecla_match = re.fullmatch(r"tecla_([1-9][0-9]*)", tag)
        pad_match = re.fullmatch(r"pad_([1-9][0-9]*)", tag)
        pad_map_match = re.fullmatch(r"padmap_([1-9][0-9]*)", tag)
        match = tecla_match or pad_match or pad_map_match
        if match is None:
            continue
        index = int(match.group(1))
        if index > MAX_INDEXED_XML_ACTION_SLOTS:
            return None
        content = (element.text or "").strip()
        if not re.fullmatch(r"[0-9]{1,3}", content):
            return None
        value = int(content)
        if value > 255:
            return None
        if tecla_match:
            if index in tecla_values:
                return None
            tecla_values[index] = value
        elif pad_match:
            if index in pad_indexes:
                return None
            pad_indexes.add(index)
        else:
            if index in pad_map_indexes:
                return None
            pad_map_indexes.add(index)

    if not 8 <= len(tecla_values) <= MAX_INDEXED_XML_ACTION_SLOTS:
        return None
    if set(tecla_values) != set(range(1, len(tecla_values) + 1)):
        return None
    signature_size = min(8, len(tecla_values))
    if not set(range(1, signature_size + 1)).issubset(pad_indexes):
        return None
    if not set(range(1, signature_size + 1)).issubset(pad_map_indexes):
        return None

    labels = action_labels or {}
    detected: dict[str, dict[str, str]] = {}
    movement_parts = {1: "up", 2: "down", 3: "right", 4: "left"}
    for slot, value in tecla_values.items():
        if value == 0:
            continue
        input_name = _windows_virtual_key_name(value)
        if input_name is None:
            continue
        action = labels.get(slot, f"game_action_{slot}")
        _add_detected_mapping(
            detected,
            input_name,
            action,
            part=movement_parts.get(slot, "") if slot in labels else "",
        )
    return _IndexedXmlMapping(detected, len(tecla_values))


def _parse_dotnet_input_xml(text: str) -> dict[str, dict[str, str]]:
    """Parse XML-serialized input-button lists used by .NET/MonoGame games."""
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return {}
    detected: dict[str, dict[str, str]] = {}
    for container in root.iter():
        action = container.tag.rsplit("}", 1)[-1]
        normalized_action = re.sub(r"[^a-z0-9]+", "", action.casefold())
        if normalized_action in {"inputbutton", "key", "mouseleft", "mouseright"}:
            continue
        if not _looks_like_action_field(action):
            continue
        clean_action = _clean_action_field(action)
        if not clean_action:
            continue
        for value in container.iter():
            tag = value.tag.rsplit("}", 1)[-1].casefold()
            content = (value.text or "").strip()
            if tag == "key" and content.casefold() not in {"none", "0"}:
                _add_detected_mapping(detected, content, clean_action)
            elif tag == "mouseleft" and content.casefold() == "true":
                _add_detected_mapping(detected, "leftClick", clean_action)
            elif tag == "mouseright" and content.casefold() == "true":
                _add_detected_mapping(detected, "rightClick", clean_action)
    return detected


def _cil_integer(instruction: object) -> int | None:
    name = getattr(getattr(instruction, "opcode", None), "name", "")
    if name == "ldc.i4.m1":
        return -1
    match = re.fullmatch(r"ldc\.i4\.([0-8])", name)
    if match:
        return int(match.group(1))
    if name in {"ldc.i4", "ldc.i4.s"}:
        operand = getattr(instruction, "operand", None)
        return int(operand) if isinstance(operand, int) else None
    return None


def _parse_dotnet_embedded_keymap(path: Path) -> dict[str, dict[str, str]]:
    """Extract default key lists from managed IL by structure, not game identity."""
    import dnfile
    from dncil.cil.body.reader import read_method_body_from_bytes

    assembly = dnfile.dnPE(str(path))
    if assembly.net is None:
        return {}
    input_constructor_kinds: dict[int, str] = {}
    for type_row in assembly.net.mdtables.TypeDef.rows:
        type_name = str(type_row.TypeName)
        type_fields = {str(item.row.Name).casefold() for item in type_row.FieldList}
        input_like = any(word in type_name.casefold() for word in ("input", "button", "key", "bind"))
        has_mouse_flags = {"mouseleft", "mouseright"}.issubset(type_fields)
        for method_index in type_row.MethodList:
            method = method_index.row
            if not input_like or str(method.Name) != ".ctor":
                continue
            signature = bytes(method.Signature.value)
            if has_mouse_flags and signature.endswith(b"\x02"):
                input_constructor_kinds[method_index.row_index] = "mouse"
            else:
                input_constructor_kinds[method_index.row_index] = "key"

    for type_row in assembly.net.mdtables.TypeDef.rows:
        field_indexes = {item.row_index: str(item.row.Name) for item in type_row.FieldList}
        action_fields = {
            index: name for index, name in field_indexes.items() if _looks_like_action_field(name)
        }
        if len(action_fields) < 4:
            continue
        methods = [item.row for item in type_row.MethodList if item.row.Rva]
        methods.sort(
            key=lambda method: (
                0
                if "default" in str(method.Name).casefold()
                and any(word in str(method.Name).casefold() for word in ("control", "input", "key", "bind"))
                else 1 if str(method.Name) == ".ctor" else 2
            )
        )
        for method in methods:
            method_name = str(method.Name).casefold()
            if not (
                str(method.Name) == ".ctor"
                or "default" in method_name
                and any(word in method_name for word in ("control", "input", "key", "bind"))
            ):
                continue
            try:
                body = read_method_body_from_bytes(assembly.get_data(method.Rva, 0x10000))
            except Exception:
                continue
            detected: dict[str, dict[str, str]] = {}
            block_start = 0
            for index, instruction in enumerate(body.instructions):
                if getattr(instruction.opcode, "name", "") != "stfld":
                    continue
                token = getattr(getattr(instruction, "operand", None), "value", 0)
                field_index = token & 0x00FFFFFF if token >> 24 == 0x04 else 0
                field_name = action_fields.get(field_index)
                if not field_name:
                    block_start = index + 1
                    continue
                action = _clean_action_field(field_name)
                for new_index in range(block_start, index):
                    new_instruction = body.instructions[new_index]
                    if getattr(new_instruction.opcode, "name", "") != "newobj":
                        continue
                    new_token = getattr(getattr(new_instruction, "operand", None), "value", 0)
                    method_index = new_token & 0x00FFFFFF if new_token >> 24 == 0x06 else 0
                    kind = input_constructor_kinds.get(method_index)
                    if kind is None:
                        continue
                    value = None
                    for prior in range(new_index - 1, max(block_start - 1, new_index - 4), -1):
                        value = _cil_integer(body.instructions[prior])
                        if value is not None:
                            break
                    if value is None:
                        continue
                    raw_input = (
                        "leftClick" if kind == "mouse" and value == 0
                        else "rightClick" if kind == "mouse" and value == 1
                        else _xna_key_name(value) if kind == "key"
                        else None
                    )
                    _add_detected_mapping(detected, raw_input, action)
                block_start = index + 1
            if len(detected) >= 4:
                return detected
    return {}


_VALVE_INPUT_ALIASES = {
    "MOUSE1": "leftClick",
    "MOUSE2": "rightClick",
    "MOUSE3": "middleClick",
    "MOUSE4": "mouseButton4",
    "MOUSE5": "mouseButton5",
    "MWHEELUP": "mouseWheelUp",
    "MWHEELDOWN": "mouseWheelDown",
    "INS": "Insert",
    "DEL": "Delete",
    "PGUP": "PageUp",
    "PGDN": "PageDown",
    "KP_INS": "NumPad0",
    "KP_END": "NumPad1",
    "KP_DOWNARROW": "NumPad2",
    "KP_PGDN": "NumPad3",
    "KP_LEFTARROW": "NumPad4",
    "KP_5": "NumPad5",
    "KP_RIGHTARROW": "NumPad6",
    "KP_HOME": "NumPad7",
    "KP_UPARROW": "NumPad8",
    "KP_PGUP": "NumPad9",
    "KP_SLASH": "NumPadDivide",
    "KP_MULTIPLY": "NumPadMultiply",
    "KP_MINUS": "NumPadSubtract",
    "KP_PLUS": "NumPadAdd",
    "KP_ENTER": "Enter",
    "KP_DEL": "NumPadDecimal",
}

_VALVE_BASE_CONFIG_NAMES = {
    "config.cfg",
    "config_default.cfg",
    "config_default_pc.cfg",
    "default.cfg",
}
_VALVE_OVERLAY_CONFIG_NAMES = {"userconfig.cfg", "autoexec.cfg"}
_VALVE_CONFIG_NAMES = _VALVE_BASE_CONFIG_NAMES | _VALVE_OVERLAY_CONFIG_NAMES


def _parse_valve_action_list(text: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for match in re.finditer(
        r'(?m)^\s*"([^"]+)"\s+"([^"]+)"\s*(?://.*)?$',
        text,
    ):
        command = match.group(1).strip().casefold()
        label = match.group(2).strip()
        if command and label:
            labels[command] = label[:120]
    return labels


def _parse_valve_bind_cfg(
    text: str,
    action_labels: dict[str, str] | None = None,
) -> dict[str, dict[str, str]]:
    detected: dict[str, dict[str, str]] = {}
    _apply_valve_bind_cfg(detected, text, action_labels)
    return detected


def _apply_valve_bind_cfg(
    detected: dict[str, dict[str, str]],
    text: str,
    action_labels: dict[str, str] | None = None,
) -> bool:
    """Apply Valve bind/unbind commands in execution order to a key map."""
    labels = action_labels or {}
    recognized = False
    for line in text.splitlines():
        command_text = line.strip()
        if re.match(r"^unbindall(?:\s*(?://.*)?)?$", command_text, re.I):
            detected.clear()
            recognized = True
            continue
        unbind_match = re.match(
            r'^unbind\s+(?:"([^"]+)"|(\S+))\s*(?://.*)?$',
            command_text,
            re.IGNORECASE,
        )
        if unbind_match is not None:
            canonical = _canonical_input_name(
                _VALVE_INPUT_ALIASES.get(
                    (unbind_match.group(1) or unbind_match.group(2) or "").upper(),
                    unbind_match.group(1) or unbind_match.group(2),
                )
            )
            if canonical is not None:
                detected.pop(canonical[0], None)
            recognized = True
            continue
        match = re.match(
            r'^\s*bind\s+(?:"([^"]+)"|(\S+))\s+(?:"([^"]*)"|(.+?))\s*$',
            line,
            re.IGNORECASE,
        )
        if match is None:
            continue
        recognized = True
        raw_input = (match.group(1) or match.group(2) or "").strip()
        command_line = (match.group(3) or match.group(4) or "").strip()
        if not raw_input or not command_line:
            continue
        first_command = command_line.split(";", 1)[0].strip().split(None, 1)[0]
        normalized_command = first_command.casefold()
        action = labels.get(normalized_command, command_line)
        input_name = _VALVE_INPUT_ALIASES.get(raw_input.upper(), raw_input)
        parsed_mapping: dict[str, dict[str, str]] = {}
        if not _add_detected_mapping(parsed_mapping, input_name, action):
            continue
        canonical = _canonical_input_name(input_name)
        if canonical is None:
            continue
        direction = _movement_direction(normalized_command, canonical[0])
        if direction:
            parsed_mapping[canonical[0]]["movement_direction"] = direction
        detected[canonical[0]] = parsed_mapping[canonical[0]]
    return recognized


def _read_game_config(path: Path) -> str:
    data = _read_bounded_bytes(path, MAX_GAME_CONFIG_FILE_BYTES)
    if len(data) > MAX_GAME_CONFIG_FILE_BYTES:
        raise ValueError("配置文件过大")
    encodings = ["utf-8-sig"]
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings.insert(0, "utf-16")
    encodings.extend(("gb18030", "cp1252"))
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("不支持的文本编码")


_KLEI_PROFILE_ACTIONS_V10: dict[int, str] = {
    4: "视角左转",
    7: "视角右转",
    14: "打开地图",
    16: "物品栏快捷槽1",
    17: "物品栏快捷槽2",
    18: "物品栏快捷槽3",
    19: "物品栏快捷槽4",
    20: "物品栏快捷槽5",
    21: "物品栏快捷槽6",
    22: "物品栏快捷槽7",
    23: "物品栏快捷槽8",
    24: "物品栏快捷槽9",
    25: "物品栏快捷槽10",
    26: "物品栏快捷槽11",
    27: "物品栏快捷槽12",
    28: "主要操作",
    29: "次要操作",
    32: "强制检查修饰键",
    34: "物品堆叠修饰键",
    35: "强制攻击修饰键",
    36: "自动攻击",
    37: "自动操作",
    38: "打开控制台",
    40: "调试信息",
    41: "缩小视角",
    42: "放大视角",
    44: "打开制作栏",
    61: "聊天",
    62: "私聊",
    63: "玩家状态",
    64: "斜杠命令",
    70: "检查自己",
    71: "服务器暂停",
    72: "制作栏搜索修饰键",
    73: "制作栏左固定",
    74: "制作栏右固定",
    87: "锁定目标",
    88: "轴对齐放置修饰键",
    89: "切换放置网格",
}


def _klei_input_name(code: int) -> tuple[str, str] | None:
    if 65 <= code <= 90:
        return chr(code), "keyboard"
    if 97 <= code <= 122:
        return chr(code).upper(), "keyboard"
    if 48 <= code <= 57:
        return chr(code), "keyboard"
    aliases: dict[int, tuple[str, str]] = {
        8: ("Backspace", "keyboard"),
        9: ("Tab", "keyboard"),
        13: ("Enter", "keyboard"),
        19: ("Pause", "keyboard"),
        27: ("Esc", "keyboard"),
        32: ("Space", "keyboard"),
        37: ("Left", "keyboard"),
        38: ("Up", "keyboard"),
        39: ("Right", "keyboard"),
        40: ("Down", "keyboard"),
        44: (",", "keyboard"),
        45: ("-", "keyboard"),
        46: (".", "keyboard"),
        47: ("/", "keyboard"),
        59: (";", "keyboard"),
        61: ("=", "keyboard"),
        91: ("[", "keyboard"),
        92: ("\\", "keyboard"),
        93: ("]", "keyboard"),
        96: ("Tilde", "keyboard"),
        127: ("Delete", "keyboard"),
        160: ("Shift", "keyboard"),
        161: ("Shift", "keyboard"),
        162: ("Ctrl", "keyboard"),
        163: ("Ctrl", "keyboard"),
        164: ("Alt", "keyboard"),
        165: ("Alt", "keyboard"),
        186: (";", "keyboard"),
        187: ("=", "keyboard"),
        188: (",", "keyboard"),
        189: ("-", "keyboard"),
        190: (".", "keyboard"),
        191: ("/", "keyboard"),
        192: ("Tilde", "keyboard"),
        219: ("[", "keyboard"),
        220: ("\\", "keyboard"),
        221: ("]", "keyboard"),
        273: ("Up", "keyboard"),
        274: ("Down", "keyboard"),
        275: ("Right", "keyboard"),
        276: ("Left", "keyboard"),
        277: ("Insert", "keyboard"),
        278: ("Home", "keyboard"),
        279: ("End", "keyboard"),
        280: ("PageUp", "keyboard"),
        281: ("PageDown", "keyboard"),
        303: ("Shift", "keyboard"),
        304: ("Shift", "keyboard"),
        305: ("Ctrl", "keyboard"),
        306: ("Ctrl", "keyboard"),
        307: ("Alt", "keyboard"),
        308: ("Alt", "keyboard"),
        400: ("Alt", "keyboard"),
        401: ("Ctrl", "keyboard"),
        402: ("Shift", "keyboard"),
        1000: ("leftClick", "mouse"),
        1001: ("rightClick", "mouse"),
        1002: ("middleClick", "mouse"),
        1003: ("mouseWheelUp", "mouse"),
        1004: ("mouseWheelDown", "mouse"),
    }
    if code in aliases:
        return aliases[code]
    if 112 <= code <= 123:
        return f"F{code - 111}", "keyboard"
    if 282 <= code <= 293:
        return f"F{code - 281}", "keyboard"
    return None


def _decode_klei_control_blob_v10(
    encoded: str,
) -> tuple[list[list[tuple[int, int]]], list[list[tuple[int, int]]]]:
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) < 12 or len(raw) % 4:
        raise ValueError("Klei键位数据长度无效")
    values = struct.unpack(f"<{len(raw) // 4}I", raw)
    if values[0] != 10 or values[1] != 7:
        raise ValueError("不支持的Klei键位档案版本")
    control_count = int(values[2])
    if not 1 <= control_count <= 256:
        raise ValueError("Klei键位数量无效")
    position = 3

    def read_mappings(count: int) -> list[list[tuple[int, int]]]:
        nonlocal position
        result: list[list[tuple[int, int]]] = []
        for _ in range(count):
            if position >= len(values):
                raise ValueError("Klei键位数据不完整")
            input_count = int(values[position])
            position += 1
            if input_count > 8 or position + input_count * 2 > len(values):
                raise ValueError("Klei键位输入数量无效")
            inputs = []
            for _ in range(input_count):
                inputs.append((int(values[position]), int(values[position + 1])))
                position += 2
            result.append(inputs)
        return result

    controls = read_mappings(control_count)
    movement: list[list[tuple[int, int]]] = []
    if position < len(values):
        movement_count = int(values[position])
        position += 1
        if 0 <= movement_count <= 16:
            movement = read_mappings(movement_count)
    return controls, movement


def _decode_klei_profile(path: Path) -> dict[str, dict[str, str]]:
    maximum_payload_size = 16 * 1024**2
    if path.stat().st_size > maximum_payload_size:
        raise ValueError("Klei玩家档案过大")
    text = path.read_text(encoding="ascii")
    if not text.startswith("KLEI") or len(text) < 16:
        raise ValueError("不是KLEI玩家档案")
    encoded = text[11:].strip()
    envelope = base64.b64decode(encoded, validate=True)
    if len(envelope) < 18:
        raise ValueError("KLEI玩家档案不完整")
    _, header_size, payload_size, compressed_size = struct.unpack(
        "<IIII",
        envelope[:16],
    )
    if (
        header_size != 16
        or payload_size > maximum_payload_size
        or compressed_size != len(envelope) - 16
    ):
        raise ValueError("KLEI玩家档案头无效")
    decompressor = zlib.decompressobj()
    payload_bytes = decompressor.decompress(
        envelope[16:],
        maximum_payload_size + 1,
    )
    if (
        len(payload_bytes) != payload_size
        or len(payload_bytes) > maximum_payload_size
        or not decompressor.eof
    ):
        raise ValueError("KLEI玩家档案解压长度无效")
    payload = json.loads(payload_bytes.decode("utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("controls"), list):
        raise ValueError("KLEI玩家档案没有键位数据")

    best: tuple[list[list[tuple[int, int]]], list[list[tuple[int, int]]]] | None = None
    best_score = -1
    for device in payload["controls"]:
        if not isinstance(device, dict) or not isinstance(device.get("data"), str):
            continue
        try:
            controls, movement = _decode_klei_control_blob_v10(device["data"])
        except (ValueError, TypeError, binascii.Error, struct.error):
            continue
        score = sum(
            1
            for mapping in movement[:4]
            for code, modifier in mapping
            if modifier == 0
            and (_klei_input_name(code) or (None, None))[1] == "keyboard"
        )
        if score > best_score:
            best_score = score
            best = controls, movement
    if best is None or best_score <= 0:
        raise ValueError("没有找到Klei键鼠映射")

    controls, movement = best
    detected: dict[str, dict[str, str]] = {}
    for control_index, action in _KLEI_PROFILE_ACTIONS_V10.items():
        if control_index >= len(controls):
            continue
        for code, modifier in controls[control_index]:
            if modifier:
                continue
            canonical = _klei_input_name(code)
            if canonical is None:
                continue
            input_name, device_type = canonical
            _add_detected_mapping(detected, input_name, action)
            if input_name in detected:
                detected[input_name]["type"] = device_type
    movement_directions = ("L", "R", "B", "W")
    movement_actions = {"L": "左移", "R": "右移", "B": "后退", "W": "前进"}
    for mapping, direction in zip(movement[:4], movement_directions):
        for code, modifier in mapping:
            if modifier:
                continue
            canonical = _klei_input_name(code)
            if canonical is None or canonical[1] != "keyboard":
                continue
            input_name = canonical[0]
            _add_detected_mapping(detected, input_name, movement_actions[direction])
            detected[input_name]["movement_direction"] = direction
    return detected


def _windows_documents_directory() -> Path:
    if sys.platform == "win32":
        buffer = ctypes.create_unicode_buffer(32768)
        try:
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0:
                return Path(buffer.value)
        except (AttributeError, OSError):
            pass
    return Path.home() / "Documents"


_KLEI_CONTROL_SCHEMA_MARKERS = (
    b"CONTROL_MOVE_UP",
    b"CONTROL_ATTACK",
    b"CONTROL_ACTION",
    b"CONTROL_ROTATE_LEFT",
    b"CONTROL_MAP",
    b"MOUSEBUTTON_LEFT",
)


def _klei_profile_roots() -> list[Path]:
    bases = [_windows_documents_directory() / "Klei"]
    one_drive = os.environ.get("OneDrive")
    if one_drive:
        bases.append(Path(one_drive) / "Documents" / "Klei")
    roots: list[Path] = []
    seen: set[Path] = set()
    for base in bases:
        try:
            resolved = base.resolve()
        except OSError:
            resolved = base
        if resolved in seen or not base.is_dir():
            continue
        seen.add(resolved)
        try:
            roots.extend(path for path in base.iterdir() if path.is_dir())
        except OSError:
            continue
    return roots


def _normalized_product_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _profile_product_name(profile: Path) -> str:
    parts = profile.parts
    for index, part in enumerate(parts[:-1]):
        if part.casefold() == "klei" and index + 1 < len(parts):
            return parts[index + 1]
    try:
        return profile.parents[2].name
    except IndexError:
        return ""


def _compatible_klei_scripts_archive(game_directory: Path) -> Path | None:
    preferred = [
        game_directory / "data" / "databundles" / "scripts.zip",
        game_directory / "data" / "scripts.zip",
        game_directory / "scripts.zip",
    ]
    candidates: list[Path] = []
    seen: set[Path] = set()
    for path in preferred:
        if path.is_file():
            candidates.append(path)
            seen.add(path.resolve())
    if not candidates:
        base_depth = len(game_directory.parts)
        visited = 0
        for current_root, directory_names, file_names in os.walk(
            game_directory,
            followlinks=False,
        ):
            _prune_walk_links(current_root, directory_names, file_names)
            visited += len(directory_names) + len(file_names)
            depth = len(Path(current_root).parts) - base_depth
            if depth >= 5 or visited > MAX_GAME_DIRECTORY_ENTRIES:
                directory_names[:] = []
            for file_name in file_names:
                if file_name.casefold() != "scripts.zip":
                    continue
                path = Path(current_root) / file_name
                if _is_link_or_junction_path(path):
                    continue
                try:
                    resolved = path.resolve()
                except OSError:
                    continue
                if resolved not in seen:
                    candidates.append(path)
                    seen.add(resolved)
    for scripts_archive in candidates:
        try:
            with zipfile.ZipFile(scripts_archive) as archive:
                constants_info = next(
                    (
                        info
                        for info in archive.infolist()
                        if info.filename.replace("\\", "/").casefold().endswith(
                            "/constants.lua"
                        )
                        or info.filename.casefold() == "constants.lua"
                    ),
                    None,
                )
                if constants_info is None or constants_info.file_size > 1024**2:
                    continue
                constants = archive.read(constants_info)
            if all(marker in constants for marker in _KLEI_CONTROL_SCHEMA_MARKERS):
                return scripts_archive
        except (OSError, KeyError, zipfile.BadZipFile):
            continue
    return None


def discover_klei_keymap(
    game_directory: Path,
    profile_roots: list[Path] | None = None,
) -> KeymapDiscovery:
    scripts_archive = _compatible_klei_scripts_archive(game_directory)
    if scripts_archive is None:
        return KeymapDiscovery({}, (), 0, False)

    roots = profile_roots if profile_roots is not None else _klei_profile_roots()
    profiles: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        profiles.extend(root.glob("*/client_save/profile"))
        profiles.extend(root.glob("*/*/client_save/profile"))
        direct_profile = root / "client_save" / "profile"
        if direct_profile.is_file():
            profiles.append(direct_profile)
    game_name = _normalized_product_name(game_directory.name)
    profile_records: list[tuple[bool, float, Path]] = []
    for path in {path.resolve() for path in profiles if path.is_file()}:
        try:
            product_name = _normalized_product_name(_profile_product_name(path))
            profile_records.append((product_name == game_name, path.stat().st_mtime, path))
        except OSError:
            continue
    profiles = [
        path
        for _, _, path in sorted(
            profile_records,
            key=lambda item: (item[0], item[1]),
            reverse=True,
        )
    ]
    for profile in profiles:
        try:
            detected = _decode_klei_profile(profile)
        except (OSError, ValueError, json.JSONDecodeError, zlib.error, binascii.Error):
            continue
        return KeymapDiscovery(
            detected,
            (scripts_archive, profile),
            2,
            False,
        )
    return KeymapDiscovery({}, (scripts_archive,), 1, False)


def _addressables_int32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError("Addressables目录数据不完整")
    return struct.unpack_from("<i", data, offset)[0]


def _addressables_key_object(data: bytes, offset: int) -> object:
    if offset < 0 or offset >= len(data):
        raise ValueError("Addressables键数据偏移无效")
    object_type = data[offset]
    offset += 1
    if object_type in {0, 1}:
        length = _addressables_int32(data, offset)
        start = offset + 4
        if length < 0 or start + length > len(data):
            raise ValueError("Addressables字符串长度无效")
        encoding = "ascii" if object_type == 0 else "utf-16-le"
        return data[start : start + length].decode(encoding)
    if object_type == 2:
        if offset + 2 > len(data):
            raise ValueError("Addressables UInt16数据不完整")
        return struct.unpack_from("<H", data, offset)[0]
    if object_type == 3:
        if offset + 4 > len(data):
            raise ValueError("Addressables UInt32数据不完整")
        return struct.unpack_from("<I", data, offset)[0]
    if object_type == 4:
        return _addressables_int32(data, offset)
    raise ValueError("不支持的Addressables键类型")


def _addressables_input_bundle_candidates(catalog_path: Path) -> list[Path]:
    if catalog_path.stat().st_size > MAX_UNITY_CATALOG_BYTES:
        raise ValueError("Unity Addressables目录过大")
    document = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict):
        raise ValueError("Unity Addressables目录格式无效")
    internal_ids = document.get("m_InternalIds")
    provider_ids = document.get("m_ProviderIds")
    if not isinstance(internal_ids, list) or not isinstance(provider_ids, list):
        raise ValueError("Unity Addressables目录缺少资源索引")
    try:
        key_data = base64.b64decode(document["m_KeyDataString"], validate=True)
        bucket_data = base64.b64decode(document["m_BucketDataString"], validate=True)
        entry_data = base64.b64decode(document["m_EntryDataString"], validate=True)
    except (KeyError, TypeError, binascii.Error) as exc:
        raise ValueError("Unity Addressables目录编码无效") from exc

    bucket_count = _addressables_int32(bucket_data, 0)
    if not 1 <= bucket_count <= 1_000_000:
        raise ValueError("Unity Addressables键数量无效")
    buckets: list[tuple[int, list[int]]] = []
    position = 4
    total_references = 0
    for _ in range(bucket_count):
        data_offset = _addressables_int32(bucket_data, position)
        entry_count = _addressables_int32(bucket_data, position + 4)
        position += 8
        if entry_count < 0 or entry_count > 100_000:
            raise ValueError("Unity Addressables资源引用数量无效")
        total_references += entry_count
        if total_references > 4_000_000 or position + entry_count * 4 > len(bucket_data):
            raise ValueError("Unity Addressables资源引用过多")
        entries = [
            _addressables_int32(bucket_data, position + index * 4)
            for index in range(entry_count)
        ]
        position += entry_count * 4
        buckets.append((data_offset, entries))
    keys = [_addressables_key_object(key_data, offset) for offset, _ in buckets]

    entry_count = _addressables_int32(entry_data, 0)
    if not 1 <= entry_count <= 1_000_000 or 4 + entry_count * 28 > len(entry_data):
        raise ValueError("Unity Addressables资源数量无效")
    entries = [
        struct.unpack_from("<7i", entry_data, 4 + index * 28)
        for index in range(entry_count)
    ]
    input_key_indexes = [
        index
        for index, key in enumerate(keys)
        if isinstance(key, str) and key.casefold().endswith(".inputactions")
    ]
    input_key_indexes.sort(
        key=lambda index: (
            str(keys[index]).replace("\\", "/").casefold().startswith("packages/"),
            str(keys[index]).casefold(),
        )
    )

    catalog_root = catalog_path.parent.resolve()
    candidates: list[Path] = []
    seen: set[Path] = set()
    for key_index in input_key_indexes:
        for asset_entry_index in buckets[key_index][1]:
            if not 0 <= asset_entry_index < len(entries):
                continue
            dependency_key_index = entries[asset_entry_index][2]
            if not 0 <= dependency_key_index < len(buckets):
                continue
            dependency_candidates = 0
            for bundle_entry_index in buckets[dependency_key_index][1]:
                if not 0 <= bundle_entry_index < len(entries):
                    continue
                internal_id_index, provider_index = entries[bundle_entry_index][:2]
                if not (
                    0 <= internal_id_index < len(internal_ids)
                    and 0 <= provider_index < len(provider_ids)
                ):
                    continue
                if "assetbundleprovider" not in str(provider_ids[provider_index]).casefold():
                    continue
                internal_id = str(internal_ids[internal_id_index])
                match = re.search(r"([^\\/]+\.bundle)$", internal_id, re.IGNORECASE)
                if match is None:
                    continue
                relative_text = internal_id.split("}", 1)[-1].lstrip("\\/")
                relative_parts = [
                    part
                    for part in re.split(r"[\\/]", relative_text)
                    if part and part not in {".", ".."}
                ]
                if not relative_parts:
                    continue
                bundle_path = catalog_root.joinpath(*relative_parts).resolve()
                if not bundle_path.is_relative_to(catalog_root) or not bundle_path.is_file():
                    continue
                dependency_candidates += 1
                if bundle_path not in seen:
                    seen.add(bundle_path)
                    candidates.append(bundle_path)
                if dependency_candidates >= 4 or len(candidates) >= 32:
                    break
            if candidates:
                break
        if candidates:
            break
    return candidates


def discover_unity_addressables_keymap(game_directory: Path) -> KeymapDiscovery:
    catalogs = [
        data_directory / "StreamingAssets" / "aa" / "catalog.json"
        for data_directory in game_directory.glob("*_Data")
        if data_directory.is_dir()
    ]
    scanned = 0
    for catalog_path in catalogs:
        if not catalog_path.is_file():
            continue
        scanned += 1
        try:
            bundle_candidates = _addressables_input_bundle_candidates(catalog_path)
        except (OSError, ValueError, json.JSONDecodeError, struct.error):
            continue
        for bundle_path in bundle_candidates:
            scanned += 1
            try:
                if bundle_path.stat().st_size > MAX_UNITY_INPUT_BUNDLE_BYTES:
                    continue
                import UnityPy

                environment = UnityPy.load(str(bundle_path))
                detected: dict[str, dict[str, str]] = {}
                for asset_object in environment.objects:
                    if asset_object.type.name != "MonoBehaviour":
                        continue
                    try:
                        parsed = _parse_unity_serialized_input_actions(
                            asset_object.parse_as_dict()
                        )
                    except Exception:
                        continue
                    for input_name, mapping in parsed.items():
                        existing = detected.get(input_name)
                        if existing is None:
                            detected[input_name] = dict(mapping)
                            continue
                        actions = [
                            item.strip() for item in existing["action"].split(" / ")
                        ]
                        for action in mapping["action"].split(" / "):
                            action = action.strip()
                            if action and action not in actions:
                                actions.append(action)
                        existing["action"] = " / ".join(actions)[:240]
                        if mapping.get("movement_direction"):
                            existing["movement_direction"] = mapping["movement_direction"]
                if detected:
                    return KeymapDiscovery(
                        detected,
                        (catalog_path, bundle_path),
                        scanned,
                        False,
                    )
            except Exception:
                continue
    return KeymapDiscovery({}, (), scanned, False)


def discover_unity_binary_input_manager(game_directory: Path) -> KeymapDiscovery:
    """Read legacy Unity bindings embedded in binary player data."""
    manager_files = [
        data_directory / "globalgamemanagers"
        for data_directory in game_directory.glob("*_Data")
        if data_directory.is_dir()
    ]
    scanned = 0
    sources: list[Path] = []
    detected: dict[str, dict[str, str]] = {}
    for manager_path in manager_files:
        if not manager_path.is_file():
            continue
        scanned += 1
        try:
            if manager_path.stat().st_size > MAX_UNITY_INPUT_BUNDLE_BYTES:
                continue
            import UnityPy

            environment = UnityPy.load(str(manager_path))
            for asset_object in environment.objects:
                if asset_object.type.name != "InputManager":
                    continue
                parsed = _parse_unity_serialized_input_manager(
                    asset_object.parse_as_dict()
                )
                if not parsed:
                    continue
                if manager_path not in sources:
                    sources.append(manager_path)
                for input_name, mapping in parsed.items():
                    _add_detected_mapping(
                        detected,
                        input_name,
                        mapping["action"],
                        part=mapping.get("movement_direction", ""),
                    )
                    if mapping.get("movement_direction") and input_name in detected:
                        detected[input_name]["movement_direction"] = mapping[
                            "movement_direction"
                        ]
        except Exception:
            continue
    return KeymapDiscovery(detected, tuple(sources), scanned, False)


def _bounded_identity_config_roots(
    game_directory: Path,
) -> tuple[list[Path], bool]:
    identities = {
        re.sub(r"[^a-z0-9]+", "", game_directory.name.casefold()),
    }
    truncated = False
    try:
        executables = game_directory.glob("*.exe")
        for index, executable in enumerate(executables):
            if index >= MAX_GAME_DIRECTORY_ENTRIES:
                truncated = True
                break
            identities.add(
                re.sub(r"[^a-z0-9]+", "", executable.stem.casefold())
            )
    except (OSError, RuntimeError):
        pass
    identities.discard("")
    appdata = os.environ.get("APPDATA")
    local_appdata = os.environ.get("LOCALAPPDATA")
    bases = [Path(value) for value in (appdata, local_appdata) if value]
    if local_appdata:
        bases.append(Path(local_appdata).parent / "LocalLow")
    matches: list[Path] = []
    visited_entries = 0
    started = time.monotonic()
    stop_scanning = False

    def directory_entries(path: Path):
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    entry_path = Path(entry.path)
                    try:
                        linked = entry.is_symlink() or entry_path.is_junction()
                        is_directory = (
                            not linked and entry.is_dir(follow_symlinks=False)
                        )
                    except OSError:
                        is_directory = False
                    yield entry_path, is_directory
        except OSError:
            return

    for base in dict.fromkeys(bases):
        if not base.is_dir():
            continue
        for candidate, is_directory in directory_entries(base):
            visited_entries += 1
            if (
                visited_entries > MAX_GAME_DIRECTORY_ENTRIES
                or time.monotonic() - started > MAX_INDEXED_IDENTITY_SCAN_SECONDS
            ):
                truncated = True
                stop_scanning = True
                break
            if not is_directory:
                continue
            normalized = re.sub(r"[^a-z0-9]+", "", candidate.name.casefold())
            if normalized in identities:
                matches.append(candidate)
                continue
            for child, is_directory in directory_entries(candidate):
                visited_entries += 1
                if (
                    visited_entries > MAX_GAME_DIRECTORY_ENTRIES
                    or time.monotonic() - started
                    > MAX_INDEXED_IDENTITY_SCAN_SECONDS
                ):
                    truncated = True
                    stop_scanning = True
                    break
                if not is_directory:
                    continue
                child_name = re.sub(r"[^a-z0-9]+", "", child.name.casefold())
                if child_name in identities:
                    matches.append(child)
            if stop_scanning:
                break
        if stop_scanning:
            break
    return list(dict.fromkeys(matches)), truncated


def _identity_config_roots(game_directory: Path) -> list[Path]:
    roots, _truncated = _bounded_identity_config_roots(game_directory)
    return roots


_PIXPIL_GARCHIVE_MAGIC = 0x6A37
_PIXPIL_GAMEPAD_INPUTS = {
    "a": "gamepadA",
    "b": "gamepadB",
    "x": "gamepadX",
    "y": "gamepadY",
    "lb": "gamepadLeftShoulder",
    "rb": "gamepadRightShoulder",
    "lt": "gamepadLeftTrigger",
    "rt": "gamepadRightTrigger",
    "l3": "gamepadLeftThumb",
    "r3": "gamepadRightThumb",
    "start": "gamepadStart",
    "back": "gamepadBack",
    "up": "gamepadDpadUp",
    "down": "gamepadDpadDown",
    "left": "gamepadDpadLeft",
    "right": "gamepadDpadRight",
    "lup": "gamepadLeftStickUp",
    "ldown": "gamepadLeftStickDown",
    "lleft": "gamepadLeftStickLeft",
    "lright": "gamepadLeftStickRight",
    "rup": "gamepadRightStickUp",
    "rdown": "gamepadRightStickDown",
    "rleft": "gamepadRightStickLeft",
    "rright": "gamepadRightStickRight",
}
_PIXPIL_MOUSE_INPUTS = {
    "left": "leftClick",
    "right": "rightClick",
    "middle": "middleClick",
}
_PIXPIL_KEYBOARD_INPUTS = {
    "lshift": "Shift",
    "rshift": "Shift",
    "lctrl": "Ctrl",
    "rctrl": "Ctrl",
    "lalt": "Alt",
    "ralt": "Alt",
}
_PIXPIL_GAMEPAD_STRONG_INPUTS = {
    key
    for key in _PIXPIL_GAMEPAD_INPUTS
    if key not in {"a", "b", "x", "y", "up", "down", "left", "right"}
}


def _read_bounded_bytes(path: Path, maximum_size: int) -> bytes:
    if maximum_size < 0:
        raise ValueError("Invalid file-size limit")
    if _is_link_or_junction_path(path):
        raise ValueError("Linked files are not accepted")
    with path.open("rb") as stream:
        data = stream.read(maximum_size + 1)
    if len(data) > maximum_size:
        raise ValueError("File exceeds the safety limit")
    return data


def _read_pixpil_garchive(
    path: Path,
) -> tuple[bytes, dict[str, _PixpilArchiveEntry]]:
    data = _read_bounded_bytes(path, MAX_PIXPIL_ARCHIVE_BYTES)
    if len(data) < 8:
        raise ValueError("Pixpil GArchive size is outside the safety limit")
    magic, count = struct.unpack_from("<II", data, 0)
    if magic != _PIXPIL_GARCHIVE_MAGIC or not 1 <= count <= MAX_PIXPIL_ARCHIVE_ENTRIES:
        raise ValueError("Invalid Pixpil GArchive header")
    position = 8
    entries: dict[str, _PixpilArchiveEntry] = {}
    for _index in range(count):
        name_end_limit = min(len(data), position + 512)
        name_end = data.find(b"\0", position, name_end_limit)
        if name_end < 0:
            raise ValueError("Invalid Pixpil GArchive entry name")
        try:
            name = data[position:name_end].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Invalid Pixpil GArchive entry encoding") from exc
        position = name_end + 1
        if not name or position + 16 > len(data) or name in entries:
            raise ValueError("Invalid Pixpil GArchive entry table")
        offset, codec, raw_size, stored_size = struct.unpack_from(
            "<IIII", data, position
        )
        position += 16
        if offset > len(data) or stored_size > len(data) - offset:
            raise ValueError("Pixpil GArchive entry points outside the archive")
        entries[name] = _PixpilArchiveEntry(
            name,
            offset,
            codec,
            raw_size,
            stored_size,
        )
    previous_end = position
    for entry in sorted(entries.values(), key=lambda item: item.offset):
        if entry.offset < previous_end:
            raise ValueError("Pixpil GArchive entries overlap their header or data")
        previous_end = entry.offset + entry.stored_size
    return data, entries


def _extract_pixpil_garchive_entry(
    archive_data: bytes,
    entry: _PixpilArchiveEntry,
    *,
    maximum_size: int = MAX_PIXPIL_ENTRY_BYTES,
) -> bytes:
    if (
        entry.uncompressed_size <= 0
        or entry.stored_size <= 0
        or entry.uncompressed_size > maximum_size
        or entry.stored_size > maximum_size
    ):
        raise ValueError("Pixpil GArchive entry exceeds the safety limit")
    stored = archive_data[entry.offset : entry.offset + entry.stored_size]
    if entry.codec == 2:
        try:
            frame_size = zstandard.frame_content_size(stored)
            if frame_size == zstandard.CONTENTSIZE_ERROR:
                raise ValueError("Invalid Zstandard frame in Pixpil GArchive")
            if (
                frame_size != zstandard.CONTENTSIZE_UNKNOWN
                and frame_size != entry.uncompressed_size
            ):
                raise ValueError("Pixpil GArchive entry declares the wrong output size")
            payload = zstandard.ZstdDecompressor().decompress(
                stored,
                max_output_size=entry.uncompressed_size,
                allow_extra_data=False,
            )
        except zstandard.ZstdError as exc:
            raise ValueError("Invalid Zstandard data in Pixpil GArchive") from exc
    elif entry.codec in {0, 1} and entry.stored_size == entry.uncompressed_size:
        payload = stored
    else:
        raise ValueError("Unsupported Pixpil GArchive entry codec")
    if len(payload) != entry.uncompressed_size:
        raise ValueError("Pixpil GArchive entry has an invalid output size")
    return payload


class _LuaJitBytecodeReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.position = 0

    def read_byte(self) -> int:
        if self.position >= len(self.data):
            raise ValueError("Unexpected end of LuaJIT bytecode")
        value = self.data[self.position]
        self.position += 1
        return value

    def read_uleb128(self) -> int:
        value = 0
        for shift in range(0, 35, 7):
            byte = self.read_byte()
            value |= (byte & 0x7F) << shift
            if byte < 0x80:
                if value > 0xFFFFFFFF:
                    raise ValueError("LuaJIT ULEB128 value is too large")
                return value
        raise ValueError("Invalid LuaJIT ULEB128 value")

    def read_uleb128_33(self) -> int:
        first = self.read_byte()
        value = first >> 1
        if value < 0x40:
            return value
        value &= 0x3F
        shift = -1
        for _index in range(5):
            byte = self.read_byte()
            shift += 7
            value |= (byte & 0x7F) << shift
            if byte < 0x80:
                return value & 0xFFFFFFFF
        raise ValueError("Invalid LuaJIT 33-bit ULEB128 value")

    def read(self, size: int) -> bytes:
        if size < 0 or size > len(self.data) - self.position:
            raise ValueError("Unexpected end of LuaJIT bytecode")
        value = self.data[self.position : self.position + size]
        self.position += size
        return value


def _read_luajit_table_value(reader: _LuaJitBytecodeReader) -> object:
    value_type = reader.read_uleb128()
    if value_type >= 5:
        length = value_type - 5
        if length > MAX_PIXPIL_LUA_BYTES:
            raise ValueError("LuaJIT string constant exceeds the safety limit")
        return reader.read(length).decode("utf-8", errors="replace")
    if value_type == 3:
        value = reader.read_uleb128()
        return value - 0x100000000 if value & 0x80000000 else value
    if value_type == 4:
        low = reader.read_uleb128()
        high = reader.read_uleb128()
        return struct.unpack("<d", struct.pack("<II", low, high))[0]
    if value_type == 0:
        return None
    if value_type == 1:
        return False
    if value_type == 2:
        return True
    raise ValueError("Invalid LuaJIT table constant type")


def _read_luajit_template_table(
    reader: _LuaJitBytecodeReader,
) -> tuple[list[object], list[tuple[object, object]]]:
    array_count = reader.read_uleb128()
    hash_count = reader.read_uleb128()
    if array_count + hash_count * 2 > MAX_LUAJIT_TABLE_ITEMS:
        raise ValueError("LuaJIT template table exceeds the safety limit")
    array = [_read_luajit_table_value(reader) for _index in range(array_count)]
    hashed = [
        (_read_luajit_table_value(reader), _read_luajit_table_value(reader))
        for _index in range(hash_count)
    ]
    return array, hashed


def _extract_luajit_template_tables(
    data: bytes,
) -> list[tuple[list[object], list[tuple[object, object]]]]:
    if not 5 <= len(data) <= MAX_PIXPIL_LUA_BYTES or data[:3] != b"\x1bLJ":
        return []
    reader = _LuaJitBytecodeReader(data)
    if reader.read(3) != b"\x1bLJ" or reader.read_byte() != 2:
        return []
    flags = reader.read_uleb128()
    if flags & 0x01 or flags & ~0x8000001F:
        return []
    stripped = bool(flags & 0x02)
    if not stripped:
        chunk_name_size = reader.read_uleb128()
        if chunk_name_size > MAX_PIXPIL_LUA_BYTES:
            raise ValueError("LuaJIT chunk name exceeds the safety limit")
        reader.read(chunk_name_size)
    tables: list[tuple[list[object], list[tuple[object, object]]]] = []
    prototype_count = 0
    constant_count = 0
    terminated = False
    while reader.position < len(data):
        prototype_size = reader.read_uleb128()
        if prototype_size == 0:
            terminated = True
            break
        prototype_count += 1
        if prototype_count > MAX_LUAJIT_PROTOTYPES or prototype_size > MAX_PIXPIL_LUA_BYTES:
            raise ValueError("LuaJIT prototype exceeds the safety limit")
        prototype = _LuaJitBytecodeReader(reader.read(prototype_size))
        prototype.read_byte()  # Prototype flags.
        prototype.read_byte()  # Parameter count.
        prototype.read_byte()  # Frame size.
        upvalue_count = prototype.read_byte()
        gc_constant_count = prototype.read_uleb128()
        number_constant_count = prototype.read_uleb128()
        bytecode_count = prototype.read_uleb128()
        constant_count += gc_constant_count + number_constant_count
        if constant_count > MAX_LUAJIT_CONSTANTS:
            raise ValueError("LuaJIT constant pool exceeds the safety limit")
        debug_size = 0 if stripped else prototype.read_uleb128()
        if debug_size:
            prototype.read_uleb128()  # First source line.
            prototype.read_uleb128()  # Source line span.
        prototype.read(bytecode_count * 4)
        prototype.read(upvalue_count * 2)
        for _index in range(gc_constant_count):
            constant_type = prototype.read_uleb128()
            if constant_type >= 5:
                prototype.read(constant_type - 5)
            elif constant_type == 1:
                tables.append(_read_luajit_template_table(prototype))
                if len(tables) > MAX_LUAJIT_CONSTANTS:
                    raise ValueError("Too many LuaJIT template tables")
            elif constant_type in {2, 3}:
                prototype.read_uleb128()
                prototype.read_uleb128()
            elif constant_type == 4:
                for _part in range(4):
                    prototype.read_uleb128()
            elif constant_type != 0:
                raise ValueError("Invalid LuaJIT GC constant type")
        for _index in range(number_constant_count):
            if prototype.position >= len(prototype.data):
                raise ValueError("Invalid LuaJIT number constant")
            is_number = bool(prototype.data[prototype.position] & 1)
            prototype.read_uleb128_33()
            if is_number:
                prototype.read_uleb128()
        if debug_size:
            prototype.read(debug_size)
        if prototype.position != len(prototype.data):
            raise ValueError("Invalid LuaJIT prototype length")
    if not terminated or reader.position != len(data):
        raise ValueError("Invalid LuaJIT bytecode terminator")
    return tables


def _normalized_pixpil_input(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _canonical_pixpil_input(
    raw_input: object,
    device_type: str,
) -> str | None:
    if not isinstance(raw_input, str):
        return None
    compact = _normalized_pixpil_input(raw_input)
    if device_type == "gamepad":
        return _PIXPIL_GAMEPAD_INPUTS.get(compact) if compact else None
    if device_type == "mouse":
        return _PIXPIL_MOUSE_INPUTS.get(compact) if compact else None
    if device_type != "keyboard":
        return None
    if compact and compact in _PIXPIL_KEYBOARD_INPUTS:
        return _PIXPIL_KEYBOARD_INPUTS[compact]
    canonical = _canonical_input_name(raw_input)
    if canonical is None or canonical[1] != "keyboard":
        return None
    return canonical[0]


def _add_pixpil_mapping(
    keymap: dict[str, dict[str, str]],
    raw_input: object,
    action: object,
    device_type: str,
    *,
    movement_direction: str | None = None,
) -> bool:
    input_name = _canonical_pixpil_input(raw_input, device_type)
    if input_name is None or not isinstance(action, str) or not action.strip():
        return False
    action_name = action.strip()[:120]
    direction = movement_direction or _movement_direction(action_name, input_name)
    existing = keymap.get(input_name)
    if existing is None:
        mapping = {"type": device_type, "action": action_name}
        if direction in {"W", "B", "L", "R"}:
            mapping["movement_direction"] = direction
        keymap[input_name] = mapping
        return True
    actions = [item.strip() for item in existing["action"].split(" / ")]
    if action_name not in actions:
        existing["action"] = " / ".join([*actions, action_name])[:240]
    if direction in {"W", "B", "L", "R"} and "movement_direction" not in existing:
        existing["movement_direction"] = direction
    return True


def _infer_pixpil_table_device(values: list[str]) -> str:
    compact_values = {_normalized_pixpil_input(value) for value in values}
    if compact_values.intersection(_PIXPIL_GAMEPAD_STRONG_INPUTS):
        return "gamepad"
    if len(values) >= 2 and compact_values.issubset(_PIXPIL_MOUSE_INPUTS):
        return "mouse"
    return "keyboard"


def _parse_pixpil_template_tables(
    tables: list[tuple[list[object], list[tuple[object, object]]]],
) -> dict[str, dict[str, str]]:
    detected: dict[str, dict[str, str]] = {}
    for _array, hashed in tables:
        string_pairs = [
            (action, raw_input)
            for action, raw_input in hashed
            if isinstance(action, str) and isinstance(raw_input, str)
        ]
        if len(string_pairs) < 3 or len(string_pairs) != len(hashed):
            continue
        device_type = _infer_pixpil_table_device(
            [raw_input for _action, raw_input in string_pairs]
        )
        recognized = [
            (action, raw_input)
            for action, raw_input in string_pairs
            if _canonical_pixpil_input(raw_input, device_type) is not None
        ]
        if len(recognized) < 3 or len(recognized) * 4 < len(string_pairs) * 3:
            continue
        for action, raw_input in recognized:
            _add_pixpil_mapping(detected, raw_input, action, device_type)

    direction_words = {"up": "W", "down": "B", "left": "L", "right": "R"}
    wasd_words = {"w": "W", "s": "B", "a": "L", "d": "R"}
    left_stick_words = {
        "lup": "W",
        "ldown": "B",
        "lleft": "L",
        "lright": "R",
    }
    action_names = {"W": "move_up", "B": "move_down", "L": "move_left", "R": "move_right"}
    for array, _hashed in tables:
        values = [value for value in array if isinstance(value, str) and value.strip()]
        compact_values = [_normalized_pixpil_input(value) for value in values]
        if not compact_values:
            continue
        left_stick_directions = {
            left_stick_words[value]
            for value in compact_values
            if value in left_stick_words
        }
        if len(left_stick_directions) == 1:
            direction = next(iter(left_stick_directions))
            for raw_input in values:
                compact = _normalized_pixpil_input(raw_input)
                if direction_words.get(compact) == direction or left_stick_words.get(compact) == direction:
                    _add_pixpil_mapping(
                        detected,
                        raw_input,
                        action_names[direction],
                        "gamepad",
                        movement_direction=direction,
                    )
            continue
        keyboard_directions = {
            direction
            for value in compact_values
            for direction in (direction_words.get(value), wasd_words.get(value))
            if direction is not None
        }
        has_wasd = any(value in wasd_words for value in compact_values)
        has_arrow = any(value in direction_words for value in compact_values)
        if len(keyboard_directions) != 1 or not (has_wasd and has_arrow):
            continue
        direction = next(iter(keyboard_directions))
        for raw_input in values:
            compact = _normalized_pixpil_input(raw_input)
            if direction_words.get(compact) == direction or wasd_words.get(compact) == direction:
                _add_pixpil_mapping(
                    detected,
                    raw_input,
                    action_names[direction],
                    "keyboard",
                    movement_direction=direction,
                )
    return detected


def _parse_pixpil_mapping_document(
    document: object,
    overridden_actions: set[tuple[str, str]] | None = None,
) -> dict[str, dict[str, str]]:
    detected: dict[str, dict[str, str]] = {}
    device_names = {
        "keyboard": "keyboard",
        "key": "keyboard",
        "mouse": "mouse",
        "joystick": "gamepad",
        "gamepad": "gamepad",
        "controller": "gamepad",
    }

    def raw_values(value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, str)]
        if isinstance(value, dict):
            for field in ("key", "button", "input", "binding", "value"):
                if field in value:
                    return raw_values(value[field])
        return []

    if not isinstance(document, dict):
        return detected
    mappings = document.get("mappings", document)
    if not isinstance(mappings, dict):
        return detected
    for raw_device, bindings in mappings.items():
        if not isinstance(raw_device, str) or not isinstance(bindings, dict):
            continue
        normalized_device = re.sub(r"[^a-z]+", "", raw_device.casefold())
        device_type = device_names.get(normalized_device)
        if device_type is None:
            continue
        for action, value in bindings.items():
            if not isinstance(action, str) or not action.strip():
                continue
            action_name = action.strip()[:120]
            if overridden_actions is not None:
                overridden_actions.add((device_type, action_name))
            for raw_input in raw_values(value):
                _add_pixpil_mapping(
                    detected,
                    raw_input,
                    action_name,
                    device_type,
                )
    return detected


def _validate_pixpil_json_depth(raw: bytes) -> None:
    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in {0x5B, 0x7B}:
            depth += 1
            if depth > MAX_PIXPIL_PROFILE_JSON_DEPTH:
                raise ValueError("Pixpil input settings JSON is nested too deeply")
        elif byte in {0x5D, 0x7D}:
            depth -= 1
            if depth < 0:
                raise ValueError("Invalid Pixpil input settings JSON nesting")


def _decode_pixpil_safe_settings(path: Path) -> object:
    packed = _read_bounded_bytes(path, MAX_PIXPIL_PROFILE_BYTES)
    if not packed:
        raise ValueError("Pixpil input settings exceed the safety limit")
    stripped = packed.lstrip()
    if stripped.startswith((b"{", b"[")):
        raw = stripped
    else:
        if len(packed) <= 64:
            raise ValueError("Invalid Pixpil safe settings envelope")
        decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
        raw = decompressor.decompress(
            packed[64:],
            MAX_PIXPIL_PROFILE_JSON_BYTES + 1,
        )
        if (
            len(raw) > MAX_PIXPIL_PROFILE_JSON_BYTES
            or decompressor.unconsumed_tail
            or decompressor.unused_data
            or not decompressor.eof
        ):
            raise ValueError("Invalid or oversized Pixpil input settings")
    if len(raw) > MAX_PIXPIL_PROFILE_JSON_BYTES:
        raise ValueError("Pixpil input settings exceed the output safety limit")
    _validate_pixpil_json_depth(raw)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ValueError("Invalid Pixpil input settings JSON") from exc


def _pixpil_profile_roots(
    game_directory: Path,
    project_info: dict[str, object],
) -> list[Path]:
    roots = _identity_config_roots(game_directory)
    author = project_info.get("author")
    product = project_info.get("name")
    safe_author = isinstance(author, str) and author not in {"", ".", ".."} and not re.search(r"[\\/]", author)
    safe_product = isinstance(product, str) and product not in {"", ".", ".."} and not re.search(r"[\\/]", product)
    appdata = os.environ.get("APPDATA")
    local_appdata = os.environ.get("LOCALAPPDATA")
    bases = [Path(value) for value in (appdata, local_appdata) if value]
    if local_appdata:
        bases.append(Path(local_appdata).parent / "LocalLow")
    for base in bases:
        candidates: list[Path] = []
        if safe_product:
            candidates.append(base / str(product))
        if safe_author and safe_product:
            candidates.append(base / str(author) / str(product))
        for candidate in candidates:
            if candidate.is_dir():
                roots.append(candidate)
    resolved_roots: list[Path] = []
    seen: set[Path] = set()
    for path in roots:
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            resolved = path
        if resolved not in seen:
            seen.add(resolved)
            resolved_roots.append(resolved)
    return resolved_roots


def _pixpil_player_input_files(
    game_directory: Path,
    project_info: dict[str, object],
) -> tuple[list[Path], bool]:
    candidates: list[Path] = []
    seen: set[Path] = set()
    truncated = False
    visited_entries = 0
    target_names = {"input_settings", "input_settings_farm"}

    for root in _pixpil_profile_roots(game_directory, project_info):
        for current_root, directory_names, file_names in os.walk(root, followlinks=False):
            _prune_walk_links(current_root, directory_names, file_names)
            visited_entries += len(directory_names) + len(file_names)
            if visited_entries > MAX_GAME_DIRECTORY_ENTRIES:
                directory_names[:] = []
                truncated = True
                break
            try:
                depth = len(Path(current_root).relative_to(root).parts)
            except (ValueError, RuntimeError):
                directory_names[:] = []
                continue
            if depth >= 4:
                directory_names[:] = []
            for file_name in file_names:
                if file_name.casefold() not in target_names:
                    continue
                path = Path(current_root) / file_name
                try:
                    identity = path.resolve()
                except (OSError, RuntimeError):
                    identity = path
                if identity in seen:
                    continue
                seen.add(identity)
                candidates.append(path)
        if visited_entries > MAX_GAME_DIRECTORY_ENTRIES:
            break
    candidates.sort(
        key=lambda path: (
            path.name.casefold() != "input_settings",
            str(path).casefold(),
        )
    )
    return candidates, truncated


def _parse_vdf_scalar_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in re.finditer(
        r'"((?:\\.|[^"\\])*)"\s*"((?:\\.|[^"\\])*)"',
        text,
    ):
        key = re.sub(r'\\(["\\])', r'\1', match.group(1)).casefold()
        value = re.sub(r'\\(["\\])', r'\1', match.group(2))
        fields.setdefault(key, value)
    return fields


def _validated_steam_root(value: str, *, value_is_launcher: bool) -> Path | None:
    if not value or "\0" in value or len(value) > 32_767:
        return None
    candidate = Path(value.strip())
    launcher = candidate if value_is_launcher else candidate / "steam.exe"
    if (
        not candidate.is_absolute()
        or launcher.name.casefold() != "steam.exe"
    ):
        return None
    try:
        if not launcher.is_file():
            return None
        root = launcher.parent.resolve()
        if not (root / "steamapps").is_dir():
            return None
    except (OSError, RuntimeError):
        return None
    return root


def _windows_steam_registry_roots() -> tuple[Path, ...]:
    if sys.platform != "win32":
        return ()
    try:
        import winreg
    except ImportError:
        return ()
    locations: list[tuple[object, str, tuple[str, ...], int]] = [
        (
            winreg.HKEY_CURRENT_USER,
            r"Software\Valve\Steam",
            ("SteamPath", "SteamExe"),
            0,
        )
    ]
    read_flag = getattr(winreg, "KEY_READ", 0)
    for view_flag in dict.fromkeys(
        (
            getattr(winreg, "KEY_WOW64_32KEY", 0),
            getattr(winreg, "KEY_WOW64_64KEY", 0),
        )
    ):
        locations.append(
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Valve\Steam",
                ("InstallPath",),
                read_flag | view_flag,
            )
        )
    roots: list[Path] = []
    for hive, key_name, value_names, flags in locations:
        try:
            with winreg.OpenKey(hive, key_name, 0, flags or read_flag) as key:
                for value_name in value_names:
                    try:
                        raw_value, _value_type = winreg.QueryValueEx(key, value_name)
                    except OSError:
                        continue
                    if not isinstance(raw_value, str):
                        continue
                    root = _validated_steam_root(
                        raw_value,
                        value_is_launcher=value_name.casefold().endswith("exe"),
                    )
                    if root is not None and root not in roots:
                        roots.append(root)
        except OSError:
            continue
    return tuple(roots)


def _steam_root_candidates(
    library_root: Path,
    launcher_path: str | None,
) -> tuple[Path, ...]:
    roots: list[Path] = []
    if launcher_path:
        launcher_root = _validated_steam_root(
            launcher_path,
            value_is_launcher=True,
        )
        if launcher_root is not None:
            roots.append(launcher_root)
    roots.extend(
        root for root in _windows_steam_registry_roots() if root not in roots
    )
    try:
        library_root = library_root.resolve()
    except (OSError, RuntimeError):
        pass
    if library_root not in roots:
        roots.append(library_root)
    return tuple(roots)


def _steam_game_manifest(game_directory: Path) -> _SteamGameManifest | None:
    try:
        resolved = game_directory.resolve()
    except (OSError, RuntimeError):
        resolved = game_directory
    common_directory = next(
        (
            candidate
            for candidate in (resolved, *resolved.parents)
            if candidate.name.casefold() == "common"
        ),
        None,
    )
    if common_directory is None:
        return None
    steamapps = common_directory.parent
    try:
        manifests = sorted(
            itertools.islice(steamapps.glob("appmanifest_*.acf"), 4_096),
            key=lambda path: path.name.casefold(),
        )
    except (OSError, RuntimeError):
        return None
    try:
        relative_game = resolved.relative_to(common_directory)
    except ValueError:
        return None
    if not relative_game.parts:
        return None
    expected_install_dir = relative_game.parts[0].casefold()
    for manifest in manifests:
        try:
            text = _read_bounded_bytes(
                manifest,
                MAX_GAME_CONFIG_FILE_BYTES,
            ).decode("utf-8-sig", errors="replace")
        except (OSError, ValueError):
            continue
        fields = _parse_vdf_scalar_fields(text)
        install_dir = fields.get("installdir", "").strip()
        if install_dir.casefold() != expected_install_dir:
            continue
        file_match = re.fullmatch(r"appmanifest_([0-9]{1,10})\.acf", manifest.name, re.I)
        file_app_id = file_match.group(1) if file_match else None
        field_app_id = fields.get("appid", "").strip()
        if field_app_id and (
            not field_app_id.isdigit()
            or int(field_app_id) <= 0
            or file_app_id is not None
            and int(field_app_id) != int(file_app_id)
        ):
            continue
        app_id = field_app_id or file_app_id
        if app_id is not None:
            app_id = str(int(app_id))
        owner = fields.get("lastowner", "").strip()
        last_owner = owner if owner.isdigit() and int(owner) > 0 else None
        return _SteamGameManifest(
            manifest,
            _steam_root_candidates(
                steamapps.parent,
                fields.get("launcherpath"),
            ),
            app_id,
            last_owner,
            "lastowner" in fields,
        )
    return None


def _steam_last_owner(game_directory: Path) -> str | None:
    manifest = _steam_game_manifest(game_directory)
    return manifest.last_owner if manifest is not None else None


def _steam_userdata_account_id(steam_id: str | None) -> str | None:
    if steam_id is None or not steam_id.isdigit():
        return None
    value = int(steam_id)
    if not 0 < value <= 0xFFFFFFFFFFFFFFFF:
        return None
    if value >= STEAM_ID64_ACCOUNT_BASE:
        account_id = value - STEAM_ID64_ACCOUNT_BASE
        if not 0 < account_id <= 0xFFFFFFFF:
            return None
        return str(account_id)
    if value <= 0xFFFFFFFF:
        return str(value)
    return None


def _steam_remote_candidate(
    steam_root: Path,
    account_id: str,
    app_id: str,
    file_name: str,
) -> Path | None:
    relative_path = Path(file_name)
    if (
        not file_name
        or "\0" in file_name
        or len(file_name) > 1_024
        or relative_path.is_absolute()
        or bool(relative_path.drive)
        or any(part in {"", ".", ".."} for part in relative_path.parts)
    ):
        return None
    try:
        resolved_steam_root = steam_root.resolve()
        userdata_root = (resolved_steam_root / "userdata").resolve()
        if userdata_root.parent != resolved_steam_root:
            return None
        account_root = (userdata_root / account_id).resolve()
        if account_root.parent != userdata_root:
            return None
        app_root = (account_root / app_id).resolve()
        if app_root.parent != account_root:
            return None
        remote_root = (app_root / "remote").resolve()
        if remote_root.parent != app_root:
            return None
        candidate = (remote_root / relative_path).resolve()
    except (OSError, RuntimeError):
        return None
    try:
        candidate.relative_to(remote_root)
    except ValueError:
        return None
    try:
        if (
            not candidate.is_file()
            or candidate.stat().st_size > MAX_GAME_CONFIG_FILE_BYTES
        ):
            return None
    except OSError:
        return None
    return candidate


def _steam_remote_config_candidates(
    manifest: _SteamGameManifest | None,
    file_name: str,
) -> tuple[list[Path], bool]:
    if (
        manifest is None
        or manifest.app_id is None
        or not file_name
    ):
        return [], False
    account_id = _steam_userdata_account_id(manifest.last_owner)
    if account_id is not None:
        known_owner_candidates: list[Path] = []
        for steam_root in manifest.steam_roots:
            candidate = _steam_remote_candidate(
                steam_root,
                account_id,
                manifest.app_id,
                file_name,
            )
            if candidate is not None and candidate not in known_owner_candidates:
                known_owner_candidates.append(candidate)
        return known_owner_candidates, False
    if manifest.last_owner_present:
        return [], False

    candidates: list[Path] = []
    visited_accounts = 0
    truncated = False
    for steam_root in manifest.steam_roots:
        try:
            resolved_steam_root = steam_root.resolve()
            userdata = (resolved_steam_root / "userdata").resolve()
        except (OSError, RuntimeError):
            continue
        if userdata.parent != resolved_steam_root:
            continue
        try:
            with os.scandir(userdata) as entries:
                for entry in entries:
                    visited_accounts += 1
                    if visited_accounts > MAX_STEAM_USERDATA_ACCOUNTS:
                        truncated = True
                        break
                    name = entry.name
                    if (
                        not name.isdigit()
                        or str(int(name)) != name
                        or not 0 < int(name) <= 0xFFFFFFFF
                    ):
                        continue
                    try:
                        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    candidate = _steam_remote_candidate(
                        steam_root,
                        name,
                        manifest.app_id,
                        file_name,
                    )
                    if candidate is not None and candidate not in candidates:
                        candidates.append(candidate)
        except OSError:
            continue
        if truncated:
            break

    def modified_ns(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return -1

    candidates.sort(
        key=lambda path: (-modified_ns(path), str(path).casefold())
    )
    if len(candidates) > MAX_INDEXED_XML_CONFIG_FILES:
        truncated = True
        del candidates[MAX_INDEXED_XML_CONFIG_FILES:]
    return candidates, truncated


def _steam_remote_config_path(
    manifest: _SteamGameManifest | None,
    file_name: str,
) -> Path | None:
    candidates, _truncated = _steam_remote_config_candidates(manifest, file_name)
    return candidates[0] if candidates else None


def _modified_time_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


def _valve_config_order(path: Path) -> tuple[int, str]:
    priority = {
        "default.cfg": 0,
        "config_default.cfg": 1,
        "config_default_pc.cfg": 2,
        "config.cfg": 3,
        "userconfig.cfg": 4,
        "autoexec.cfg": 5,
    }
    return priority.get(path.name.casefold(), 99), str(path).casefold()


def _valve_install_config_candidates(
    game_directory: Path,
) -> tuple[list[Path], list[Path], bool]:
    configs: list[Path] = []
    label_files: list[Path] = []
    visited_entries = 0
    truncated = False
    for current_root, directory_names, file_names in os.walk(
        game_directory,
        followlinks=False,
    ):
        _prune_walk_links(current_root, directory_names, file_names)
        directory_names[:] = [
            name
            for name in directory_names
            if name.casefold()
            not in {
                "binaries",
                "crashreportclient",
                "maps",
                "materials",
                "models",
                "movies",
                "paks",
                "sound",
                "sounds",
            }
        ]
        visited_entries += len(directory_names) + len(file_names)
        if visited_entries > MAX_GAME_DIRECTORY_ENTRIES:
            truncated = True
            break
        for file_name in file_names:
            lowered = file_name.casefold()
            if lowered not in _VALVE_CONFIG_NAMES and lowered != "kb_act.lst":
                continue
            path = Path(current_root) / file_name
            try:
                if (
                    not path.is_file()
                    or path.stat().st_size > MAX_GAME_CONFIG_FILE_BYTES
                ):
                    continue
            except OSError:
                continue
            target = label_files if lowered == "kb_act.lst" else configs
            target.append(path)
            if len(configs) + len(label_files) >= MAX_GAME_CONFIG_FILES:
                truncated = True
                break
        if truncated:
            break
    configs.sort(key=_valve_config_order)
    label_files.sort(key=lambda path: str(path).casefold())
    return configs, label_files, truncated


def _valve_install_config_group(candidates: list[Path]) -> list[Path]:
    live_configs = [
        path for path in candidates if path.name.casefold() == "config.cfg"
    ]
    anchor_candidates = live_configs or [
        path
        for path in candidates
        if path.name.casefold() in _VALVE_BASE_CONFIG_NAMES
    ]
    if not anchor_candidates:
        anchor_candidates = candidates
    if not anchor_candidates:
        return []
    anchor = max(
        anchor_candidates,
        key=lambda path: (_modified_time_ns(path), -len(path.parts)),
    )
    selected = [path for path in candidates if path.parent == anchor.parent]
    return sorted(selected, key=_valve_config_order)


def _steam_remote_root_from_config(path: Path) -> Path | None:
    for candidate in (path.parent, *path.parents):
        if candidate.name.casefold() == "remote":
            try:
                return candidate.resolve()
            except (OSError, RuntimeError):
                return None
    return None


def _valve_steam_cloud_config_group(
    manifest: _SteamGameManifest | None,
) -> tuple[list[Path], bool]:
    relative_paths = [
        f"{prefix}{name}"
        for prefix in ("cfg/", "")
        for name in (
            "config.cfg",
            "userconfig.cfg",
            "autoexec.cfg",
        )
    ]
    candidates: list[Path] = []
    truncated = False
    for relative_path in relative_paths:
        found, found_truncated = _steam_remote_config_candidates(
            manifest,
            relative_path,
        )
        truncated = truncated or found_truncated
        for path in found:
            if path not in candidates:
                candidates.append(path)
    if not candidates:
        return [], truncated
    live_configs = [
        path for path in candidates if path.name.casefold() == "config.cfg"
    ]
    anchor_candidates = live_configs or candidates
    anchor = max(
        anchor_candidates,
        key=lambda path: (_modified_time_ns(path), str(path).casefold()),
    )
    remote_root = _steam_remote_root_from_config(anchor)
    if remote_root is None:
        return [], truncated
    selected = [
        path
        for path in candidates
        if _steam_remote_root_from_config(path) == remote_root
        and path.parent == anchor.parent
    ]
    return sorted(selected, key=_valve_config_order), truncated


def discover_valve_keymap(game_directory: Path) -> KeymapDiscovery:
    """Discover Source/GoldSrc binds, preferring the active Steam Cloud profile."""
    install_configs, label_files, truncated = _valve_install_config_candidates(
        game_directory
    )
    scanned_label_count = len(label_files)
    install_group = _valve_install_config_group(install_configs)
    cloud_group, cloud_truncated = _valve_steam_cloud_config_group(
        _steam_game_manifest(game_directory)
    )
    truncated = truncated or cloud_truncated
    preferred_label_files: list[Path] = []
    if install_group:
        config_parent = install_group[-1].parent
        mod_root = config_parent.parent if config_parent.name.casefold() == "cfg" else config_parent
        for path in label_files:
            try:
                path.relative_to(mod_root)
            except ValueError:
                continue
            preferred_label_files.append(path)
    if preferred_label_files:
        label_files = preferred_label_files
    action_labels: dict[str, str] = {}
    used_label_files: list[Path] = []
    for path in label_files:
        try:
            parsed_labels = _parse_valve_action_list(_read_game_config(path))
        except (OSError, ValueError):
            continue
        if parsed_labels:
            action_labels.update(parsed_labels)
            used_label_files.append(path)

    cloud_has_live_config = any(
        path.name.casefold() == "config.cfg" for path in cloud_group
    )
    active_configs = (
        cloud_group if cloud_has_live_config else [*install_group, *cloud_group]
    )
    detected: dict[str, dict[str, str]] = {}
    sources: list[Path] = []
    authoritative = False
    for path in active_configs:
        try:
            text = _read_game_config(path)
        except (OSError, ValueError):
            continue

        if not _apply_valve_bind_cfg(detected, text, action_labels):
            continue
        sources.append(path)
        if path.name.casefold() == "config.cfg":
            authoritative = True
    if detected and used_label_files:
        sources[:0] = used_label_files
    return KeymapDiscovery(
        detected,
        tuple(dict.fromkeys(sources)),
        len(install_configs) + scanned_label_count + len(cloud_group),
        truncated,
        authoritative,
    )


def _valid_unreal_identity(value: str) -> str | None:
    value = value.strip().strip('"\'')
    if (
        not value
        or len(value) > 96
        or value in {".", ".."}
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.\-]*", value)
    ):
        return None
    return value


def _unreal_executable_identity(stem: str) -> str | None:
    identity = re.sub(
        r"(?i)(?:-(?:win32|win64|windows|wingdk))?"
        r"-(?:shipping|test|development)$",
        "",
        stem,
    )
    identity = re.sub(r"(?i)-(?:win32|win64|windows|wingdk)$", "", identity)
    return _valid_unreal_identity(identity)


def _unreal_install_candidates_and_identities(
    game_directory: Path,
) -> tuple[list[Path], set[str], bool]:
    input_configs: list[Path] = []
    identities: set[str] = set()
    identity_truncated = False

    def add_identity(identity: str | None) -> None:
        nonlocal identity_truncated
        if not identity or identity in identities:
            return
        if len(identities) >= MAX_UNREAL_IDENTITY_NAMES:
            identity_truncated = True
            return
        identities.add(identity)

    root_identity = _valid_unreal_identity(game_directory.name)
    add_identity(root_identity)
    excluded_executable_words = {
        "anticheat",
        "crashreport",
        "crashsender",
        "directx",
        "dxsetup",
        "epicwebhelper",
        "launcher",
        "prereq",
        "redistributable",
        "unrealcefsubprocess",
        "unrealpak",
    }
    visited_entries = 0
    truncated = False
    for current_root, directory_names, file_names in os.walk(
        game_directory,
        followlinks=False,
    ):
        _prune_walk_links(current_root, directory_names, file_names)
        directory_names[:] = [
            name
            for name in directory_names
            if name.casefold()
            not in {
                "content",
                "deriveddatacache",
                "movies",
                "paks",
                "plugins",
                "saved",
            }
        ]
        visited_entries += len(directory_names) + len(file_names)
        if visited_entries > MAX_GAME_DIRECTORY_ENTRIES:
            truncated = True
            break
        current_path = Path(current_root)
        try:
            relative_parts = current_path.relative_to(game_directory).parts
        except ValueError:
            relative_parts = ()
        lowered_parts = {part.casefold() for part in relative_parts}
        for file_name in file_names:
            path = current_path / file_name
            lowered = file_name.casefold()
            if lowered.endswith("input.ini") and "engine" not in lowered_parts:
                try:
                    if (
                        path.is_file()
                        and path.stat().st_size <= MAX_GAME_CONFIG_FILE_BYTES
                    ):
                        input_configs.append(path)
                except OSError:
                    pass
            elif lowered.endswith(".uproject"):
                identity = _valid_unreal_identity(path.stem)
                add_identity(identity)
                try:
                    if path.stat().st_size <= MAX_GAME_CONFIG_FILE_BYTES:
                        document = json.loads(_read_game_config(path))
                        modules = (
                            document.get("Modules", [])
                            if isinstance(document, dict)
                            else []
                        )
                        for module in modules:
                            if not isinstance(module, dict):
                                continue
                            module_identity = _valid_unreal_identity(
                                str(module.get("Name") or "")
                            )
                            add_identity(module_identity)
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
            elif lowered.endswith(".exe") and (
                current_path == game_directory or "binaries" in lowered_parts
            ):
                compact_stem = re.sub(r"[^a-z0-9]+", "", path.stem.casefold())
                if any(word in compact_stem for word in excluded_executable_words):
                    continue
                identity = _unreal_executable_identity(path.stem)
                add_identity(identity)
                for index, part in enumerate(relative_parts):
                    if part.casefold() != "binaries" or index == 0:
                        continue
                    directory_identity = _valid_unreal_identity(
                        relative_parts[index - 1]
                    )
                    add_identity(directory_identity)
                    break

    def input_config_order(path: Path) -> tuple[int, int, str]:
        name = path.name.casefold()
        priority = (
            0 if name == "baseinput.ini" else
            1 if name == "defaultinput.ini" else
            2 if name.startswith("default") else
            3
        )
        return priority, len(path.parts), str(path).casefold()

    input_configs.sort(key=input_config_order)
    return input_configs, identities, truncated or identity_truncated


def _unreal_localappdata_roots(
    identities: set[str],
) -> tuple[list[Path], bool]:
    raw_local_appdata = os.environ.get("LOCALAPPDATA")
    if not raw_local_appdata or not identities:
        return [], False
    try:
        local_appdata = Path(raw_local_appdata).resolve()
    except (OSError, RuntimeError):
        return [], False
    if not local_appdata.is_dir():
        return [], False
    normalized_identities = {
        re.sub(r"[^a-z0-9]+", "", identity.casefold())
        for identity in identities
    }
    normalized_identities.discard("")
    matched_roots: list[Path] = []
    visited_entries = 0
    truncated = False
    try:
        with os.scandir(local_appdata) as entries:
            for entry in entries:
                visited_entries += 1
                if visited_entries > MAX_UNREAL_LOCALAPPDATA_ENTRIES:
                    truncated = True
                    break
                normalized = re.sub(r"[^a-z0-9]+", "", entry.name.casefold())
                if normalized not in normalized_identities:
                    continue
                try:
                    entry_path = Path(entry.path)
                    if (
                        entry.is_symlink()
                        or entry_path.is_junction()
                        or not entry.is_dir(follow_symlinks=False)
                    ):
                        continue
                    resolved_entry = entry_path.resolve()
                    if resolved_entry.parent != local_appdata:
                        continue
                except (OSError, RuntimeError):
                    continue
                matched_roots.append(resolved_entry)
    except OSError:
        return [], truncated

    matched_roots = list(dict.fromkeys(matched_roots))
    matched_roots.sort(key=lambda path: str(path).casefold())
    return matched_roots, truncated


def _unreal_localappdata_input_candidates(
    identities: set[str],
) -> tuple[list[Path], bool]:
    matched_roots, truncated = _unreal_localappdata_roots(identities)
    candidates: list[Path] = []
    for matched_root in matched_roots:
        config_root = matched_root / "Saved" / "Config"
        try:
            resolved_config_root = config_root.resolve()
            resolved_config_root.relative_to(matched_root)
            if not resolved_config_root.is_dir():
                continue
            with os.scandir(resolved_config_root) as platforms:
                for index, platform in enumerate(platforms):
                    if index >= MAX_UNREAL_CONFIG_PLATFORMS:
                        truncated = True
                        break
                    lowered = platform.name.casefold()
                    if not (
                        lowered.startswith("windows")
                        or lowered in {"wingdk", "win64", "win32"}
                    ):
                        continue
                    try:
                        platform_path = Path(platform.path)
                        if (
                            platform.is_symlink()
                            or platform_path.is_junction()
                            or not platform.is_dir(follow_symlinks=False)
                        ):
                            continue
                        candidate = (platform_path / "Input.ini").resolve()
                        candidate.relative_to(resolved_config_root)
                        if (
                            candidate.is_file()
                            and candidate.stat().st_size <= MAX_GAME_CONFIG_FILE_BYTES
                        ):
                            candidates.append(candidate)
                    except (OSError, RuntimeError, ValueError):
                        continue
        except (OSError, RuntimeError, ValueError):
            continue
    candidates = list(dict.fromkeys(candidates))
    candidates.sort(
        key=lambda path: (-_modified_time_ns(path), str(path).casefold())
    )
    return candidates, truncated


def _unreal_localappdata_user_option_candidates(
    identities: set[str],
) -> tuple[list[Path], bool]:
    """Return exact, bounded UE player-option saves for matched project roots."""
    matched_roots, truncated = _unreal_localappdata_roots(identities)
    candidates: list[Path] = []
    for matched_root in matched_roots:
        try:
            candidate = (
                matched_root / "Saved" / "SaveGames" / "UserOption.sav"
            ).resolve()
            candidate.relative_to(matched_root)
            if (
                candidate.is_file()
                and not _is_link_or_junction_path(candidate)
                and candidate.stat().st_size <= MAX_GAME_CONFIG_FILE_BYTES
            ):
                candidates.append(candidate)
        except (OSError, RuntimeError, ValueError):
            continue
    candidates = list(dict.fromkeys(candidates))
    candidates.sort(
        key=lambda path: (-_modified_time_ns(path), str(path).casefold())
    )
    return candidates, truncated


def _unreal_kraken_helper_config() -> KrakenHelperConfig:
    """Use the same bundled EXE as an isolated decoder worker when frozen."""

    if getattr(sys, "frozen", False):
        return KrakenHelperConfig(
            (sys.executable, "--internal-unreal-kraken-helper"),
            timeout_seconds=12.0,
        )
    return KrakenHelperConfig()


def discover_unreal_keymap(game_directory: Path) -> KeymapDiscovery:
    """Resolve tightly matched Unreal GVAS/Input.ini player settings and defaults."""
    install_configs, identities, truncated = (
        _unreal_install_candidates_and_identities(game_directory)
    )
    option_saves, option_truncated = _unreal_localappdata_user_option_candidates(
        identities
    )
    gvas_result = discover_gvas_player_keymap(option_saves)
    player_configs, player_truncated = _unreal_localappdata_input_candidates(
        identities
    )
    truncated = (
        truncated
        or option_truncated
        or gvas_result.truncated
        or player_truncated
    )
    if gvas_result.has_verified_player_config:
        verified_gvas_keymap: dict[str, dict[str, str]] = {}
        for input_name, mapping in gvas_result.keymap.items():
            for action in mapping.get("action", "").split("；"):
                _add_detected_mapping(
                    verified_gvas_keymap,
                    input_name,
                    action,
                )
            direction = mapping.get("movement_direction")
            if (
                direction in {"W", "B", "L", "R"}
                and input_name in verified_gvas_keymap
            ):
                verified_gvas_keymap[input_name]["movement_direction"] = direction
        return KeymapDiscovery(
            verified_gvas_keymap,
            (gvas_result.source_file,) if gvas_result.source_file else (),
            gvas_result.scanned_files,
            truncated,
            True,
            notice=gvas_result.diagnostic,
            has_verified_player_config=True,
            has_recognized_player_file=True,
            blocks_heuristic_fallback=True,
            source_records=(
                (
                    KeymapSource(
                        gvas_result.source_file,
                        "verified_player",
                    ),
                )
                if gvas_result.source_file is not None
                else ()
            ),
            binding_authority=KEYMAP_AUTHORITY_VERIFIED_PLAYER,
            apply_mode=KEYMAP_APPLY_REPLACE,
        )

    # A player save that explicitly contains a key-config section but uses an
    # unknown layout is more authoritative than plausible installation
    # defaults.  Refuse to hide that unknown current-player state with a Pak
    # fallback.
    if gvas_result.recognized and gvas_result.has_key_config:
        source_files = (
            (gvas_result.source_file,) if gvas_result.source_file else ()
        )
        return KeymapDiscovery(
            {},
            source_files,
            gvas_result.scanned_files,
            truncated,
            False,
            notice=gvas_result.diagnostic,
            has_recognized_player_file=True,
            blocks_heuristic_fallback=True,
            source_records=(
                (
                    KeymapSource(
                        gvas_result.source_file,
                        "unmapped_player_file",
                        contributes_bindings=False,
                    ),
                )
                if gvas_result.source_file is not None
                else ()
            ),
            apply_mode=KEYMAP_APPLY_NONE,
        )

    pak_result = discover_default_input_from_game_directory(
        game_directory,
        kraken_helper=_unreal_kraken_helper_config(),
    )
    truncated = truncated or pak_result.truncated
    bindings: list[_UnrealInputBinding] = []
    sources: list[Path] = []
    source_records: list[KeymapSource] = []
    scanned = gvas_result.scanned_files + pak_result.scanned_paks
    install_recognized = False
    loose_default_recognized = False
    invalid_install_configs: list[Path] = []

    def apply_install_configs(paths: list[Path]) -> None:
        nonlocal scanned, install_recognized, loose_default_recognized
        for path in paths:
            scanned += 1
            try:
                operations, recognized, malformed = _unreal_input_operations(
                    _read_game_config(path)
                )
            except (OSError, ValueError):
                invalid_install_configs.append(path)
                continue
            if malformed:
                invalid_install_configs.append(path)
                continue
            if not recognized:
                continue
            _apply_unreal_input_operations(bindings, operations)
            sources.append(path)
            source_records.append(KeymapSource(path, "loose_default"))
            install_recognized = True
            if path.name.casefold() == "defaultinput.ini":
                loose_default_recognized = True

    # BaseInput precedes the project's packaged DefaultInput.  A loose project
    # config is then allowed to patch the packaged baseline.
    base_configs = [
        path for path in install_configs
        if path.name.casefold() == "baseinput.ini"
    ]
    project_configs = [
        path for path in install_configs
        if path.name.casefold() != "baseinput.ini"
    ]
    apply_install_configs(base_configs)

    packaged_default_recognized = False
    pak_parse_failure = ""
    if pak_result.found:
        try:
            operations, recognized, malformed = _unreal_input_operations(
                pak_result.config_text
            )
        except ValueError as exc:
            recognized = False
            malformed = True
            operations = []
            pak_parse_failure = f"DefaultInput.ini 解析失败：{exc}"
        if recognized and not malformed:
            _apply_unreal_input_operations(bindings, operations)
            packaged_default_recognized = True
            for path in pak_result.source_paks:
                sources.append(path)
                source_records.append(
                    KeymapSource(
                        path,
                        "pak_default",
                        virtual_path=pak_result.internal_path,
                    )
                )
        elif not pak_parse_failure:
            pak_parse_failure = (
                "已验证游戏安装包中的 DefaultInput.ini，但其中没有可安全读取的 "
                "ActionMappings/AxisMappings，未采用该默认键位。"
            )

    apply_install_configs(project_configs)

    if gvas_result.source_file is not None and gvas_result.recognized:
        sources.append(gvas_result.source_file)
        source_records.append(
            KeymapSource(
                gvas_result.source_file,
                "unmapped_player_file",
                contributes_bindings=False,
            )
        )

    authoritative = False
    unmapped_player_config: Path | None = None
    invalid_player_config: Path | None = None
    invalid_player_detail = ""
    for path in player_configs:
        scanned += 1
        try:
            player_text = _read_game_config(path)
            operations, recognized, malformed = _unreal_input_operations(
                player_text
            )
        except (OSError, ValueError):
            invalid_player_config = path
            invalid_player_detail = "该文件无法在安全大小和编码限制内完整读取"
            break
        if malformed:
            invalid_player_config = path
            invalid_player_detail = (
                "其中存在无法完整解析的 ActionMappings/AxisMappings 语法"
            )
            break
        if not recognized:
            # A number of Unreal games create an Input.ini containing only a
            # section header/comments on first run, then serialize bindings only
            # after the player changes and applies a key.  Remember that state so
            # the UI does not claim that the game has never been launched.
            if unmapped_player_config is None:
                unmapped_player_config = path
            # Candidates are newest-first.  A newer, valid-but-empty current
            # file must not be replaced by stale bindings from an older UE
            # platform directory after an engine migration.
            break
        _apply_unreal_input_operations(bindings, operations)
        sources.append(path)
        source_records.append(KeymapSource(path, "verified_player"))
        authoritative = True
        break
    detected = _unreal_bindings_keymap(bindings)
    pak_failure = pak_result.error_code or pak_parse_failure
    install_failure = (
        "无法完整解析后置的安装目录输入配置："
        + "、".join(str(path) for path in dict.fromkeys(invalid_install_configs))
        if invalid_install_configs
        else ""
    )
    baseline_failure_details = []
    if pak_failure:
        baseline_failure_details.append(
            pak_parse_failure or pak_result.diagnostic or str(pak_failure)
        )
    if install_failure:
        baseline_failure_details.append(install_failure)
    baseline_failure_detail = "；".join(baseline_failure_details)
    baseline_failure = bool(baseline_failure_details)
    has_complete_default_baseline = bool(
        packaged_default_recognized or loose_default_recognized
    )
    if invalid_player_config is not None:
        sources.append(invalid_player_config)
        failure_records = [
            KeymapSource(
                record.physical_path,
                (
                    "unapplied_pak_default"
                    if record.kind == "pak_default"
                    else "unapplied_loose_default"
                    if record.kind == "loose_default"
                    else record.kind
                ),
                virtual_path=record.virtual_path,
                contributes_bindings=False,
            )
            for record in source_records
        ]
        failure_records.append(
            KeymapSource(
                invalid_player_config,
                "invalid_player_config",
                contributes_bindings=False,
            )
        )
        failure_source_paths = (
            *(pak_result.source_paks if pak_failure else ()),
            *invalid_install_configs,
        )
        for path in failure_source_paths:
            if path not in sources:
                sources.append(path)
            if not any(
                record.physical_path == path for record in failure_records
            ):
                failure_records.append(
                    KeymapSource(
                        path,
                        (
                            "pak_default_error"
                            if path in pak_result.source_paks
                            else "loose_default_error"
                        ),
                        virtual_path=(
                            pak_result.internal_path
                            if path in pak_result.source_paks
                            else ""
                        ),
                        contributes_bindings=False,
                    )
                )
        baseline_detail = (
            f" 同时，默认键位基线也无法安全读取："
            f"{baseline_failure_detail}"
            if baseline_failure
            else ""
        )
        return KeymapDiscovery(
            {},
            tuple(dict.fromkeys(sources)),
            scanned,
            truncated,
            False,
            notice=(
                f"已找到玩家 Input.ini，但{invalid_player_detail}："
                f"{invalid_player_config}。"
                f"{baseline_detail} 为避免把默认值误报为玩家实际配置，本次没有载入任何键位。"
            ),
            has_recognized_player_file=True,
            blocks_heuristic_fallback=True,
            source_records=tuple(dict.fromkeys(failure_records)),
            apply_mode=KEYMAP_APPLY_NONE,
        )
    if authoritative and baseline_failure:
        # Unreal Input.ini normally stores array operations relative to
        # DefaultInput rather than a complete standalone table.  If the Pak
        # baseline could not be authenticated/decompressed, returning only the
        # player's delta would silently drop every unchanged action.
        failure_records = [
            KeymapSource(
                record.physical_path,
                (
                    "unapplied_player_override"
                    if record.kind == "verified_player"
                    else "unapplied_pak_default"
                    if record.kind == "pak_default"
                    else "unapplied_loose_default"
                    if record.kind == "loose_default"
                    else record.kind
                ),
                virtual_path=record.virtual_path,
                contributes_bindings=False,
            )
            for record in source_records
        ]
        failure_source_paths = (
            *(pak_result.source_paks if pak_failure else ()),
            *invalid_install_configs,
        )
        for path in failure_source_paths:
            sources.append(path)
            failure_records.append(
                KeymapSource(
                    path,
                    (
                        "pak_default_error"
                        if path in pak_result.source_paks
                        else "loose_default_error"
                    ),
                    virtual_path=(
                        pak_result.internal_path
                        if path in pak_result.source_paks
                        else ""
                    ),
                    contributes_bindings=False,
                )
            )
        failure_detail = baseline_failure_detail
        return KeymapDiscovery(
            {},
            tuple(dict.fromkeys(sources)),
            scanned,
            truncated,
            False,
            notice=(
                "已找到玩家 Input.ini，但该文件是相对于游戏 DefaultInput 的增量配置。"
                f"当前无法安全读取完整默认基线：{failure_detail} "
                "为避免遗漏所有未改动作，本次没有载入任何键位。"
            ),
            has_recognized_player_file=True,
            blocks_heuristic_fallback=True,
            source_records=tuple(dict.fromkeys(failure_records)),
            apply_mode=KEYMAP_APPLY_NONE,
        )
    if authoritative and not has_complete_default_baseline:
        partial_records = tuple(
            KeymapSource(
                record.physical_path,
                (
                    "unverified_player_override"
                    if record.kind == "verified_player"
                    else record.kind
                ),
                virtual_path=record.virtual_path,
                contributes_bindings=record.contributes_bindings,
            )
            for record in source_records
        )
        return KeymapDiscovery(
            detected,
            tuple(dict.fromkeys(sources)),
            scanned,
            truncated,
            False,
            notice=(
                "已找到玩家 Input.ini，但没有找到可完整验证的游戏 DefaultInput 基线。"
                "Unreal Input.ini 通常只保存相对于默认表的增量操作，因此当前只能读取"
                "其中明确出现的部分绑定，不能把它视为完整玩家键位。"
            ),
            has_recognized_player_file=True,
            blocks_heuristic_fallback=True,
            source_records=partial_records,
            binding_authority=KEYMAP_AUTHORITY_MIXED_UNVERIFIED,
            apply_mode=(
                KEYMAP_APPLY_MERGE if detected else KEYMAP_APPLY_NONE
            ),
        )
    if authoritative:
        return KeymapDiscovery(
            detected,
            tuple(dict.fromkeys(sources)),
            scanned,
            truncated,
            True,
            notice=(
                "已将玩家 Input.ini 应用于游戏默认键位。"
                if packaged_default_recognized or install_recognized
                else "已读取玩家 Input.ini 键位配置。"
            ),
            has_verified_player_config=True,
            has_recognized_player_file=True,
            blocks_heuristic_fallback=True,
            source_records=tuple(dict.fromkeys(source_records)),
            binding_authority=KEYMAP_AUTHORITY_VERIFIED_PLAYER,
            apply_mode=KEYMAP_APPLY_REPLACE,
        )

    recognized_player_file = bool(
        gvas_result.recognized or unmapped_player_config is not None
    )
    if unmapped_player_config is not None:
        sources.append(unmapped_player_config)
        source_records.append(
            KeymapSource(
                unmapped_player_config,
                "unmapped_player_file",
                contributes_bindings=False,
            )
        )

    if baseline_failure:
        abandoned_records = [
            KeymapSource(
                record.physical_path,
                (
                    "unapplied_loose_default"
                    if record.kind == "loose_default"
                    else "unapplied_pak_default"
                    if record.kind == "pak_default"
                    else record.kind
                ),
                virtual_path=record.virtual_path,
                contributes_bindings=False,
            )
            for record in source_records
        ]
        source_records = abandoned_records
        failure_source_paths = (
            *(pak_result.source_paks if pak_failure else ()),
            *invalid_install_configs,
        )
        for path in failure_source_paths:
            sources.append(path)
            source_records.append(
                KeymapSource(
                    path,
                    (
                        "pak_default_error"
                        if path in pak_result.source_paks
                        else "loose_default_error"
                    ),
                    virtual_path=(
                        pak_result.internal_path
                        if path in pak_result.source_paks
                        else ""
                    ),
                    contributes_bindings=False,
                )
            )
        failure_notice = baseline_failure_detail
        if gvas_result.recognized and gvas_result.diagnostic:
            failure_notice = (
                f"{gvas_result.diagnostic}\n\n"
                f"同时无法安全读取游戏安装包默认键位：{failure_notice}"
            )
        return KeymapDiscovery(
            {},
            tuple(dict.fromkeys(sources)),
            scanned,
            truncated,
            False,
            notice=failure_notice,
            has_recognized_player_file=recognized_player_file,
            blocks_heuristic_fallback=True,
            source_records=tuple(dict.fromkeys(source_records)),
            apply_mode=KEYMAP_APPLY_NONE,
        )

    has_install_default = install_recognized or packaged_default_recognized
    if has_install_default:
        default_origin = (
            "已验证并读取游戏安装包中的 DefaultInput.ini 默认键位。"
            if packaged_default_recognized
            else "已读取游戏安装目录中的 Unreal 默认键位。"
        )
        player_state = ""
        if gvas_result.recognized and gvas_result.diagnostic:
            player_state = f"\n{gvas_result.diagnostic}"
        elif unmapped_player_config is not None:
            player_state = (
                "\n已找到玩家 Input.ini，但其中尚未保存 ActionMappings/"
                "AxisMappings 键位记录。"
            )
        notice = (
            f"{default_origin}{player_state}\n"
            "当前载入内容是游戏内置默认值，不是玩家实际改键记录；"
            "DefaultInput.ini 只提供英文动作标识，不能自动生成可靠的中文动作语义。"
            "请对照游戏内键位设置逐项核对并改为中文，未完成前不能继续录制。"
        )
        return KeymapDiscovery(
            detected,
            tuple(dict.fromkeys(sources)),
            scanned,
            truncated,
            True,
            notice=notice,
            has_recognized_player_file=recognized_player_file,
            source_records=tuple(dict.fromkeys(source_records)),
            binding_authority=KEYMAP_AUTHORITY_DEFAULT_ONLY,
            apply_mode=KEYMAP_APPLY_REPLACE,
        )

    if gvas_result.recognized:
        return KeymapDiscovery(
            detected,
            tuple(dict.fromkeys(sources)),
            scanned,
            truncated,
            False,
            notice=gvas_result.diagnostic,
            has_recognized_player_file=True,
            blocks_heuristic_fallback=gvas_result.has_key_config,
            source_records=tuple(dict.fromkeys(source_records)),
            apply_mode=KEYMAP_APPLY_NONE if not detected else KEYMAP_APPLY_MERGE,
        )
    notice = ""
    if unmapped_player_config is not None:
        notice = (
            "已找到玩家 Input.ini，但文件中尚无 ActionMappings/AxisMappings "
            "键位记录。仅启动并退出游戏不一定会保存默认键位；请进入游戏的按键设置，"
            "实际修改并应用至少一个键位后正常退出，再重新选择游戏目录。"
        )
    return KeymapDiscovery(
        detected,
        tuple(sources),
        scanned,
        truncated,
        False,
        notice=notice,
        has_recognized_player_file=unmapped_player_config is not None,
        source_records=tuple(dict.fromkeys(source_records)),
        apply_mode=KEYMAP_APPLY_NONE if not detected else KEYMAP_APPLY_MERGE,
    )


def _indexed_xml_install_candidates(
    game_directory: Path,
) -> tuple[list[Path], bool]:
    candidates: list[Path] = []
    visited_entries = 0
    truncated = False
    for current_root, directory_names, file_names in os.walk(
        game_directory,
        followlinks=False,
    ):
        _prune_walk_links(current_root, directory_names, file_names)
        directory_names[:] = [
            name
            for name in directory_names
            if name.casefold() not in {"movies", "sound", "sounds", "voices"}
        ]
        visited_entries += len(directory_names) + len(file_names)
        if visited_entries > MAX_GAME_DIRECTORY_ENTRIES:
            truncated = True
            break
        for file_name in file_names:
            path = Path(current_root) / file_name
            if path.suffix.casefold() not in {".cfg", ".xml"}:
                continue
            try:
                if path.stat().st_size > MAX_GAME_CONFIG_FILE_BYTES:
                    continue
            except OSError:
                continue
            candidates.append(path)
            if len(candidates) >= MAX_INDEXED_XML_CONFIG_FILES:
                truncated = True
                break
        if truncated:
            break
    candidates.sort(
        key=lambda path: (
            path.parent != game_directory or path.name.casefold() != "config.cfg",
            path.name.casefold() != "config.cfg",
            path.suffix.casefold() != ".cfg",
            len(path.relative_to(game_directory).parts),
            str(path).casefold(),
        )
    )
    return candidates, truncated


def _indexed_menu_action_labels(
    game_directory: Path,
) -> tuple[dict[int, str], Path | None, int, bool]:
    candidates: list[Path] = []
    visited_entries = 0
    truncated = False
    for current_root, directory_names, file_names in os.walk(
        game_directory,
        followlinks=False,
    ):
        _prune_walk_links(current_root, directory_names, file_names)
        visited_entries += len(directory_names) + len(file_names)
        if visited_entries > MAX_GAME_DIRECTORY_ENTRIES:
            truncated = True
            break
        for file_name in file_names:
            if file_name.casefold() != "dictio_menu_en.bin":
                continue
            candidates.append(Path(current_root) / file_name)
            if len(candidates) >= MAX_INDEXED_MENU_DICTIONARIES:
                truncated = True
                break
        if truncated:
            break
    candidates.sort(key=lambda path: (len(path.relative_to(game_directory).parts), str(path).casefold()))
    scanned = 0
    for path in candidates:
        scanned += 1
        try:
            reference_data = _read_bounded_bytes(
                path,
                MAX_INDEXED_MENU_DICTIONARY_BYTES,
            )
            labels = _indexed_action_labels_from_dictionary(reference_data)
        except (OSError, ValueError, struct.error):
            continue
        if labels:
            for localized_path in _indexed_localized_dictionary_candidates(path):
                scanned += 1
                try:
                    localized_labels = _indexed_action_labels_from_dictionary(
                        reference_data,
                        _read_bounded_bytes(
                            localized_path,
                            MAX_INDEXED_MENU_DICTIONARY_BYTES,
                        ),
                    )
                except (OSError, ValueError, struct.error):
                    continue
                if localized_labels and all(
                    _contains_chinese_character(action)
                    and not _contains_latin_letter(action)
                    for action in localized_labels.values()
                ):
                    return localized_labels, localized_path, scanned, truncated
            return labels, path, scanned, truncated
    return {}, None, scanned, truncated


def _indexed_localized_dictionary_candidates(reference_path: Path) -> list[Path]:
    reference_name = reference_path.name
    marker = "_en.bin"
    if not reference_name.casefold().endswith(marker):
        base_name = re.sub(
            r"_[^_.]+\.bin$",
            "",
            reference_name,
            flags=re.IGNORECASE,
        )
    else:
        base_name = reference_name[: -len(marker)]
    candidates: list[Path] = []
    for language_code in _INDEXED_CHINESE_LANGUAGE_CODES:
        candidate = reference_path.with_name(
            f"{base_name}_{language_code}.bin"
        )
        try:
            if (
                _is_link_or_junction_path(candidate)
                or not candidate.is_file()
                or candidate.stat().st_size > MAX_INDEXED_MENU_DICTIONARY_BYTES
            ):
                continue
        except OSError:
            continue
        candidates.append(candidate)
    return candidates


def _indexed_fixed_function_keymap(
    menu_dictionary_path: Path | None,
) -> tuple[dict[str, dict[str, str]], Path | None, int]:
    """Read a strict sibling tutorial dictionary for non-remappable F-keys."""
    if menu_dictionary_path is None:
        return {}, None, 0
    candidate = menu_dictionary_path.with_name("dictio_dialog_en.bin")
    try:
        if (
            _is_link_or_junction_path(candidate)
            or not candidate.is_file()
            or candidate.stat().st_size > MAX_INDEXED_MENU_DICTIONARY_BYTES
        ):
            return {}, None, 0
        reference_keymap = _indexed_function_key_labels_from_dictionary(
            _read_bounded_bytes(candidate, MAX_INDEXED_MENU_DICTIONARY_BYTES)
        )
    except (OSError, ValueError, struct.error):
        return {}, None, 1
    if not reference_keymap:
        return {}, None, 1
    scanned = 1
    for localized_path in _indexed_localized_dictionary_candidates(candidate):
        scanned += 1
        try:
            localized_keymap = _indexed_function_key_labels_from_dictionary(
                _read_bounded_bytes(
                    localized_path,
                    MAX_INDEXED_MENU_DICTIONARY_BYTES,
                )
            )
        except (OSError, ValueError, struct.error):
            continue
        if (
            set(reference_keymap).issubset(localized_keymap)
            and all(
                _contains_chinese_character(
                    str(localized_keymap[input_name].get("action", ""))
                )
                and not _contains_latin_letter(
                    str(localized_keymap[input_name].get("action", ""))
                )
                for input_name in reference_keymap
            )
        ):
            return (
                {
                    input_name: dict(localized_keymap[input_name])
                    for input_name in reference_keymap
                },
                localized_path,
                scanned,
            )
    return reference_keymap, candidate, scanned


def _indexed_identity_config_candidates(
    game_directory: Path,
    file_name: str,
) -> tuple[list[Path], bool]:
    candidates: list[Path] = []
    roots, truncated = _bounded_identity_config_roots(game_directory)
    visited_entries = 0
    started = time.monotonic()
    stop_scanning = False

    def is_linked_directory(path: Path) -> bool:
        try:
            return path.is_symlink() or path.is_junction()
        except OSError:
            return True

    for root in roots:
        if is_linked_directory(root):
            continue
        pending: list[tuple[Path, int]] = [(root, 0)]
        while pending and not stop_scanning:
            current_root, depth = pending.pop()
            try:
                entries_context = os.scandir(current_root)
            except OSError:
                continue
            try:
                with entries_context as entries:
                    for entry in entries:
                        visited_entries += 1
                        if (
                            visited_entries > MAX_GAME_DIRECTORY_ENTRIES
                            or time.monotonic() - started
                            > MAX_INDEXED_IDENTITY_SCAN_SECONDS
                        ):
                            truncated = True
                            stop_scanning = True
                            break
                        candidate = Path(entry.path)
                        try:
                            linked = entry.is_symlink() or candidate.is_junction()
                            is_directory = entry.is_dir(follow_symlinks=False)
                            is_file = entry.is_file(follow_symlinks=False)
                        except OSError:
                            continue
                        if linked:
                            continue
                        if is_directory:
                            if depth < 4:
                                pending.append((candidate, depth + 1))
                            continue
                        if (
                            not is_file
                            or entry.name.casefold() != file_name.casefold()
                        ):
                            continue
                        try:
                            if (
                                entry.stat(follow_symlinks=False).st_size
                                <= MAX_GAME_CONFIG_FILE_BYTES
                            ):
                                candidates.append(candidate)
                        except OSError:
                            continue
            except OSError:
                continue
        if stop_scanning:
            break

    def modified_ns(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return -1

    ordered = sorted(
        dict.fromkeys(candidates),
        key=lambda path: (-modified_ns(path), str(path).casefold()),
    )
    if len(ordered) > MAX_INDEXED_XML_CONFIG_FILES:
        truncated = True
        del ordered[MAX_INDEXED_XML_CONFIG_FILES:]
    return ordered, truncated


def discover_indexed_xml_keymap(game_directory: Path) -> KeymapDiscovery:
    """Discover structurally identified numbered TinyXML keyboard mappings."""
    install_candidates, truncated = _indexed_xml_install_candidates(game_directory)
    if not install_candidates:
        return KeymapDiscovery({}, (), 0, truncated)
    labels, dictionary_path, dictionary_scanned, dictionary_truncated = (
        _indexed_menu_action_labels(game_directory)
    )
    fixed_keymap, fixed_dictionary_path, fixed_dictionary_scanned = (
        _indexed_fixed_function_keymap(dictionary_path)
    )
    truncated = truncated or dictionary_truncated
    manifest = _steam_game_manifest(game_directory)
    scanned = dictionary_scanned + fixed_dictionary_scanned
    parsed_cache: dict[Path, _IndexedXmlMapping | None] = {}

    def parse_path(path: Path) -> _IndexedXmlMapping | None:
        nonlocal scanned
        try:
            identity = path.resolve()
        except (OSError, RuntimeError):
            identity = path
        if identity in parsed_cache:
            return parsed_cache[identity]
        scanned += 1
        try:
            parsed = _parse_indexed_xml_keymap(_read_game_config(path), labels)
        except (OSError, ValueError):
            parsed = None
        parsed_cache[identity] = parsed
        return parsed

    for install_path in install_candidates:
        install_mapping = parse_path(install_path)
        if install_mapping is None:
            continue
        try:
            install_identity = install_path.resolve()
        except (OSError, RuntimeError):
            install_identity = install_path
        preferred, steam_truncated = _steam_remote_config_candidates(
            manifest,
            install_path.name,
        )
        identity_candidates, identity_truncated = (
            _indexed_identity_config_candidates(game_directory, install_path.name)
        )
        truncated = truncated or steam_truncated or identity_truncated
        preferred.extend(identity_candidates)
        preferred.append(install_path)
        seen: set[Path] = set()
        for path in preferred:
            try:
                identity = path.resolve()
            except (OSError, RuntimeError):
                identity = path
            if identity in seen:
                continue
            seen.add(identity)
            mapping = install_mapping if identity == install_identity else parse_path(path)
            if mapping is None:
                continue
            combined_keymap = {
                key: dict(value)
                for key, value in mapping.keymap.items()
            }
            for input_name, fixed_mapping in fixed_keymap.items():
                _add_detected_mapping(
                    combined_keymap,
                    input_name,
                    fixed_mapping["action"],
                )
            sources = [path]
            if dictionary_path is not None:
                sources.append(dictionary_path)
            if fixed_dictionary_path is not None:
                sources.append(fixed_dictionary_path)
            return KeymapDiscovery(
                combined_keymap,
                tuple(sources),
                scanned,
                truncated,
                True,
            )
    return KeymapDiscovery({}, (), scanned, truncated)


def _pixpil_player_input_groups(
    paths: list[Path],
    preferred_owner: str | None,
) -> tuple[list[tuple[Path, ...]], bool]:
    grouped: dict[Path, list[Path]] = {}
    for path in paths:
        try:
            parent = path.parent.resolve()
        except (OSError, RuntimeError):
            parent = path.parent
        grouped.setdefault(parent, []).append(path)

    def modified_ns(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return -1

    groups = [
        tuple(
            sorted(
                group,
                key=lambda path: (
                    path.name.casefold() != "input_settings",
                    str(path).casefold(),
                ),
            )
        )
        for group in grouped.values()
    ]
    if preferred_owner:
        preferred_name = f"steam_{preferred_owner}".casefold()
        groups = [
            group
            for group in groups
            if group and group[0].parent.name.casefold() == preferred_name
        ]
    groups.sort(
        key=lambda group: (
            max((modified_ns(path) for path in group), default=-1),
            str(group[0].parent).casefold() if group else "",
        ),
        reverse=True,
    )
    truncated = len(groups) > MAX_PIXPIL_PROFILE_GROUPS
    return groups[:MAX_PIXPIL_PROFILE_GROUPS], truncated


def _overlay_pixpil_keymap(
    base: dict[str, dict[str, str]],
    override: dict[str, dict[str, str]],
    overridden_actions: set[tuple[str, str]] | None = None,
) -> None:
    actions_to_replace = overridden_actions or {
        (mapping["type"], action.strip())
        for mapping in override.values()
        for action in mapping["action"].split(" / ")
        if action.strip()
    }
    for input_name in list(base):
        remaining_actions = [
            action.strip()
            for action in base[input_name]["action"].split(" / ")
            if action.strip()
            and (base[input_name]["type"], action.strip()) not in actions_to_replace
        ]
        if not remaining_actions:
            del base[input_name]
            continue
        base[input_name]["action"] = " / ".join(remaining_actions)
        remaining_direction = next(
            (
                direction
                for action in remaining_actions
                if (direction := _movement_direction(action, input_name)) is not None
            ),
            None,
        )
        if remaining_direction is None:
            base[input_name].pop("movement_direction", None)
        else:
            base[input_name]["movement_direction"] = remaining_direction
    for input_name, mapping in override.items():
        existing = base.get(input_name)
        if existing is None:
            base[input_name] = dict(mapping)
            continue
        for action in mapping["action"].split(" / "):
            _add_detected_mapping(
                base,
                input_name,
                action,
                part=mapping.get("movement_direction", ""),
            )
        if mapping.get("movement_direction"):
            existing["movement_direction"] = mapping["movement_direction"]


def _merge_pixpil_keymap(
    target: dict[str, dict[str, str]],
    addition: dict[str, dict[str, str]],
) -> None:
    for input_name, mapping in addition.items():
        existing = target.get(input_name)
        if existing is None:
            target[input_name] = dict(mapping)
            continue
        actions = [item.strip() for item in existing["action"].split(" / ")]
        for action in mapping["action"].split(" / "):
            action = action.strip()
            if action and action not in actions:
                actions.append(action)
        existing["action"] = " / ".join(actions)[:240]
        if mapping.get("movement_direction") and not existing.get(
            "movement_direction"
        ):
            existing["movement_direction"] = mapping["movement_direction"]


def _pixpil_player_config_name(
    config_keymaps: dict[str, dict[str, dict[str, str]]],
    player_file_name: str,
) -> str:
    farm = player_file_name.casefold() == "input_settings_farm"
    candidates: list[tuple[int, str]] = []
    for name in config_keymaps:
        normalized = _normalized_pixpil_input(name)
        if not normalized.endswith("defaultinputmappingconfig"):
            continue
        if farm != ("farm" in normalized):
            continue
        candidates.append((len(normalized), name))
    if candidates:
        candidates.sort()
        return candidates[0][1]
    return "__player_farm__" if farm else "__player_regular__"


def _apply_pixpil_player_mapping(
    config_keymaps: dict[str, dict[str, dict[str, str]]],
    player_file_name: str,
    player_mapping: dict[str, dict[str, str]],
    overridden_actions: set[tuple[str, str]],
) -> None:
    config_name = _pixpil_player_config_name(config_keymaps, player_file_name)
    base = config_keymaps.setdefault(config_name, {})
    _overlay_pixpil_keymap(base, player_mapping, overridden_actions)


def _flatten_pixpil_keymaps(
    config_keymaps: dict[str, dict[str, dict[str, str]]],
) -> dict[str, dict[str, str]]:
    flattened: dict[str, dict[str, str]] = {}
    for keymap in config_keymaps.values():
        _merge_pixpil_keymap(flattened, keymap)
    return flattened


def discover_pixpil_keymap(game_directory: Path) -> KeymapDiscovery:
    """Discover Pixpil/MOAI GArchive defaults and optional player overrides."""
    manifest_candidates = [
        game_directory / "content" / "packages.json",
        game_directory / "packages.json",
        *(game_directory / name / "packages.json" for name in ("Content", "game", "Game")),
    ]
    manifest_path: Path | None = None
    manifest: dict[str, object] | None = None
    for path in dict.fromkeys(manifest_candidates):
        try:
            if not path.is_file():
                continue
            manifest_bytes = _read_bounded_bytes(path, MAX_GAME_CONFIG_FILE_BYTES)
            _validate_pixpil_json_depth(manifest_bytes)
            candidate = json.loads(manifest_bytes.decode("utf-8-sig"))
        except (OSError, ValueError, UnicodeDecodeError, RecursionError):
            continue
        if not isinstance(candidate, dict):
            continue
        packages = candidate.get("packages")
        project_info = candidate.get("project_info")
        if not isinstance(packages, dict) or not isinstance(project_info, dict):
            continue
        script_package = packages.get("script")
        if (
            not isinstance(script_package, dict)
            or str(script_package.get("mode", "")).casefold() != "packed"
        ):
            continue
        manifest_path = path
        manifest = candidate
        break
    if manifest_path is None or manifest is None:
        return KeymapDiscovery({}, (), 0, False)

    scanned = 1
    sources: list[Path] = [manifest_path]
    truncated = False
    try:
        content_directories = [
            path for path in manifest_path.parent.iterdir() if path.is_dir()
        ]
    except OSError:
        content_directories = []
    archive_directories = [
        manifest_path.parent / "game",
        *content_directories,
    ]
    script_path: Path | None = None
    for directory in dict.fromkeys(archive_directories):
        candidate = directory / "script.g"
        if candidate.is_file():
            script_path = candidate
            break
    if script_path is None:
        return KeymapDiscovery({}, tuple(sources), scanned, False)
    config_path = script_path.with_name("config.g")

    try:
        script_data, script_entries = _read_pixpil_garchive(script_path)
    except (OSError, ValueError):
        return KeymapDiscovery({}, tuple([*sources, script_path]), scanned + 1, False)
    scanned += 1
    sources.append(script_path)

    candidate_names: list[str] = []
    if config_path.is_file():
        scanned += 1
        sources.insert(1, config_path)
        try:
            config_data, config_entries = _read_pixpil_garchive(config_path)
            library_entry = config_entries.get("script_library")
            if library_entry is not None:
                library_data = _extract_pixpil_garchive_entry(
                    config_data,
                    library_entry,
                    maximum_size=MAX_PIXPIL_LIBRARY_BYTES,
                )
                _validate_pixpil_json_depth(library_data)
                library = json.loads(library_data.decode("utf-8"))
                exports = library.get("export") if isinstance(library, dict) else None
                if isinstance(exports, dict):
                    ranked: list[tuple[int, str]] = []
                    for symbol, resource in exports.items():
                        if not isinstance(symbol, str) or not isinstance(resource, str):
                            continue
                        normalized_symbol = re.sub(r"[^a-z]+", "", symbol.casefold())
                        priority = (
                            0 if "globalmanager" in normalized_symbol else
                            1 if (
                                "input" in normalized_symbol
                                or "binding" in normalized_symbol
                                or re.search(r"control(?!ler)", normalized_symbol)
                            ) else
                            2 if "settings" in normalized_symbol else
                            3 if "manager" in normalized_symbol else 4
                        )
                        if priority >= 4:
                            continue
                        entry_name = resource.replace("\\", "/").rsplit("/", 1)[-1]
                        if entry_name in script_entries:
                            ranked.append((priority, entry_name))
                    ranked.sort()
                    preferred = [item for item in ranked if item[0] < 3]
                    selected = preferred or ranked
                    candidate_names = list(
                        dict.fromkeys(name for _priority, name in selected)
                    )
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            RecursionError,
        ):
            pass

    if len(candidate_names) > MAX_PIXPIL_SCRIPT_CANDIDATES:
        candidate_names = candidate_names[:MAX_PIXPIL_SCRIPT_CANDIDATES]
        truncated = True
    if not candidate_names:
        candidate_names = [
            entry.name
            for entry in script_entries.values()
            if entry.uncompressed_size <= MAX_PIXPIL_LUA_BYTES
        ][:MAX_PIXPIL_SCRIPT_CANDIDATES]
        if len(script_entries) > len(candidate_names):
            truncated = True

    config_keymaps: dict[str, dict[str, dict[str, str]]] = {}
    decompressed_bytes = 0
    scan_deadline = time.monotonic() + MAX_PIXPIL_SCAN_SECONDS
    for name in candidate_names:
        if time.monotonic() > scan_deadline:
            truncated = True
            break
        entry = script_entries[name]
        if decompressed_bytes + entry.uncompressed_size > MAX_PIXPIL_ENTRY_BYTES:
            truncated = True
            break
        try:
            bytecode = _extract_pixpil_garchive_entry(
                script_data,
                entry,
                maximum_size=MAX_PIXPIL_LUA_BYTES,
            )
            decompressed_bytes += len(bytecode)
            lowered = bytecode.lower()
            if (
                b"mappingconfig" not in lowered
                or b"mappings" not in lowered
                or not any(
                    device in lowered
                    for device in (b"keyboard", b"mouse", b"joystick")
                )
            ):
                continue
            recovered_configs = extract_mapping_configs(bytecode)
        except (TypeError, ValueError, zstandard.ZstdError):
            continue
        for config_name, document in recovered_configs.items():
            parsed = _parse_pixpil_mapping_document(document)
            if parsed:
                _merge_pixpil_keymap(
                    config_keymaps.setdefault(config_name, {}),
                    parsed,
                )

    project_info = manifest.get("project_info")
    assert isinstance(project_info, dict)
    player_files, player_truncated = _pixpil_player_input_files(
        game_directory,
        project_info,
    )
    profile_groups, group_truncated = _pixpil_player_input_groups(
        player_files,
        _steam_last_owner(game_directory),
    )
    truncated = truncated or player_truncated or group_truncated
    for profile_group in profile_groups:
        parsed_group: list[
            tuple[Path, dict[str, dict[str, str]], set[tuple[str, str]]]
        ] = []
        for path in profile_group:
            scanned += 1
            try:
                overridden_actions: set[tuple[str, str]] = set()
                player_document = _decode_pixpil_safe_settings(path)
                if (
                    not isinstance(player_document, dict)
                    or not isinstance(player_document.get("mappings"), dict)
                ):
                    continue
                player_mapping = _parse_pixpil_mapping_document(
                    player_document,
                    overridden_actions,
                )
            except (
                OSError,
                ValueError,
                json.JSONDecodeError,
                UnicodeDecodeError,
                zlib.error,
            ):
                continue
            parsed_group.append((path, player_mapping, overridden_actions))
        if parsed_group:
            for path, player_mapping, overridden_actions in parsed_group:
                if path not in sources:
                    sources.append(path)
                _apply_pixpil_player_mapping(
                    config_keymaps,
                    path.name,
                    player_mapping,
                    overridden_actions,
                )
            break
    return KeymapDiscovery(
        _flatten_pixpil_keymaps(config_keymaps),
        tuple(sources),
        scanned,
        truncated,
    )


def discover_dotnet_monogame_keymap(game_directory: Path) -> KeymapDiscovery:
    """Discover XML player overrides, then managed-code defaults for .NET games."""
    scanned = 0
    xml_names = {
        "default_options",
        "startup_preferences",
        "options.xml",
        "settings.xml",
        "controls.xml",
        "keybindings.xml",
    }
    xml_candidates: list[Path] = []
    for root in (game_directory, *_identity_config_roots(game_directory)):
        for current_root, directory_names, file_names in os.walk(root, followlinks=False):
            _prune_walk_links(current_root, directory_names, file_names)
            relative_depth = len(Path(current_root).relative_to(root).parts)
            if relative_depth >= 4:
                directory_names[:] = []
            for file_name in file_names:
                if file_name.casefold() in xml_names:
                    xml_candidates.append(Path(current_root) / file_name)
                    if len(xml_candidates) >= 64:
                        break
            if len(xml_candidates) >= 64:
                break
    for path in xml_candidates:
        scanned += 1
        try:
            if path.stat().st_size > MAX_GAME_CONFIG_FILE_BYTES:
                continue
            parsed = _parse_dotnet_input_xml(_read_game_config(path))
        except (OSError, ValueError):
            continue
        if parsed:
            return KeymapDiscovery(parsed, (path,), scanned, False)

    directory_identity = re.sub(r"[^a-z0-9]+", "", game_directory.name.casefold())
    assemblies: list[Path] = []
    for path in game_directory.glob("*.dll"):
        try:
            size = path.stat().st_size
            if not 1_024 <= size <= MAX_DOTNET_ASSEMBLY_BYTES:
                continue
            normalized = re.sub(r"[^a-z0-9]+", "", path.stem.casefold())
            marker = path.read_bytes()
            if normalized == directory_identity or (
                b"InputButton" in marker
                and any(word in marker for word in (b"moveUpButton", b"setControlsToDefault", b"KeyBinding"))
            ):
                assemblies.append(path)
        except OSError:
            continue
    assemblies.sort(
        key=lambda path: (
            re.sub(r"[^a-z0-9]+", "", path.stem.casefold()) != directory_identity,
            path.name.casefold(),
        )
    )
    for path in assemblies[:8]:
        scanned += 1
        try:
            parsed = _parse_dotnet_embedded_keymap(path)
        except Exception:
            continue
        if parsed:
            return KeymapDiscovery(parsed, (path,), scanned, False)
    return KeymapDiscovery({}, (), scanned, False)


_MANUAL_PDF_FILE_HINTS = (
    "manual",
    "handbook",
    "guide",
    "control",
    "keyboard",
    "keybind",
)
_MANUAL_PDF_DIRECTORY_NAMES = {
    "docs",
    "documentation",
    "manual",
    "manuals",
    "support",
}


def _manual_key_tokens(line: str) -> list[str]:
    """Return canonical keys from a short key label found in a manual."""
    raw = line.strip().strip("[]{}<>")
    if not raw or len(raw) > 80:
        return []
    raw = re.sub(
        r"^(?:keyboard\s+)?keys?\s*[:：]\s*",
        "",
        raw,
        flags=re.IGNORECASE,
    )
    for symbol, name in {
        "↑": "Up",
        "↓": "Down",
        "←": "Left",
        "→": "Right",
    }.items():
        raw = raw.replace(symbol, f",{name},")
    raw = raw.strip(" ,")
    if not raw:
        return []
    canonical = _canonical_input_name(raw)
    if canonical is not None:
        return [canonical[0]]
    parts = [
        item.strip().strip("[]{}<>")
        for item in re.split(r"\s*(?:[,;/|]|\bor\b)\s*", raw, flags=re.IGNORECASE)
        if item.strip()
    ]
    if len(parts) == 1 and " " in raw:
        whitespace_parts = raw.split()
        if 1 < len(whitespace_parts) <= 8 and all(
            re.fullmatch(r"[a-z0-9]|f(?:[1-9]|1[0-9]|2[0-4])", item, re.IGNORECASE)
            for item in whitespace_parts
        ):
            parts = whitespace_parts
    if not 1 < len(parts) <= 8:
        return []
    keys: list[str] = []
    for part in parts:
        canonical = _canonical_input_name(part)
        if canonical is None:
            return []
        if canonical[0] not in keys:
            keys.append(canonical[0])
    return keys


def _parse_manual_control_text(text: str) -> dict[str, dict[str, str]]:
    """Parse common key/action lists extracted from a game manual page."""
    detected: dict[str, dict[str, str]] = {}
    pending_keys: list[str] = []
    table_key_column: int | None = None
    bullet_pattern = re.compile(r"^(?:[•‣◦⁃▪∙]\s*|[-*]\s+)(.+)$")
    for source_line in text.replace("\u00a0", " ").splitlines():
        line = source_line.strip()
        if not line:
            continue
        bullet = bullet_pattern.match(line)
        if bullet is not None:
            action = bullet.group(1).strip().strip(".;。；")
            if not pending_keys or not action or len(action) > 160:
                continue
            for key in pending_keys:
                _add_detected_mapping(detected, key, action)
            continue
        columns = re.split(r"(?:\t+|\s{2,})", line, maxsplit=1)
        if len(columns) == 2:
            headings = [
                re.sub(r"[^a-z]+", "", item.casefold()) for item in columns
            ]
            key_headings = {"button", "buttons", "input", "key", "keys"}
            action_headings = {
                "action",
                "actions",
                "command",
                "commands",
                "description",
                "function",
                "functions",
            }
            if headings[0] in key_headings and headings[1] in action_headings:
                table_key_column = 0
                pending_keys = []
                continue
            if headings[1] in key_headings and headings[0] in action_headings:
                table_key_column = 1
                pending_keys = []
                continue
        if len(columns) == 2 and table_key_column is not None:
            inline_keys = _manual_key_tokens(columns[table_key_column])
            action = columns[1 - table_key_column].strip().strip(".;。；")
            if inline_keys and action and len(action) <= 160:
                for key in inline_keys:
                    _add_detected_mapping(detected, key, action)
                pending_keys = inline_keys
                continue
        pending_keys = _manual_key_tokens(line)
    return detected


def _manual_page_has_controls(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.casefold())
    return bool(
        re.search(
            r"\b(?:game\s+controls?|keyboard(?:\s+controls?)?|"
            r"key\s*(?:bindings?|mapping|commands?)|default\s+keys?)\b",
            normalized,
        )
        or any(term in text for term in ("游戏控制", "键位", "按键", "键盘操作"))
    )


def _is_manual_pdf_candidate(path: Path) -> bool:
    if path.suffix.casefold() != ".pdf":
        return False
    stem = path.stem.casefold()
    directory_names = {part.casefold() for part in path.parent.parts}
    return any(hint in stem for hint in _MANUAL_PDF_FILE_HINTS) or bool(
        directory_names.intersection(_MANUAL_PDF_DIRECTORY_NAMES)
    )


def discover_manual_keymap(game_directory: Path) -> KeymapDiscovery:
    """Read default controls from a bundled PDF only when no live config exists."""
    candidates: list[Path] = []
    entries = 0
    truncated = False
    for current_root, directory_names, file_names in os.walk(
        game_directory,
        followlinks=False,
    ):
        _prune_walk_links(current_root, directory_names, file_names)
        directory_names[:] = [
            name
            for name in directory_names
            if name.casefold()
            not in {"binaries", "paks", "movies", "crashreportclient", "__pycache__"}
        ]
        entries += len(directory_names) + len(file_names)
        if entries > MAX_GAME_DIRECTORY_ENTRIES:
            truncated = True
            break
        for file_name in file_names:
            path = Path(current_root) / file_name
            if not _is_manual_pdf_candidate(path):
                continue
            if _is_link_or_junction_path(path):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if not 0 < size <= MAX_MANUAL_PDF_BYTES:
                continue
            candidates.append(path)
            if len(candidates) >= MAX_MANUAL_PDF_CANDIDATES:
                truncated = True
                break
        if truncated:
            break
    candidates.sort(
        key=lambda path: (
            0
            if any(
                part.casefold() in {"english", "en", "en-us", "en_us"}
                for part in path.parts
            )
            else 1,
            0 if path.stem.casefold() in {"manual", "game_manual", "controls"} else 1,
            len(path.parts),
            str(path).casefold(),
        )
    )
    if len(candidates) > MAX_MANUAL_PDF_FILES:
        candidates = candidates[:MAX_MANUAL_PDF_FILES]
        truncated = True
    try:
        from pypdf import PdfReader
        from pypdf import filters as pdf_filters
    except ImportError:
        return KeymapDiscovery({}, (), 0, truncated)
    for limit_name in (
        "JBIG2_MAX_OUTPUT_LENGTH",
        "LZW_MAX_OUTPUT_LENGTH",
        "RUN_LENGTH_MAX_OUTPUT_LENGTH",
        "ZLIB_MAX_OUTPUT_LENGTH",
    ):
        current_limit = getattr(pdf_filters, limit_name, MAX_MANUAL_PAGE_STREAM_BYTES)
        setattr(
            pdf_filters,
            limit_name,
            min(current_limit, MAX_MANUAL_PAGE_STREAM_BYTES),
        )
    scanned = 0
    scan_deadline = time.monotonic() + MAX_MANUAL_SCAN_SECONDS
    for path in candidates:
        if time.monotonic() > scan_deadline:
            truncated = True
            break
        scanned += 1
        try:
            reader = PdfReader(str(path), strict=False)
            if reader.is_encrypted and not reader.decrypt(""):
                continue
            detected: dict[str, dict[str, str]] = {}
            remaining_context_pages = 0
            extracted_chars = 0
            for page in reader.pages[:MAX_MANUAL_PAGES]:
                if time.monotonic() > scan_deadline:
                    truncated = True
                    break
                page_text = page.extract_text() or ""
                extracted_chars += len(page_text)
                if extracted_chars > MAX_MANUAL_TEXT_CHARS:
                    truncated = True
                    break
                if _manual_page_has_controls(page_text):
                    remaining_context_pages = 2
                elif remaining_context_pages:
                    remaining_context_pages -= 1
                else:
                    continue
                parsed = _parse_manual_control_text(page_text)
                for input_name, mapping in parsed.items():
                    _add_detected_mapping(
                        detected,
                        input_name,
                        mapping["action"],
                        part=mapping.get("movement_direction", ""),
                    )
                    if mapping.get("movement_direction") and input_name in detected:
                        detected[input_name]["movement_direction"] = mapping[
                            "movement_direction"
                        ]
            if len(detected) >= 3:
                return KeymapDiscovery(detected, (path,), scanned, truncated)
        except Exception:
            continue
    return KeymapDiscovery({}, (), scanned, truncated)


def _is_bounded_script_keymap_candidate(path: Path) -> bool:
    if not is_script_keymap_candidate(path):
        return False
    suffix = path.suffix.casefold()
    name = path.name.casefold()
    compact = re.sub(r"[^a-z0-9]+", "", path.stem.casefold())
    if suffix == ".rpy":
        return True
    if name in {
        "conf.lua",
        "main.lua",
        "rpg_core.js",
        "rpg_managers.js",
    }:
        return True
    return any(
        word in compact
        for word in ("bind", "control", "input", "keymap", "keybind")
    )


def _is_complete_keyvalues_config(path: Path, format_name: str) -> bool:
    name = path.name.casefold()
    compact = re.sub(r"[^a-z0-9]+", "", path.stem.casefold())
    if format_name == "source2_vcfg":
        return any(word in compact for word in ("bindings", "keybindings", "userkeys"))
    if format_name != "idtech_cfg":
        return False
    if name == "config.cfg":
        return True
    return compact.endswith("config") and compact not in {
        "autoexec",
        "configdefault",
        "defaultconfig",
        "userconfig",
    }


def _is_game_config_candidate(path: Path) -> bool:
    name = path.name.casefold()
    suffix = path.suffix.casefold()
    if name == "kb_act.lst":
        return True
    if is_keyvalues_cfg_candidate(path) or _is_bounded_script_keymap_candidate(path):
        return True
    if name == "project.godot" or is_common_xml_keymap_candidate(name):
        return True
    if suffix == ".cfg" and (
        name in {"config.cfg", "userconfig.cfg", "autoexec.cfg"}
        or any(word in name for word in ("input", "key", "bind", "control"))
    ):
        return True
    if suffix == ".inputactions" or name == "inputmanager.asset":
        return True
    if suffix == ".ini":
        return True
    if suffix in {".yaml", ".yml", ".lua"}:
        return any(word in name for word in ("input", "key", "bind", "control"))
    return suffix == ".json" and any(
        word in name for word in ("input", "keymap", "keybind", "binding", "control")
    )


_PLAYER_CONFIG_EXACT_NAMES = {
    "bindings.json",
    "controls.ini",
    "controls.json",
    "controls.xml",
    "config_player.xml",
    "input.ini",
    "input.xml",
    "input profiles.json",
    "inputprofiles.json",
    "inputbindings.json",
    "keybindings.xml",
    "keybindings.json",
    "keybinds.json",
    "keyprefs.xml",
    "project.godot",
    "settings.celeste",
    "settings.save",
}
_BACKUP_PATH_WORDS = {
    "archive",
    "archived",
    "backup",
    "backups",
    "bak",
    "copy",
    "old",
    "previous",
    "temp",
    "temporary",
}
_MOD_PATH_WORDS = {
    "addon",
    "addons",
    "mod",
    "mods",
    "plugin",
    "plugins",
    "workshop",
}


def _player_path_tokens(parts: tuple[str, ...]) -> set[str]:
    return {
        token
        for part in parts
        for token in re.findall(r"[a-z0-9]+", part.casefold())
    }


def _is_external_player_keymap_candidate(path: Path) -> bool:
    """Select small player files by metadata; content parsers make the decision."""
    name = path.name.casefold()
    suffix = path.suffix.casefold()
    if name in _PLAYER_CONFIG_EXACT_NAMES:
        return True
    if is_keyvalues_cfg_candidate(path) or _is_bounded_script_keymap_candidate(path):
        return True
    # Numbered TinyXML games use ordinary .cfg/.xml names, so retain those in
    # an already identity-matched player directory.  The structural parser is
    # strict enough to reject unrelated configuration XML.
    if suffix in {".cfg", ".xml", ".celeste"}:
        return True
    if suffix in {".json", ".save", ".ini", ".yaml", ".yml", ".lua"}:
        return any(
            word in name
            for word in ("input", "key", "bind", "control", "option", "setting")
        )
    return False


def _merge_keymap_records(
    target: dict[str, dict[str, str]],
    addition: dict[str, dict[str, str]],
) -> None:
    for input_name, mapping in addition.items():
        existing = target.get(input_name)
        if existing is None:
            target[input_name] = dict(mapping)
            continue
        actions = [item.strip() for item in existing["action"].split(" / ")]
        for action in mapping["action"].split(" / "):
            action = action.strip()
            if action and action not in actions:
                actions.append(action)
        existing["action"] = " / ".join(actions)[:240]
        if mapping.get("movement_direction") and not existing.get(
            "movement_direction"
        ):
            existing["movement_direction"] = mapping["movement_direction"]


def _keymap_action_names(
    keymap: dict[str, dict[str, str]],
) -> set[str]:
    return {
        action.strip()
        for mapping in keymap.values()
        for action in mapping.get("action", "").split(" / ")
        if action.strip()
    }


def _remove_overridden_actions(
    keymap: dict[str, dict[str, str]],
    overridden_actions: frozenset[str] | set[str],
) -> None:
    normalized = {
        re.sub(r"[^a-z0-9]+", "", action.casefold())
        for action in overridden_actions
        if action
    }
    if not normalized:
        return
    for input_name in list(keymap):
        mapping = keymap[input_name]
        remaining = [
            action.strip()
            for action in mapping.get("action", "").split(" / ")
            if action.strip()
            and re.sub(r"[^a-z0-9]+", "", action.strip().casefold())
            not in normalized
        ]
        if not remaining:
            del keymap[input_name]
            continue
        mapping["action"] = " / ".join(remaining)[:240]
        direction = next(
            (
                candidate
                for action in remaining
                if (candidate := _movement_direction(action, input_name))
            ),
            None,
        )
        if direction:
            mapping["movement_direction"] = direction
        else:
            mapping.pop("movement_direction", None)


def _matches_selected_steam_account(
    path: Path,
    account_id: str | None,
) -> bool:
    if account_id is None or not account_id.isdigit():
        return True
    expected_steam_id64 = str(STEAM_ID64_ACCOUNT_BASE + int(account_id))
    embedded_ids = {
        part
        for part in path.parts
        if re.fullmatch(r"7656119[0-9]{10}", part)
    }
    return not embedded_ids or embedded_ids == {expected_steam_id64}


def _player_file_rank(
    path: Path,
    discovery: PlayerConfigDiscovery,
) -> tuple[int, int, int, int, int, str]:
    root_index = len(discovery.roots)
    relative_parts = path.parts
    for index, root in enumerate(discovery.roots):
        try:
            relative = path.relative_to(root.path)
        except ValueError:
            continue
        root_index = index
        relative_parts = relative.parts
        break
    path_tokens = _player_path_tokens(relative_parts)
    backup_penalty = int(bool(path_tokens.intersection(_BACKUP_PATH_WORDS)))
    mod_penalty = int(bool(path_tokens.intersection(_MOD_PATH_WORDS)))
    exact_penalty = int(path.name.casefold() not in _PLAYER_CONFIG_EXACT_NAMES)
    return (
        root_index,
        backup_penalty,
        mod_penalty,
        exact_penalty,
        len(relative_parts),
        str(path).casefold(),
    )


def _is_backup_player_file(
    path: Path,
    discovery: PlayerConfigDiscovery,
) -> bool:
    for root in discovery.roots:
        try:
            parts = path.relative_to(root.path).parts
            break
        except ValueError:
            continue
    else:
        parts = path.parts
    tokens = _player_path_tokens(parts)
    return bool(tokens.intersection(_BACKUP_PATH_WORDS))


def _is_authoritative_player_path(
    path: Path,
    discovery: PlayerConfigDiscovery,
) -> bool:
    """Require an exact game root and exclude nested mod/add-on configs."""
    for root in discovery.roots:
        try:
            relative = path.relative_to(root.path)
        except ValueError:
            continue
        if _player_path_tokens(relative.parts).intersection(_MOD_PATH_WORDS):
            return False
        if root.source == "steam":
            return True
        root_identity = "".join(
            character
            for character in unicodedata.normalize("NFKC", root.path.name).casefold()
            if character.isalnum()
        )
        return root_identity in discovery.identities
    return False


def _is_fuzzy_player_root_path(
    path: Path,
    discovery: PlayerConfigDiscovery,
) -> bool:
    for root in discovery.roots:
        try:
            path.relative_to(root.path)
        except ValueError:
            continue
        if root.source == "steam":
            return False
        root_identity = "".join(
            character
            for character in unicodedata.normalize("NFKC", root.path.name).casefold()
            if character.isalnum()
        )
        return root_identity not in discovery.identities
    return True


def discover_external_player_keymap(
    game_directory: Path,
    player_files: PlayerConfigDiscovery | None = None,
) -> KeymapDiscovery:
    """Parse actual per-player configs from Steam, AppData and Documents."""
    discovery = player_files or discover_player_config_files(
        game_directory,
        _is_external_player_keymap_candidate,
    )
    partial: dict[str, dict[str, str]] = {}
    partial_sources: list[Path] = []
    overridden_actions: set[str] = set()
    scanned = 0
    ranked_files = sorted(
        discovery.files,
        key=lambda path: _player_file_rank(path, discovery),
    )
    for path in ranked_files:
        scanned += 1
        if _is_backup_player_file(path, discovery):
            continue
        if not _matches_selected_steam_account(
            path,
            discovery.steam_account_id,
        ):
            continue
        if _is_fuzzy_player_root_path(path, discovery):
            continue
        name = path.name.casefold()
        suffix = path.suffix.casefold()
        authoritative_path = _is_authoritative_player_path(path, discovery)
        try:
            text = _read_game_config(path)
        except (OSError, ValueError):
            continue

        if (
            is_keyvalues_cfg_candidate(path)
            and name not in _VALVE_CONFIG_NAMES
            and name != "kb_act.lst"
        ):
            keyvalues = parse_keyvalues_cfg(text, file_name=path.name)
            if keyvalues.recognized_config:
                if authoritative_path and _is_complete_keyvalues_config(
                    path,
                    keyvalues.format_name,
                ):
                    return KeymapDiscovery(
                        keyvalues.keymap,
                        (path,),
                        scanned,
                        discovery.truncated or keyvalues.truncated,
                        True,
                        has_verified_player_config=True,
                    )
                _merge_keymap_records(partial, keyvalues.keymap)
                if path not in partial_sources:
                    partial_sources.append(path)
                continue

        if suffix in {".json", ".save"}:
            try:
                _validate_pixpil_json_depth(text.encode("utf-8"))
                document = json.loads(text)
            except (ValueError, RecursionError):
                continue
            native = _parse_native_keymap(document)
            if native:
                if authoritative_path:
                    return KeymapDiscovery(
                        native,
                        (path,),
                        scanned,
                        discovery.truncated,
                        True,
                        has_verified_player_config=True,
                    )
                _merge_keymap_records(partial, native)
                overridden_actions.update(_keymap_action_names(native))
                partial_sources.append(path)
                continue
            unity = _parse_unity_input_actions(document)
            if unity:
                if authoritative_path:
                    return KeymapDiscovery(
                        unity,
                        (path,),
                        scanned,
                        discovery.truncated,
                        True,
                        has_verified_player_config=True,
                    )
                _merge_keymap_records(partial, unity)
                overridden_actions.update(_keymap_action_names(unity))
                partial_sources.append(path)
                continue
            structured = parse_structured_json_keymap(
                document,
                source_name=path.name,
            )
            if not structured.recognized_config:
                continue
            # Complete profiles replace defaults. Sparse player overrides keep
            # action tombstones so their old/default bindings can be removed
            # after install-time maps have been collected.
            if authoritative_path and structured.authoritative:
                return KeymapDiscovery(
                    structured.keymap,
                    (path,),
                    scanned,
                    discovery.truncated,
                    True,
                    structured.overridden_actions,
                    has_verified_player_config=True,
                )
            _merge_keymap_records(partial, structured.keymap)
            overridden_actions.update(structured.overridden_actions)
            overridden_actions.update(_keymap_action_names(structured.keymap))
            partial_sources.append(path)
            continue

        if suffix in {".xml", ".celeste"}:
            parsed_xml = parse_common_xml_keymap(text, file_name=path.name)
            if parsed_xml.recognized:
                if authoritative_path:
                    return KeymapDiscovery(
                        parsed_xml.keymap,
                        (path,),
                        scanned,
                        discovery.truncated,
                        True,
                        has_verified_player_config=True,
                    )
                _merge_keymap_records(partial, parsed_xml.keymap)
                overridden_actions.update(_keymap_action_names(parsed_xml.keymap))
                partial_sources.append(path)
            continue

        if _is_bounded_script_keymap_candidate(path):
            script_result = parse_script_keymap(text, source_name=path.name)
            if script_result.recognized_config:
                _merge_keymap_records(partial, script_result.keymap)
                overridden_actions.update(_keymap_action_names(script_result.keymap))
                if path not in partial_sources:
                    partial_sources.append(path)
                continue

        parsed: dict[str, dict[str, str]] = {}
        if name == "project.godot" or suffix in {".cfg", ".ini"}:
            parsed = _parse_godot_input_map(text)
        if not parsed and suffix in {".cfg", ".ini", ".lua"}:
            parsed = _parse_simple_text_keymap(text)
        if not parsed and suffix in {".yaml", ".yml"}:
            parsed = _parse_simple_yaml_keymap(text)
        if parsed and name == "project.godot" and authoritative_path:
            return KeymapDiscovery(
                parsed,
                (path,),
                scanned,
                discovery.truncated,
                True,
                has_verified_player_config=True,
            )
        if parsed:
            _merge_keymap_records(partial, parsed)
            overridden_actions.update(_keymap_action_names(parsed))
            if path not in partial_sources:
                partial_sources.append(path)
    return KeymapDiscovery(
        partial,
        tuple(partial_sources),
        scanned,
        discovery.truncated,
        False,
        frozenset(overridden_actions),
        has_verified_player_config=bool(partial_sources),
    )


def _with_executed_scan_metadata(
    discovery: KeymapDiscovery,
    *previously_executed: KeymapDiscovery,
) -> KeymapDiscovery:
    """Preserve counts/limits from every discovery that already ran this turn."""

    return replace(
        discovery,
        scanned_files=(
            discovery.scanned_files
            + sum(item.scanned_files for item in previously_executed)
        ),
        truncated=(
            discovery.truncated
            or any(item.truncated for item in previously_executed)
        ),
    )


def discover_keymap_from_game_directory(directory: Path) -> KeymapDiscovery:
    root = directory.resolve()
    if not root.is_dir():
        raise ValueError("选择的游戏目录不存在或不可访问")
    foundation_result = discover_foundation_registry_keymap(root)
    foundation_sources = (
        (foundation_result.executable_path,)
        if foundation_result.executable_path is not None
        else ()
    )
    foundation_authoritative = bool(
        foundation_result.recognized
        and (foundation_result.used_registry or foundation_result.keymap)
    )
    foundation_discovery = KeymapDiscovery(
        foundation_result.keymap,
        foundation_sources,
        foundation_result.scanned_executables,
        foundation_result.truncated,
        foundation_authoritative,
        frozenset(),
        foundation_result.diagnostic,
        foundation_result.requires_game_launch,
        foundation_result.used_registry,
    )
    # A structurally validated current-player registry is authoritative even
    # when every known action is unbound; an empty map must clear the template.
    if foundation_result.recognized and foundation_result.used_registry:
        return foundation_discovery
    numeric_ini_result = discover_fromsoftware_numeric_ini_keymap(root)
    numeric_ini_sources = tuple(
        source
        for source in (
            numeric_ini_result.source_file,
            numeric_ini_result.executable_path,
        )
        if source is not None
    )
    numeric_ini_discovery = KeymapDiscovery(
        numeric_ini_result.keymap,
        numeric_ini_sources,
        numeric_ini_result.scanned_executables
        + (1 if numeric_ini_result.source_file is not None else 0),
        numeric_ini_result.truncated,
        numeric_ini_result.has_verified_player_config,
        notice=numeric_ini_result.diagnostic,
        requires_game_launch=numeric_ini_result.requires_game_launch,
        has_verified_player_config=numeric_ini_result.has_verified_player_config,
    )
    # The executable declaration and the INI ACTION table are matched exactly
    # before this result is marked verified.  It therefore replaces templates,
    # including when the player's valid file intentionally binds no actions.
    if numeric_ini_result.has_verified_player_config:
        return _with_executed_scan_metadata(
            numeric_ini_discovery,
            foundation_discovery,
        )
    # Once an executable has authoritatively declared this player-file format,
    # do not let broad installation-tree scanners mistake defaults for current
    # player state.  A missing file must retain its first-launch guidance; an
    # invalid existing file must retain the parser's precise safety diagnostic.
    if numeric_ini_result.recognized:
        return _with_executed_scan_metadata(
            numeric_ini_discovery,
            foundation_discovery,
        )
    player_files = discover_player_config_files(
        root,
        _is_external_player_keymap_candidate,
    )
    external_player = discover_external_player_keymap(root, player_files)
    if external_player.recognized_config:
        return _with_executed_scan_metadata(
            external_player,
            foundation_discovery,
            numeric_ini_discovery,
        )
    has_external_override = bool(
        external_player.keymap or external_player.overridden_actions
    )
    # A structurally verified Unreal player save must outrank installation
    # defaults (including otherwise authoritative JSON/XML/Valve templates).
    unreal_discovery = discover_unreal_keymap(root)
    if unreal_discovery.has_verified_player_config:
        return _with_executed_scan_metadata(
            unreal_discovery,
            foundation_discovery,
            numeric_ini_discovery,
            external_player,
        )
    if (
        unreal_discovery.blocks_heuristic_fallback
        and unreal_discovery.apply_mode == KEYMAP_APPLY_NONE
    ):
        # The player save contains a KeyConfigSettings section, but its future
        # layout could not be consumed safely, or an exact Pak default could
        # not be validated completely.  Do not mask unknown state with a
        # plausible-looking generic installation scan.
        return _with_executed_scan_metadata(
            unreal_discovery,
            foundation_discovery,
            numeric_ini_discovery,
            external_player,
        )
    if unreal_discovery.binding_authority == KEYMAP_AUTHORITY_DEFAULT_ONLY:
        if not has_external_override:
            # An authenticated Unreal DefaultInput is more precise than generic
            # install-tree JSON/XML/CFG matches and must win before those scans.
            return _with_executed_scan_metadata(
                unreal_discovery,
                foundation_discovery,
                numeric_ini_discovery,
                external_player,
            )
        mixed = {
            input_name: dict(mapping)
            for input_name, mapping in unreal_discovery.keymap.items()
        }
        _remove_overridden_actions(mixed, external_player.overridden_actions)
        _merge_keymap_records(mixed, external_player.keymap)
        mixed_sources = tuple(
            dict.fromkeys(
                (*unreal_discovery.source_files, *external_player.source_files)
            )
        )
        mixed_records = tuple(
            dict.fromkeys(
                (
                    *unreal_discovery.source_records,
                    *(
                        KeymapSource(path, "unverified_player_override")
                        for path in external_player.source_files
                    ),
                )
            )
        )
        mixed_discovery = KeymapDiscovery(
            mixed,
            mixed_sources,
            unreal_discovery.scanned_files + external_player.scanned_files,
            unreal_discovery.truncated or external_player.truncated,
            True,
            unreal_discovery.overridden_actions
            | external_player.overridden_actions,
            notice=(
                f"{unreal_discovery.notice}\n"
                "另合并了通用玩家配置候选中的部分覆盖；由于无法证明其完整性，"
                "最终键位仍须逐项人工核对。"
            ),
            has_recognized_player_file=bool(
                unreal_discovery.has_recognized_player_file
                or external_player.source_files
            ),
            source_records=mixed_records,
            binding_authority=KEYMAP_AUTHORITY_MIXED_UNVERIFIED,
            apply_mode=KEYMAP_APPLY_REPLACE,
        )
        return _with_executed_scan_metadata(
            mixed_discovery,
            foundation_discovery,
            numeric_ini_discovery,
        )
    if unreal_discovery.binding_authority == KEYMAP_AUTHORITY_MIXED_UNVERIFIED:
        # This is already a tightly matched Unreal player delta whose complete
        # baseline is unavailable.  Preserve it as an explicitly partial,
        # manual-review result instead of mixing in unrelated generic scans.
        return _with_executed_scan_metadata(
            unreal_discovery,
            foundation_discovery,
            numeric_ini_discovery,
            external_player,
        )
    # Complete external player configs outrank format defaults. Partial generic
    # scans are deliberately not mixed into this schema because their English
    # action labels cannot safely override the verified Chinese action table.
    if foundation_authoritative:
        selected_foundation = replace(
            foundation_discovery,
            recognized_config=True,
            notice=foundation_discovery.notice,
            requires_game_launch=foundation_discovery.requires_game_launch,
            has_verified_player_config=(
                foundation_discovery.has_verified_player_config
            ),
        )
        return _with_executed_scan_metadata(
            selected_foundation,
            numeric_ini_discovery,
            external_player,
            unreal_discovery,
        )
    structured_json = discover_structured_json_keymaps(
        root,
        include_player_roots=False,
    )
    structured_discovery = KeymapDiscovery(
        structured_json.keymap,
        structured_json.source_files,
        structured_json.scanned_files,
        structured_json.truncated,
        structured_json.authoritative,
        structured_json.overridden_actions,
    )
    if (
        structured_json.authoritative
        and not has_external_override
    ):
        return _with_executed_scan_metadata(
            structured_discovery,
            foundation_discovery,
            numeric_ini_discovery,
            external_player,
            unreal_discovery,
        )
    indexed_discovery = discover_indexed_xml_keymap(root)
    if indexed_discovery.recognized_config and not has_external_override:
        return _with_executed_scan_metadata(
            indexed_discovery,
            foundation_discovery,
            numeric_ini_discovery,
            external_player,
            unreal_discovery,
            structured_discovery,
        )
    valve_discovery = discover_valve_keymap(root)
    if valve_discovery.recognized_config and not has_external_override:
        return _with_executed_scan_metadata(
            valve_discovery,
            foundation_discovery,
            numeric_ini_discovery,
            external_player,
            unreal_discovery,
            structured_discovery,
            indexed_discovery,
        )
    if unreal_discovery.recognized_config and not has_external_override:
        return _with_executed_scan_metadata(
            unreal_discovery,
            foundation_discovery,
            numeric_ini_discovery,
            external_player,
            structured_discovery,
            indexed_discovery,
            valve_discovery,
        )
    special_discoveries = (
        structured_discovery,
        valve_discovery,
        unreal_discovery,
        discover_klei_keymap(root),
        discover_unity_addressables_keymap(root),
        discover_unity_binary_input_manager(root),
        discover_dotnet_monogame_keymap(root),
        discover_pixpil_keymap(root),
        indexed_discovery,
    )
    candidates: list[Path] = []
    entry_count = 0
    truncated = False
    for current_root, directory_names, file_names in os.walk(root, followlinks=False):
        _prune_walk_links(current_root, directory_names, file_names)
        directory_names[:] = [
            name
            for name in directory_names
            if name.casefold() not in {"binaries", "paks", "movies", "crashreportclient"}
        ]
        entry_count += len(directory_names) + len(file_names)
        if entry_count > MAX_GAME_DIRECTORY_ENTRIES:
            truncated = True
            break
        for file_name in file_names:
            path = Path(current_root) / file_name
            if not _is_game_config_candidate(path):
                continue
            if _is_link_or_junction_path(path):
                continue
            try:
                if path.stat().st_size > MAX_GAME_CONFIG_FILE_BYTES:
                    continue
            except OSError:
                continue
            candidates.append(path)
            if len(candidates) >= MAX_GAME_CONFIG_FILES:
                truncated = True
                break
        if truncated:
            break
    candidates.sort(
        key=lambda path: (
            0 if path.name.casefold().endswith("_keymap.json") else
            1 if path.name.casefold() in {
                "q3config.cfg", "doomconfig.cfg", "wolfconfig.cfg", "etconfig.cfg"
            } else
            2 if path.suffix.casefold() == ".vcfg" else
            3 if path.suffix.casefold() == ".inputactions" else
            4 if "input" in path.name.casefold() and path.suffix.casefold() == ".ini" else
            5 if path.suffix.casefold() == ".ini" else
            6 if path.name.casefold() == "inputmanager.asset" else
            7 if path.name.casefold() == "kb_act.lst" else
            8 if path.name.casefold() == "config.cfg" else 9,
            str(path).casefold(),
        )
    )
    valve_action_labels: dict[str, str] = {}
    valve_label_sources: list[Path] = []
    for path in candidates:
        if path.name.casefold() != "kb_act.lst":
            continue
        try:
            labels = _parse_valve_action_list(_read_game_config(path))
        except (OSError, ValueError):
            continue
        if labels:
            valve_action_labels.update(labels)
            valve_label_sources.append(path)
    detected: dict[str, dict[str, str]] = {}
    sources: list[Path] = []
    for special_discovery in special_discoveries:
        sources.extend(
            path for path in special_discovery.source_files if path not in sources
        )
        for input_name, mapping in special_discovery.keymap.items():
            detected[input_name] = dict(mapping)
    valve_labels_recorded = False
    for path in candidates:
        if (
            path.name.casefold() in _VALVE_CONFIG_NAMES
            or path.name.casefold() == "kb_act.lst"
            or path in unreal_discovery.source_files
        ):
            continue
        try:
            text = _read_game_config(path)
            parsed: dict[str, dict[str, str]] = {}
            if is_keyvalues_cfg_candidate(path):
                keyvalues = parse_keyvalues_cfg(text, file_name=path.name)
                truncated = truncated or keyvalues.truncated
                if keyvalues.recognized_config:
                    if _is_complete_keyvalues_config(path, keyvalues.format_name):
                        complete_keyvalues = KeymapDiscovery(
                            keyvalues.keymap,
                            (path,),
                            sum(
                                discovery.scanned_files
                                for discovery in special_discoveries
                            )
                            + len(candidates),
                            truncated
                            or any(
                                discovery.truncated
                                for discovery in special_discoveries
                            ),
                            True,
                        )
                        return _with_executed_scan_metadata(
                            complete_keyvalues,
                            foundation_discovery,
                            numeric_ini_discovery,
                            external_player,
                        )
                    parsed = keyvalues.keymap
            if not parsed and path.suffix.casefold() in {".json", ".inputactions"}:
                _validate_pixpil_json_depth(text.encode("utf-8"))
                document = json.loads(text)
                parsed = _parse_native_keymap(document)
                if not parsed:
                    parsed = _parse_unity_input_actions(document)
                if not parsed:
                    parsed = parse_structured_json_keymap(
                        document,
                        source_name=path.name,
                    ).keymap
                if not parsed and _is_bounded_script_keymap_candidate(path):
                    parsed = parse_script_keymap(
                        text,
                        source_name=path.name,
                    ).keymap
            elif not parsed and path.name.casefold() == "inputmanager.asset":
                parsed = _parse_unity_input_manager(text)
            elif not parsed and path.suffix.casefold() == ".ini":
                parsed = _parse_unreal_input_ini(text)
                if not parsed:
                    parsed = _parse_godot_input_map(text)
                if not parsed:
                    parsed = _parse_simple_text_keymap(text)
            elif not parsed and path.suffix.casefold() == ".cfg":
                parsed = _parse_valve_bind_cfg(text, valve_action_labels)
                if not parsed:
                    parsed = _parse_godot_input_map(text)
                if not parsed:
                    parsed = _parse_simple_text_keymap(text)
            elif not parsed and path.name.casefold() == "project.godot":
                parsed = _parse_godot_input_map(text)
            elif not parsed and path.suffix.casefold() in {".xml", ".celeste"}:
                parsed_xml = parse_common_xml_keymap(
                    text,
                    file_name=path.name,
                )
                if (
                    parsed_xml.recognized
                    and path.name.casefold()
                    in {"config_player.xml", "keyprefs.xml", "settings.celeste"}
                ):
                    complete_xml = KeymapDiscovery(
                        parsed_xml.keymap,
                        (path,),
                        sum(
                            discovery.scanned_files
                            for discovery in special_discoveries
                        )
                        + len(candidates),
                        truncated
                        or any(
                            discovery.truncated
                            for discovery in special_discoveries
                        ),
                        True,
                    )
                    return _with_executed_scan_metadata(
                        complete_xml,
                        foundation_discovery,
                        numeric_ini_discovery,
                        external_player,
                    )
                parsed = parsed_xml.keymap
            elif not parsed and path.suffix.casefold() in {".yaml", ".yml"}:
                parsed = _parse_simple_yaml_keymap(text)
            elif not parsed and _is_bounded_script_keymap_candidate(path):
                parsed = parse_script_keymap(text, source_name=path.name).keymap
                if not parsed and path.suffix.casefold() == ".lua":
                    parsed = _parse_simple_text_keymap(text)
        except (OSError, ValueError, RecursionError):
            continue
        if not parsed:
            continue
        if path.suffix.casefold() == ".cfg" and not valve_labels_recorded:
            sources.extend(
                label_source
                for label_source in valve_label_sources
                if label_source not in sources
            )
            valve_labels_recorded = True
        sources.append(path)
        for input_name, mapping in parsed.items():
            _add_detected_mapping(
                detected,
                input_name,
                mapping["action"],
                part=mapping.get("movement_direction", ""),
            )
            if mapping.get("movement_direction") and input_name in detected:
                detected[input_name]["movement_direction"] = mapping["movement_direction"]
    if external_player.keymap or external_player.overridden_actions:
        _remove_overridden_actions(
            detected,
            external_player.overridden_actions,
        )
        _merge_keymap_records(detected, external_player.keymap)
        sources.extend(
            path for path in external_player.source_files if path not in sources
        )

    manual_discovery = KeymapDiscovery({}, (), 0, False)
    if not detected and not any(
        discovery.recognized_config for discovery in special_discoveries
    ):
        manual_discovery = discover_manual_keymap(root)
        sources.extend(
            path for path in manual_discovery.source_files if path not in sources
        )
        detected.update(
            {
                input_name: dict(mapping)
                for input_name, mapping in manual_discovery.keymap.items()
            }
        )
    all_discoveries = (
        *special_discoveries,
        external_player,
        manual_discovery,
        foundation_discovery,
        numeric_ini_discovery,
    )
    other_recognized = any(
        discovery.recognized_config
        for discovery in (*special_discoveries, external_player, manual_discovery)
    )
    keep_foundation_guidance = bool(
        foundation_discovery.requires_game_launch
        and not detected
        and not other_recognized
    )
    keep_unreal_player_guidance = bool(
        unreal_discovery.notice
        and not detected
        and not other_recognized
    )
    missing_player_keymap = not detected and not other_recognized
    generic_launch_notice = (
        "未找到可解析的玩家实际键位配置（注册表、配置文件或云存档）。"
        "游戏可能尚未首次启动，或尚未在正常退出时写入键位配置。"
        "请先启动游戏、进入按键设置，实际修改并应用至少一个键位后正常退出，"
        "然后重新选择游戏目录。"
    )
    aggregated_source_records: list[KeymapSource] = []
    for discovery in all_discoveries:
        for record in discovery.source_records:
            if record not in aggregated_source_records:
                aggregated_source_records.append(record)
    return KeymapDiscovery(
        detected,
        tuple(sources),
        sum(discovery.scanned_files for discovery in all_discoveries) + len(candidates),
        truncated or any(discovery.truncated for discovery in all_discoveries),
        any(discovery.recognized_config for discovery in all_discoveries),
        notice=(
            unreal_discovery.notice
            if keep_unreal_player_guidance
            else foundation_discovery.notice
            if keep_foundation_guidance
            else generic_launch_notice
            if missing_player_keymap
            else ""
        ),
        requires_game_launch=(
            keep_foundation_guidance
            or (missing_player_keymap and not keep_unreal_player_guidance)
        ),
        has_recognized_player_file=(
            unreal_discovery.has_recognized_player_file
        ),
        source_records=tuple(aggregated_source_records),
    )


def _apply_keymap_discovery(
    current: dict[str, dict[str, str]],
    discovery: KeymapDiscovery,
) -> dict[str, dict[str, str]]:
    """Apply complete player configs by replacement and partial scans by merge."""
    apply_mode = discovery.apply_mode
    if apply_mode == KEYMAP_APPLY_INFER:
        apply_mode = (
            KEYMAP_APPLY_REPLACE
            if discovery.recognized_config
            else KEYMAP_APPLY_MERGE
        )
    if apply_mode == KEYMAP_APPLY_NONE:
        return {key: dict(value) for key, value in current.items()}
    if apply_mode not in {KEYMAP_APPLY_REPLACE, KEYMAP_APPLY_MERGE}:
        raise ValueError(f"未知键位应用模式：{apply_mode}")
    applied = (
        {}
        if apply_mode == KEYMAP_APPLY_REPLACE
        else {key: dict(value) for key, value in current.items()}
    )
    applied.update(
        {key: dict(value) for key, value in discovery.keymap.items()}
    )
    return applied


def _keymap_requires_player_config_confirmation(
    game_directory: Path,
    discovery: KeymapDiscovery,
) -> bool:
    """Return true when only install/default sources support a detected config."""
    if (
        discovery.has_verified_player_config
        or discovery.binding_authority == KEYMAP_AUTHORITY_VERIFIED_PLAYER
    ):
        return False
    if discovery.binding_authority in {
        KEYMAP_AUTHORITY_DEFAULT_ONLY,
        KEYMAP_AUTHORITY_MIXED_UNVERIFIED,
    }:
        return bool(discovery.keymap or discovery.recognized_config)
    if discovery.has_recognized_player_file:
        return bool(discovery.keymap)
    if not discovery.keymap and not discovery.recognized_config:
        return False
    try:
        game_root = game_directory.resolve()
    except (OSError, RuntimeError):
        return True
    for source in discovery.source_files:
        if not source.is_absolute():
            continue
        try:
            resolved_source = source.resolve()
            resolved_source.relative_to(game_root)
        except ValueError:
            # Player files discovered in AppData, Documents or Steam Cloud are
            # outside the install tree and therefore represent persisted state.
            return False
        except (OSError, RuntimeError):
            continue
    return True


def _keymap_source_display_labels(
    game_directory: Path,
    discovery: KeymapDiscovery,
) -> list[str]:
    """Render physical and optional archive-virtual provenance without fake Paths."""

    labels: list[str] = []
    if discovery.source_records:
        records = discovery.source_records
    else:
        records = tuple(
            KeymapSource(path, "legacy") for path in discovery.source_files
        )
    for record in records[:5]:
        path = record.physical_path
        try:
            physical = str(path.relative_to(game_directory))
        except (ValueError, OSError, RuntimeError):
            physical = path.name or str(path)
        virtual = record.virtual_path.strip().replace("\\", "/")
        label = f"{physical}!{virtual}" if virtual else physical
        if label not in labels:
            labels.append(label)
    if len(records) > 5:
        labels.append(f"……另有 {len(records) - 5} 个来源")
    return labels


@dataclass(frozen=True)
class CaptureTarget:
    label: str
    monitor_index: int
    left: int
    top: int
    width: int
    height: int
    hwnd: int | None = None
    process_id: int | None = None


def mouse_acceleration_disabled() -> bool:
    """Return True only when Windows pointer acceleration is disabled."""
    if sys.platform != "win32":
        return True
    parameters = (ctypes.c_int * 3)()
    spi_getmouse = 0x0003
    if not ctypes.windll.user32.SystemParametersInfoW(
        spi_getmouse,
        0,
        ctypes.byref(parameters),
        0,
    ):
        raise ctypes.WinError()
    return parameters[2] == 0


def window_client_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Return a visible top-level window's client rectangle in screen coordinates."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    window = wintypes.HWND(hwnd)
    if (
        not user32.IsWindow(window)
        or not user32.IsWindowVisible(window)
        or user32.IsIconic(window)
    ):
        return None
    rect = wintypes.RECT()
    origin = wintypes.POINT(0, 0)
    if not user32.GetClientRect(window, ctypes.byref(rect)):
        return None
    if not user32.ClientToScreen(window, ctypes.byref(origin)):
        return None
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width <= 0 or height <= 0:
        return None
    return int(origin.x), int(origin.y), width, height


def window_is_foreground(hwnd: int) -> bool:
    if sys.platform != "win32":
        return True
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    return int(user32.GetForegroundWindow() or 0) == hwnd


def window_process_id(hwnd: int) -> int | None:
    if sys.platform != "win32":
        return None
    process_id = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(
        wintypes.HWND(hwnd),
        ctypes.byref(process_id),
    )
    return int(process_id.value) or None


def foreground_window() -> int | None:
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    hwnd = int(user32.GetForegroundWindow() or 0)
    return hwnd or None


def native_top_level_window_handle(hwnd: int) -> int:
    """Return the native top-level HWND for a Tk or child window handle."""
    if sys.platform != "win32":
        return hwnd
    try:
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        root = user32.GetAncestor(wintypes.HWND(hwnd), 2)  # GA_ROOT
        return int(root or hwnd)
    except (AttributeError, OSError, ValueError, ctypes.ArgumentError):
        return hwnd


def rectangle_dimension_coverage(
    inner: tuple[int, int, int, int],
    outer: CaptureTarget,
) -> tuple[float, float]:
    """Return intersection width/height ratios against the capture target."""
    left, top, width, height = inner
    intersection_width = max(
        0,
        min(left + width, outer.left + outer.width) - max(left, outer.left),
    )
    intersection_height = max(
        0,
        min(top + height, outer.top + outer.height) - max(top, outer.top),
    )
    return (
        intersection_width / outer.width,
        intersection_height / outer.height,
    )


def window_meets_capture_dimensions(
    hwnd: int | None,
    target: CaptureTarget,
    *,
    expected_process_id: int | None = None,
    excluded_process_id: int | None = None,
) -> bool:
    """Check the foreground window used as the clean-game-area proxy."""
    if hwnd is None:
        return False
    process_id = window_process_id(hwnd)
    if process_id is None:
        return False
    if expected_process_id is not None and process_id != expected_process_id:
        return False
    if excluded_process_id is not None and process_id == excluded_process_id:
        return False
    rect = window_client_rect(hwnd)
    if rect is None:
        return False
    width_ratio, height_ratio = rectangle_dimension_coverage(rect, target)
    return (
        width_ratio >= MIN_CLEAN_DIMENSION_RATIO
        and height_ratio >= MIN_CLEAN_DIMENSION_RATIO
    )


def enumerate_recordable_windows(
) -> list[tuple[int, int, str, tuple[int, int, int, int]]]:
    """Enumerate visible, titled windows with a non-empty client area."""
    if sys.platform != "win32":
        return []
    user32 = ctypes.windll.user32
    windows: list[tuple[int, int, str, tuple[int, int, int, int]]] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd: int, _lparam: int) -> bool:
        window = wintypes.HWND(hwnd)
        length = user32.GetWindowTextLengthW(window)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(window, buffer, len(buffer))
        title = buffer.value.strip()
        rect = window_client_rect(int(hwnd))
        process_id = window_process_id(int(hwnd))
        if title and rect is not None and process_id is not None:
            windows.append((int(hwnd), process_id, title, rect))
        return True

    callback_ref = callback_type(callback)
    if not user32.EnumWindows(callback_ref, 0):
        raise ctypes.WinError()
    windows.sort(key=lambda item: item[2].casefold())
    return windows


class RawInputDevice(ctypes.Structure):
    _fields_ = [
        ("usage_page", wintypes.USHORT),
        ("usage", wintypes.USHORT),
        ("flags", wintypes.DWORD),
        ("target", wintypes.HWND),
    ]


class RawInputHeader(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("size", wintypes.DWORD),
        ("device", wintypes.HANDLE),
        ("wparam", wintypes.WPARAM),
    ]


class RawMouseButtons(ctypes.Structure):
    _fields_ = [
        ("flags", wintypes.USHORT),
        ("data", wintypes.USHORT),
    ]


class RawMouseButtonUnion(ctypes.Union):
    _fields_ = [
        ("buttons", wintypes.ULONG),
        ("detail", RawMouseButtons),
    ]


class RawMouse(ctypes.Structure):
    _anonymous_ = ("button_union",)
    _fields_ = [
        ("flags", wintypes.USHORT),
        ("button_union", RawMouseButtonUnion),
        ("raw_buttons", wintypes.ULONG),
        ("last_x", wintypes.LONG),
        ("last_y", wintypes.LONG),
        ("extra_information", wintypes.ULONG),
    ]


class RawKeyboard(ctypes.Structure):
    _fields_ = [
        ("make_code", wintypes.USHORT),
        ("flags", wintypes.USHORT),
        ("reserved", wintypes.USHORT),
        ("vkey", wintypes.USHORT),
        ("message", wintypes.UINT),
        ("extra_information", wintypes.ULONG),
    ]


class RawInputData(ctypes.Union):
    _fields_ = [
        ("mouse", RawMouse),
        ("keyboard", RawKeyboard),
    ]


class RawInput(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [
        ("header", RawInputHeader),
        ("data", RawInputData),
    ]


class XInputGamepad(ctypes.Structure):
    _fields_ = [
        ("buttons", wintypes.WORD),
        ("left_trigger", ctypes.c_ubyte),
        ("right_trigger", ctypes.c_ubyte),
        ("thumb_lx", ctypes.c_short),
        ("thumb_ly", ctypes.c_short),
        ("thumb_rx", ctypes.c_short),
        ("thumb_ry", ctypes.c_short),
    ]


class XInputState(ctypes.Structure):
    _fields_ = [
        ("packet_number", wintypes.DWORD),
        ("gamepad", XInputGamepad),
    ]


WindowProcedure = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WindowClass(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("window_procedure", WindowProcedure),
        ("class_extra", ctypes.c_int),
        ("window_extra", ctypes.c_int),
        ("instance", wintypes.HINSTANCE),
        ("icon", wintypes.HICON),
        ("cursor", wintypes.HANDLE),
        ("background", wintypes.HBRUSH),
        ("menu_name", wintypes.LPCWSTR),
        ("class_name", wintypes.LPCWSTR),
    ]


class InputEventTracker:
    """Collect Windows Raw Input and build the required annotation schema."""

    WM_INPUT = 0x00FF
    WM_CLOSE = 0x0010
    WM_DESTROY = 0x0002
    RID_INPUT = 0x10000003
    RIM_TYPEMOUSE = 0
    RIM_TYPEKEYBOARD = 1
    RIDEV_INPUTSINK = 0x00000100
    RI_KEY_BREAK = 0x0001
    RI_KEY_E0 = 0x0002
    MOUSE_MOVE_ABSOLUTE = 0x0001
    MOUSE_LEFT_DOWN = 0x0001
    MOUSE_LEFT_UP = 0x0002
    MOUSE_RIGHT_DOWN = 0x0004
    MOUSE_RIGHT_UP = 0x0008
    MOUSE_MIDDLE_DOWN = 0x0010
    MOUSE_MIDDLE_UP = 0x0020
    MOUSE_BUTTON_4_DOWN = 0x0040
    MOUSE_BUTTON_4_UP = 0x0080
    MOUSE_BUTTON_5_DOWN = 0x0100
    MOUSE_BUTTON_5_UP = 0x0200
    MOUSE_WHEEL = 0x0400
    MOUSE_HWHEEL = 0x0800
    XINPUT_GAMEPAD_LEFT_THUMB_DEADZONE = 7849
    XINPUT_GAMEPAD_RIGHT_THUMB_DEADZONE = 8689
    XINPUT_SUCCESS = 0
    GAMEPAD_BUTTONS = {
        0x0001: "gamepadDpadUp",
        0x0002: "gamepadDpadDown",
        0x0004: "gamepadDpadLeft",
        0x0008: "gamepadDpadRight",
        0x0010: "gamepadStart",
        0x0020: "gamepadBack",
        0x0040: "gamepadLeftThumb",
        0x0080: "gamepadRightThumb",
        0x0100: "gamepadLeftShoulder",
        0x0200: "gamepadRightShoulder",
        0x1000: "gamepadA",
        0x2000: "gamepadB",
        0x4000: "gamepadX",
        0x8000: "gamepadY",
    }

    SPECIAL_KEYS = {
        0x08: "Backspace",
        0x09: "Tab",
        0x0D: "Enter",
        0x10: "Shift",
        0x11: "Ctrl",
        0x12: "Alt",
        0x13: "Pause",
        0x14: "CapsLock",
        0x1B: "Esc",
        0x20: "Space",
        0x21: "PageUp",
        0x22: "PageDown",
        0x23: "End",
        0x24: "Home",
        0x25: "Left",
        0x26: "Up",
        0x27: "Right",
        0x28: "Down",
        0x2C: "PrintScreen",
        0x2D: "Insert",
        0x2E: "Delete",
        0x5B: "Win",
        0x5C: "Win",
        0x5D: "Apps",
        0x60: "NumPad0",
        0x61: "NumPad1",
        0x62: "NumPad2",
        0x63: "NumPad3",
        0x64: "NumPad4",
        0x65: "NumPad5",
        0x66: "NumPad6",
        0x67: "NumPad7",
        0x68: "NumPad8",
        0x69: "NumPad9",
        0x6A: "NumPadMultiply",
        0x6B: "NumPadAdd",
        0x6D: "NumPadSubtract",
        0x6E: "NumPadDecimal",
        0x6F: "NumPadDivide",
        0x90: "NumLock",
        0x91: "ScrollLock",
        0xA0: "Shift",
        0xA1: "Shift",
        0xA2: "Ctrl",
        0xA3: "Ctrl",
        0xA4: "Alt",
        0xA5: "Alt",
        0xBA: ";",
        0xBB: "=",
        0xBC: ",",
        0xBD: "-",
        0xBE: ".",
        0xBF: "/",
        0xC0: "Tilde",
        0xDB: "[",
        0xDC: "\\",
        0xDD: "]",
        0xDE: "'",
    }

    def __init__(
        self,
        session: SessionConfig,
        session_id: str,
        recording_started_at: datetime,
        segment_seconds: int,
        screen_left: int,
        screen_top: int,
        screen_width: int,
        screen_height: int,
        recording_started_perf: float,
        capture_backend: str,
        capture_mode: str,
        encoder_name: str = "unknown",
        excluded_window: int | None = None,
    ) -> None:
        self.session = session
        self.session_id = session_id
        self.recording_started_at = recording_started_at
        self.segment_seconds = segment_seconds
        self.screen_left = screen_left
        self.screen_top = screen_top
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.recording_started_perf = recording_started_perf
        self.capture_backend = capture_backend
        self.capture_mode = capture_mode
        self.encoder_name = encoder_name
        self.excluded_window = excluded_window
        self.lock = threading.Lock()
        self.video_time = 0.0
        self.frame_capture_times: list[float] = []
        self.mouse_motion_by_frame: dict[int, dict[str, object]] = {}
        self.keyboard_events: list[dict[str, object]] = []
        self.mouse_button_events: list[dict[str, object]] = []
        self.mouse_scroll_events: list[dict[str, object]] = []
        self.gamepad_frames: dict[int, dict[str, object]] = {}
        self.gamepad_button_events: list[dict[str, object]] = []
        self._last_gamepad_buttons: dict[int, int] = {}
        self._last_gamepad_triggers: dict[int, tuple[bool, bool]] = {}
        self.xinput_get_state = self._load_xinput()
        self.ready = threading.Event()
        self.thread: threading.Thread | None = None
        self.thread_id = 0
        self.window_handle: int | None = None
        self.startup_error: Exception | None = None
        self.window_procedure: WindowProcedure | None = None
        self.class_name = f"ScreenRecorderRawInput_{id(self):x}"

    @staticmethod
    def _load_xinput() -> object | None:
        if sys.platform != "win32":
            return None
        for library_name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
            try:
                library = ctypes.WinDLL(library_name)
                function = library.XInputGetState
                function.argtypes = [wintypes.DWORD, ctypes.POINTER(XInputState)]
                function.restype = wintypes.DWORD
                return function
            except (AttributeError, OSError):
                continue
        return None

    def start(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Raw Input 采集仅支持 Windows")
        self._configure_winapi()
        self.thread = threading.Thread(
            target=self._message_loop,
            name="raw-input-listener",
            daemon=True,
        )
        self.thread.start()
        if not self.ready.wait(timeout=5):
            raise RuntimeError("Raw Input 监听器启动超时")
        if self.startup_error is not None:
            raise RuntimeError(f"Raw Input 启动失败：{self.startup_error}")

    @staticmethod
    def _configure_winapi() -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.RegisterRawInputDevices.argtypes = [
            ctypes.POINTER(RawInputDevice),
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.RegisterRawInputDevices.restype = wintypes.BOOL
        user32.GetRawInputData.argtypes = [
            wintypes.HANDLE,
            wintypes.UINT,
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.UINT),
            wintypes.UINT,
        ]
        user32.GetRawInputData.restype = wintypes.UINT
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.UnregisterClassW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.HINSTANCE,
        ]
        user32.UnregisterClassW.restype = wintypes.BOOL
        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.PostThreadMessageW.argtypes = [
            wintypes.DWORD,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostThreadMessageW.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.GetMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = ctypes.c_ssize_t
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetKeyNameTextW.argtypes = [
            wintypes.LONG,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        user32.GetKeyNameTextW.restype = ctypes.c_int

    def update_video_frame(self, frame_index: int, captured_at: float) -> None:
        with self.lock:
            self.video_time = max(0.0, frame_index / FPS)
            self.frame_capture_times.append(captured_at)
        self._poll_gamepads(frame_index, frame_index / FPS)

    @staticmethod
    def _normalized_axis(value: int, deadzone: int) -> float:
        if abs(value) <= deadzone:
            return 0.0
        magnitude = (abs(value) - deadzone) / (32767 - deadzone)
        return round(max(-1.0, min(1.0, magnitude if value > 0 else -magnitude)), 6)

    def _poll_gamepads(self, frame_index: int, global_time: float) -> None:
        get_state = self.xinput_get_state
        if get_state is None:
            return
        for controller_index in range(4):
            state = XInputState()
            if get_state(controller_index, ctypes.byref(state)) != self.XINPUT_SUCCESS:
                self._last_gamepad_buttons.pop(controller_index, None)
                self._last_gamepad_triggers.pop(controller_index, None)
                continue
            gamepad = state.gamepad
            buttons = int(gamepad.buttons)
            left_x = self._normalized_axis(
                int(gamepad.thumb_lx),
                self.XINPUT_GAMEPAD_LEFT_THUMB_DEADZONE,
            )
            left_y = self._normalized_axis(
                int(gamepad.thumb_ly),
                self.XINPUT_GAMEPAD_LEFT_THUMB_DEADZONE,
            )
            right_x = self._normalized_axis(
                int(gamepad.thumb_rx),
                self.XINPUT_GAMEPAD_RIGHT_THUMB_DEADZONE,
            )
            right_y = self._normalized_axis(
                int(gamepad.thumb_ry),
                self.XINPUT_GAMEPAD_RIGHT_THUMB_DEADZONE,
            )
            frame_state = {
                "controller": controller_index,
                "left_x": left_x,
                "left_y": left_y,
                "right_x": right_x,
                "right_y": right_y,
                "left_trigger": round(int(gamepad.left_trigger) / 255.0, 6),
                "right_trigger": round(int(gamepad.right_trigger) / 255.0, 6),
                "buttons": buttons,
            }
            with self.lock:
                self.gamepad_frames[frame_index] = frame_state

            previous = self._last_gamepad_buttons.get(controller_index, 0)
            changed = previous ^ buttons
            for mask, name in self.GAMEPAD_BUTTONS.items():
                if not (changed & mask):
                    continue
                with self.lock:
                    self.gamepad_button_events.append(
                        {
                            "global_time": global_time,
                            "controller": controller_index,
                            "button": name,
                            "pressed": bool(buttons & mask),
                        }
                    )
            self._last_gamepad_buttons[controller_index] = buttons
            trigger_states = (
                int(gamepad.left_trigger) >= GAMEPAD_TRIGGER_THRESHOLD,
                int(gamepad.right_trigger) >= GAMEPAD_TRIGGER_THRESHOLD,
            )
            previous_triggers = self._last_gamepad_triggers.get(
                controller_index,
                (False, False),
            )
            for name, pressed, was_pressed in (
                (
                    "gamepadLeftTrigger",
                    trigger_states[0],
                    previous_triggers[0],
                ),
                (
                    "gamepadRightTrigger",
                    trigger_states[1],
                    previous_triggers[1],
                ),
            ):
                if pressed == was_pressed:
                    continue
                with self.lock:
                    self.gamepad_button_events.append(
                        {
                            "global_time": global_time,
                            "controller": controller_index,
                            "button": name,
                            "pressed": pressed,
                            "analog_value": (
                                round(int(gamepad.left_trigger) / 255.0, 6)
                                if name == "gamepadLeftTrigger"
                                else round(int(gamepad.right_trigger) / 255.0, 6)
                            ),
                        }
                    )
            self._last_gamepad_triggers[controller_index] = trigger_states

    def stop(self) -> None:
        thread = self.thread
        if thread is None:
            return
        if self.window_handle:
            ctypes.windll.user32.PostMessageW(
                wintypes.HWND(self.window_handle),
                self.WM_CLOSE,
                0,
                0,
            )
        elif self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(
                self.thread_id,
                0x0012,
                0,
                0,
            )
        thread.join(timeout=3)
        self.thread = None
        self.window_handle = None

    def _message_loop(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        instance = kernel32.GetModuleHandleW(None)
        self.thread_id = kernel32.GetCurrentThreadId()
        self.window_procedure = WindowProcedure(self._window_callback)
        window_class = WindowClass()
        window_class.window_procedure = self.window_procedure
        window_class.instance = instance
        window_class.class_name = self.class_name
        atom = 0
        hwnd = None
        try:
            atom = user32.RegisterClassW(ctypes.byref(window_class))
            if not atom:
                raise ctypes.WinError()
            hwnd = user32.CreateWindowExW(
                0,
                self.class_name,
                self.class_name,
                0,
                0,
                0,
                0,
                0,
                None,
                None,
                instance,
                None,
            )
            if not hwnd:
                raise ctypes.WinError()
            self.window_handle = int(hwnd)
            devices = (RawInputDevice * 2)(
                RawInputDevice(0x01, 0x02, self.RIDEV_INPUTSINK, hwnd),
                RawInputDevice(0x01, 0x06, self.RIDEV_INPUTSINK, hwnd),
            )
            if not user32.RegisterRawInputDevices(
                devices,
                len(devices),
                ctypes.sizeof(RawInputDevice),
            ):
                raise ctypes.WinError()
            self.ready.set()
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            self.startup_error = exc
            self.ready.set()
        finally:
            if hwnd and user32.IsWindow(hwnd):
                user32.DestroyWindow(hwnd)
            if atom:
                user32.UnregisterClassW(self.class_name, instance)

    def _window_callback(
        self,
        hwnd: int,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        user32 = ctypes.windll.user32
        if message == self.WM_INPUT:
            self._handle_raw_input(lparam)
            return 0
        if message == self.WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if message == self.WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _handle_raw_input(self, raw_handle: int) -> None:
        user32 = ctypes.windll.user32
        size = wintypes.UINT(0)
        header_size = ctypes.sizeof(RawInputHeader)
        if (
            user32.GetRawInputData(
                raw_handle,
                self.RID_INPUT,
                None,
                ctypes.byref(size),
                header_size,
            )
            == 0xFFFFFFFF
            or size.value == 0
        ):
            return
        buffer = ctypes.create_string_buffer(size.value)
        if (
            user32.GetRawInputData(
                raw_handle,
                self.RID_INPUT,
                buffer,
                ctypes.byref(size),
                header_size,
            )
            == 0xFFFFFFFF
        ):
            return
        raw = ctypes.cast(buffer, ctypes.POINTER(RawInput)).contents
        if raw.header.type == self.RIM_TYPEMOUSE:
            self._record_mouse(raw.mouse)
        elif raw.header.type == self.RIM_TYPEKEYBOARD:
            self._record_keyboard(raw.keyboard)

    def _current_time_and_frame(self) -> tuple[float, int]:
        global_time = max(0.0, time.perf_counter() - self.recording_started_perf)
        return global_time, max(0, round(global_time * FPS))

    def _cursor_position(self) -> tuple[int, int]:
        point = wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return int(point.x), int(point.y)
        return 0, 0

    def _record_mouse(self, event: RawMouse) -> None:
        global_time, frame_index = self._current_time_and_frame()
        x, y = self._cursor_position()
        if self._is_control_window_point(x, y):
            return
        is_absolute = bool(event.flags & self.MOUSE_MOVE_ABSOLUTE)
        dx = 0 if is_absolute else int(event.last_x)
        dy = 0 if is_absolute else int(event.last_y)
        if dx or dy or is_absolute:
            with self.lock:
                existing = self.mouse_motion_by_frame.get(frame_index)
                if existing is None:
                    self.mouse_motion_by_frame[frame_index] = {
                        "global_time": global_time,
                        "dx": dx,
                        "dy": dy,
                        "x": x,
                        "y": y,
                        "absolute_device": is_absolute,
                    }
                else:
                    existing["dx"] = int(existing["dx"]) + dx
                    existing["dy"] = int(existing["dy"]) + dy
                    existing["x"] = x
                    existing["y"] = y

        button_flags = int(event.detail.flags)
        button_data = int(event.detail.data)
        button_changes = (
            (self.MOUSE_LEFT_DOWN, "leftClick", True),
            (self.MOUSE_LEFT_UP, "leftClick", False),
            (self.MOUSE_RIGHT_DOWN, "rightClick", True),
            (self.MOUSE_RIGHT_UP, "rightClick", False),
            (self.MOUSE_MIDDLE_DOWN, "middleClick", True),
            (self.MOUSE_MIDDLE_UP, "middleClick", False),
            (self.MOUSE_BUTTON_4_DOWN, "mouseButton4", True),
            (self.MOUSE_BUTTON_4_UP, "mouseButton4", False),
            (self.MOUSE_BUTTON_5_DOWN, "mouseButton5", True),
            (self.MOUSE_BUTTON_5_UP, "mouseButton5", False),
        )
        for flag, button, pressed in button_changes:
            if button_flags & flag:
                with self.lock:
                    self.mouse_button_events.append(
                        {
                            "global_time": global_time,
                            "button": button,
                            "pressed": pressed,
                            "x": x,
                            "y": y,
                        }
                    )
        if button_flags & self.MOUSE_WHEEL:
            wheel_delta = ctypes.c_short(button_data).value
            with self.lock:
                self.mouse_scroll_events.append(
                    {
                        "global_time": global_time,
                        "dx": 0,
                        "dy": wheel_delta,
                        "x": x,
                        "y": y,
                    }
                )
        if button_flags & self.MOUSE_HWHEEL:
            wheel_delta = ctypes.c_short(button_data).value
            with self.lock:
                self.mouse_scroll_events.append(
                    {
                        "global_time": global_time,
                        "dx": wheel_delta,
                        "dy": 0,
                        "x": x,
                        "y": y,
                    }
                )

    def _is_control_window_point(self, x: int, y: int) -> bool:
        excluded = self.excluded_window
        if not excluded or sys.platform != "win32":
            return False
        user32 = ctypes.windll.user32
        window = user32.WindowFromPoint(wintypes.POINT(x, y))
        if not window:
            return False
        root_window = user32.GetAncestor(window, 2)  # GA_ROOT
        excluded_root = user32.GetAncestor(
            wintypes.HWND(excluded),
            2,
        )
        return int(root_window or window) == int(excluded_root or excluded)

    def _record_keyboard(self, event: RawKeyboard) -> None:
        if event.vkey == 0xFF:
            return
        global_time, _frame_index = self._current_time_and_frame()
        key_name = self._key_name(
            int(event.vkey),
            int(event.make_code),
            int(event.flags),
        )
        with self.lock:
            self.keyboard_events.append(
                {
                    "global_time": global_time,
                    "key": key_name,
                    "pressed": not bool(event.flags & self.RI_KEY_BREAK),
                    "scan_code": int(event.make_code),
                    "extended": bool(event.flags & self.RI_KEY_E0),
                }
            )

    @classmethod
    def _key_name(cls, vkey: int, scan_code: int, flags: int) -> str:
        if 0x30 <= vkey <= 0x39 or 0x41 <= vkey <= 0x5A:
            return chr(vkey)
        if 0x70 <= vkey <= 0x87:
            return f"F{vkey - 0x6F}"
        if vkey == 0x0D and flags & cls.RI_KEY_E0:
            return "NumPadEnter"
        if vkey in cls.SPECIAL_KEYS:
            return cls.SPECIAL_KEYS[vkey]
        name_buffer = ctypes.create_unicode_buffer(64)
        key_lparam = scan_code << 16
        if flags & cls.RI_KEY_E0:
            key_lparam |= 1 << 24
        if ctypes.windll.user32.GetKeyNameTextW(
            key_lparam,
            name_buffer,
            len(name_buffer),
        ):
            return name_buffer.value.replace(" ", "")
        return f"VK_{vkey:02X}"

    _CHORD_MODIFIERS = ("Ctrl", "Alt", "Shift", "Win")

    @classmethod
    def _keyboard_mapping_key(
        cls,
        key: str,
        held_keys: set[str],
        keymap: dict[str, dict[str, str]],
    ) -> str | None:
        """Resolve the most specific configured modifier chord for a key press."""
        if key in cls._CHORD_MODIFIERS:
            return key if key in keymap else None
        active_modifiers = [
            modifier for modifier in cls._CHORD_MODIFIERS if modifier in held_keys
        ]
        if active_modifiers:
            active_set = set(active_modifiers)
            candidates: list[tuple[int, str]] = []
            for mapping_key in keymap:
                parts = mapping_key.split("+")
                if len(parts) < 2 or parts[-1] != key:
                    continue
                required = parts[:-1]
                if (
                    all(part in cls._CHORD_MODIFIERS for part in required)
                    and set(required).issubset(active_set)
                ):
                    candidates.append((len(required), mapping_key))
            if candidates:
                best_size = max(size for size, _mapping_key in candidates)
                best = [
                    mapping_key
                    for size, mapping_key in candidates
                    if size == best_size
                ]
                if len(best) == 1:
                    return best[0]
                # Two equally specific modifier bindings are ambiguous.  Do not
                # invent an action when the game may resolve them differently.
                return None
            # Extra held modifiers must not suppress a configured plain binding;
            # this is common while Shift-sprinting and pressing movement keys.
            return key if key in keymap else None
        return key if key in keymap else None

    @classmethod
    def _pointing_mapping_key(
        cls,
        pointing_input: str,
        held_modifiers: set[str],
        keymap: dict[str, dict[str, str]],
    ) -> str | None:
        """Resolve a mouse button/wheel binding at the input event's time."""
        active_modifiers = [
            modifier
            for modifier in cls._CHORD_MODIFIERS
            if modifier in held_modifiers
        ]
        if active_modifiers:
            active_set = set(active_modifiers)
            candidates: list[tuple[int, str]] = []
            for mapping_key in keymap:
                parts = mapping_key.split("+")
                if len(parts) < 2 or parts[-1] != pointing_input:
                    continue
                required = parts[:-1]
                if (
                    all(part in cls._CHORD_MODIFIERS for part in required)
                    and set(required).issubset(active_set)
                ):
                    candidates.append((len(required), mapping_key))
            if candidates:
                best_size = max(size for size, _mapping_key in candidates)
                best = [
                    mapping_key
                    for size, mapping_key in candidates
                    if size == best_size
                ]
                if len(best) == 1:
                    return best[0]
        # A modifier that has no matching mouse chord must not suppress an
        # existing plain mouse binding.  This preserves the recorder's prior
        # behaviour for games where, for example, Shift and attack overlap.
        return pointing_input if pointing_input in keymap else None

    def _configured_physical_inputs(self) -> set[str]:
        configured: set[str] = set()
        for input_name in self.session.keymap:
            configured.update(
                part for part in input_name.split("+") if part
            )
        return configured

    def _movement_direction(self, keys: set[str]) -> str:
        directions = {
            str(self.session.keymap[mapping_key].get("movement_direction", ""))
            for key in keys
            if (
                mapping_key := self._keyboard_mapping_key(
                    key,
                    keys,
                    self.session.keymap,
                )
            )
        }
        vertical = ""
        horizontal = ""
        if "W" in directions and "B" not in directions:
            vertical = "W"
        elif "B" in directions and "W" not in directions:
            vertical = "B"
        if "L" in directions and "R" not in directions:
            horizontal = "L"
        elif "R" in directions and "L" not in directions:
            horizontal = "R"
        return vertical + horizontal

    def _build_global_movement_events(
        self,
        total_duration: float,
    ) -> list[dict[str, object]]:
        """Merge keyboard and left-stick movement into document direction intervals."""
        held: set[str] = set()
        current_direction = ""
        interval_start = 0.0
        intervals: list[dict[str, object]] = []
        # Chord movement bindings can only be resolved from the complete held
        # keyboard state, so retain all keyboard transitions here.
        movement_events = list(self.keyboard_events)
        movement_events.extend(
            {
                "global_time": event["global_time"],
                "key": event["button"],
                "pressed": event["pressed"],
            }
            for event in self.mouse_button_events
            if self.session.keymap.get(str(event["button"]), {}).get(
                "movement_direction"
            )
        )
        movement_events.sort(key=lambda event: float(event["global_time"]))
        event_index = 0
        total_frames = round(total_duration * FPS)
        for frame_index in range(total_frames + 1):
            event_time = frame_index / FPS
            while (
                event_index < len(movement_events)
                and float(movement_events[event_index]["global_time"]) <= event_time
            ):
                event = movement_events[event_index]
                key = str(event["key"])
                if bool(event["pressed"]):
                    held.add(key)
                else:
                    held.discard(key)
                event_index += 1
            keyboard_direction = self._movement_direction(held)
            gamepad = self.gamepad_frames.get(frame_index)
            gamepad_direction = self._gamepad_movement_direction(gamepad)
            new_direction = keyboard_direction or gamepad_direction
            if new_direction == current_direction:
                continue
            if current_direction and event_time > interval_start:
                intervals.append(
                    {
                        "direction": current_direction,
                        "start_time": interval_start,
                        "end_time": event_time,
                    }
                )
            current_direction = new_direction
            interval_start = event_time
        if current_direction and total_duration > interval_start:
            intervals.append(
                {
                    "direction": current_direction,
                    "start_time": interval_start,
                    "end_time": total_duration,
                }
            )
        return intervals

    @staticmethod
    def _gamepad_movement_direction(
        gamepad: dict[str, object] | None,
    ) -> str:
        if not gamepad:
            return ""
        x = float(gamepad["left_x"])
        y = float(gamepad["left_y"])
        vertical = "W" if y > 0 else "B" if y < 0 else ""
        horizontal = "R" if x > 0 else "L" if x < 0 else ""
        return vertical + horizontal

    @staticmethod
    def _looking_direction(dx: int, dy: int) -> str:
        vertical = "U" if dy < 0 else "D" if dy > 0 else ""
        horizontal = "L" if dx < 0 else "R" if dx > 0 else ""
        return vertical + horizontal or "Neutral"

    def _movement_for_segment(
        self,
        intervals: list[dict[str, object]],
        segment_index: int,
    ) -> list[dict[str, object]]:
        segment_start = segment_index * self.segment_seconds
        segment_end = segment_start + self.segment_seconds
        result: list[dict[str, object]] = []
        for interval in intervals:
            start = max(float(interval["start_time"]), segment_start)
            end = min(float(interval["end_time"]), segment_end)
            if end <= start:
                continue
            chunk_start = start
            while chunk_start < end:
                chunk_end = min(end, chunk_start + 30.0)
                result.append(
                    {
                        "direction": interval["direction"],
                        "start_time": round(chunk_start - segment_start, 6),
                        "end_time": round(chunk_end - segment_start, 6),
                    }
                )
                chunk_start = chunk_end
        return result

    def _looking_for_segment(self, segment_index: int) -> list[dict[str, object]]:
        frames_per_segment = round(self.segment_seconds * FPS)
        first_frame = segment_index * frames_per_segment
        result: list[dict[str, object]] = []
        last_x = self.screen_left + self.screen_width // 2
        last_y = self.screen_top + self.screen_height // 2
        for local_frame in range(frames_per_segment):
            global_frame = first_frame + local_frame
            motion = self.mouse_motion_by_frame.get(global_frame)
            dx = int(motion["dx"]) if motion else 0
            dy = int(motion["dy"]) if motion else 0
            source = "mouse_raw"
            gamepad = self.gamepad_frames.get(global_frame)
            right_x = float(gamepad["right_x"]) if gamepad else 0.0
            right_y = float(gamepad["right_y"]) if gamepad else 0.0
            if dx == 0 and dy == 0 and (right_x != 0.0 or right_y != 0.0):
                dx = round(right_x * 32767)
                dy = round(-right_y * 32767)
                source = "gamepad_right_stick"
            if motion:
                last_x = int(motion["x"])
                last_y = int(motion["y"])
            start_time = local_frame / FPS
            result.append(
                {
                    "start_time": round(start_time, 6),
                    "end_time": round((local_frame + 1) / FPS, 6),
                    "dx": dx,
                    "dy": dy,
                    "x": last_x,
                    "y": last_y,
                    "direction": self._looking_direction(dx, dy),
                    "source": source,
                    "gamepad_right_x": right_x,
                    "gamepad_right_y": right_y,
                }
            )
        return result

    @staticmethod
    def _scroll_input_name(event: dict[str, object]) -> str:
        dy = int(event["dy"])
        dx = int(event["dx"])
        return (
            "mouseWheelUp"
            if dy > 0
            else "mouseWheelDown"
            if dy < 0
            else "mouseWheelRight"
            if dx > 0
            else "mouseWheelLeft"
        )

    def _action_for_segment(
        self,
        segment_index: int,
        complete_keymap: dict[str, dict[str, str]],
    ) -> list[dict[str, object]]:
        segment_start = segment_index * self.segment_seconds
        segment_end = segment_start + self.segment_seconds
        result: list[dict[str, object]] = []
        held_keys: set[str] = set()
        for event in self.keyboard_events:
            key = str(event["key"])
            pressed = bool(event["pressed"])
            event_time = float(event["global_time"])
            is_new_press = pressed and key not in held_keys
            if pressed:
                held_keys.add(key)
            else:
                held_keys.discard(key)
            mapping_key = (
                self._keyboard_mapping_key(
                    key,
                    held_keys,
                    self.session.keymap,
                )
                if is_new_press
                else None
            )
            if (
                not is_new_press
                or mapping_key is None
                or bool(event.get("synthetic"))
                or bool(
                    self.session.keymap.get(mapping_key, {}).get(
                        "movement_direction"
                    )
                )
                or not (segment_start <= event_time < segment_end)
            ):
                continue
            mapping = complete_keymap.get(mapping_key)
            if mapping is None:
                continue
            result.append(
                {
                    "timestamp": round(event_time - segment_start, 6),
                    "mouse_event": "",
                    "keyboard_event": mapping_key,
                    "physical_key": key,
                    "key_chord": mapping_key if mapping_key != key else "",
                    "action": mapping["action"],
                }
            )

        # Mouse chords cross two Raw Input device streams.  Replay modifier
        # transitions up to each mouse event so a later/final keyboard state
        # can never change the action assigned to an earlier click or wheel.
        modifier_events = sorted(
            (
                (float(event["global_time"]), index, event)
                for index, event in enumerate(self.keyboard_events)
                if str(event["key"]) in self._CHORD_MODIFIERS
            ),
            key=lambda item: (item[0], item[1]),
        )
        pointing_events: list[
            tuple[float, int, str, dict[str, object]]
        ] = []
        for index, event in enumerate(self.mouse_button_events):
            if not bool(event["pressed"]) or bool(event.get("synthetic")):
                continue
            pointing_events.append(
                (
                    float(event["global_time"]),
                    index,
                    "button",
                    event,
                )
            )
        scroll_index_offset = len(self.mouse_button_events)
        for index, event in enumerate(self.mouse_scroll_events):
            pointing_events.append(
                (
                    float(event["global_time"]),
                    scroll_index_offset + index,
                    "scroll",
                    event,
                )
            )
        pointing_events.sort(key=lambda item: (item[0], item[1]))

        held_modifiers: set[str] = set()
        modifier_index = 0
        for event_time, _event_index, event_kind, event in pointing_events:
            while (
                modifier_index < len(modifier_events)
                and modifier_events[modifier_index][0] <= event_time
            ):
                modifier_event = modifier_events[modifier_index][2]
                modifier = str(modifier_event["key"])
                if bool(modifier_event["pressed"]):
                    held_modifiers.add(modifier)
                else:
                    held_modifiers.discard(modifier)
                modifier_index += 1
            if not (segment_start <= event_time < segment_end):
                continue
            physical_input = (
                str(event["button"])
                if event_kind == "button"
                else self._scroll_input_name(event)
            )
            mapping_key = self._pointing_mapping_key(
                physical_input,
                held_modifiers,
                self.session.keymap,
            )
            if mapping_key is None:
                continue
            mapping = complete_keymap.get(mapping_key)
            if mapping is None or mapping.get("movement_direction"):
                continue
            action_event = {
                "timestamp": round(event_time - segment_start, 6),
                "mouse_event": physical_input,
                "keyboard_event": "",
                "key_chord": (
                    mapping_key if mapping_key != physical_input else ""
                ),
                "action": mapping["action"],
            }
            if event_kind == "scroll":
                action_event["gamepad_event"] = ""
            result.append(action_event)
        for event in self.gamepad_button_events:
            if not bool(event["pressed"]):
                continue
            if bool(event.get("synthetic")):
                continue
            event_time = float(event["global_time"])
            if not (segment_start <= event_time < segment_end):
                continue
            button = str(event["button"])
            mapping = complete_keymap.get(button)
            if mapping is None or mapping.get("movement_direction"):
                continue
            result.append(
                {
                    "timestamp": round(event_time - segment_start, 6),
                    "mouse_event": "",
                    "keyboard_event": "",
                    "gamepad_event": button,
                    "controller": event["controller"],
                    "action": mapping["action"],
                }
            )
        result.sort(key=lambda item: float(item["timestamp"]))
        return result

    def _raw_for_segment(self, segment_index: int) -> dict[str, object]:
        segment_start = segment_index * self.segment_seconds
        segment_end = segment_start + self.segment_seconds
        configured_inputs = self._configured_physical_inputs()

        def local_time(event: dict[str, object]) -> float:
            return round(float(event["global_time"]) - segment_start, 6)

        mouse_moves: list[dict[str, object]] = []
        first_frame = round(segment_start * FPS)
        last_frame = round(segment_end * FPS)
        for frame_index in sorted(self.mouse_motion_by_frame):
            if not (first_frame <= frame_index < last_frame):
                continue
            event = self.mouse_motion_by_frame[frame_index]
            mouse_moves.append(
                {
                    "t": local_time(event),
                    "frame_index": frame_index - first_frame,
                    "dx": event["dx"],
                    "dy": event["dy"],
                    "x": event["x"],
                    "y": event["y"],
                }
            )

        def slice_events(
            events: list[dict[str, object]],
            name_field: str,
        ) -> list[dict[str, object]]:
            result: list[dict[str, object]] = []
            for event in events:
                event_time = float(event["global_time"])
                if not (segment_start <= event_time < segment_end):
                    continue
                if str(event[name_field]) not in configured_inputs:
                    continue
                copied = {
                    key: value
                    for key, value in event.items()
                    if key != "global_time"
                }
                copied["t"] = round(event_time - segment_start, 6)
                result.append(copied)
            return result

        configured_scroll_events = [
            event
            for event in self.mouse_scroll_events
            if self._scroll_input_name(event) in configured_inputs
        ]

        return {
            "mouse_move_events": mouse_moves,
            "mouse_button_events": slice_events(
                self.mouse_button_events,
                "button",
            ),
            "mouse_scroll_events": [
                {
                    **{
                        key: value
                        for key, value in event.items()
                        if key != "global_time"
                    },
                    "t": round(float(event["global_time"]) - segment_start, 6),
                }
                for event in configured_scroll_events
                if segment_start <= float(event["global_time"]) < segment_end
            ],
            "keyboard_events": slice_events(self.keyboard_events, "key"),
            "gamepad_button_events": slice_events(
                self.gamepad_button_events,
                "button",
            ),
            "gamepad_frames": [
                {
                    "frame_index": frame_index - first_frame,
                    "t": round((frame_index - first_frame) / FPS, 6),
                    **state,
                }
                for frame_index, state in sorted(self.gamepad_frames.items())
                if first_frame <= frame_index < last_frame
            ],
        }

    def _complete_keymap(self) -> dict[str, dict[str, str]]:
        # The delivered keymap is a semantic contract, not an inventory of
        # every Raw Input message seen while recording.  Never invent actions.
        return {
            key: dict(value)
            for key, value in self.session.keymap.items()
        }

    def _max_input_alignment_error_ms(
        self,
        segment_start: float = 0.0,
        segment_end: float | None = None,
    ) -> float:
        if not self.frame_capture_times:
            return 0.0
        worst = 0.0
        configured_inputs = self._configured_physical_inputs()
        all_events = (
            [
                event
                for event in self.keyboard_events
                if str(event["key"]) in configured_inputs
            ]
            + [
                event
                for event in self.mouse_button_events
                if str(event["button"]) in configured_inputs
            ]
            + [
                event
                for event in self.mouse_scroll_events
                if self._scroll_input_name(event) in configured_inputs
            ]
            + [
                event
                for event in self.gamepad_button_events
                if str(event["button"]) in configured_inputs
            ]
            + list(self.mouse_motion_by_frame.values())
        )
        for event in all_events:
            event_time = float(event["global_time"])
            if event_time < segment_start:
                continue
            if segment_end is not None and event_time >= segment_end:
                continue
            event_perf = self.recording_started_perf + event_time
            index = bisect.bisect_left(self.frame_capture_times, event_perf)
            candidates = []
            if index < len(self.frame_capture_times):
                candidates.append(self.frame_capture_times[index])
            if index:
                candidates.append(self.frame_capture_times[index - 1])
            if candidates:
                worst = max(worst, min(abs(event_perf - value) for value in candidates))
        return round(worst * 1000.0, 3)

    def snapshot(self) -> InputEventTracker:
        """Copy mutable event buffers quickly without blocking Raw Input during JSON I/O."""
        with self.lock:
            clone = object.__new__(InputEventTracker)
            clone.__dict__ = dict(self.__dict__)
            clone.lock = threading.Lock()
            clone.frame_capture_times = list(self.frame_capture_times)
            clone.mouse_motion_by_frame = {
                key: dict(value)
                for key, value in self.mouse_motion_by_frame.items()
            }
            clone.keyboard_events = [
                dict(event) for event in self.keyboard_events
            ]
            clone.mouse_button_events = [
                dict(event) for event in self.mouse_button_events
            ]
            clone.mouse_scroll_events = [
                dict(event) for event in self.mouse_scroll_events
            ]
            clone.gamepad_frames = {
                key: dict(value)
                for key, value in self.gamepad_frames.items()
            }
            clone.gamepad_button_events = [
                dict(event) for event in self.gamepad_button_events
            ]
        return clone

    def compact_before(self, cutoff: float) -> None:
        """Discard committed input while preserving held-button state at the boundary."""
        cutoff_frame = round(cutoff * FPS)

        def compact_button_events(
            events: list[dict[str, object]],
            name_field: str,
        ) -> list[dict[str, object]]:
            held: dict[str, dict[str, object]] = {}
            retained: list[dict[str, object]] = []
            for event in events:
                event_time = float(event["global_time"])
                name = str(event[name_field])
                if event_time < cutoff:
                    if bool(event["pressed"]):
                        held[name] = event
                    else:
                        held.pop(name, None)
                else:
                    retained.append(event)
            synthetic = []
            for event in held.values():
                copied = dict(event)
                copied["global_time"] = cutoff
                copied["synthetic"] = True
                synthetic.append(copied)
            return synthetic + retained

        with self.lock:
            self.keyboard_events = compact_button_events(
                self.keyboard_events,
                "key",
            )
            self.mouse_button_events = compact_button_events(
                self.mouse_button_events,
                "button",
            )
            self.gamepad_button_events = compact_button_events(
                self.gamepad_button_events,
                "button",
            )
            self.mouse_scroll_events = [
                event
                for event in self.mouse_scroll_events
                if float(event["global_time"]) >= cutoff
            ]
            self.mouse_motion_by_frame = {
                frame_index: event
                for frame_index, event in self.mouse_motion_by_frame.items()
                if frame_index >= cutoff_frame
            }
            self.gamepad_frames = {
                frame_index: state
                for frame_index, state in self.gamepad_frames.items()
                if frame_index >= cutoff_frame
            }
            cutoff_perf = self.recording_started_perf + cutoff
            self.frame_capture_times = [
                value
                for value in self.frame_capture_times
                if value >= cutoff_perf
            ]

    def write_json_file(
        self,
        video_path: Path,
        segment_index: int,
        quality_report: dict[str, object],
        video_sha256: str,
    ) -> Path:
        segment_start = segment_index * self.segment_seconds
        segment_end = segment_start + self.segment_seconds
        movement_intervals = self._build_global_movement_events(segment_end)
        complete_keymap = self._complete_keymap()
        alignment_error_ms = self._max_input_alignment_error_ms(
            segment_start,
            segment_end,
        )
        json_path = video_path.with_suffix(".json")
        keymap_path = video_path.with_name(f"{video_path.stem}_keymap.json")
        segment_started_at = self.recording_started_at + timedelta(
            seconds=segment_start
        )
        document = {
                "schema_version": SCHEMA_VERSION,
                "tool": {
                    "name": APP_NAME,
                    "version": APP_VERSION,
                },
                "game_metadata": {
                    "game_title": self.session.game_title,
                    "session_id": self.session_id,
                    "video_name": video_path.name,
                    "start_time": segment_started_at.isoformat(timespec="milliseconds"),
                    "duration_seconds": self.segment_seconds,
                    "segment_index": segment_index + 1,
                    "fps": FPS,
                    "resolution": {
                        "width": self.screen_width,
                        "height": self.screen_height,
                    },
                    "capture_backend": self.capture_backend,
                    "capture_mode": self.capture_mode,
                    "video_encoder": self.encoder_name,
                    # The requirement is dimensional: both the clean width and
                    # clean height must reach 70%.  Keep the derived area floor
                    # for older consumers and expose the two authoritative values.
                    "minimum_effective_area_ratio": round(
                        MIN_CLEAN_DIMENSION_RATIO**2,
                        2,
                    ),
                    "minimum_clean_width_ratio": MIN_CLEAN_DIMENSION_RATIO,
                    "minimum_clean_height_ratio": MIN_CLEAN_DIMENSION_RATIO,
                    "mouse_acceleration_disabled": True,
                    "max_input_alignment_error_ms": alignment_error_ms,
                    "video_sha256": video_sha256,
                },
                "movement_events": self._movement_for_segment(
                    movement_intervals,
                    segment_index,
                ),
                "looking_events": self._looking_for_segment(segment_index),
                "action_events": self._action_for_segment(
                    segment_index,
                    complete_keymap,
                ),
                "raw_input": self._raw_for_segment(segment_index),
                "quality_control": quality_report,
            }
        atomic_write_json(json_path, document)
        atomic_write_json(keymap_path, complete_keymap)
        return json_path


class QualityMonitor:
    """Constant-memory, per-segment frame quality aggregation."""

    def __init__(self) -> None:
        self.previous_signature: int | None = None
        self.total_frames = 0
        self._segment_frames = 0
        self._first_captured_at = 0.0
        self._last_captured_at = 0.0
        self._max_gap = 0.0
        self._black_frames = 0
        self._black_run = 0
        self._max_black_run = 0
        self._frozen_run = 0
        self._max_frozen_run = 0
        self._completed: dict[int, dict[str, object]] = {}

    def observe(
        self,
        frame: np.ndarray,
        captured_at: float,
        frames_per_segment: int | None = None,
    ) -> None:
        if frame.ndim != 3 or frame.shape[2] < 3:
            raise RuntimeError("捕获帧格式无效，无法执行质量检查")
        sample = np.ascontiguousarray(frame[::16, ::16, :3])
        mean = float(sample.mean())
        deviation = float(sample.std())
        signature = zlib.crc32(sample.tobytes())
        duplicate = (
            self.previous_signature is not None
            and signature == self.previous_signature
        )
        self.previous_signature = signature
        black = mean < 5.0 and deviation < 4.0
        if self._segment_frames == 0:
            self._first_captured_at = captured_at
        elif self._last_captured_at:
            self._max_gap = max(
                self._max_gap,
                captured_at - self._last_captured_at,
            )
        self._last_captured_at = captured_at
        self._segment_frames += 1
        self.total_frames += 1
        if black:
            self._black_frames += 1
        self._black_run = self._black_run + 1 if black else 0
        self._max_black_run = max(self._max_black_run, self._black_run)
        self._frozen_run = self._frozen_run + 1 if duplicate else 0
        self._max_frozen_run = max(self._max_frozen_run, self._frozen_run)
        if (
            frames_per_segment is not None
            and self._segment_frames == frames_per_segment
        ):
            segment_index = self.total_frames // frames_per_segment - 1
            self._completed[segment_index] = self._finish_current(
                frames_per_segment
            )

    def _finish_current(self, frames_per_segment: int) -> dict[str, object]:
        frame_count = self._segment_frames
        elapsed = (
            self._last_captured_at - self._first_captured_at + 1.0 / FPS
            if frame_count
            else 0.0
        )
        actual_fps = frame_count / elapsed if elapsed > 0 else 0.0
        black_duration = self._black_frames / FPS
        black_ratio = self._black_frames / frame_count if frame_count else 0.0
        black_run = self._max_black_run / FPS
        frozen_run = self._max_frozen_run / FPS
        failures: list[str] = []
        warnings: list[str] = []
        if frame_count != frames_per_segment:
            failures.append("帧数不完整")
        if actual_fps < MIN_ACCEPTABLE_FPS:
            failures.append(
                f"实际采集帧率 {actual_fps:.2f} FPS 低于 {MIN_ACCEPTABLE_FPS:.0f}"
            )
        if self._max_gap >= MAX_SINGLE_CAPTURE_STALL_SECONDS:
            failures.append(
                f"单次采集停顿 {self._max_gap * 1000:.1f} ms 达到 "
                f"{MAX_SINGLE_CAPTURE_STALL_SECONDS * 1000:.0f} ms 卡顿门限"
            )
        elif self._max_gap > FRAME_GAP_WARNING_SECONDS:
            warnings.append(
                f"检测到单次采集间隔 {self._max_gap * 1000:.1f} ms；"
                f"未达到 {MAX_SINGLE_CAPTURE_STALL_SECONDS * 1000:.0f} ms "
                "卡顿门限"
            )
        if black_run >= BLACK_RUN_FAILURE_SECONDS:
            failures.append(
                f"连续近黑画面 {black_run:.2f} 秒，达到 "
                f"{BLACK_RUN_FAILURE_SECONDS:.0f} 秒门限"
            )
        elif black_run >= BLACK_RUN_WARNING_SECONDS:
            warnings.append(
                f"检测到连续近黑画面 {black_run:.2f} 秒，可能为游戏转场或加载页；"
                f"未达到 {BLACK_RUN_FAILURE_SECONDS:.0f} 秒失败门限"
            )
        if frozen_run >= FROZEN_RUN_FAILURE_SECONDS:
            failures.append(
                f"连续重复帧 {frozen_run:.2f} 秒，达到 "
                f"{FROZEN_RUN_FAILURE_SECONDS:.0f} 秒门限"
            )
        elif frozen_run >= FROZEN_RUN_WARNING_SECONDS:
            warnings.append(
                f"检测到连续重复帧 {frozen_run:.2f} 秒；未达到 "
                f"{FROZEN_RUN_FAILURE_SECONDS:.0f} 秒失败门限"
            )
        report: dict[str, object] = {
            "passed": not failures,
            "expected_fps": FPS,
            "actual_capture_fps": round(actual_fps, 3),
            "frame_count": frame_count,
            "max_frame_gap_ms": round(self._max_gap * 1000.0, 3),
            "frame_gap_warning_threshold_ms": round(
                FRAME_GAP_WARNING_SECONDS * 1000.0,
                3,
            ),
            "capture_stall_failure_threshold_ms": round(
                MAX_SINGLE_CAPTURE_STALL_SECONDS * 1000.0,
                3,
            ),
            "black_frame_count": self._black_frames,
            "black_duration_seconds": round(black_duration, 3),
            "black_frame_ratio": round(black_ratio, 6),
            "longest_black_run_seconds": round(black_run, 3),
            "black_run_warning_threshold_seconds": BLACK_RUN_WARNING_SECONDS,
            "black_run_failure_threshold_seconds": BLACK_RUN_FAILURE_SECONDS,
            "longest_frozen_run_seconds": round(frozen_run, 3),
            "frozen_run_warning_threshold_seconds": FROZEN_RUN_WARNING_SECONDS,
            "frozen_run_failure_threshold_seconds": FROZEN_RUN_FAILURE_SECONDS,
            "warnings": warnings,
            "failures": failures,
        }
        self._segment_frames = 0
        self._first_captured_at = 0.0
        self._last_captured_at = 0.0
        self._max_gap = 0.0
        self._black_frames = 0
        self._black_run = 0
        self._max_black_run = 0
        self._frozen_run = 0
        self._max_frozen_run = 0
        return report

    def report_for_segment(self, segment_index: int) -> dict[str, object]:
        try:
            return dict(self._completed[segment_index])
        except KeyError as exc:
            raise RuntimeError(
                f"切片 {segment_index + 1} 的质量统计尚未完成"
            ) from exc

    def reports(
        self,
        complete_segment_count: int,
        frames_per_segment: int,
    ) -> list[dict[str, object]]:
        # Compatibility wrapper used by tests and final consistency checks.
        if (
            self._segment_frames == frames_per_segment
            and len(self._completed) < complete_segment_count
        ):
            segment_index = self.total_frames // frames_per_segment - 1
            self._completed[segment_index] = self._finish_current(
                frames_per_segment
            )
        return [
            self.report_for_segment(index)
            for index in range(complete_segment_count)
        ]


class DesktopCaptureBackend:
    """DXGI Desktop Duplication capture with an MSS compatibility fallback."""

    def __init__(
        self,
        target: CaptureTarget,
        monitor: dict[str, int],
    ) -> None:
        self.target = target
        self.monitor = monitor
        self.name = ""
        self.camera: object | None = None
        self.mss_capture: mss.mss | None = None
        self.pending_frame: np.ndarray | None = None
        self.next_mss_frame_time = 0.0

    def start(self) -> None:
        dxcam_error: Exception | None = None
        camera = None
        try:
            local_left = self.target.left - int(self.monitor["left"])
            local_top = self.target.top - int(self.monitor["top"])
            region = (
                local_left,
                local_top,
                local_left + self.target.width,
                local_top + self.target.height,
            )
            camera = dxcam.create(
                output_idx=self.target.monitor_index - 1,
                output_color="BGRA",
                backend="dxgi",
                processor_backend="numpy",
                max_buffer_len=8,
            )
            camera.start(
                region=region,
                target_fps=round(FPS),
                video_mode=True,
            )
            frame = camera.get_latest_frame()
            self._validate_frame(frame)
            self.camera = camera
            self.pending_frame = frame
            self.name = "dxgi_desktop_duplication"
            return
        except Exception as exc:
            dxcam_error = exc
            if camera is not None:
                try:
                    camera.release()
                except Exception:
                    pass

        try:
            self.mss_capture = mss.mss()
            frame = self._grab_mss()
            self._validate_frame(frame)
            self.pending_frame = frame
            self.next_mss_frame_time = time.perf_counter()
            self.name = f"mss_gdi_fallback ({dxcam_error})"
        except Exception as exc:
            self.close()
            raise RuntimeError(
                f"DXGI 与 MSS 捕获均不可用；DXGI：{dxcam_error}；MSS：{exc}"
            ) from exc

    def _grab_mss(self) -> np.ndarray:
        if self.mss_capture is None:
            raise RuntimeError("MSS 捕获器尚未启动")
        shot = self.mss_capture.grab(
            {
                "left": self.target.left,
                "top": self.target.top,
                "width": self.target.width,
                "height": self.target.height,
            }
        )
        return np.frombuffer(shot.raw, dtype=np.uint8).reshape(
            self.target.height,
            self.target.width,
            4,
        )

    def _validate_frame(self, frame: object) -> None:
        if not isinstance(frame, np.ndarray):
            raise RuntimeError("捕获后端没有返回BGRA图像")
        expected = (self.target.height, self.target.width, 4)
        if frame.shape != expected:
            raise RuntimeError(
                f"捕获尺寸为 {frame.shape}，预期为 {expected}；"
                "显示器映射可能已经改变"
            )

    def grab(self, stop_event: threading.Event) -> np.ndarray:
        if self.pending_frame is not None:
            frame = self.pending_frame
            self.pending_frame = None
            return frame
        if self.camera is not None:
            frame = self.camera.get_latest_frame()
            self._validate_frame(frame)
            return frame
        frame_interval = 1.0 / FPS
        self.next_mss_frame_time += frame_interval
        delay = self.next_mss_frame_time - time.perf_counter()
        if delay > 0:
            stop_event.wait(delay)
        elif delay < -frame_interval * 3:
            self.next_mss_frame_time = time.perf_counter()
        return self._grab_mss()

    def close(self) -> None:
        camera = self.camera
        self.camera = None
        if camera is not None:
            try:
                # Keep DXcam's singleton alive for subsequent recordings.
                # Releasing its COM wrappers here can cause a second release
                # during interpreter shutdown on some Python/comtypes builds.
                camera.stop()
            except Exception:
                pass
        capture = self.mss_capture
        self.mss_capture = None
        if capture is not None:
            capture.close()


class H264Encoder:
    """One-pass raw BGRA -> H.264/MP4 encoder backed by bundled FFmpeg."""

    def __init__(
        self,
        output_path: Path,
        input_width: int,
        input_height: int,
        output_width: int,
        output_height: int,
        segment_seconds: int,
    ) -> None:
        self.output_path = output_path
        self.input_width = input_width
        self.input_height = input_height
        self.output_width = output_width
        self.output_height = output_height
        self.segment_seconds = segment_seconds
        self.bitrate = target_bitrate(output_width, output_height)
        self.encoder_name = select_h264_encoder()
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        bitrate = str(self.bitrate)
        ffmpeg_path, _ffprobe_path = media_tools()
        command = [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pixel_format",
            "bgra",
            "-video_size",
            f"{self.input_width}x{self.input_height}",
            "-framerate",
            str(int(FPS)),
            "-i",
            "pipe:0",
            "-an",
        ]
        if (
            self.input_width != self.output_width
            or self.input_height != self.output_height
        ):
            command.extend(
                ["-vf", f"crop={self.output_width}:{self.output_height}:0:0"]
            )
        if self.encoder_name == "h264_nvenc":
            command.extend(
                [
                    "-c:v",
                    "h264_nvenc",
                    "-preset",
                    "p6",
                    "-tune",
                    "hq",
                    "-rc",
                    "cbr",
                    "-multipass",
                    "fullres",
                    "-profile:v",
                    "high",
                    "-forced-idr",
                    "1",
                    "-no-scenecut",
                    "1",
                    "-strict_gop",
                    "1",
                ]
            )
        elif self.encoder_name == "h264_qsv":
            command.extend(
                [
                    "-c:v",
                    "h264_qsv",
                    "-preset",
                    "medium",
                    "-profile:v",
                    "high",
                    "-forced_idr",
                    "1",
                    "-adaptive_i",
                    "0",
                ]
            )
        elif self.encoder_name == "h264_amf":
            command.extend(
                [
                    "-c:v",
                    "h264_amf",
                    "-quality",
                    "quality",
                    "-rc",
                    "cbr",
                    "-profile:v",
                    "high",
                    "-forced_idr",
                    "1",
                ]
            )
        else:
            command.extend(
                [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-profile:v",
                    "high",
                ]
            )
        command.extend(
            [
                "-bf",
                "0",
                "-pix_fmt",
                "yuv420p",
                "-b:v",
                bitrate,
                "-minrate",
                bitrate,
                "-maxrate",
                bitrate,
                "-bufsize",
                str(self.bitrate * 2),
                "-g",
                str(int(FPS * 2)),
                "-keyint_min",
                "1",
                "-sc_threshold",
                "0",
                "-force_key_frames",
                (
                    "expr:gte(n,n_forced*"
                    f"{round(self.segment_seconds * FPS)})"
                ),
            ]
        )
        if self.encoder_name == "libx264":
            command.extend(
                [
                    "-x264-params",
                    (
                        "nal-hrd=cbr:force-cfr=1:filler=1:"
                        "aq-mode=3:aq-strength=1.0:"
                        "colorprim=bt709:transfer=bt709:colormatrix=bt709"
                    ),
                ]
            )
        command.extend(
            [
                "-color_range",
                "tv",
                "-colorspace",
                "bt709",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-tag:v",
                "avc1",
                "-f",
                "segment",
                "-segment_time",
                str(self.segment_seconds),
                "-segment_time_delta",
                f"{1 / (2 * FPS):.6f}",
                "-segment_start_number",
                "1",
                "-reset_timestamps",
                "1",
                "-segment_format",
                "mp4",
                "-segment_format_options",
                "movflags=+faststart",
                str(self.output_path),
            ]
        )

        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=creation_flags,
        )

    def write_frame(self, frame: bytes | bytearray | np.ndarray) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("H.264 编码器尚未启动")
        self.process.stdin.write(frame)

    def finish(self) -> None:
        if self.process is None:
            return
        process = self.process
        self.process = None
        if process.stdin is not None:
            process.stdin.close()
        stderr = b""
        if process.stderr is not None:
            stderr = process.stderr.read()
        return_code = process.wait()
        if return_code != 0:
            details = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(details or f"FFmpeg 退出码：{return_code}")

    def abort(self) -> None:
        if self.process is None:
            return
        process = self.process
        self.process = None
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _parse_frame_rate(value: str) -> float:
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator)
    return float(value)


def verify_encoded_video(
    video_path: Path,
    duration_seconds: int,
    width: int,
    height: int,
) -> dict[str, object]:
    """Validate one slice using real ffprobe output and a complete decode."""
    required_bitrate = minimum_required_bitrate(width, height)
    ffmpeg_path, ffprobe_path = media_tools()
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    probe = subprocess.run(
        [
            ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            (
                "stream=codec_name,profile,width,height,pix_fmt,"
                "r_frame_rate,avg_frame_rate,bit_rate,duration,nb_frames:"
                "format=duration,bit_rate"
            ),
            "-of",
            "json",
            str(video_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creation_flags,
        check=False,
        timeout=30,
    )
    if probe.returncode != 0:
        details = probe.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"{video_path.name} ffprobe失败：{details}")
    try:
        metadata = json.loads(probe.stdout.decode("utf-8"))
        stream = metadata["streams"][0]
        container = metadata["format"]
        actual_width = int(stream["width"])
        actual_height = int(stream["height"])
        actual_fps = _parse_frame_rate(
            str(stream.get("avg_frame_rate") or stream["r_frame_rate"])
        )
        actual_duration = float(
            stream.get("duration") or container["duration"]
        )
        encoded_frame_count = int(stream["nb_frames"])
        stream_bitrate = int(
            stream.get("bit_rate") or container.get("bit_rate") or 0
        )
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"{video_path.name} 的ffprobe元数据不完整：{exc}"
        ) from exc
    if str(stream.get("codec_name")) != "h264":
        raise RuntimeError(f"{video_path.name} 不是H.264视频")
    if (actual_width, actual_height) != (width, height):
        raise RuntimeError(
            f"{video_path.name} 分辨率为 {actual_width}×{actual_height}，"
            f"预期 {width}×{height}"
        )
    if actual_fps < MIN_ACCEPTABLE_FPS:
        raise RuntimeError(
            f"{video_path.name} 的ffprobe帧率仅 {actual_fps:.3f} FPS"
        )
    expected_frame_count = round(duration_seconds * FPS)
    if encoded_frame_count != expected_frame_count:
        raise RuntimeError(
            f"{video_path.name} 编码帧数为 {encoded_frame_count}，"
            f"预期 {expected_frame_count} 帧"
        )
    if abs(actual_duration - duration_seconds) > 0.1:
        raise RuntimeError(
            f"{video_path.name} 时长为 {actual_duration:.3f} 秒，"
            f"预期 {duration_seconds} 秒"
        )
    if stream_bitrate < required_bitrate:
        raise RuntimeError(
            f"{video_path.name} 的ffprobe bit_rate为 "
            f"{stream_bitrate / 1_000_000:.2f} Mbps，"
            f"低于 {required_bitrate / 1_000_000:.0f} Mbps"
        )
    decode = subprocess.run(
        [
            ffmpeg_path,
            "-v",
            "error",
            "-i",
            str(video_path),
            "-map",
            "0:v:0",
            "-nostats",
            "-progress",
            "pipe:1",
            "-f",
            "null",
            "-",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creation_flags,
        check=False,
        timeout=max(120, duration_seconds * 2),
    )
    if decode.returncode != 0:
        details = decode.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"{video_path.name} 无法由FFmpeg完整解码："
            f"{details or decode.returncode}"
        )
    decoded_frames = [
        int(value)
        for value in re.findall(
            rb"(?m)^frame=(\d+)\s*$",
            decode.stdout,
        )
    ]
    if not decoded_frames or decoded_frames[-1] != expected_frame_count:
        actual_decoded = decoded_frames[-1] if decoded_frames else "未知"
        raise RuntimeError(
            f"{video_path.name} 完整解码得到 {actual_decoded} 帧，"
            f"预期 {expected_frame_count} 帧"
        )
    return {
        "ffmpeg_decode_verified": True,
        "codec_name": stream["codec_name"],
        "profile": stream.get("profile", ""),
        "pixel_format": stream.get("pix_fmt", ""),
        "width": actual_width,
        "height": actual_height,
        "fps": round(actual_fps, 6),
        "duration_seconds": round(actual_duration, 6),
        "encoded_frame_count": encoded_frame_count,
        "decoded_frame_count": decoded_frames[-1],
        "stream_bit_rate_bps": stream_bitrate,
        "minimum_required_bitrate_bps": required_bitrate,
    }


def verify_encoded_videos(
    video_paths: list[Path],
    duration_seconds: int,
    width: int,
    height: int,
) -> list[int]:
    """Compatibility wrapper returning verified stream bitrates."""
    return [
        int(
            verify_encoded_video(
                path,
                duration_seconds,
                width,
                height,
            )["stream_bit_rate_bps"]
        )
        for path in video_paths
    ]


class SegmentFinalizer:
    """Validate and commit closed slices while capture continues."""

    def __init__(
        self,
        save_directory: Path,
        segment_prefix: str,
        segment_seconds: int,
        width: int,
        height: int,
        input_tracker: InputEventTracker,
        quality_monitor: QualityMonitor,
        notifications: queue.Queue[RecordingResult] | None = None,
    ) -> None:
        self.save_directory = save_directory
        self.segment_prefix = segment_prefix
        self.segment_seconds = segment_seconds
        self.width = width
        self.height = height
        self.input_tracker = input_tracker
        self.quality_monitor = quality_monitor
        self.notifications = notifications
        self.jobs: queue.Queue[int | None] = queue.Queue(
            maxsize=FINALIZER_QUEUE_SIZE
        )
        self.encoder_done = threading.Event()
        self.failed = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name="segment-finalizer",
            daemon=True,
        )
        self.lock = threading.Lock()
        self.scheduled: set[int] = set()
        self.committed: list[dict[str, object]] = []
        self.errors: list[str] = []
        self.quality_reports: dict[int, dict[str, object]] = {}
        self.manifest_path = (
            self.save_directory / f"{self.segment_prefix}_manifest.json"
        )

    def start(self) -> None:
        self._write_manifest("recording")
        self.thread.start()

    def schedule(self, segment_index: int) -> None:
        with self.lock:
            if segment_index in self.scheduled:
                return
            self.scheduled.add(segment_index)
        try:
            self.jobs.put(segment_index, timeout=1.0)
        except queue.Full as exc:
            self.failed.set()
            raise RuntimeError(
                "切片质检队列积压超过4个，已停止录制以避免内存持续增长"
            ) from exc

    def finish(self) -> None:
        self.encoder_done.set()
        self.jobs.put(None)
        self.thread.join()
        if self.errors:
            raise RuntimeError(self.errors[0])
        self._write_manifest("complete")

    def abort(self, reason: str) -> None:
        self.encoder_done.set()
        try:
            self.jobs.put_nowait(None)
        except queue.Full:
            pass
        if self.thread.is_alive():
            self.thread.join(timeout=30)
        final_reason = self.errors[0] if self.errors else reason
        self._write_manifest(
            "failed" if self.errors else "interrupted",
            final_reason,
        )

    def _video_path(self, segment_index: int) -> Path:
        return self.save_directory / (
            f"{self.segment_prefix}_{segment_index + 1:03d}.mp4"
        )

    def _wait_until_closed(self, segment_index: int) -> Path:
        path = self._video_path(segment_index)
        next_path = self._video_path(segment_index + 1)
        deadline = time.monotonic() + max(30.0, self.segment_seconds)
        while True:
            if path.exists() and (next_path.exists() or self.encoder_done.is_set()):
                return path
            if time.monotonic() >= deadline:
                raise RuntimeError(f"{path.name} 等待FFmpeg关闭文件超时")
            time.sleep(0.05)

    def _run(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                break
            try:
                self._finalize(job)
            except Exception as exc:
                reason = f"切片 {job + 1} 验收失败：{exc}"
                self.errors.append(reason)
                self.failed.set()
                self._reject(job, reason)
                self._write_manifest("failed", reason)
                break

    def _finalize(self, segment_index: int) -> None:
        video_path = self._wait_until_closed(segment_index)
        segment_start = segment_index * self.segment_seconds
        segment_end = segment_start + self.segment_seconds
        snapshot = self.input_tracker.snapshot()
        report = self.quality_monitor.report_for_segment(segment_index)
        self.quality_reports[segment_index] = report
        alignment_error_ms = snapshot._max_input_alignment_error_ms(
            segment_start,
            segment_end,
        )
        if alignment_error_ms > MAX_INPUT_ALIGNMENT_ERROR_SECONDS * 1000:
            report["passed"] = False
            failures = report["failures"]
            if isinstance(failures, list):
                failures.append(
                    f"输入与视频最大对齐误差 {alignment_error_ms:.1f} ms 超过100 ms"
                )
        if not bool(report["passed"]):
            raise RuntimeError("；".join(str(item) for item in report["failures"]))
        report["media_verification"] = verify_encoded_video(
            video_path,
            self.segment_seconds,
            self.width,
            self.height,
        )
        video_hash = sha256_file(video_path)
        json_path = snapshot.write_json_file(
            video_path,
            segment_index,
            report,
            video_hash,
        )
        keymap_path = video_path.with_name(f"{video_path.stem}_keymap.json")
        record = {
            "segment_index": segment_index + 1,
            "video": video_path.name,
            "operation_json": json_path.name,
            "keymap_json": keymap_path.name,
            "sha256": {
                "video": video_hash,
                "operation_json": sha256_file(json_path),
                "keymap_json": sha256_file(keymap_path),
            },
            "quality_passed": True,
        }
        with self.lock:
            self.committed.append(record)
            self.committed.sort(key=lambda item: int(item["segment_index"]))
        self.input_tracker.compact_before(segment_end)
        self._write_manifest("recording")
        if self.notifications is not None:
            self.notifications.put(
                RecordingResult(
                    "progress",
                    video_path,
                    f"切片 {segment_index + 1} 已通过验收并落盘",
                )
            )

    def _reject(self, segment_index: int, reason: str) -> None:
        rejected_directory = self.save_directory / "rejected"
        rejected_directory.mkdir(exist_ok=True)
        video_path = self._video_path(segment_index)
        moved_files: list[str] = []
        for path in (
            video_path,
            video_path.with_suffix(".json"),
            video_path.with_name(f"{video_path.stem}_keymap.json"),
        ):
            if path.exists():
                destination = rejected_directory / path.name
                os.replace(path, destination)
                moved_files.append(destination.name)
        atomic_write_json(
            rejected_directory
            / f"{self.segment_prefix}_{segment_index + 1:03d}_failure.json",
            {
                "schema_version": SCHEMA_VERSION,
                "tool_version": APP_VERSION,
                "segment_index": segment_index + 1,
                "reason": reason,
                "files": moved_files,
                "quality_control": self.quality_reports.get(segment_index, {}),
                "created_at": datetime.now().astimezone().isoformat(
                    timespec="milliseconds"
                ),
            },
        )

    def _write_manifest(self, status: str, error: str = "") -> None:
        with self.lock:
            segments = [dict(item) for item in self.committed]
        atomic_write_json(
            self.manifest_path,
            {
                "schema_version": SCHEMA_VERSION,
                "tool": {"name": APP_NAME, "version": APP_VERSION},
                "session_id": self.input_tracker.session_id,
                "game_title": self.input_tracker.session.game_title,
                "status": status,
                "updated_at": datetime.now().astimezone().isoformat(
                    timespec="milliseconds"
                ),
                "error": error,
                "segments": segments,
            },
        )


class ScreenCapture:
    def __init__(
        self,
        save_directory: Path,
        segment_seconds: int,
        session: SessionConfig,
        capture_target: CaptureTarget,
        stop_event: threading.Event,
        results: queue.Queue[RecordingResult],
        control_window: int | None = None,
    ) -> None:
        self.save_directory = save_directory
        self.segment_seconds = segment_seconds
        self.session = session
        self.capture_target = capture_target
        self.stop_event = stop_event
        self.results = results
        self.control_window = control_window

    def _check_disk_space(self, width: int, height: int) -> None:
        free_bytes = shutil.disk_usage(self.save_directory).free
        estimated_segment_bytes = (
            target_bitrate(width, height)
            * self.segment_seconds
            // 8
        )
        required = max(MIN_FREE_SPACE_BYTES, estimated_segment_bytes * 2)
        if free_bytes < required:
            raise RuntimeError(
                f"磁盘剩余空间仅 {free_bytes / 1024**3:.2f} GB；"
                f"至少需要 {required / 1024**3:.2f} GB 才能安全继续录制"
            )

    def run(self) -> None:
        encoder: H264Encoder | None = None
        segment_prefix = ""
        captured_frame_count = 0
        input_tracker: InputEventTracker | None = None
        capture_backend: DesktopCaptureBackend | None = None
        finalizer: SegmentFinalizer | None = None
        encoder_thread: threading.Thread | None = None
        frame_queue: queue.Queue[np.ndarray | None] | None = None
        failure_reason = ""
        try:
            self.save_directory.mkdir(parents=True, exist_ok=True)
            session_created_at = datetime.now().astimezone()
            stamp = session_created_at.strftime("%Y-%m-%d_%H-%M-%S")
            session_id = f"{session_created_at:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}"
            segment_prefix = (
                f"{safe_file_component(self.session.game_title)}_{stamp}_{session_id[-8:]}"
            )
            output_path = self.save_directory / f"{segment_prefix}_%03d.mp4"

            with mss.mss() as capture:
                if not 1 <= self.capture_target.monitor_index < len(capture.monitors):
                    raise RuntimeError("所选显示器已经不可用，请重新启动工具")
                monitor = dict(capture.monitors[self.capture_target.monitor_index])

            target = self.capture_target
            expected_rect = (target.left, target.top, target.width, target.height)
            focus_process_id: int | None = None
            if target.hwnd is not None:
                focus_hwnd = foreground_window()
                if focus_hwnd is None:
                    raise RuntimeError("没有检测到前台游戏窗口")
                focus_process_id = target.process_id or window_process_id(focus_hwnd)
                if focus_process_id is None:
                    raise RuntimeError("无法识别游戏进程")
                if (
                    target.process_id is not None
                    and window_process_id(focus_hwnd) != target.process_id
                ):
                    raise RuntimeError("当前前台窗口不属于已选择的游戏进程")
                focus_rect = window_client_rect(focus_hwnd)
                if focus_rect is None:
                    raise RuntimeError("前台游戏窗口不可见或已最小化")
                width_ratio, height_ratio = rectangle_dimension_coverage(
                    focus_rect,
                    target,
                )
                if (
                    width_ratio < MIN_CLEAN_DIMENSION_RATIO
                    or height_ratio < MIN_CLEAN_DIMENSION_RATIO
                ):
                    raise RuntimeError(
                        "前台窗口覆盖录制区域的宽度或高度低于70%"
                    )
                if focus_rect != expected_rect:
                    raise RuntimeError(
                        "游戏窗口的位置或客户区尺寸已经改变，请重新启动工具并选择窗口"
                    )
            input_width = target.width
            input_height = target.height
            if input_width < MIN_WIDTH or input_height < MIN_HEIGHT:
                raise RuntimeError(
                    f"实际录制区域为 {input_width}×{input_height}，"
                    f"低于 {MIN_WIDTH}×{MIN_HEIGHT}"
                )
            width = input_width // 2 * 2
            height = input_height // 2 * 2
            self._check_disk_space(width, height)

            capture_backend = DesktopCaptureBackend(target, monitor)
            capture_backend.start()
            quality_monitor = QualityMonitor()

            encoder = H264Encoder(
                output_path,
                input_width,
                input_height,
                width,
                height,
                self.segment_seconds,
            )
            encoder.start()
            recording_started_at = datetime.now().astimezone()
            recording_started_perf = time.perf_counter()
            input_tracker = InputEventTracker(
                self.session,
                session_id,
                recording_started_at,
                self.segment_seconds,
                target.left,
                target.top,
                width,
                height,
                recording_started_perf,
                capture_backend.name,
                "window_client" if target.hwnd is not None else "monitor",
                encoder.encoder_name,
                self.control_window,
            )
            frames_per_segment = round(self.segment_seconds * FPS)
            finalizer = SegmentFinalizer(
                self.save_directory,
                segment_prefix,
                self.segment_seconds,
                width,
                height,
                input_tracker,
                quality_monitor,
                self.results,
            )

            frame_queue = queue.Queue(
                maxsize=encoder_queue_capacity(input_width, input_height)
            )
            encoder_errors: list[Exception] = []

            def encode_frames() -> None:
                try:
                    while True:
                        queued_frame = frame_queue.get()
                        if queued_frame is None:
                            break
                        encoder.write_frame(queued_frame)
                    encoder.finish()
                except Exception as exc:
                    encoder_errors.append(exc)
                    encoder.abort()

            encoder_thread = threading.Thread(
                target=encode_frames,
                name="video-encoder",
                daemon=True,
            )
            encoder_thread.start()
            input_tracker.start()
            finalizer.start()
            last_window_check = recording_started_perf
            last_disk_check = recording_started_perf

            try:
                while True:
                    if finalizer.failed.is_set():
                        raise RuntimeError(
                            finalizer.errors[0]
                            if finalizer.errors
                            else "切片后台验收失败"
                        )
                    frame = capture_backend.grab(self.stop_event)
                    captured_at = time.perf_counter()
                    quality_monitor.observe(
                        frame,
                        captured_at,
                        frames_per_segment,
                    )
                    while True:
                        if encoder_errors:
                            raise RuntimeError(
                                f"视频编码失败：{encoder_errors[0]}"
                            )
                        try:
                            frame_queue.put(frame, timeout=0.2)
                            captured_frame_count += 1
                            input_tracker.update_video_frame(
                                captured_frame_count - 1,
                                captured_at,
                            )
                            break
                        except queue.Full:
                            continue

                    if (
                        captured_frame_count > frames_per_segment
                        and captured_frame_count % frames_per_segment == 1
                    ):
                        finalizer.schedule(
                            captured_frame_count // frames_per_segment - 1
                        )
                    if (
                        target.hwnd is not None
                        and captured_at - last_window_check >= 1.0
                    ):
                        current_foreground = foreground_window()
                        current_focus_rect = (
                            window_client_rect(current_foreground)
                            if current_foreground is not None
                            else None
                        )
                        current_process_id = (
                            window_process_id(current_foreground)
                            if current_foreground is not None
                            else None
                        )
                        if (
                            target.hwnd is not None
                            and current_focus_rect != expected_rect
                        ):
                            raise RuntimeError(
                                "录制期间游戏窗口被移动、缩放或最小化；"
                                "为避免无效画面，本次录制已作废"
                            )
                        if current_process_id != focus_process_id:
                            raise RuntimeError(
                                "录制期间前台进程已离开游戏；"
                                "为避免录入桌面、弹窗或其他程序，本次录制已作废"
                            )
                        if (
                            current_focus_rect is None
                            or any(
                                ratio < MIN_CLEAN_DIMENSION_RATIO
                                for ratio in rectangle_dimension_coverage(
                                    current_focus_rect,
                                    target,
                                )
                            )
                        ):
                            raise RuntimeError(
                                "录制期间游戏画面覆盖录制区域的宽度或高度低于70%；"
                                "本次录制已作废"
                            )
                        last_window_check = captured_at
                    if captured_at - last_disk_check >= DISK_CHECK_INTERVAL_SECONDS:
                        self._check_disk_space(width, height)
                        last_disk_check = captured_at
                    if self.stop_event.is_set():
                        break
            finally:
                input_tracker.stop()
                capture_backend.close()
                capture_backend = None
                while encoder_thread.is_alive():
                    try:
                        frame_queue.put(None, timeout=0.2)
                        break
                    except queue.Full:
                        if encoder_errors:
                            break
                encoder_thread.join(timeout=60)
                if encoder_thread.is_alive():
                    encoder.abort()
                    encoder_thread.join(timeout=5)
                    raise RuntimeError("FFmpeg编码器停止超时")

            encoder = None
            if encoder_errors:
                raise RuntimeError(f"视频编码失败：{encoder_errors[0]}")

            segment_paths = sorted(
                self.save_directory.glob(f"{segment_prefix}_[0-9][0-9][0-9].mp4")
            )
            if not segment_paths:
                raise RuntimeError("录制结束，但没有生成视频切片")

            complete_segment_count = captured_frame_count // frames_per_segment
            incomplete_paths = segment_paths[complete_segment_count:]
            for incomplete_path in incomplete_paths:
                incomplete_path.unlink()

            if complete_segment_count == 0:
                finalizer.abort("录制不足一个完整切片")
                if finalizer.manifest_path.exists():
                    finalizer.manifest_path.unlink()
                self.results.put(
                    RecordingResult(
                        "discarded",
                        message=(
                            f"录制不足 {self.segment_seconds} 秒，"
                            "未完成的切片已删除"
                        ),
                    )
                )
                return

            if finalizer.failed.is_set():
                raise RuntimeError(
                    finalizer.errors[0]
                    if finalizer.errors
                    else "切片后台验收失败"
                )
            for segment_index in range(complete_segment_count):
                finalizer.schedule(segment_index)
            finalizer.finish()
            committed = list(finalizer.committed)
            if len(committed) != complete_segment_count:
                raise RuntimeError(
                    f"应提交 {complete_segment_count} 个切片，"
                    f"实际仅提交 {len(committed)} 个"
                )
            first_path = self.save_directory / str(committed[0]["video"])
            summary = f"共 {len(committed)} 组完整三件套"
            if incomplete_paths:
                summary += "，末尾不足时长的切片已删除"
            self.results.put(
                RecordingResult(
                    "saved",
                    first_path,
                    summary,
                )
            )
        except Exception as exc:
            failure_reason = str(exc)
            if encoder is not None:
                encoder.abort()
            if input_tracker is not None:
                input_tracker.stop()
            if capture_backend is not None:
                capture_backend.close()
            if finalizer is not None:
                finalizer.abort(failure_reason)
            committed_names: set[str] = set()
            committed_count = 0
            if finalizer is not None:
                committed_count = len(finalizer.committed)
                for record in finalizer.committed:
                    committed_names.update(
                        {
                            str(record["video"]),
                            str(record["operation_json"]),
                            str(record["keymap_json"]),
                        }
                    )
            if segment_prefix:
                for partial_path in self.save_directory.glob(f"{segment_prefix}_*"):
                    if (
                        partial_path.is_file()
                        and partial_path.name not in committed_names
                        and partial_path.name
                        != f"{segment_prefix}_manifest.json"
                        and partial_path.suffix in {
                        ".mp4",
                        ".json",
                        ".tmp",
                        }
                    ):
                        try:
                            partial_path.unlink()
                        except OSError:
                            pass
            retained = (
                f"；已保留前面 {committed_count} 组验收通过的三件套"
                if committed_count
                else ""
            )
            self.results.put(
                RecordingResult(
                    "error",
                    message=f"{failure_reason}{retained}",
                )
            )


class FloatingRecorder:
    def __init__(
        self,
        root: Tk,
        save_directory: Path,
        segment_seconds: int,
        session: SessionConfig,
        capture_target: CaptureTarget,
    ) -> None:
        self.root = root
        self.save_directory = save_directory
        self.segment_seconds = segment_seconds
        self.session = session
        self.capture_target = capture_target
        self.results: queue.Queue[RecordingResult] = queue.Queue()
        self.stop_event: threading.Event | None = None
        self.worker: threading.Thread | None = None
        self.started_at = 0.0
        self.arming_deadline = 0.0
        self.arming_generation = 0
        self.state = "idle"
        self.status_detail = ""
        self.close_after_stop = False
        self.press_mouse = (0, 0)
        self.press_window = (0, 0)
        self.was_dragged = False

        self._configure_window()
        self._build_button()
        self._build_menu()
        self._draw()
        self.root.after(100, self._poll_results)

    def _configure_window(self) -> None:
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT_COLOR)
        try:
            self.root.wm_attributes("-transparentcolor", TRANSPARENT_COLOR)
        except tk.TclError:
            pass

        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = max(12, screen_width - BUTTON_SIZE - 28)
        y = max(12, screen_height // 2 - BUTTON_SIZE // 2)
        self.root.geometry(f"{BUTTON_SIZE}x{BUTTON_SIZE}+{x}+{y}")
        self.root.protocol("WM_DELETE_WINDOW", self._request_exit)
        self.root.update_idletasks()
        self._make_no_activate()
        self._exclude_from_capture()

    def _make_no_activate(self) -> None:
        if sys.platform != "win32":
            return
        try:
            hwnd = wintypes.HWND(
                native_top_level_window_handle(self.root.winfo_id())
            )
            user32 = ctypes.windll.user32
            gwl_exstyle = -20
            ws_ex_toolwindow = 0x00000080
            ws_ex_noactivate = 0x08000000
            swp_nosize = 0x0001
            swp_nomove = 0x0002
            swp_nozorder = 0x0004
            swp_noactivate = 0x0010
            swp_framechanged = 0x0020
            getter = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            setter = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            getter.argtypes = [wintypes.HWND, ctypes.c_int]
            getter.restype = ctypes.c_ssize_t
            setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            setter.restype = ctypes.c_ssize_t
            current = getter(hwnd, gwl_exstyle)
            setter(
                hwnd,
                gwl_exstyle,
                current | ws_ex_toolwindow | ws_ex_noactivate,
            )
            user32.SetWindowPos.argtypes = [
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            ]
            user32.SetWindowPos.restype = wintypes.BOOL
            user32.SetWindowPos(
                hwnd,
                wintypes.HWND(0),
                0,
                0,
                0,
                0,
                swp_nosize
                | swp_nomove
                | swp_nozorder
                | swp_noactivate
                | swp_framechanged,
            )
        except (AttributeError, OSError, ValueError, ctypes.ArgumentError):
            pass

    def _exclude_from_capture(self) -> None:
        if sys.platform != "win32":
            return
        try:
            hwnd = native_top_level_window_handle(self.root.winfo_id())
            user32 = ctypes.windll.user32
            user32.SetWindowDisplayAffinity.argtypes = [
                wintypes.HWND,
                wintypes.DWORD,
            ]
            user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
            # WDA_EXCLUDEFROMCAPTURE, supported by modern Windows 10/11.
            if not user32.SetWindowDisplayAffinity(
                wintypes.HWND(hwnd),
                0x11,
            ):
                # Older Windows can at least replace the window with a blank area.
                user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), 0x01)
        except (AttributeError, OSError, ValueError, ctypes.ArgumentError):
            pass

    def _build_button(self) -> None:
        self.canvas = tk.Canvas(
            self.root,
            width=BUTTON_SIZE,
            height=BUTTON_SIZE,
            bg=TRANSPARENT_COLOR,
            highlightthickness=0,
            cursor="hand2",
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<ButtonRelease-3>", self._show_menu)

    def _build_menu(self) -> None:
        self.menu = Menu(self.root, tearoff=False)
        self.menu.add_command(label="打开保存目录", command=self._open_save_directory)
        self.menu.add_command(label="更换保存目录", command=self._change_save_directory)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self._request_exit)

    def _draw(self) -> None:
        self.canvas.delete("all")
        self.canvas.create_oval(7, 8, 79, 80, fill="#000000", outline="", stipple="gray50")

        if self.state == "idle":
            color, hover, text, detail = "#2563EB", "#3B82F6", "●", "录制"
        elif self.state == "arming":
            remaining = max(
                1,
                int(self.arming_deadline - time.monotonic() + 0.999),
            )
            color, hover, text, detail = "#D97706", "#F59E0B", str(remaining), "切回游戏"
        elif self.state == "recording":
            color, hover, text, detail = "#DC2626", "#EF4444", "■", self._elapsed_text()
        else:
            color, hover, text, detail = (
                "#6B7280",
                "#6B7280",
                "…",
                self.status_detail or "验收",
            )

        self.button_color = color
        self.button_hover = hover
        self.circle = self.canvas.create_oval(
            3, 3, 77, 77, fill=color, outline="#FFFFFF", width=2
        )
        self.canvas.create_text(
            40,
            31,
            text=text,
            fill="#FFFFFF",
            font=("Microsoft YaHei UI", 17, "bold"),
        )
        self.canvas.create_text(
            40,
            56,
            text=detail,
            fill="#FFFFFF",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.canvas.tag_bind(self.circle, "<Enter>", self._on_hover)
        self.canvas.tag_bind(self.circle, "<Leave>", self._on_leave)

    def _elapsed_text(self) -> str:
        elapsed = max(0, int(time.monotonic() - self.started_at))
        minutes, seconds = divmod(elapsed, 60)
        if minutes < 100:
            return f"{minutes:02d}:{seconds:02d}"
        return f"{minutes // 60:d}h{minutes % 60:02d}"

    def _on_hover(self, _event: tk.Event) -> None:
        self.canvas.itemconfigure(self.circle, fill=self.button_hover)

    def _on_leave(self, _event: tk.Event) -> None:
        self.canvas.itemconfigure(self.circle, fill=self.button_color)

    def _on_press(self, event: tk.Event) -> None:
        self.press_mouse = (event.x_root, event.y_root)
        self.press_window = (self.root.winfo_x(), self.root.winfo_y())
        self.was_dragged = False

    def _on_drag(self, event: tk.Event) -> None:
        dx = event.x_root - self.press_mouse[0]
        dy = event.y_root - self.press_mouse[1]
        if abs(dx) >= DRAG_THRESHOLD or abs(dy) >= DRAG_THRESHOLD:
            self.was_dragged = True
        if self.was_dragged:
            x = self.press_window[0] + dx
            y = self.press_window[1] + dy
            self.root.geometry(f"+{x}+{y}")

    def _on_release(self, _event: tk.Event) -> None:
        if not self.was_dragged and self.state != "stopping":
            self._toggle_recording()

    def _show_menu(self, event: tk.Event) -> None:
        if self.state != "idle":
            return
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _toggle_recording(self) -> None:
        if self.state == "idle":
            self._start_recording()
        elif self.state == "arming":
            self._cancel_arming()
        elif self.state == "recording":
            self._stop_recording()

    def _start_recording(self) -> None:
        focus_hwnd = foreground_window()
        expected_process = self.capture_target.process_id
        if self.capture_target.hwnd is not None:
            if not window_meets_capture_dimensions(
                focus_hwnd,
                self.capture_target,
                expected_process_id=expected_process,
                excluded_process_id=os.getpid(),
            ):
                messagebox.showinfo(
                    "请先切回游戏",
                    (
                        "当前前台游戏窗口覆盖录制区域的宽度或高度不足70%。\n"
                        "请最大化已选择的游戏窗口后再开始录制。"
                    ),
                    parent=self.root,
                )
                return
            self._begin_recording()
            return

        if window_meets_capture_dimensions(
            focus_hwnd,
            self.capture_target,
            excluded_process_id=os.getpid(),
        ):
            self._begin_recording()
        else:
            self._arm_recording()

    def _arm_recording(self) -> None:
        """Wait non-modally for a large foreground game window."""
        self.arming_generation += 1
        generation = self.arming_generation
        self.arming_deadline = time.monotonic() + ARMING_TIMEOUT_SECONDS
        self.status_detail = "切回游戏"
        self.state = "arming"
        self._draw()
        self.root.after(
            ARMING_POLL_MILLISECONDS,
            lambda: self._poll_arming(generation),
        )

    def _poll_arming(self, generation: int) -> None:
        if self.state != "arming" or generation != self.arming_generation:
            return
        if window_meets_capture_dimensions(
            foreground_window(),
            self.capture_target,
            excluded_process_id=os.getpid(),
        ):
            self._begin_recording()
            return
        if time.monotonic() >= self.arming_deadline:
            self._cancel_arming()
            self._show_discarded_notice(
                "10秒内未检测到宽、高均达到70%的前台游戏画面"
            )
            return
        self._draw()
        self.root.after(
            ARMING_POLL_MILLISECONDS,
            lambda: self._poll_arming(generation),
        )

    def _cancel_arming(self) -> None:
        self.arming_generation += 1
        self.arming_deadline = 0.0
        self.status_detail = ""
        self.state = "idle"
        self._draw()

    def _begin_recording(self) -> None:
        if self.state == "recording":
            return
        self.arming_generation += 1
        self.stop_event = threading.Event()
        capture = ScreenCapture(
            self.save_directory,
            self.segment_seconds,
            self.session,
            self.capture_target,
            self.stop_event,
            self.results,
            native_top_level_window_handle(self.root.winfo_id()),
        )
        self.worker = threading.Thread(
            target=capture.run,
            name="screen-capture",
            daemon=True,
        )
        self.started_at = time.monotonic()
        self.status_detail = ""
        self.state = "recording"
        self._draw()
        self.worker.start()
        self._update_elapsed()

    def _stop_recording(self) -> None:
        if self.stop_event is None:
            return
        self.state = "stopping"
        self.status_detail = "验收中"
        self.stop_event.set()
        self._draw()

    def _update_elapsed(self) -> None:
        if self.state != "recording":
            return
        self._draw()
        self.root.after(500, self._update_elapsed)

    def _poll_results(self) -> None:
        try:
            while True:
                result = self.results.get_nowait()
                if result.kind == "progress":
                    if self.state == "stopping":
                        self.status_detail = (
                            f"已完成{result.path.stem.rsplit('_', 1)[-1]}"
                            if result.path is not None
                            else "验收中"
                        )
                        self._draw()
                    continue
                self.worker = None
                self.stop_event = None
                self.state = "idle"
                self.status_detail = ""
                self._draw()

                if self.close_after_stop:
                    self.root.destroy()
                    return
                if result.kind == "saved" and result.path is not None:
                    self._show_saved_notice(result.path, result.message)
                elif result.kind == "discarded":
                    self._show_discarded_notice(result.message)
                else:
                    messagebox.showerror(
                        "录屏失败",
                        f"录制过程中发生错误：\n{result.message}",
                        parent=self.root,
                    )
        except queue.Empty:
            pass
        self.root.after(100, self._poll_results)

    def _show_saved_notice(self, path: Path, summary: str) -> None:
        notice = tk.Toplevel(self.root)
        notice.overrideredirect(True)
        notice.attributes("-topmost", True)
        notice.configure(bg="#111827")
        x = max(10, self.root.winfo_x() - 205)
        y = self.root.winfo_y() + 12
        notice.geometry(f"200x60+{x}+{y}")
        tk.Label(
            notice,
            text=f"录制已保存（{summary}）\n{path.name}",
            bg="#111827",
            fg="#FFFFFF",
            font=("Microsoft YaHei UI", 9),
            justify="left",
        ).pack(fill="both", expand=True, padx=10, pady=7)
        notice.after(2600, notice.destroy)

    def _show_discarded_notice(self, message: str) -> None:
        notice = tk.Toplevel(self.root)
        notice.overrideredirect(True)
        notice.attributes("-topmost", True)
        notice.configure(bg="#7F1D1D")
        x = max(10, self.root.winfo_x() - 205)
        y = self.root.winfo_y() + 12
        notice.geometry(f"200x60+{x}+{y}")
        tk.Label(
            notice,
            text=message,
            bg="#7F1D1D",
            fg="#FFFFFF",
            font=("Microsoft YaHei UI", 9),
            wraplength=180,
            justify="left",
        ).pack(fill="both", expand=True, padx=10, pady=7)
        notice.after(3000, notice.destroy)

    def _open_save_directory(self) -> None:
        try:
            os.startfile(self.save_directory)  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            subprocess.Popen(["explorer", str(self.save_directory)])

    def _change_save_directory(self) -> None:
        if self.state != "idle":
            messagebox.showinfo("正在录制", "请先停止录制，再更换保存目录。", parent=self.root)
            return
        selected = filedialog.askdirectory(
            title="选择录屏保存目录",
            initialdir=str(self.save_directory),
            mustexist=True,
            parent=self.root,
        )
        if selected:
            self.save_directory = Path(selected)

    def _request_exit(self) -> None:
        if self.state == "stopping":
            self.close_after_stop = True
            return
        if self.state == "recording":
            should_exit = messagebox.askyesno(
                "结束录屏",
                "正在录屏。是否停止录制、保存视频并退出？",
                parent=self.root,
            )
            if not should_exit:
                return
            self.close_after_stop = True
            self._stop_recording()
            return
        self.root.destroy()


class StartupSettingsDialog(simpledialog.Dialog):
    def __init__(self, parent: Tk, capture_target: CaptureTarget) -> None:
        videos_directory = Path.home() / "Videos"
        default_directory = videos_directory if videos_directory.is_dir() else Path.home()
        self.path_variable = tk.StringVar(value=str(default_directory))
        self.segment_variable = tk.StringVar(value=str(DEFAULT_SEGMENT_SECONDS))
        self.path_entry: ttk.Entry
        self.result: StartupSettings | None = None
        self._validated_result: StartupSettings | None = None
        self.capture_target = capture_target
        super().__init__(parent, title="录屏设置")

    def body(self, master: tk.Frame) -> tk.Widget:
        master.configure(padx=14, pady=12)
        ttk.Label(
            master,
            text=f"录制范围：{self.capture_target.label}",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        ttk.Label(master, text="录屏保存路径：").grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(0, 4),
        )
        self.path_entry = ttk.Entry(
            master,
            width=62,
            textvariable=self.path_variable,
        )
        self.path_entry.grid(row=2, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(master, text="浏览…", command=self._browse_directory).grid(
            row=2,
            column=1,
            sticky="ew",
        )

        ttk.Label(
            master,
            text=(
                "每个视频切片时长（秒）："
                f"{MIN_SEGMENT_SECONDS}–{MAX_SEGMENT_SECONDS}，默认 "
                f"{DEFAULT_SEGMENT_SECONDS}"
            ),
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(14, 4))
        tk.Spinbox(
            master,
            from_=MIN_SEGMENT_SECONDS,
            to=MAX_SEGMENT_SECONDS,
            increment=30,
            width=15,
            textvariable=self.segment_variable,
        ).grid(row=4, column=0, sticky="w")

        ttk.Label(
            master,
            text="确认后将继续设置游戏名称和键位映射。",
            foreground="#4B5563",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(14, 0))
        master.grid_columnconfigure(0, weight=1)
        return self.path_entry

    def buttonbox(self) -> None:
        box = ttk.Frame(self)
        ttk.Button(box, text="确定", width=12, command=self.ok).pack(
            side=tk.LEFT,
            padx=6,
        )
        ttk.Button(box, text="取消", width=12, command=self.cancel).pack(
            side=tk.LEFT,
            padx=6,
        )
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)
        box.pack(pady=(0, 12))

    def _browse_directory(self) -> None:
        current = Path(self.path_variable.get().strip().strip('"')).expanduser()
        initial_directory = current if current.is_dir() else Path.home()
        selected = filedialog.askdirectory(
            title="选择录屏保存路径",
            initialdir=str(initial_directory),
            mustexist=True,
            parent=self,
        )
        if selected:
            self.path_variable.set(selected)
            self.path_entry.icursor("end")

    def validate(self) -> bool:
        try:
            self._validated_result = parse_startup_settings(
                self.path_variable.get(),
                self.segment_variable.get(),
            )
        except ValueError as exc:
            messagebox.showerror("录屏设置错误", str(exc), parent=self)
            return False
        return True

    def apply(self) -> None:
        self.result = self._validated_result


def select_startup_settings(
    root: Tk,
    capture_target: CaptureTarget,
) -> StartupSettings | None:
    dialog = StartupSettingsDialog(root, capture_target)
    return dialog.result


def capture_target_for_point(
    monitors: list[dict[str, int]],
    cursor_position: tuple[int, int] | None,
) -> CaptureTarget | None:
    if not monitors:
        return None
    selected_index = 1
    if cursor_position is not None:
        cursor_x, cursor_y = cursor_position
        for index, monitor in enumerate(monitors, start=1):
            left = int(monitor["left"])
            top = int(monitor["top"])
            if (
                left <= cursor_x < left + int(monitor["width"])
                and top <= cursor_y < top + int(monitor["height"])
            ):
                selected_index = index
                break
    monitor = monitors[selected_index - 1]
    return CaptureTarget(
        label=(
            f"当前显示器 {selected_index} "
            f"{monitor['width']}×{monitor['height']}"
        ),
        monitor_index=selected_index,
        left=int(monitor["left"]),
        top=int(monitor["top"]),
        width=int(monitor["width"]),
        height=int(monitor["height"]),
    )


def select_capture_target(root: Tk) -> CaptureTarget | None:
    with mss.mss() as capture:
        monitors = [dict(monitor) for monitor in capture.monitors[1:]]
    if not monitors:
        messagebox.showerror("显示器错误", "没有检测到可录制的显示器。", parent=root)
        return None
    cursor_position: tuple[int, int] | None = None
    if sys.platform == "win32":
        point = wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            cursor_position = int(point.x), int(point.y)
    target = capture_target_for_point(monitors, cursor_position)
    if target is None:
        messagebox.showerror("显示器错误", "无法确定当前显示器。", parent=root)
        return None
    if target.width < MIN_WIDTH or target.height < MIN_HEIGHT:
        messagebox.showerror(
            "录制区域不合格",
            (
                f"鼠标所在显示器的分辨率为 {target.width}×{target.height}，"
                f"低于 {MIN_WIDTH}×{MIN_HEIGHT}。"
            ),
            parent=root,
        )
        return None
    return target


_CAPTURE_MODIFIER_ORDER = ("Ctrl", "Alt", "Shift", "Win")
_CAPTURE_MOUSE_BUTTONS = {
    1: "leftClick",
    2: "middleClick",
    3: "rightClick",
    4: "mouseButton4",
    5: "mouseButton5",
}


def _captured_tk_key_name(keysym: str, keycode: int) -> str | None:
    """Convert one Tk key event to the recorder's canonical input name."""

    tkinter_aliases = {
        "shift_l": "Shift",
        "shift_r": "Shift",
        "control_l": "Ctrl",
        "control_r": "Ctrl",
        "alt_l": "Alt",
        "alt_r": "Alt",
        "meta_l": "Win",
        "meta_r": "Win",
        "super_l": "Win",
        "super_r": "Win",
        "kp_enter": "NumPadEnter",
        "kp_add": "NumPadAdd",
        "kp_subtract": "NumPadSubtract",
        "kp_multiply": "NumPadMultiply",
        "kp_divide": "NumPadDivide",
        "kp_decimal": "NumPadDecimal",
        "equal": "=",
        "bracketleft": "[",
        "bracketright": "]",
        "backslash": "\\",
        "grave": "Tilde",
        "quoteleft": "Tilde",
        "apostrophe": "'",
        "quoteright": "'",
    }
    folded = keysym.casefold()
    if folded in tkinter_aliases:
        return tkinter_aliases[folded]
    numpad = re.fullmatch(r"kp_([0-9])", folded)
    if numpad:
        return f"NumPad{numpad.group(1)}"
    if sys.platform == "win32" and 0 < keycode <= 0xFF:
        captured = InputEventTracker._key_name(keycode, 0, 0)
        if captured and not captured.startswith("VK_"):
            return captured
    canonical = _canonical_input_name(keysym)
    if canonical is not None and canonical[1] == "keyboard":
        return canonical[0]
    return None


def _compose_captured_input(
    base_input: str,
    modifiers: set[str] | frozenset[str],
) -> str:
    if base_input in _CAPTURE_MODIFIER_ORDER:
        return base_input
    ordered = [
        modifier
        for modifier in _CAPTURE_MODIFIER_ORDER
        if modifier in modifiers
    ]
    return "+".join([*ordered, base_input])


class MappingCaptureDialog(simpledialog.Dialog):
    """Capture a physical key/button and a manually entered action semantic."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        initial_input: str = "",
        initial_device: str = "keyboard",
        initial_action: str = "",
        title: str = "新增键位映射",
    ) -> None:
        self.initial_input = initial_input
        self.initial_device = initial_device
        self.initial_action = initial_action
        self.captured_input = initial_input
        self.captured_device = initial_device
        self.result: CapturedMapping | None = None
        self._capture_active = False
        self._pressed_modifiers: set[str] = set()
        self.capture_var: tk.StringVar
        self.action_entry: tk.Entry
        self.capture_entry: tk.Entry
        super().__init__(parent, title=title)

    def body(self, master: tk.Frame) -> tk.Widget:
        tk.Label(master, text="按键").grid(
            row=0,
            column=0,
            sticky="w",
            padx=(10, 6),
            pady=(10, 4),
        )
        tk.Label(master, text="动作语义").grid(
            row=0,
            column=1,
            sticky="w",
            padx=(6, 10),
            pady=(10, 4),
        )
        self.capture_var = tk.StringVar(
            master=master,
            value=self.initial_input or "点击此框后按下按键",
        )
        self.capture_entry = tk.Entry(
            master,
            textvariable=self.capture_var,
            width=28,
            state="readonly",
            readonlybackground="white",
            cursor="hand2",
            justify="center",
        )
        self.capture_entry.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=(10, 6),
            pady=(0, 4),
        )
        self.action_entry = tk.Entry(master, width=36)
        self.action_entry.insert(0, self.initial_action)
        self.action_entry.grid(
            row=1,
            column=1,
            sticky="ew",
            padx=(6, 10),
            pady=(0, 4),
        )
        tk.Label(
            master,
            text=(
                "点击左框进入捕获状态；可按键盘键、组合键，或再次点击/滚动鼠标。"
                "右框请填写当前游戏中的中文动作语义。"
            ),
            foreground="#6B7280",
            justify="left",
            wraplength=560,
        ).grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="w",
            padx=10,
            pady=(2, 10),
        )
        master.grid_columnconfigure(0, weight=1)
        master.grid_columnconfigure(1, weight=1)
        self.capture_entry.bind("<ButtonPress>", self._capture_mouse_button)
        self.capture_entry.bind("<MouseWheel>", self._capture_mouse_wheel)
        self.capture_entry.bind("<KeyPress>", self._capture_key_press)
        self.capture_entry.bind("<KeyRelease>", self._capture_key_release)
        return self.action_entry

    def buttonbox(self) -> None:
        box = tk.Frame(self)
        tk.Button(
            box,
            text="确定",
            width=12,
            command=self.ok,
            default=tk.ACTIVE,
        ).pack(side=tk.LEFT, padx=5, pady=(4, 10))
        tk.Button(
            box,
            text="取消",
            width=12,
            command=self.cancel,
        ).pack(side=tk.LEFT, padx=5, pady=(4, 10))
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)
        box.pack()

    def _begin_capture(self) -> None:
        self._capture_active = True
        self._pressed_modifiers.clear()
        self.capture_var.set("请按下按键…")
        self.capture_entry.focus_set()

    def _finish_capture(self, input_name: str, device_type: str) -> None:
        self.captured_input = input_name
        self.captured_device = device_type
        self._capture_active = False
        self._pressed_modifiers.clear()
        self.capture_var.set(input_name)
        self.action_entry.focus_set()

    def _capture_key_press(self, event: tk.Event) -> str:
        if not self._capture_active:
            self._begin_capture()
        key_name = _captured_tk_key_name(
            str(getattr(event, "keysym", "")),
            int(getattr(event, "keycode", 0) or 0),
        )
        if key_name is None:
            self.bell()
            return "break"
        if key_name in _CAPTURE_MODIFIER_ORDER:
            self._pressed_modifiers.add(key_name)
            ordered = [
                modifier
                for modifier in _CAPTURE_MODIFIER_ORDER
                if modifier in self._pressed_modifiers
            ]
            self.capture_var.set("+".join([*ordered, "…"]))
            return "break"
        self._finish_capture(
            _compose_captured_input(key_name, self._pressed_modifiers),
            "keyboard",
        )
        return "break"

    def _capture_key_release(self, event: tk.Event) -> str:
        if not self._capture_active:
            return "break"
        key_name = _captured_tk_key_name(
            str(getattr(event, "keysym", "")),
            int(getattr(event, "keycode", 0) or 0),
        )
        if (
            key_name in _CAPTURE_MODIFIER_ORDER
            and self._pressed_modifiers == {key_name}
        ):
            self._finish_capture(key_name, "keyboard")
        elif key_name in _CAPTURE_MODIFIER_ORDER:
            self._pressed_modifiers.discard(key_name)
        return "break"

    def _capture_mouse_button(self, event: tk.Event) -> str:
        if not self._capture_active:
            self._begin_capture()
            return "break"
        button = _CAPTURE_MOUSE_BUTTONS.get(
            int(getattr(event, "num", 0) or 0)
        )
        if button is None:
            self.bell()
            return "break"
        self._finish_capture(
            _compose_captured_input(button, self._pressed_modifiers),
            "mouse",
        )
        return "break"

    def _capture_mouse_wheel(self, event: tk.Event) -> str:
        if not self._capture_active:
            self._begin_capture()
            return "break"
        delta = int(getattr(event, "delta", 0) or 0)
        if delta == 0:
            return "break"
        button = "mouseWheelUp" if delta > 0 else "mouseWheelDown"
        self._finish_capture(
            _compose_captured_input(button, self._pressed_modifiers),
            "mouse",
        )
        return "break"

    def validate(self) -> bool:
        action = self.action_entry.get().strip()
        if not self.captured_input:
            messagebox.showerror(
                "尚未记录按键",
                "请点击左侧按键框，然后按下需要记录的按键。",
                parent=self,
            )
            return False
        if not action:
            messagebox.showerror(
                "动作语义为空",
                "请在右侧输入该按键对应的中文动作语义。",
                parent=self,
            )
            return False
        self.result = CapturedMapping(
            self.captured_input,
            self.captured_device,
            action,
        )
        return True

    def apply(self) -> None:
        return


class SessionConfigDialog(simpledialog.Dialog):
    def __init__(self, parent: Tk) -> None:
        self.game_title_entry: tk.Entry
        self.keymap_tree: ttk.Treeview
        self.result: SessionConfig | None = None
        self._keymap_needs_manual_confirmation = False
        super().__init__(parent, title="设置游戏信息与按键映射")

    def body(self, master: tk.Frame) -> tk.Widget:
        tk.Label(
            master,
            text="游戏名称（必填）：",
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 3))
        self.game_title_entry = tk.Entry(master, width=68)
        self.game_title_entry.grid(row=1, column=0, sticky="ew", padx=8)

        tk.Label(
            master,
            text=(
                "按键映射（动作语义必须按当前游戏修改；"
                "移动方向可选 W/B/L/R）："
            ),
            anchor="w",
            justify="left",
        ).grid(row=2, column=0, sticky="ew", padx=8, pady=(10, 3))
        tk.Label(
            master,
            text=(
                "提示：动作语义必须全部使用中文。自动识别或导入的英文名称需逐项翻译；"
                "按键名（如 W、F1、Space）、设备和移动方向无需翻译。"
            ),
            anchor="w",
            justify="left",
            foreground="#9A3412",
            wraplength=760,
        ).grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 5))
        table_frame = tk.Frame(master)
        table_frame.grid(
            row=4,
            column=0,
            sticky="nsew",
            padx=8,
            pady=(0, 5),
        )
        columns = ("input", "type", "movement", "action")
        self.keymap_tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=17,
        )
        for column, title, width in (
            ("input", "输入", 135),
            ("type", "设备", 90),
            ("movement", "移动方向", 80),
            ("action", "动作语义", 320),
        ):
            self.keymap_tree.heading(column, text=title)
            self.keymap_tree.column(column, width=width, anchor="w")
        scrollbar = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.keymap_tree.yview,
        )
        self.keymap_tree.configure(yscrollcommand=scrollbar.set)
        self.keymap_tree.pack(side=tk.LEFT, fill="both", expand=True)
        scrollbar.pack(side=tk.RIGHT, fill="y")
        self.keymap_tree.bind("<Double-Button-1>", self._edit_selected_mapping)

        editor = tk.Frame(master)
        editor.grid(row=5, column=0, sticky="ew", padx=8, pady=(0, 5))
        tk.Button(
            editor,
            text="新增键位",
            width=12,
            command=self._open_add_mapping,
        ).pack(side=tk.LEFT, padx=(0, 5))
        tk.Button(
            editor,
            text="编辑所选",
            width=12,
            command=self._edit_selected_mapping,
        ).pack(side=tk.LEFT, padx=(0, 5))
        tk.Button(
            editor,
            text="删除",
            width=10,
            command=self._delete_mapping,
        ).pack(side=tk.LEFT)
        tk.Label(
            editor,
            text="提示：双击表格中的键位也可编辑。",
            foreground="#6B7280",
        ).pack(side=tk.LEFT, padx=(12, 0))
        self._set_keymap(DEFAULT_KEYMAP)
        master.grid_columnconfigure(0, weight=1)
        master.grid_rowconfigure(4, weight=1)
        return self.game_title_entry

    def buttonbox(self) -> None:
        box = tk.Frame(self)
        tk.Button(
            box,
            text="从游戏目录获取",
            width=14,
            command=self._discover_from_game_directory,
        ).pack(side=tk.LEFT, padx=5, pady=8)
        tk.Button(
            box,
            text="导入Keymap",
            width=12,
            command=self._import_keymap,
        ).pack(side=tk.LEFT, padx=5, pady=8)
        tk.Button(
            box,
            text="导出Keymap",
            width=12,
            command=self._export_keymap,
        ).pack(side=tk.LEFT, padx=5, pady=8)
        tk.Button(
            box,
            text="WASD模板",
            width=10,
            command=lambda: self._set_keymap(DEFAULT_KEYMAP),
        ).pack(side=tk.LEFT, padx=5, pady=8)
        tk.Button(
            box,
            text="方向键模板",
            width=10,
            command=lambda: self._set_keymap(ARROW_KEY_KEYMAP),
        ).pack(side=tk.LEFT, padx=5, pady=8)
        tk.Button(
            box,
            text="确定",
            width=12,
            command=self.ok,
            default=tk.ACTIVE,
        ).pack(side=tk.LEFT, padx=5, pady=8)
        tk.Button(
            box,
            text="取消",
            width=12,
            command=self.cancel,
        ).pack(side=tk.LEFT, padx=5, pady=8)
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)
        box.pack()

    def _discover_from_game_directory(self) -> None:
        selected = filedialog.askdirectory(
            title="选择游戏安装目录",
            mustexist=True,
            parent=self,
        )
        if not selected:
            return
        directory = Path(selected)
        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            discovery = discover_keymap_from_game_directory(directory)
        except (OSError, ValueError) as exc:
            messagebox.showerror("键位检测失败", str(exc), parent=self)
            return
        finally:
            self.configure(cursor="")
        if not discovery.keymap and not discovery.recognized_config:
            self._keymap_needs_manual_confirmation = True
            game_title_entry = getattr(self, "game_title_entry", None)
            if (
                game_title_entry is not None
                and not game_title_entry.get().strip()
            ):
                game_title_entry.insert(0, directory.name)
            limit_notice = (
                "\n部分搜索因时间或数量安全限制提前结束。"
                if discovery.truncated
                else ""
            )
            if discovery.notice:
                guidance = discovery.notice
            elif discovery.requires_game_launch:
                guidance = discovery.notice or (
                    "已识别该游戏的键位存储格式，但当前用户尚未生成可读取的键位配置。\n"
                    "请先启动游戏、进入按键设置，实际修改并应用至少一个键位后正常退出，"
                    "然后重新选择游戏目录。"
                )
            else:
                guidance = (
                    "没有在所选游戏目录或玩家配置位置中找到可解析的键位配置。\n"
                    "该游戏可能使用二进制/加密格式。请保留模板并手动修改，"
                    "也可以使用“导入Keymap”。"
                )
            player_source_paths = [
                record.physical_path
                for record in discovery.source_records
                if record.kind in {
                    "verified_player",
                    "unmapped_player_file",
                    "invalid_player_config",
                    "unapplied_player_override",
                    "unverified_player_override",
                }
            ]
            if discovery.has_recognized_player_file and player_source_paths:
                scan_summary = (
                    "已解析玩家配置文件："
                    f"{player_source_paths[-1]}"
                )
            elif discovery.has_recognized_player_file:
                scan_summary = "已识别玩家配置状态，但没有可载入的完整玩家键位。"
            else:
                scan_summary = (
                    f"已解析 {discovery.scanned_files} 个候选文件或可执行文件"
                    "（该数字不是玩家配置文件数量）。"
                )
            unchanged_notice = (
                "\n\n本次检测没有修改当前键位表。当前显示内容可能仍是通用模板，"
                "不能视为已经读取到该游戏的实际键位；请导入 Keymap 或逐项人工核对。"
            )
            warning_title = (
                "玩家配置中没有已保存的自定义键位"
                if discovery.has_recognized_player_file
                else "未找到玩家键位配置"
            )
            messagebox.showwarning(
                warning_title,
                (
                    f"{guidance}\n\n"
                    f"{scan_summary}"
                    f"{unchanged_notice}"
                    f"{limit_notice}"
                ),
                parent=self,
            )
            return
        requires_player_confirmation = (
            _keymap_requires_player_config_confirmation(directory, discovery)
        )
        if requires_player_confirmation:
            # The current table is not verified player state.  Keep the final
            # review gate enabled even when the user declines to load defaults.
            self._keymap_needs_manual_confirmation = True
            warning_detail = discovery.notice or (
                "当前只检测到安装目录或程序内置的默认键位。"
            )
            is_packaged_default = any(
                record.kind == "pak_default"
                for record in discovery.source_records
            )
            is_partial_player_delta = bool(
                discovery.binding_authority == KEYMAP_AUTHORITY_MIXED_UNVERIFIED
                and any(
                    record.kind == "unverified_player_override"
                    for record in discovery.source_records
                )
            )
            confirmation_title = (
                "仅检测到游戏打包默认键位"
                if is_packaged_default
                else "仅检测到不完整的玩家键位增量"
                if is_partial_player_delta
                else "未找到玩家实际键位"
            )
            source_description = (
                "工具已从经过完整性校验的游戏 Pak 安装包中读取默认键位，"
                "但这不是玩家实际改键记录。"
                if is_packaged_default
                else
                "工具已读取玩家 Input.ini 中明确保存的增量绑定，但没有找到"
                "完整 DefaultInput 基线，因此无法恢复未改动的键位。"
                if is_partial_player_delta
                else
                "当前未找到可验证的玩家实际键位配置（注册表、配置文件或云存档），"
                "工具只能载入安装目录或程序内置的默认键位。"
            )
            confirmation_question = (
                "是否仍要把这部分玩家键位增量合并到当前表格？"
                if is_partial_player_delta
                else "是否仍要载入默认键位？"
            )
            mapping_warning = (
                "该增量只包含玩家改动过的项目；直接使用会遗漏未改动作，导致"
                "生成的 JSON 动作与实际按键不对应。"
                if is_partial_player_delta
                else
                "默认键位可能与游戏中的实际设置不一致；直接开始录制可能导致"
                "生成的 JSON 动作与实际按键不对应。"
            )
            recommendation = (
                "建议点击“否”，改为导入完整 Keymap，或先手工补齐并逐项核对。"
                if is_partial_player_delta
                else
                "建议点击“否”，先启动游戏、进入按键设置，实际修改并应用至少一个键位后"
                "正常退出，再重新选择游戏目录。"
            )
            use_default_keymap = messagebox.askyesno(
                confirmation_title,
                (
                    f"{source_description}\n"
                    f"{mapping_warning}\n\n"
                    f"{recommendation}\n\n"
                    f"检测详情：{warning_detail}\n\n"
                    f"{confirmation_question}"
                ),
                icon=messagebox.WARNING,
                default=messagebox.NO,
                parent=self,
            )
            if not use_default_keymap:
                return
        try:
            current = self._keymap_document()
        except ValueError:
            current = {}
        applied = _apply_keymap_discovery(current, discovery)
        self._set_keymap(applied)
        # Accepting installation/default bindings only authorizes loading them
        # into the editor.  It does not prove that they match the player's
        # current in-game settings, so keep the final, default-NO manual review
        # gate enabled until validate().  A verified player config can clear it.
        self._keymap_needs_manual_confirmation = requires_player_confirmation
        if not self.game_title_entry.get().strip():
            self.game_title_entry.insert(0, directory.name)
        displayed_sources = _keymap_source_display_labels(directory, discovery)
        source_text = (
            "\n".join(f"• {source}" for source in displayed_sources)
            if displayed_sources
            else "• 未提供文件路径"
        )
        limit_notice = (
            "\n注意：部分候选来源因时间、数量、深度或文件大小安全上限未完成检查，"
            "结果可能不完整。"
            if discovery.truncated
            else ""
        )
        parser_notice = f"\n\n{discovery.notice}" if discovery.notice else ""
        if discovery.recognized_config and not discovery.keymap:
            if requires_player_confirmation:
                result_text = (
                    "已识别安装目录或程序内置的完整键位配置，但其中没有已绑定的"
                    "可识别键盘按键。\n"
                    "已按刚才的确认清空默认模板，请核对是否需要手动补充。"
                )
            else:
                result_text = (
                    "已识别当前玩家的完整键位配置，但其中没有已绑定的可识别键盘按键。\n"
                    "已按玩家配置清空默认模板，请确认是否需要手动补充。"
                )
        else:
            application = "替换" if discovery.recognized_config else "合并到"
            result_text = (
                f"检测到 {len(discovery.keymap)} 个输入映射，已{application}当前表格。\n"
                "请在开始录制前核对实际键位与动作语义；"
                "如仍有英文动作名称，请先改为中文。"
            )
        messagebox.showinfo(
            "键位检测完成",
            (
                f"{result_text}\n\n"
                f"配置来源：\n{source_text}{limit_notice}{parser_notice}"
            ),
            parent=self,
        )

    def _import_keymap(self) -> None:
        selected = filedialog.askopenfilename(
            title="导入Keymap JSON",
            filetypes=[("JSON文件", "*.json"), ("所有文件", "*.*")],
            parent=self,
        )
        if not selected:
            return
        try:
            raw = _read_bounded_bytes(Path(selected), MAX_GAME_CONFIG_FILE_BYTES)
            _validate_pixpil_json_depth(raw)
            document = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(document, dict):
                raise ValueError("顶层必须是JSON对象")
        except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
            messagebox.showerror("导入失败", str(exc), parent=self)
            return
        self._set_keymap(document)
        self._keymap_needs_manual_confirmation = False

    def _export_keymap(self) -> None:
        try:
            document = self._keymap_document()
        except ValueError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        selected = filedialog.asksaveasfilename(
            title="导出Keymap JSON",
            defaultextension=".json",
            filetypes=[("JSON文件", "*.json")],
            parent=self,
        )
        if not selected:
            return
        try:
            atomic_write_json(Path(selected), document)
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    def _set_keymap(self, document: dict[object, object]) -> None:
        for item in self.keymap_tree.get_children():
            self.keymap_tree.delete(item)
        for raw_key, raw_value in document.items():
            if not isinstance(raw_key, str) or not isinstance(raw_value, dict):
                continue
            self.keymap_tree.insert(
                "",
                "end",
                iid=raw_key,
                values=(
                    raw_key,
                    raw_value.get("type", ""),
                    raw_value.get("movement_direction", ""),
                    raw_value.get("action", ""),
                ),
            )

    def _keymap_document(self) -> dict[str, dict[str, str]]:
        document: dict[str, dict[str, str]] = {}
        for item in self.keymap_tree.get_children():
            key, device_type, movement, action = self.keymap_tree.item(
                item,
                "values",
            )
            key = str(key).strip()
            device_type = str(device_type)
            action = str(action).strip()
            if not key or device_type not in {"keyboard", "mouse", "gamepad"}:
                raise ValueError(f"输入 {key!r} 的设备类型无效")
            if not action:
                raise ValueError(f"输入 {key!r} 缺少动作语义")
            mapping = {"type": device_type, "action": action}
            if movement:
                if movement not in {"W", "B", "L", "R"}:
                    raise ValueError(f"输入 {key!r} 的移动方向无效")
                mapping["movement_direction"] = str(movement)
            document[key] = mapping
        if not document:
            raise ValueError("Keymap不能为空")
        return document

    def _open_add_mapping(self) -> None:
        dialog = MappingCaptureDialog(self)
        if dialog.result is not None:
            self._store_captured_mapping(dialog.result)

    def _store_captured_mapping(
        self,
        captured: CapturedMapping,
        *,
        previous_input: str = "",
        movement: str = "",
    ) -> None:
        key = captured.input_name
        if self.keymap_tree.exists(key) and key != previous_input:
            overwrite = messagebox.askyesno(
                "按键已经存在",
                f"按键 {key} 已存在映射，是否覆盖原有动作语义？",
                icon=messagebox.WARNING,
                default=messagebox.NO,
                parent=self,
            )
            if not overwrite:
                return
        values = (key, captured.device_type, movement, captured.action)
        if previous_input and previous_input != key:
            if self.keymap_tree.exists(key):
                self.keymap_tree.delete(key)
            if self.keymap_tree.exists(previous_input):
                self.keymap_tree.delete(previous_input)
        if self.keymap_tree.exists(key):
            self.keymap_tree.item(key, values=values)
        else:
            self.keymap_tree.insert("", "end", iid=key, values=values)

    def _delete_mapping(self) -> None:
        for item in self.keymap_tree.selection():
            self.keymap_tree.delete(item)

    def _edit_selected_mapping(self, _event: tk.Event | None = None) -> None:
        selected = self.keymap_tree.selection()
        if not selected:
            if _event is None:
                messagebox.showinfo(
                    "尚未选择键位",
                    "请先在表格中选择需要编辑的键位。",
                    parent=self,
                )
            return
        key, device_type, movement, action = self.keymap_tree.item(
            selected[0],
            "values",
        )
        dialog = MappingCaptureDialog(
            self,
            initial_input=str(key),
            initial_device=str(device_type),
            initial_action=str(action),
            title="编辑键位映射",
        )
        if dialog.result is not None:
            self._store_captured_mapping(
                dialog.result,
                previous_input=str(key),
                movement=str(movement),
            )

    def _focus_mapping(self, input_name: str) -> None:
        if not self.keymap_tree.exists(input_name):
            return
        self.keymap_tree.selection_set(input_name)
        self.keymap_tree.focus(input_name)
        self.keymap_tree.see(input_name)

    def validate(self) -> bool:
        game_title = self.game_title_entry.get().strip()
        if not game_title:
            messagebox.showerror("配置错误", "请填写游戏名称。", parent=self)
            return False
        try:
            parsed = self._keymap_document()
        except ValueError as exc:
            messagebox.showerror("Keymap格式错误", str(exc), parent=self)
            return False
        normalized: dict[str, dict[str, str]] = {}
        for raw_key, raw_value in parsed.items():
            if (
                not isinstance(raw_key, str)
                or not isinstance(raw_value, dict)
                or raw_value.get("type") not in {"keyboard", "mouse", "gamepad"}
                or not isinstance(raw_value.get("action"), str)
                or not raw_value["action"].strip()
            ):
                messagebox.showerror(
                    "Keymap 格式错误",
                    (
                        f"按键 {raw_key!r} 的值必须包含 type"
                        "（keyboard/mouse/gamepad）和非空 action。"
                    ),
                    parent=self,
                )
                return False
            key = raw_key.strip()
            if raw_value["type"] == "keyboard" and len(key) == 1:
                key = key.upper()
            if key in normalized:
                messagebox.showerror(
                    "Keymap格式错误",
                    f"输入 {key!r} 重复。",
                    parent=self,
                )
                return False
            normalized[key] = {
                "type": str(raw_value["type"]),
                "action": str(raw_value["action"]).strip(),
            }
            movement_direction = raw_value.get("movement_direction")
            if movement_direction is not None:
                if movement_direction not in {"W", "B", "L", "R"}:
                    messagebox.showerror(
                        "Keymap 格式错误",
                        (
                            f"按键 {raw_key!r} 的 movement_direction "
                            "只能是 W、B、L 或 R。"
                        ),
                        parent=self,
                    )
                    return False
                normalized[key]["movement_direction"] = movement_direction
        # Use the table document here so the first invalid row can still be
        # focused when a one-character keyboard input is normalized to upper case.
        semantic_issues = _action_semantic_chinese_issues(parsed)
        if semantic_issues:
            self._focus_mapping(semantic_issues[0][0])
            displayed = semantic_issues[:8]
            issue_lines = [
                f"• {input_name}：{action}（{reason}）"
                for input_name, action, reason in displayed
            ]
            if len(semantic_issues) > len(displayed):
                issue_lines.append(
                    f"• ……另有 {len(semantic_issues) - len(displayed)} 项"
                )
            messagebox.showerror(
                "动作语义必须使用中文",
                (
                    "以下动作语义尚未完成中文化：\n\n"
                    + "\n".join(issue_lines)
                    + "\n\n请逐项改为中文后再点击“确定”。"
                    "按键名（如 W、F1、Space）无需修改。"
                ),
                parent=self,
            )
            return False
        if getattr(self, "_keymap_needs_manual_confirmation", False):
            confirmed = messagebox.askyesno(
                "确认已人工核对键位",
                (
                    "自动检测没有读取到已保存的游戏键位，当前表格可能仍是通用模板。\n"
                    "如果直接使用，录制 JSON 中的动作可能与游戏实际按键不对应。\n\n"
                    "是否确认你已经根据当前游戏逐项核对或修正了键位与中文动作语义？"
                ),
                icon=messagebox.WARNING,
                default=messagebox.NO,
                parent=self,
            )
            if not confirmed:
                return False
            self._keymap_needs_manual_confirmation = False
        self.result = SessionConfig(game_title, normalized)
        return True

    def apply(self) -> None:
        # validate() already stores the immutable result.
        return


def select_session_config(root: Tk) -> SessionConfig | None:
    dialog = SessionConfigDialog(root)
    return dialog.result


def main() -> None:
    # Used by the packaging smoke test. Normal users never set this variable.
    if os.environ.get("SCREEN_RECORDER_MEDIA_SMOKE_TEST") == "1":
        ffmpeg_path, ffprobe_path = media_tools()
        creation_flags = (
            subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        for executable in (ffmpeg_path, ffprobe_path):
            subprocess.run(
                [executable, "-version"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
                check=True,
                timeout=30,
            )
        select_h264_encoder()
        return
    if os.environ.get("SCREEN_RECORDER_SMOKE_TEST") == "1":
        return
    enable_high_dpi()
    root = Tk()
    root.withdraw()
    # Resolve the current display before any dialog moves the mouse to another
    # monitor; normal startup does not show a capture-region selection window.
    capture_target = select_capture_target(root)
    if capture_target is None:
        root.destroy()
        return
    startup_settings = select_startup_settings(root, capture_target)
    if startup_settings is None:
        root.destroy()
        return
    save_directory = startup_settings.save_directory
    segment_seconds = startup_settings.segment_seconds
    try:
        recovered_sessions, quarantined_files = recover_interrupted_sessions(
            save_directory
        )
    except OSError as exc:
        messagebox.showerror(
            "恢复检查失败",
            f"无法检查上次未完成的录制：{exc}",
            parent=root,
        )
        root.destroy()
        return
    if recovered_sessions:
        messagebox.showwarning(
            "已恢复未正常结束的录制",
            (
                f"检测到 {recovered_sessions} 个未正常结束的会话；"
                f"已保留合格三件套，并将 {quarantined_files} 个"
                "未提交文件移入 rejected 目录。"
            ),
            parent=root,
        )
    session = select_session_config(root)
    if session is None:
        root.destroy()
        return
    try:
        acceleration_disabled = mouse_acceleration_disabled()
    except OSError as exc:
        messagebox.showerror(
            "鼠标设置检查失败",
            f"无法读取Windows鼠标加速度设置：{exc}",
            parent=root,
        )
        root.destroy()
        return
    if not acceleration_disabled:
        messagebox.showerror(
            "请关闭鼠标加速度",
            (
                "检测到Windows鼠标加速度/“提高指针精确度”仍处于开启状态。\n"
                "请在Windows鼠标设置中关闭后重新启动工具；"
                "为满足采集规范，本次不会开始录制。"
            ),
            parent=root,
        )
        root.destroy()
        return
    try:
        media_tools()
        select_h264_encoder()
    except Exception as exc:
        messagebox.showerror(
            "媒体组件启动失败",
            f"无法准备内置FFmpeg/ffprobe：{exc}",
            parent=root,
        )
        root.destroy()
        return
    root.deiconify()
    FloatingRecorder(
        root,
        save_directory,
        segment_seconds,
        session,
        capture_target,
    )
    root.mainloop()


if __name__ == "__main__":
    main()
