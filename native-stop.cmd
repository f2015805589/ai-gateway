@echo off
rem Native (no Docker) mode: native-stop
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0native-stop.ps1"
pause