#!/bin/bash
# 快捷测试脚本

CASE=${1:-all}

case $CASE in
  core)
    uv run pytest tests/core/ -v
    ;;
  layers)
    uv run pytest tests/layers/ -v
    ;;
  unit)
    uv run pytest tests/ -m "not e2e" -v
    ;;
  e2e)
    uv run pytest tests/e2e/ -v
    ;;
  all|*)
    uv run pytest tests/ -v
    ;;
esac
