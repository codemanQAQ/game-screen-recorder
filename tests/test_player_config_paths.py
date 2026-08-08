from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from player_config_paths import (
    PlayerConfigLimits,
    discover_player_config_files,
)


def _write(path: Path, text: str = "{}") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _environment(profile: Path, one_drive: Path | None = None) -> dict[str, str]:
    environment = {
        "USERPROFILE": str(profile),
        "APPDATA": str(profile / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(profile / "AppData" / "Local"),
    }
    if one_drive is not None:
        environment["OneDrive"] = str(one_drive)
    for path in (
        Path(environment["APPDATA"]),
        Path(environment["LOCALAPPDATA"]),
        profile / "AppData" / "LocalLow",
        profile / "Documents",
    ):
        path.mkdir(parents=True, exist_ok=True)
    if one_drive is not None:
        (one_drive / "Documents").mkdir(parents=True, exist_ok=True)
    return environment


class PlayerConfigPathTests(unittest.TestCase):
    def test_steam_manifest_uses_only_last_owner_remote(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            steam = root / "Steam"
            game = steam / "steamapps" / "common" / "Example Game"
            game.mkdir(parents=True)
            _write(game / "ExampleGame.exe", "binary")
            _write(steam / "steam.exe", "binary")
            launcher = str(steam / "steam.exe").replace("\\", "\\\\")
            owner = 76_561_197_960_265_728 + 42
            _write(
                steam / "steamapps" / "appmanifest_1234.acf",
                "\n".join(
                    (
                        '"AppState"',
                        "{",
                        '    "appid" "1234"',
                        '    "installdir" "Example Game"',
                        f'    "LauncherPath" "{launcher}"',
                        f'    "LastOwner" "{owner}"',
                        "}",
                    )
                ),
            )
            expected = _write(
                steam / "userdata" / "42" / "1234" / "remote" / "settings.save"
            )
            _write(
                steam / "userdata" / "99" / "1234" / "remote" / "settings.save"
            )
            profile = root / "profile"
            environment = _environment(profile)
            appdata_candidate = _write(
                profile / "AppData" / "Roaming" / "ExampleGame" / "settings.save"
            )

            result = discover_player_config_files(
                game,
                lambda path: path.name == "settings.save",
                environment=environment,
            )

            self.assertEqual(result.files, (expected, appdata_candidate))
            self.assertEqual(result.steam_app_id, "1234")
            self.assertEqual(result.steam_account_id, "42")
            self.assertEqual(
                [root.source for root in result.roots], ["steam", "appdata"]
            )

    def test_top_level_executable_stem_can_match_appdata_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "InstallFolder"
            game.mkdir()
            _write(game / "ActualGame.exe", "binary")
            profile = root / "profile"
            environment = _environment(profile)
            expected = _write(
                profile
                / "AppData"
                / "LocalLow"
                / "Studio"
                / "ActualGameData"
                / "config.json"
            )

            result = discover_player_config_files(
                game,
                lambda path: path.suffix == ".json",
                environment=environment,
            )

            self.assertEqual(result.files, (expected,))
            self.assertIn("actualgame", result.identities)
            self.assertEqual(result.roots[0].source, "appdata")

    def test_generic_document_and_appdata_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "profile"
            one_drive = root / "OneDrive"
            environment = _environment(profile, one_drive)
            layouts = (
                (
                    "Oxygen Not Included",
                    profile
                    / "Documents"
                    / "Klei"
                    / "OxygenNotIncluded"
                    / "keybindings.json",
                ),
                (
                    "Terraria",
                    profile
                    / "Documents"
                    / "My Games"
                    / "Terraria"
                    / "input profiles.json",
                ),
                (
                    "RimWorld",
                    profile
                    / "Documents"
                    / "RimWorld by Ludeon Studios"
                    / "Config"
                    / "KeyPrefs.xml",
                ),
                (
                    "Celeste",
                    one_drive / "Documents" / "Celeste" / "settings.celeste",
                ),
                (
                    "Barotrauma",
                    profile
                    / "AppData"
                    / "LocalLow"
                    / "Publisher"
                    / "Barotrauma"
                    / "config_player.xml",
                ),
            )
            for game_name, candidate in layouts:
                _write(candidate)
                game = root / "games" / game_name
                game.mkdir(parents=True)
                _write(game / f"{game_name.replace(' ', '')}.exe", "binary")

            for game_name, expected in layouts:
                with self.subTest(game=game_name):
                    result = discover_player_config_files(
                        root / "games" / game_name,
                        lambda path: path == expected,
                        environment=environment,
                    )
                    self.assertEqual(result.files, (expected,))

    def test_appdata_identity_depth_is_two_and_documents_depth_is_three(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "TargetGame"
            game.mkdir()
            profile = root / "profile"
            environment = _environment(profile)
            too_deep = _write(
                profile
                / "AppData"
                / "Local"
                / "Vendor"
                / "Platform"
                / "TargetGame"
                / "config.json"
            )
            expected = _write(
                profile
                / "Documents"
                / "Vendor"
                / "Platform"
                / "TargetGame"
                / "config.json"
            )

            result = discover_player_config_files(
                game,
                lambda path: path.name == "config.json",
                environment=environment,
            )

            self.assertEqual(result.files, (expected,))
            self.assertNotIn(too_deep, result.files)

    def test_godot_user_directory_matches_project_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "InstallFolder"
            game.mkdir()
            _write(game / "ActualGodotGame.exe", "binary")
            profile = root / "profile"
            environment = _environment(profile)
            expected = _write(
                profile
                / "AppData"
                / "Roaming"
                / "Godot"
                / "app_userdata"
                / "Actual Godot Game"
                / "settings.cfg"
            )
            unrelated = _write(
                profile
                / "AppData"
                / "Roaming"
                / "Godot"
                / "app_userdata"
                / "Unrelated Project"
                / "settings.cfg"
            )

            result = discover_player_config_files(
                game,
                lambda path: path.name == "settings.cfg",
                environment=environment,
            )

            self.assertEqual(result.files, (expected,))
            self.assertNotIn(unrelated, result.files)
            self.assertEqual(result.roots[0].path, expected.parent)
            self.assertEqual(result.roots[0].source, "appdata")

    def test_godot_user_directory_uses_equivalent_roaming_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "FallbackGodotGame"
            game.mkdir()
            profile = root / "profile"
            environment = _environment(profile)
            environment.pop("APPDATA")
            expected = _write(
                profile
                / "AppData"
                / "Roaming"
                / "Godot"
                / "app_userdata"
                / "FallbackGodotGame"
                / "input.cfg"
            )

            result = discover_player_config_files(
                game,
                lambda path: path.name == "input.cfg",
                environment=environment,
            )

            self.assertEqual(result.files, (expected,))
            self.assertEqual(result.roots[0].path, expected.parent)

    def test_contains_match_requires_six_normalized_characters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "profile"
            environment = _environment(profile)
            short_game = root / "Short"
            long_game = root / "Longer"
            short_game.mkdir()
            long_game.mkdir()
            short_candidate = _write(
                profile / "Documents" / "Shortened" / "short.json"
            )
            long_candidate = _write(
                profile / "Documents" / "PrefixLongerSuffix" / "long.json"
            )

            short_result = discover_player_config_files(
                short_game,
                lambda path: path.suffix == ".json",
                environment=environment,
            )
            long_result = discover_player_config_files(
                long_game,
                lambda path: path.suffix == ".json",
                environment=environment,
            )

            self.assertNotIn(short_candidate, short_result.files)
            self.assertEqual(long_result.files, (long_candidate,))

    def test_generic_helper_executables_do_not_create_identities(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "SpecificTitle"
            game.mkdir()
            for executable in (
                "game.exe",
                "launcher.exe",
                "client.exe",
                "crashpad_handler.exe",
                "shipping.exe",
            ):
                _write(game / executable, "binary")
            profile = root / "profile"
            environment = _environment(profile)
            for unrelated in ("Game", "Launcher", "Crashpad"):
                _write(
                    profile
                    / "AppData"
                    / "Roaming"
                    / unrelated
                    / "settings.json"
                )
            expected = _write(
                profile
                / "AppData"
                / "Roaming"
                / "SpecificTitle"
                / "settings.json"
            )

            result = discover_player_config_files(
                game,
                lambda path: path.name == "settings.json",
                environment=environment,
            )

            self.assertEqual(result.identities, ("specifictitle",))
            self.assertEqual(result.files, (expected,))

    def test_linked_directories_are_not_traversed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "LinkedGame"
            game.mkdir()
            profile = root / "profile"
            environment = _environment(profile)
            outside = root / "outside"
            expected_not_found = _write(outside / "config.json")
            linked_root = profile / "Documents" / "LinkedGame"
            try:
                os.symlink(outside, linked_root, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"directory symlinks are unavailable: {error}")

            result = discover_player_config_files(
                game,
                lambda path: path.suffix == ".json",
                environment=environment,
            )

            self.assertNotIn(expected_not_found, result.files)
            self.assertEqual(result.files, ())

    def test_file_size_and_result_count_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "BoundedGame"
            game.mkdir()
            profile = root / "profile"
            environment = _environment(profile)
            config_root = profile / "Documents" / "BoundedGame"
            first = _write(config_root / "a.json", "{}")
            second = _write(config_root / "b.json", "{}")
            oversized = _write(config_root / "large.json", "12345")
            limits = PlayerConfigLimits(max_files=1, max_file_bytes=4)

            result = discover_player_config_files(
                game,
                lambda path: path.suffix == ".json",
                environment=environment,
                limits=limits,
            )

            self.assertEqual(len(result.files), 1)
            self.assertIn(result.files[0], {first, second})
            self.assertNotIn(oversized, result.files)
            self.assertTrue(result.truncated)

    def test_invalid_game_directory_returns_an_empty_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = discover_player_config_files(
                Path(temporary) / "missing",
                lambda _path: True,
                environment={},
            )
            self.assertEqual(result.files, ())
            self.assertEqual(result.roots, ())
            self.assertEqual(result.scanned_entries, 0)


if __name__ == "__main__":
    unittest.main()
