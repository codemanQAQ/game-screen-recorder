"""Bounded discovery of per-player game configuration files.

The selected install directory is only an identity and Steam metadata source.
Player files are discovered in the matching Steam Cloud, AppData and Documents
trees without following symbolic links or Windows directory junctions.

The caller supplies a cheap, metadata-only ``candidate_predicate``.  Parsing is
deliberately left to format-specific modules so this module stays independent
of the recorder GUI and of individual game names.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
import ctypes
from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import time
import unicodedata
import uuid


CandidatePredicate = Callable[[Path], bool]

STEAM_ID64_ACCOUNT_BASE = 76_561_197_960_265_728
_WINDOWS_REPARSE_POINT = 0x400
_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
_IO_REPARSE_TAG_SYMLINK = 0xA000000C
_ACF_PAIR_RE = re.compile(
    r'"((?:\\.|[^"\\])*)"\s*"((?:\\.|[^"\\])*)"',
    re.DOTALL,
)
_APP_MANIFEST_RE = re.compile(r"appmanifest_([0-9]{1,10})\.acf\Z", re.I)
_NOISY_EXECUTABLE_PREFIXES = (
    "crashpad",
    "crashreportclient",
    "dotnet",
    "dxsetup",
    "eosbootstrapper",
    "installer",
    "setup",
    "unitycrashhandler",
    "unins",
    "vcredist",
)
_GENERIC_EXECUTABLE_IDENTITIES = {
    "application",
    "bootstrap",
    "bootstrapper",
    "client",
    "client32",
    "client64",
    "game",
    "game32",
    "game64",
    "gameclient",
    "gamelauncher",
    "gamelaunchhelper",
    "gamewin32",
    "gamewin64",
    "gamex86",
    "gamex64",
    "launch",
    "launcher",
    "launcher32",
    "launcher64",
    "play",
    "shipping",
    "start",
    "startgame",
    "startprotectedgame",
    "win32",
    "win64",
    "x86",
    "x64",
}


@dataclass(frozen=True)
class PlayerConfigLimits:
    """Hard bounds applied to one discovery call."""

    timeout_seconds: float = 4.0
    max_entries: int = 25_000
    max_files: int = 128
    max_roots: int = 64
    max_file_bytes: int = 8 * 1024**2
    max_manifest_bytes: int = 1024**2
    max_manifest_files: int = 4_096
    appdata_identity_depth: int = 2
    documents_identity_depth: int = 3
    candidate_depth: int = 4

    def __post_init__(self) -> None:
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")
        positive_integer_bounds = (
            self.max_entries,
            self.max_files,
            self.max_roots,
            self.max_file_bytes,
            self.max_manifest_bytes,
            self.max_manifest_files,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in positive_integer_bounds
        ):
            raise ValueError("discovery count and size limits must be positive integers")
        if (
            isinstance(self.appdata_identity_depth, bool)
            or not isinstance(self.appdata_identity_depth, int)
            or not 0 <= self.appdata_identity_depth <= 8
        ):
            raise ValueError("appdata_identity_depth must be between 0 and 8")
        if (
            isinstance(self.documents_identity_depth, bool)
            or not isinstance(self.documents_identity_depth, int)
            or not 0 <= self.documents_identity_depth <= 8
        ):
            raise ValueError("documents_identity_depth must be between 0 and 8")
        if (
            isinstance(self.candidate_depth, bool)
            or not isinstance(self.candidate_depth, int)
            or not 0 <= self.candidate_depth <= 12
        ):
            raise ValueError("candidate_depth must be between 0 and 12")


DEFAULT_LIMITS = PlayerConfigLimits()


@dataclass(frozen=True)
class PlayerConfigRoot:
    """A safely discovered directory that may contain player configuration."""

    path: Path
    source: str


@dataclass(frozen=True)
class PlayerConfigDiscovery:
    """Result of a bounded player-configuration search."""

    files: tuple[Path, ...]
    roots: tuple[PlayerConfigRoot, ...]
    identities: tuple[str, ...]
    scanned_entries: int
    truncated: bool
    steam_app_id: str | None = None
    steam_account_id: str | None = None


@dataclass(frozen=True)
class _SteamManifest:
    app_id: str
    account_id: str
    steam_roots: tuple[Path, ...]


class _Budget:
    def __init__(self, limits: PlayerConfigLimits) -> None:
        self.limits = limits
        self.deadline = time.monotonic() + limits.timeout_seconds
        self.entries = 0
        self.truncated = False

    def available(self) -> bool:
        if time.monotonic() >= self.deadline:
            self.truncated = True
            return False
        return True

    def take_entry(self) -> bool:
        if not self.available():
            return False
        if self.entries >= self.limits.max_entries:
            self.truncated = True
            return False
        self.entries += 1
        return True


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _is_link_or_junction(path: Path) -> bool:
    """Return true for symlinks and Windows mount-point junctions."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction is not None and is_junction():
            return True
        stat_result = path.lstat()
    except (OSError, RuntimeError):
        return True
    tag = getattr(stat_result, "st_reparse_tag", 0)
    if tag in {_IO_REPARSE_TAG_MOUNT_POINT, _IO_REPARSE_TAG_SYMLINK}:
        return True
    # Older Python versions do not expose the tag.  In that case, refusing an
    # unknown reparse point is safer than traversing a possible junction.
    return bool(
        getattr(stat_result, "st_file_attributes", 0)
        & _WINDOWS_REPARSE_POINT
        and not hasattr(stat_result, "st_reparse_tag")
    )


