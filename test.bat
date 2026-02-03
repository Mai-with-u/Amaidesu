@echo off
REM 快捷测试脚本 (Windows)

set CASE=%1
if "%CASE%"=="" set CASE=all

if "%CASE%"=="core" (
    uv run pytest tests/core/ -v
) else if "%CASE%"=="layers" (
    uv run pytest tests/layers/ -v
) else if "%CASE%"=="unit" (
    uv run pytest tests/ -m "not e2e" -v
) else if "%CASE%"=="e2e" (
    uv run pytest tests/e2e/ -v
) else (
    uv run pytest tests/ -v
)
