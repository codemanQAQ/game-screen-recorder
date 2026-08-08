@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    python -m venv .venv
    if errorlevel 1 goto :failed
)

".venv\Scripts\python.exe" -c "import dxcam,mss,numpy,static_ffmpeg; from importlib.metadata import version; assert version('dxcam')=='0.3.0' and version('mss')=='10.1.0' and version('numpy')=='2.3.2' and version('static-ffmpeg')=='3.0'" >nul 2>&1
if errorlevel 1 (
    echo 首次启动，正在安装运行环境，请稍候...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto :failed
)

".venv\Scripts\python.exe" -c "from static_ffmpeg import run; run.get_or_fetch_platform_executables_else_raise()" >nul
if errorlevel 1 goto :failed

start "" ".venv\Scripts\pythonw.exe" "screen_recorder.py"
exit /b 0

:failed
echo.
echo 安装失败，请确认电脑已安装 Python 3.10 或更高版本，并且网络连接正常。
pause
exit /b 1
