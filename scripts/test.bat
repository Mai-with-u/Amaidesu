@echo off
REM 快捷测试脚本 (Windows)
REM 从 scripts 目录运行，自动切换到项目根目录

cd /d "%~dp0.."

set CASE=%1
if "%CASE%"=="" set CASE=all

if "%CASE%"=="core" (
    uv run pytest tests/core/ -v
) else if "%CASE%"=="domains" (
    uv run pytest tests/domains/ -v
) else if "%CASE%"=="input" (
    uv run pytest tests/domains/input/ -v
) else if "%CASE%"=="decision" (
    uv run pytest tests/domains/decision/ -v
) else if "%CASE%"=="output" (
    uv run pytest tests/domains/output/ -v
) else if "%CASE%"=="services" (
    uv run pytest tests/services/ -v
) else if "%CASE%"=="architecture" (
    uv run pytest tests/architecture/ -v
) else if "%CASE%"=="integration" (
    uv run pytest tests/integration/ -v
) else if "%CASE%"=="prompts" (
    uv run pytest tests/prompts/ -v
) else if "%CASE%"=="unit" (
    uv run pytest tests/ -m "not integration" -v
) else (
    uv run pytest tests/ -v
)
