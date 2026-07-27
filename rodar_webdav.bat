@echo off
REM Sobe a ponte WebDAV da caixinha DNC (porta 8008 por padrao).
REM Precisa de um venv com wsgidav + cheroot instalados (ver requirements.txt).
echo Subindo WebDAV da caixinha DNC na porta 8008...
echo Mapear no Windows: Explorer -^> Mapear unidade de rede -^> http://%COMPUTERNAME%:8008/
echo (ou:  net use Z: http://IP-DESTE-PC:8008/ )
echo.
python "%~dp0dnc_webdav.py"
pause
