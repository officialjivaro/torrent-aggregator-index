@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-index.ps1" %*
exit /b %ERRORLEVEL%
