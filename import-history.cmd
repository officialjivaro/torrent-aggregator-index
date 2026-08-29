@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0import-history.ps1" %*
exit /b %ERRORLEVEL%
