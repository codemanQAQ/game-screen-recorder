from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import Mock, patch

import recorder_bootstrap


class RecorderBootstrapTests(unittest.TestCase):
    def test_helper_branch_forwards_arguments_without_starting_recorder(self) -> None:
        helper_main = Mock(return_value=7)
        recorder_main = Mock()
        helper_module = types.SimpleNamespace(main=helper_main)
        recorder_module = types.SimpleNamespace(main=recorder_main)

        with (
            patch.dict(
                sys.modules,
                {
                    "unreal_kraken_helper": helper_module,
                    "screen_recorder": recorder_module,
                },
            ),
            patch.object(
                sys,
                "argv",
                [
                    "tool.exe",
                    "--internal-unreal-kraken-helper",
                    "--input",
                    "input.bin",
                ],
            ),
        ):
            result = recorder_bootstrap.main()

        self.assertEqual(7, result)
        helper_main.assert_called_once_with(["--input", "input.bin"])
        recorder_main.assert_not_called()

    def test_normal_branch_starts_recorder(self) -> None:
        recorder_main = Mock()
        recorder_module = types.SimpleNamespace(main=recorder_main)
        with (
            patch.dict(sys.modules, {"screen_recorder": recorder_module}),
            patch.object(sys, "argv", ["tool.exe"]),
        ):
            result = recorder_bootstrap.main()

        self.assertEqual(0, result)
        recorder_main.assert_called_once_with()

    def test_frozen_and_source_helper_configurations_are_distinct(self) -> None:
        import screen_recorder

        with (
            patch.object(screen_recorder.sys, "frozen", True, create=True),
            patch.object(screen_recorder.sys, "executable", r"C:\Tool\Recorder.exe"),
        ):
            frozen = screen_recorder._unreal_kraken_helper_config()
        self.assertEqual(
            (r"C:\Tool\Recorder.exe", "--internal-unreal-kraken-helper"),
            frozen.command,
        )
        self.assertEqual(12.0, frozen.timeout_seconds)

        with patch.object(screen_recorder.sys, "frozen", False, create=True):
            source = screen_recorder._unreal_kraken_helper_config()
        self.assertIsNone(source.command)
        self.assertIsNone(source.timeout_seconds)


if __name__ == "__main__":
    unittest.main()