def _entry_is_link_or_junction(entry: os.DirEntry[str], path: Path) -> bool:
    try:
        if entry.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction is not None and is_junction():
            return True
        stat_result = entry.stat(follow_symlinks=False)
    except OSError:
        return True
    tag = getattr(stat_result, "st_reparse_tag", 0)
    if tag in {_IO_REPARSE_TAG_MOUNT_POINT, _IO_REPARSE_TAG_SYMLINK}:
        return True
    return bool(
        getattr(stat_result, "st_file_attributes", 0)
        & _WINDOWS_REPARSE_POINT
        and not hasattr(stat_result, "st_reparse_tag")
    )


def _safe_directory(path: Path) -> bool:
    try:
        absolute = Path(os.path.abspath(os.fspath(path)))
        for component in (absolute, *absolute.parents):
            if _is_link_or_junction(component):
                return False
        return absolute.is_dir()
    except OSError:
        return False


def _normalize_identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _identity_matches(directory_name: str, identities: tuple[str, ...]) -> bool:
    directory_identity = _normalize_identity(directory_name)
    if not directory_identity:
        return False
    for identity in identities:
        if directory_identity == identity:
            return True
        shorter_length = min(len(directory_identity), len(identity))
        if shorter_length >= 6 and (
            directory_identity in identity or identity in directory_identity
        ):
            return True
    return False


def _scandir(path: Path):
    try:
        return os.scandir(path)
    except (OSError, ValueError):
        return None


def _game_install_root(game_directory: Path) -> tuple[Path, Path | None]:
    """Return the top-level game directory and its Steam ``steamapps`` root."""

    try:
        absolute = Path(os.path.abspath(os.fspath(game_directory)))
    except (OSError, TypeError, ValueError):
        return game_directory, None
    common = next(
        (
            candidate
            for candidate in (absolute, *absolute.parents)
            if candidate.name.casefold() == "common"
        ),
        None,
    )
    if common is None:
        return absolute, None
    try:
        relative = absolute.relative_to(common)
    except ValueError:
        return absolute, None
    if not relative.parts:
        return absolute, None
    return common / relative.parts[0], common.parent


def _game_identities(game_root: Path, budget: _Budget) -> tuple[str, ...]:
    identities: set[str] = set()
    directory_identity = _normalize_identity(game_root.name)
    if directory_identity:
        identities.add(directory_identity)
    entries = _scandir(game_root)
    if entries is None:
        return tuple(sorted(identities))
    with entries:
        for entry in entries:
            if not budget.take_entry():
                break
            if not entry.name.casefold().endswith(".exe"):
                continue
            path = Path(entry.path)
            try:
                if (
                    _entry_is_link_or_junction(entry, path)
                    or not entry.is_file(follow_symlinks=False)
                ):
                    continue
            except OSError:
                continue
            executable_identity = _normalize_identity(path.stem)
            if (
                not executable_identity
                or executable_identity in _GENERIC_EXECUTABLE_IDENTITIES
                or executable_identity.startswith(_NOISY_EXECUTABLE_PREFIXES)
            ):
                continue
            identities.add(executable_identity)
    return tuple(sorted(identities))


