"""Minimal frozen entry point with an early isolated Kraken worker branch."""

from __future__ import annotations

import sys


_KRAKEN_HELPER_SWITCH = "--internal-unreal-kraken-helper"


def main() -> int:
    if sys.argv[1:2] == [_KRAKEN_HELPER_SWITCH]:
        from unreal_kraken_helper import main as helper_main

        return helper_main(sys.argv[2:])

    from screen_recorder import main as recorder_main

    recorder_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
