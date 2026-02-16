#!/bin/bash
# 快捷测试脚本
# 从 scripts 目录运行，自动切换到项目根目录

cd "$(dirname "$0")/.."

CASE=${1:-all}

case $CASE in
  core)
    uv run pytest tests/core/ -v
    ;;
  domains)
    uv run pytest tests/domains/ -v
    ;;
  input)
    uv run pytest tests/domains/input/ -v
    ;;
  decision)
    uv run pytest tests/domains/decision/ -v
    ;;
  output)
    uv run pytest tests/domains/output/ -v
    ;;
  services)
    uv run pytest tests/services/ -v
    ;;
  architecture)
    uv run pytest tests/architecture/ -v
    ;;
  integration)
    uv run pytest tests/integration/ -v
    ;;
  prompts)
    uv run pytest tests/prompts/ -v
    ;;
  unit)
    uv run pytest tests/ -m "not integration" -v
    ;;
  all|*)
    uv run pytest tests/ -v
    ;;
esac
