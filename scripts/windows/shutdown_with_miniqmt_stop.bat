@echo off
setlocal

set "REPO_ROOT=%~dp0..\.."
set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "STOP_SCRIPT=%REPO_ROOT%\scripts\windows\stop_miniqmt.ps1"

"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%STOP_SCRIPT%"
timeout /t 2 /nobreak >nul
shutdown /s /t 0
