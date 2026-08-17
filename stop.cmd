@echo off
rem Stop the AI gateway stack (containers stopped, data kept)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File stop.ps1
pause