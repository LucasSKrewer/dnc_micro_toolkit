@echo off
REM ============================================================
REM  Atalho pra rodar os scripts com o Python 32-bit.
REM  Se "py -3-32" nao funcionar, troque pela linha comentada
REM  abaixo apontando pro seu python.exe 32-bit.
REM ============================================================
cd /d "%~dp0"

set PY=py -3-32
REM set PY="C:\Python312-32\python.exe"

:menu
echo.
echo ===== CNC FOCAS =====
echo  1) Teste de conexao FOCAS (Fase 3)
echo  2) Passar/Receber via FOCAS (Fase 4)
echo  3) Passar/Receber via SERIAL (Romi/Siemens)
echo  0) Sair
set /p op="Escolha: "

if "%op%"=="1" ( %PY% foco_teste.py & pause & goto menu )
if "%op%"=="2" ( %PY% foco_transfer.py & pause & goto menu )
if "%op%"=="3" ( %PY% serial_adapter.py & pause & goto menu )
if "%op%"=="0" goto fim
goto menu

:fim