def _decode_acf_value(value: str) -> str:
    return value.replace(r"\"", '"').replace(r"\\", "\\")


def _acf_scalars(data: bytes) -> dict[str, str]:
    text = data.decode("utf-8-sig", errors="replace")
    result: dict[str, str] = {}
    for match in _ACF_PAIR_RE.finditer(text):
        key = _decode_acf_value(match.group(1)).casefold()
        result.setdefault(key, _decode_acf_value(match.group(2)))
    return result


def _read_bounded(path: Path, maximum: int) -> bytes:
    if _is_link_or_junction(path):
        raise OSError("linked files are not accepted")
    stat_result = path.stat(follow_symlinks=False)
    if not 0 <= stat_result.st_size <= maximum:
        raise ValueError("file is larger than the discovery limit")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("file grew beyond the discovery limit")
    return data


def _steam_account_id(last_owner: str) -> str | None:
    if not last_owner.isdigit():
        return None
    value = int(last_owner)
    if value >= STEAM_ID64_ACCOUNT_BASE:
        value -= STEAM_ID64_ACCOUNT_BASE
    if not 0 < value <= 0xFFFFFFFF:
        return None
    return str(value)


def _steam_manifest(
    game_root: Path,
    steamapps: Path | None,
    budget: _Budget,
) -> _SteamManifest | None:
    if steamapps is None or not _safe_directory(steamapps):
        return None
    entries = _scandir(steamapps)
    if entries is None:
        return None
    matching: _SteamManifest | None = None
    manifests_seen = 0
    with entries:
        for entry in entries:
            if not budget.take_entry():
                break
            match = _APP_MANIFEST_RE.fullmatch(entry.name)
            if match is None:
                continue
            manifests_seen += 1
            if manifests_seen > budget.limits.max_manifest_files:
                budget.truncated = True
                break
            path = Path(entry.path)
            try:
                if (
                    _entry_is_link_or_junction(entry, path)
                    or not entry.is_file(follow_symlinks=False)
                ):
                    continue
                fields = _acf_scalars(
                    _read_bounded(path, budget.limits.max_manifest_bytes)
                )
            except (OSError, ValueError):
                continue
            if fields.get("installdir", "").casefold() != game_root.name.casefold():
                continue
            filename_app_id = str(int(match.group(1)))
            field_app_id = fields.get("appid", "").strip()
            if field_app_id:
                if not field_app_id.isdigit() or str(int(field_app_id)) != filename_app_id:
                    continue
            account_id = _steam_account_id(fields.get("lastowner", "").strip())
            if account_id is None:
                return None
            roots = [steamapps.parent]
            launcher = fields.get("launcherpath", "").strip()
            if launcher:
                launcher_path = Path(launcher)
                if launcher_path.name.casefold() == "steam.exe":
                    roots.insert(0, launcher_path.parent)
            unique_roots: list[Path] = []
            seen: set[str] = set()
            for root in roots:
                key = _path_key(root)
                if key not in seen:
                    seen.add(key)
                    unique_roots.append(root)
            matching = _SteamManifest(
                filename_app_id,
                account_id,
                tuple(unique_roots),
            )
            break
    return matching


def _steam_remote_roots(manifest: _SteamManifest | None) -> list[PlayerConfigRoot]:
    if manifest is None:
        return []
    roots: list[PlayerConfigRoot] = []
    for steam_root in manifest.steam_roots:
        if not _safe_directory(steam_root):
            continue
        current = steam_root
        valid = True
        for component in (
            "userdata",
            manifest.account_id,
            manifest.app_id,
            "remote",
        ):
            current = current / component
            if not _safe_directory(current):
                valid = False
                break
        if valid:
            roots.append(PlayerConfigRoot(current, "steam"))
    return roots


def _environment_value(environment: Mapping[str, str], name: str) -> str | None:
    expected = name.casefold()
    for key, value in environment.items():
        if key.casefold() == expected and value:
            return value
    return None


