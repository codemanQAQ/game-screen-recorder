"""Isolated helper for bounded Oodle/Kraken decompression.

This module is intentionally a tiny command-line program.  The recorder-side
PAK parser launches it in a separate process, so the native decoder is never
imported into the recording process.  Input and output are ordinary files in a
private temporary directory created by the caller.

The helper only accepts Kraken streams (decoder type 6), exact output sizes and
strict caller-provided bounds.  Any mismatch fails without returning partial
data.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


_HARD_MAX_INPUT_BYTES = 16 * 1024 * 1024
_HARD_MAX_OUTPUT_BYTES = 16 * 1024 * 1024
_HARD_MAX_RATIO = 4096
_RATIO_SLACK_BYTES = 4096


class _HelperError(ValueError):
    pass


def _positive_int(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-size", required=True, type=_positive_int)
    parser.add_argument("--max-input", required=True, type=_positive_int)
    parser.add_argument("--max-output", required=True, type=_positive_int)
    parser.add_argument("--max-ratio", required=True, type=_positive_int)
    return parser


def _regular_file(path: Path) -> bool:
    try:
        if path.is_symlink():
            return False
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return False
        return path.is_file()
    except OSError:
        return False


def _link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(callable(is_junction) and is_junction())
    except OSError:
        return True


def _validate_paths(input_path: Path, output_path: Path) -> tuple[Path, Path]:
    if (
        _link_or_junction(input_path)
        or _link_or_junction(input_path.parent)
        or _link_or_junction(output_path.parent)
    ):
        raise _HelperError("helper paths must not use links or junctions")
    try:
        resolved_input = input_path.resolve(strict=True)
        output_parent = output_path.parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _HelperError("invalid helper path") from exc
    if not _regular_file(resolved_input):
        raise _HelperError("input is not a regular file")
    if output_path.exists() or output_path.is_symlink():
        raise _HelperError("output already exists")
    if resolved_input.parent != output_parent:
        raise _HelperError("input and output must share a private directory")
    resolved_output = output_parent / output_path.name
    if resolved_output.name in {"", ".", ".."}:
        raise _HelperError("invalid output name")
    return resolved_input, resolved_output


def _read_bounded(path: Path, maximum: int) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise _HelperError("unable to stat input") from exc
    if size <= 0 or size > maximum:
        raise _HelperError("input size exceeds bound")
    try:
        with path.open("rb") as handle:
            data = handle.read(maximum + 1)
    except OSError as exc:
        raise _HelperError("unable to read input") from exc
    if len(data) != size or len(data) > maximum:
        raise _HelperError("input changed or exceeds bound")
    return data


def _validate_kraken_stream(data: bytes) -> None:
    if len(data) < 2:
        raise _HelperError("truncated Kraken stream")
    first, second = data[0], data[1]
    if (first & 0x0F) != 0x0C or ((first >> 4) & 0x03) != 0:
        raise _HelperError("invalid Oodle stream header")
    if (second & 0x7F) != 6:
        raise _HelperError("Oodle stream is not Kraken")


def _write_exclusive(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(data)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise _HelperError("unable to write output")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _run(arguments: argparse.Namespace) -> None:
    if arguments.max_input > _HARD_MAX_INPUT_BYTES:
        raise _HelperError("max-input exceeds hard limit")
    if arguments.max_output > _HARD_MAX_OUTPUT_BYTES:
        raise _HelperError("max-output exceeds hard limit")
    if arguments.max_ratio > _HARD_MAX_RATIO:
        raise _HelperError("max-ratio exceeds hard limit")
    if arguments.expected_size > arguments.max_output:
        raise _HelperError("expected output exceeds bound")

    input_path, output_path = _validate_paths(
        Path(arguments.input),
        Path(arguments.output),
    )
    data = _read_bounded(input_path, arguments.max_input)
    if arguments.expected_size > (
        len(data) * arguments.max_ratio + _RATIO_SLACK_BYTES
    ):
        raise _HelperError("decompression ratio exceeds bound")
    _validate_kraken_stream(data)

    try:
        from kraken_decompressor import decompress
    except Exception as exc:
        raise _HelperError("Kraken decoder is unavailable") from exc

    try:
        decoded = decompress(data, arguments.expected_size)
    except Exception as exc:
        raise _HelperError("Kraken decompression failed") from exc
    if not isinstance(decoded, bytes) or len(decoded) != arguments.expected_size:
        raise _HelperError("Kraken output size mismatch")
    _write_exclusive(output_path, decoded)


def _emit_error(message: str) -> None:
    stream = sys.stderr
    if stream is None:
        return
    try:
        print(message, file=stream)
    except Exception:
        # PyInstaller --windowed may expose no usable standard streams.  The
        # process exit code remains the machine-readable failure signal.
        return


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        _run(arguments)
    except (OSError, ValueError, MemoryError) as exc:
        message = str(exc).replace("\r", " ").replace("\n", " ")[:300]
        _emit_error(message or "Kraken helper failed")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
