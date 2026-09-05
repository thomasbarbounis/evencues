@echo off
cd /d "%~dp0"
start "" http://127.0.0.1:5151
uv run evencues-web