def _unique_base_directories(paths: list[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = _path_key(path)
        if key in seen or not _safe_directory(path):
            continue
        seen.add(key)
        result.append(path)
    return tuple(result)


def _appdata_bases(environment: Mapping[str, str]) -> tuple[Path, ...]:
    paths: list[Path] = []
    roaming_paths: list[Path] = []
    roaming_value = _environment_value(environment, "APPDATA")
    local_value = _environment_value(environment, "LOCALAPPDATA")
    profile_value = _environment_value(environment, "USERPROFILE")
    if roaming_value:
        roaming = Path(roaming_value)
        paths.append(roaming)
        roaming_paths.append(roaming)
    if local_value:
        local = Path(local_value)
        roaming = local.parent / "Roaming"
        paths.extend((local, local.parent / "LocalLow", roaming))
        roaming_paths.append(roaming)
    if profile_value:
        appdata = Path(profile_value) / "AppData"
        roaming = appdata / "Roaming"
        paths.extend((roaming, appdata / "Local", appdata / "LocalLow"))
        roaming_paths.append(roaming)

    # Godot resolves ``user://`` on Windows below this shared container:
    # ``%APPDATA%/Godot/app_userdata/<project_name>``.  Treat app_userdata as
    # an additional bounded base instead of increasing the general AppData
    # identity depth.  This keeps the project name as the identity-matching
    # directory and does not broaden traversal for unrelated AppData trees.
    paths.extend(path / "Godot" / "app_userdata" for path in roaming_paths)
    return _unique_base_directories(paths)


def _documents_bases(environment: Mapping[str, str]) -> tuple[Path, ...]:
    paths: list[Path] = []
    if environment is os.environ:
        known_documents = _windows_known_documents_directory()
        if known_documents is not None:
            paths.append(known_documents)
    profile_value = _environment_value(environment, "USERPROFILE")
    if profile_value:
        paths.append(Path(profile_value) / "Documents")
    for variable in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        value = _environment_value(environment, variable)
        if value:
            paths.append(Path(value) / "Documents")
    return _unique_base_directories(paths)


class _Guid(ctypes.Structure):
    _fields_ = (
        ("data1", ctypes.c_uint32),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    )


def _windows_known_documents_directory() -> Path | None:
    """Read FOLDERID_Documents so redirected libraries are not missed."""
    if os.name != "nt":
        return None
    folder_id = _Guid.from_buffer_copy(
        uuid.UUID("fdd39ad0-238f-46af-adb4-6c85480369c7").bytes_le
    )
    allocated = ctypes.c_wchar_p()
    try:
        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
        shell32.SHGetKnownFolderPath.argtypes = (
            ctypes.POINTER(_Guid),
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_wchar_p),
        )
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        ole32.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
        ole32.CoTaskMemFree.restype = None
        result = shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id),
            0,
            None,
            ctypes.byref(allocated),
        )
        if result != 0 or not allocated.value:
            return None
        return Path(allocated.value)
    except (AttributeError, OSError, ValueError):
        return None
    finally:
        try:
            if allocated:
                ctypes.windll.ole32.CoTaskMemFree(
                    ctypes.cast(allocated, ctypes.c_void_p)
                )
        except (AttributeError, OSError, ValueError):
            pass


def _identity_roots(
    bases: tuple[Path, ...],
    identities: tuple[str, ...],
    maximum_depth: int,
    source: str,
    budget: _Budget,
) -> list[PlayerConfigRoot]:
    roots: list[PlayerConfigRoot] = []
    seen: set[str] = set()
    pending: deque[tuple[Path, int]] = deque((base, 0) for base in bases)
    while pending and budget.available():
        current, depth = pending.popleft()
        if depth >= maximum_depth:
            continue
        entries = _scandir(current)
        if entries is None:
            continue
        with entries:
            for entry in entries:
                if not budget.take_entry():
                    break
                path = Path(entry.path)
                try:
                    if (
                        _entry_is_link_or_junction(entry, path)
                        or not entry.is_dir(follow_symlinks=False)
                    ):
                        continue
                except OSError:
                    continue
                child_depth = depth + 1
                if _identity_matches(entry.name, identities):
                    key = _path_key(path)
                    if key not in seen:
                        if len(roots) >= budget.limits.max_roots:
                            budget.truncated = True
                            return roots
                        seen.add(key)
                        roots.append(PlayerConfigRoot(path, source))
                    # The matched directory is scanned separately with the
                    # candidate-depth bound; do not enumerate it twice here.
                    continue
                if child_depth < maximum_depth:
                    pending.append((path, child_depth))
    roots.sort(
        key=lambda root: (
            0 if _normalize_identity(root.path.name) in identities else 1,
            _path_key(root.path),
        )
    )
    return roots


