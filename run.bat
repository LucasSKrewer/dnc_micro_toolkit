@echo off
REM ============================================================
REM  Shortcut to run the scripts with 32-bit Python.
REM  If "py -3-32" does not work, replace it with the commented
REM  line below, pointing at your 32-bit python.exe.
REM ============================================================
cd /d "%~dp0"

set PY=py -3-32
REM set PY="C:\Python312-32\python.exe"

:menu
echo.
echo ===== DNC MICRO TOOLKIT =====
echo  1) FOCAS connection test (phase 3)
echo  2) Send/receive over FOCAS (phase 4)
echo  3) Send/receive over SERIAL (Romi/Siemens)
echo  4) Machine telemetry over FOCAS (read-only)
echo  5) Probe the WiFi DNC box
echo  6) ACCEPTANCE RUN against the DNC box (read-only)
echo  7) ACCEPTANCE RUN including write checks
echo  8) Run the test suite (no hardware needed)
echo  0) Quit
set /p op="Choose: "

if "%op%"=="1" ( %PY% focas_probe.py & pause & goto menu )
if "%op%"=="2" ( %PY% focas_transfer.py & pause & goto menu )
if "%op%"=="3" ( python serial_adapter.py & pause & goto menu )
if "%op%"=="4" ( %PY% focas_telemetry.py --watch 2 & pause & goto menu )
if "%op%"=="5" ( python dnc_tftp.py & pause & goto menu )
if "%op%"=="6" ( python hardware_check.py --read-only & pause & goto menu )
if "%op%"=="7" ( python hardware_check.py & pause & goto menu )
if "%op%"=="8" ( python -m pytest tests/ -q & pause & goto menu )
if "%op%"=="0" goto end
goto menu

:end
