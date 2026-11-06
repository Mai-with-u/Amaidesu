"""write_config_updates 的 MCP 地址不变量：启用的 MCP 条目必须有连接地址才能落盘

覆盖全局 MCP 注册表（tools.mcp.config.servers.<名>）与 Agent 私有 MCP（agents.<名>.mcp）。
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from src.modules.config.multi_file_loader import generate_default_configs
from src.modules.config.webui import write_config_updates

_SERVER = "tools.tools.mcp.config.servers.srv"
_AGENT_MCP = "agents.agents.minecraft.mcp"


class _ConfigServiceStub:
    """写管线只用到 main_config 与 reload_config 两个接口。"""

    def __init__(self) -> None:
        self.main_config: dict[str, Any] = {}

    async def reload_config(self, changed_scopes: list[str]) -> bool:
        return True


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    path = tmp_path / "config"
    generate_default_configs(path)
    return path


async def _write(config_dir: Path, *changes: tuple[str, Any]) -> Any:
    return await write_config_updates(_ConfigServiceStub(), config_dir, list(changes))  # type: ignore[arg-type]


def _read(config_dir: Path, file_name: str) -> dict[str, Any]:
    return tomllib.loads((config_dir / file_name).read_text(encoding="utf-8"))


async def test_tools_enable_without_address_rejected(config_dir: Path) -> None:
    before = (config_dir / "tools.toml").read_text(encoding="utf-8")
    result = await _write(config_dir, (_SERVER, {"enabled": True, "transport": "http", "url": ""}))
    assert result.success is False
    assert "tools.mcp.config.servers.srv" in result.message and "先配置连接地址再启用" in result.message
    assert (config_dir / "tools.toml").read_text(encoding="utf-8") == before


async def test_tools_enable_only_key_checks_file_address(config_dir: Path) -> None:
    """只写 enabled 键时地址取文件现值：现值为空拒绝，现值有地址放行。"""
    assert (await _write(config_dir, (_SERVER, {"enabled": False, "transport": "stdio"}))).success
    assert (await _write(config_dir, (f"{_SERVER}.enabled", True))).success is False

    assert (await _write(config_dir, (f"{_SERVER}.command", "npx"))).success  # 只改地址不动 enabled
    assert (await _write(config_dir, (f"{_SERVER}.enabled", True))).success
    assert _read(config_dir, "tools.toml")["tools"]["mcp"]["config"]["servers"]["srv"]["enabled"] is True


async def test_tools_enable_and_address_same_batch_allowed(config_dir: Path) -> None:
    assert (await _write(config_dir, (_SERVER, {"enabled": False}))).success
    result = await _write(config_dir, (f"{_SERVER}.enabled", True), (f"{_SERVER}.url", "http://127.0.0.1:1/mcp"))
    assert result.success is True


async def test_tools_disabled_without_address_allowed(config_dir: Path) -> None:
    assert (await _write(config_dir, (_SERVER, {"enabled": False, "url": ""}))).success


async def test_agent_enable_without_address_rejected(config_dir: Path) -> None:
    assert (await _write(config_dir, (f"{_AGENT_MCP}.url", ""), (f"{_AGENT_MCP}.enabled", False))).success
    result = await _write(config_dir, (f"{_AGENT_MCP}.enabled", True))
    assert result.success is False and "agents.minecraft.mcp" in result.message


async def test_agent_enable_with_address_allowed(config_dir: Path) -> None:
    assert (await _write(config_dir, (f"{_AGENT_MCP}.enabled", False))).success
    assert (await _write(config_dir, (f"{_AGENT_MCP}.url", "http://127.0.0.1:2/mcp"))).success
    assert (await _write(config_dir, (f"{_AGENT_MCP}.enabled", True))).success


async def test_agent_enable_and_address_same_batch_allowed(config_dir: Path) -> None:
    assert (await _write(config_dir, (f"{_AGENT_MCP}.url", ""), (f"{_AGENT_MCP}.enabled", False))).success
    result = await _write(config_dir, (f"{_AGENT_MCP}.url", "http://127.0.0.1:2/mcp"), (f"{_AGENT_MCP}.enabled", True))
    assert result.success is True


async def test_agent_disabled_without_address_allowed(config_dir: Path) -> None:
    assert (await _write(config_dir, (f"{_AGENT_MCP}.url", ""), (f"{_AGENT_MCP}.enabled", False))).success
    assert _read(config_dir, "agents.toml")["agents"]["minecraft"]["mcp"]["url"] == ""
