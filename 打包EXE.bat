@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo [1/3] 正在准备 64 位构建环境...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto :failed
)

".venv\Scripts\python.exe" -c "import struct,sys; sys.exit(0 if struct.calcsize('P') * 8 == 64 else 1)"
if errorlevel 1 (
    echo 错误：打包环境不是 64 位 Python。
    goto :failed
)

echo [2/3] 正在检查依赖...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt -r build-requirements.txt
if errorlevel 1 goto :failed

echo [3/3] 正在生成 64 位单文件 EXE...
".venv\Scripts\python.exe" -c "from static_ffmpeg import run; run.get_or_fetch_platform_executables_else_raise()"
if errorlevel 1 goto :failed

".venv\Scripts\python.exe" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name "悬浮录屏工具" ^
    --hidden-import "mss.windows" ^
    --collect-all "dxcam" ^
    --collect-all "static_ffmpeg" ^
    --collect-all "UnityPy" ^
    --collect-all "dnfile" ^
    --collect-all "dncil" ^
    --collect-all "pypdf" ^
    --collect-all "zstandard" ^
    "screen_recorder.py"
if errorlevel 1 goto :failed

echo.
echo 打包完成：dist\悬浮录屏工具.exe
pause
exit /b 0

:failed
echo.
echo 打包失败，请检查上方错误信息。
pause
exit /b 1
