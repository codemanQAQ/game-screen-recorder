# Third-party notices

This project uses third-party software. Each component remains subject to its
own copyright and license terms. The list below covers the direct runtime and
build dependencies declared by this repository; packaged releases must also
preserve the notices for their resolved transitive dependencies.

| Component | Declared version | License | Project |
| --- | ---: | --- | --- |
| DXcam | 0.3.0 | MIT | <https://github.com/ra1nty/DXcam> |
| python-mss | 10.1.0 | MIT | <https://github.com/BoboTiG/python-mss> |
| NumPy | 2.3.2 | BSD-3-Clause, plus bundled-library notices | <https://github.com/numpy/numpy> |
| static-ffmpeg | 3.0 | MIT | <https://github.com/zackees/static_ffmpeg> |
| UnityPy | 1.25.2 | MIT | <https://github.com/K0lb3/UnityPy> |
| dnfile | 0.18.0 | MIT | <https://github.com/malwarefrank/dnfile> |
| dncil | 1.0.2 | Apache-2.0 | <https://github.com/mandiant/dncil> |
| pypdf | 6.14.2 | BSD-3-Clause | <https://github.com/py-pdf/pypdf> |
| python-zstandard | 0.25.0 | BSD-3-Clause | <https://github.com/indygreg/python-zstandard> |
| kraken-decompressor | 0.2.1 | GNU GPL v3 (see upstream license) | <https://github.com/domdfcoding/kraken-decompressor> |
| PyInstaller | 6.21.0 | GPL-2.0-or-later with the PyInstaller bootloader exception | <https://github.com/pyinstaller/pyinstaller> |
| CPython | build interpreter version | Python-2.0 | <https://www.python.org/> |

## FFmpeg distributed with packaged builds

`static-ffmpeg` obtains a separate FFmpeg/ffprobe build that packaged releases
use for encoding and validation. The currently resolved Windows build reports
FFmpeg 8.0.1 and was configured with both `--enable-gpl` and
`--enable-version3`; that binary is therefore distributed under GPLv3-or-later
terms and includes further codec/library copyrights. Its authoritative license
output is available by running `ffmpeg -L`, and its complete configuration is
available through `ffmpeg -version`.

Release maintainers must record the exact FFmpeg build, retain its license and
notices, and make the corresponding source available in the manner required by
the FFmpeg build's license. A generated EXE belongs in GitHub Releases rather
than in Git history.

## Oodle/Kraken support

The bundled `kraken-decompressor` package incorporates the open-source Powzix
Kraken decoder. Copyright (C) 2016 Powzix and 2025 Dominic Davis-Foster. It is
distributed under GNU GPL v3; the upstream license file remains authoritative.
This GPL-3.0-only application uses the decoder under the version 3 terms.

The upstream decoder is not intended to consume untrusted data without a
safety boundary. This application therefore validates Pak indexes, hashes,
entry bounds, stream type, sizes and decompression ratios before invoking the
decoder in a separate, time-limited helper process. Decoder failure cannot be
treated as a partial keymap result.

This repository does not copy, dynamically load, or redistribute Epic/RAD
Oodle DLLs from games, community mirrors, or proprietary SDKs.

`smoke_frozen_kraken.py` embeds the GPL test vector from
`kraken-decompressor` v0.2.1 (`tests/text.bin`) solely to verify the frozen
helper after each build; its source URL and integrity hashes are recorded in
that script.

The full license texts installed with Python packages remain authoritative if
this summary differs from their upstream distributions.