def _deduplicate_roots(
    roots: list[PlayerConfigRoot],
    maximum: int,
    budget: _Budget,
) -> list[PlayerConfigRoot]:
    result: list[PlayerConfigRoot] = []
    seen: set[str] = set()
    for root in roots:
        key = _path_key(root.path)
        if key in seen:
            continue
        if len(result) >= maximum:
            budget.truncated = True
            break
        seen.add(key)
        result.append(root)
    return result


def _candidate_files(
    roots: list[PlayerConfigRoot],
    predicate: CandidatePredicate,
    budget: _Budget,
) -> list[Path]:
    files: list[tuple[int, Path]] = []
    seen: set[str] = set()
    for root_index, root in enumerate(roots):
        if not budget.available():
            break
        pending: deque[tuple[Path, int]] = deque(((root.path, 0),))
        while pending and budget.available():
            current, depth = pending.popleft()
            entries = _scandir(current)
            if entries is None:
                continue
            with entries:
                for entry in entries:
                    if not budget.take_entry():
                        break
                    path = Path(entry.path)
                    if _entry_is_link_or_junction(entry, path):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if depth < budget.limits.candidate_depth:
                                pending.append((path, depth + 1))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        size = entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
                    if not 0 <= size <= budget.limits.max_file_bytes:
                        continue
                    try:
                        accepted = bool(predicate(path))
                    except Exception:
                        accepted = False
                    if not budget.available():
                        break
                    if not accepted:
                        continue
                    key = _path_key(path)
                    if key in seen:
                        continue
                    if len(files) >= budget.limits.max_files:
                        budget.truncated = True
                        files.sort(key=lambda item: (item[0], _path_key(item[1])))
                        return [path for _root_index, path in files]
                    seen.add(key)
                    files.append((root_index, path))
    files.sort(key=lambda item: (item[0], _path_key(item[1])))
    return [path for _root_index, path in files]


def discover_player_config_files(
    game_dir: str | os.PathLike[str],
    candidate_predicate: CandidatePredicate,
    *,
    environment: Mapping[str, str] | None = None,
    limits: PlayerConfigLimits = DEFAULT_LIMITS,
) -> PlayerConfigDiscovery:
    """Discover bounded per-player configuration candidates for ``game_dir``.

    Identity directories are considered at most two levels below each AppData
    base and three levels below each Documents base.  A matched identity root
    is then searched to ``limits.candidate_depth``.  Steam Cloud is restricted
    to the app manifest's valid ``LastOwner`` account; other local Steam users
    are never merged.

    ``candidate_predicate`` must be a quick, side-effect-free metadata check
    (normally a filename or extension test).  Only non-linked regular files no
    larger than ``limits.max_file_bytes`` are passed to it.
    """

    if not callable(candidate_predicate):
        raise TypeError("candidate_predicate must be callable")
    if not isinstance(limits, PlayerConfigLimits):
        raise TypeError("limits must be a PlayerConfigLimits instance")
    environment = os.environ if environment is None else environment
    requested = Path(game_dir)
    game_root, steamapps = _game_install_root(requested)
    budget = _Budget(limits)
    if not _safe_directory(game_root):
        return PlayerConfigDiscovery((), (), (), 0, False)

    identities = _game_identities(game_root, budget)
    manifest = _steam_manifest(game_root, steamapps, budget)
    roots = _steam_remote_roots(manifest)
    roots.extend(
        _identity_roots(
            _appdata_bases(environment),
            identities,
            limits.appdata_identity_depth,
            "appdata",
            budget,
        )
    )
    roots.extend(
        _identity_roots(
            _documents_bases(environment),
            identities,
            limits.documents_identity_depth,
            "documents",
            budget,
        )
    )
    roots = _deduplicate_roots(roots, limits.max_roots, budget)
    files = _candidate_files(roots, candidate_predicate, budget)
    return PlayerConfigDiscovery(
        tuple(files),
        tuple(roots),
        identities,
        budget.entries,
        budget.truncated,
        manifest.app_id if manifest is not None else None,
        manifest.account_id if manifest is not None else None,
    )


__all__ = [
    "DEFAULT_LIMITS",
    "PlayerConfigDiscovery",
    "PlayerConfigLimits",
    "PlayerConfigRoot",
    "discover_player_config_files",
]
