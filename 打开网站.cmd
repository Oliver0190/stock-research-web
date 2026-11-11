@echo off
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0start.ps1" -OpenBrowser
if errorlevel 1 pause
