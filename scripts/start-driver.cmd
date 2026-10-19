@echo off
rem Starts the canon-m3-ascom camera driver (Alpaca server on port 11112). Close the window to stop it.
cd /d "%~dp0.."
title canon-m3-ascom driver
".venv\Scripts\canon-m3-ascom.exe" --config config.toml
pause
