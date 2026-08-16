@echo off
REM Bring up the WebDAV bridge to the DNC box (port 8008 by default).
REM Needs a venv with wsgidav + cheroot installed (see requirements.txt).
echo Starting the WebDAV bridge to the DNC box on port 8008...
echo Map it on Windows: Explorer -^> Map network drive -^> http://%COMPUTERNAME%:8008/
echo (or:  net use Z: http://IP-OF-THIS-PC:8008/ )
echo.
python "%~dp0dnc_webdav.py"
pause
