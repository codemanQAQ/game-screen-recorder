"""Post-build smoke test for the frozen, isolated Kraken helper.

The fixture is ``kraken-decompressor`` v0.2.1's GPL test vector:
https://github.com/domdfcoding/kraken-decompressor/blob/v0.2.1/tests/text.bin
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile


_FIXTURE_B64 = (
    "S0FSS6EFAACMBgADC4gDCUxvcmVtIGlwIArwAZ2SZeHEqvuNVkR3FdExnjlPSMbg"
    "Cbi48bQYmNGAfQCBgZzHUvVOgoBWbizAiWGhEr1bQJO0ofBbYGiB90LO36KjTtHQ"
    "7a5wIv4n7R5to4nKlTcS13eJ8w+J1CydHe52iNc8NRqtQ3XfGR8L1tYcCHhQLKU"
    "4bi+nwznXWcGjKkP8sKHU4Dsd9tTkHEsLZjGo9TT1N4kmVUm4AiPsBHM5JVtkyW7"
    "XONOJ6X60Jaeq1lTBFZOarfBYgjxXa9O0SRUs5WP+ahKQxHndsmd0xZju9RuQjKFR"
    "SsyX5iFbHgBCSpe1jviL2FxrPPtw0m3pT5UQKY5BGQ7QkW0darPzR9v20aSGZo/nl"
    "ej5EKrOCOCRl7hC/W2A793v8n5v8bP8yEVRL5A5lPrXxWR7i5ymFTEaaO60vLnSE"
    "mEfJStSUQE3Y44UQia51PmoMq36KjjVx9yM5OkbSvnorSoJi9CjBEydwpGMtlLi+p"
    "ypLOpBn5yVLJZzWqzpF+fKu/oagUDLP3Dm5KVN+pTdJ6wXnJCUYH5HX9nZDS6nO3"
    "/NMMEtYUBX1QAActfoA0PL2AfP19zP4MsDg9TzQNPf2+QDy+DQg8vLg8nPy8vXg8"
    "nL58vT/NAC3+DLg8nTz8/IA9jI0NfMg8jby+TXz9Df99TJ4tjL28vI1tH0z+PPyN"
    "bgz8zI28vU0M7X5NPUz8/L69zLy9Pf5MjPy8vW6AAAZAYMBwUIAA0PDBcLHAsFCQ"
    "QMDgITDBAcHAUQCw0KAhIdHRATGhsWBQMXHB4JFQ8tGBoiBgcZHCwFDw4RCgIkEB"
    "8UCBcaFxkBKhoYFCEWFxYlJyYbGBUZGQALHC0LAAgjJSQaLgQAAEROJBMsAQUaCgI"
    "CAA0AAgUMBgQHDAwDABENAgQGAQIdDgAEAAYNAAIBAgUDAQAACAkDAwABAgEJAAIA"
    "BwAAAAcEAQAFBkEAp8pfJ9jRoSVFaQEQsBM/fESYA5LgEv32eiN44DhRWM24LchEk"
    "5zajCXqXzhALyeg7LiUFXVQHYiWnA=="
)
_FIXTURE_SHA256 = "21fc13d89308dda3bc822bfb69319ce48f265d7038f5264c85787d2e4cd1ea86"
_OUTPUT_SHA256 = "3fc89a543c4596235024a00eb2fda4d321c5e14776709c5f6892a707506fdbfd"


def _helper_command(executable: Path, input_path: Path, output_path: Path) -> list[str]:
    return [
        str(executable),
        "--internal-unreal-kraken-helper",
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--expected-size",
        "1441",
        "--max-input",
        "1048576",
        "--max-output",
        "1048576",
        "--max-ratio",
        "4096",
    ]


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        raise SystemExit("usage: smoke_frozen_kraken.py <frozen-exe>")
    executable = Path(arguments[0]).resolve(strict=True)
    if not executable.is_file():
        raise SystemExit("frozen executable is not a regular file")

    fixture = base64.b64decode(_FIXTURE_B64, validate=True)
    if len(fixture) != 793 or hashlib.sha256(fixture).hexdigest() != _FIXTURE_SHA256:
        raise SystemExit("embedded Kraken fixture integrity check failed")
    if fixture[:4] != b"KARK" or int.from_bytes(fixture[4:8], "little") != 1441:
        raise SystemExit("embedded Kraken fixture header is invalid")
    payload = fixture[8:]
    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    )

    with tempfile.TemporaryDirectory(prefix="frozen_kraken_smoke_") as directory:
        root = Path(directory)
        input_path = root / "input.bin"
        output_path = root / "output.bin"
        input_path.write_bytes(payload)
        result = subprocess.run(
            _helper_command(executable, input_path, output_path),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=root,
            shell=False,
            timeout=30,
            check=False,
            creationflags=creationflags,
        )
        if result.returncode != 0 or not output_path.is_file():
            raise SystemExit(f"frozen Kraken helper failed: {result.returncode}")
        decoded = output_path.read_bytes()
        if len(decoded) != 1441 or hashlib.sha256(decoded).hexdigest() != _OUTPUT_SHA256:
            raise SystemExit("frozen Kraken helper output mismatch")

        bad_input = root / "truncated.bin"
        bad_output = root / "truncated-output.bin"
        bad_input.write_bytes(payload[:-1])
        bad_result = subprocess.run(
            _helper_command(executable, bad_input, bad_output),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=root,
            shell=False,
            timeout=30,
            check=False,
            creationflags=creationflags,
        )
        if bad_result.returncode == 0 or bad_output.exists():
            raise SystemExit("truncated Kraken input was not rejected")

    print("Frozen Kraken helper smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
