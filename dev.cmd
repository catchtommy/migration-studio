@echo off
rem Starts the backend and the UI on Windows (double-click, or run "dev" in a terminal). See dev.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev.ps1" %*
