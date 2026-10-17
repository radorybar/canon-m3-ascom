@echo off
rem Starts the m3alpaca camera driver (Alpaca server on port 11112). Close the window to stop it.
cd /d "%~dp0.."
title m3alpaca driver
".venv\Scripts\m3alpaca.exe" --config config.toml
pause
