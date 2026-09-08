"""通用 MCP 外部工具源（通道层，不含任何具体 MCP server 的知识）

定位：
- 读取 ``[tools.mcp]`` 配置 → 为每个 MCP server 建立连接 →
  把 server 暴露的工具**全局注册**到 ToolRegistry
  （provider 默认 = server 名，如 maicraft → "maicraft"）
- 任何 Agent（主播 / 游戏）均可像普通 code agent 一样看到并调用这些工具
  （ToolRegistry 统一分发）
- **不包含 server 特定语义**：工具名的语义化（如 goal 组装、资源
  订阅解释）由消费方内容层负责，本模块只做通道

模块结构：
- ``config.py``：配置 Schema（McpServerConfig / McpExternalConfig）
- ``client.py``：FastMCP 客户端封装（transport 选择/连接/拉工具/调用/关闭）
- ``mapper.py``：Tool→ToolSpec / CallToolResult→ToolExecutionResult 纯函数
- ``provider.py``：McpToolProvider（ToolProvider 协议实现，缓存 specs）

装配入口（生产路径，组合根调用）::

    from src.modules.mcp import bind_mcp_tools

    report = await bind_mcp_tools(registry, mcp_config_dict)
    # report = {"servers": {"<server名>": {"ok", "tools", "error"}}}
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.modules.logging import get_logger
from src.modules.mcp.client import McpClient
from src.modules.mcp.config import McpExternalConfig, McpServerConfig
from src.modules.mcp.mapper import normalize_tool_name, to_result, to_spec
from src.modules.mcp.provider import McpToolProvider
from src.modules.tools.registry import ToolRegistry

logger = get_logger("McpTools")

# 装配报告结构：{server_name: {"ok": bool, "tools": int, "error": str}}
BindReport = Dict[str, Dict[str, Any]]


async def bind_mcp_tools(
    registry: ToolRegistry,
    raw_config: Optional[Dict[str, Any]] = None,
    *,
    provider: str = "",
) -> BindReport:
    """装配 MCP 外部工具源：连接所有配置的 server 并注册其工具。

    Args:
        registry: 目标 ToolRegistry（由组合根构造持有）
        raw_config: ``[tools.mcp].config`` 原始 dict
            （键 ``servers`` → {别名: McpServerConfig}）
        provider: 注册来源标记（默认 "" = 用 **server 名** 作为 provider 标识，
            如 maicraft → "maicraft"；也可显式传值覆盖，如 "game"）

    Returns:
        ``{server_name: {"ok", "tools", "error"}}`` 报告。
        单个 server 失败不影响其它 server（隔离边界）。

    Example:
        config = McpExternalConfig.parse_extra(tools_cfg)
        report = await bind_mcp_tools(registry, {"servers": {...}})
    """
    cfg = McpExternalConfig.parse_extra(raw_config)
    report: BindReport = {}

    for server_name in cfg.enabled_servers():
        server_cfg: McpServerConfig = cfg.servers[server_name]
        entry: Dict[str, Any] = {"ok": False, "tools": 0, "error": ""}
        try:
            client = McpClient(name=server_name, config=server_cfg)
            prov = McpToolProvider(
                client=client,
                server_name=server_name,
                prefix=server_cfg.prefix,
                provider=provider or server_name,
            )
            count = await prov.setup()
            if count == 0:
                # 连接失败或 server 无工具：不注册（避免空 Provider 进入 registry）
                entry["ok"] = False
                entry["error"] = "连接失败或 server 未暴露工具"
                await client.close()
            else:
                new_count = registry.register_provider(prov)
                entry["ok"] = True
                entry["tools"] = new_count
                if new_count == 0:
                    entry["error"] = "工具已全部被先注册项占用（重名跳过）"
                logger.info(f"MCP server '{server_name}' 工具注册完成（新注册 {new_count}/{count} 个）")
        except Exception as exc:  # noqa: BLE001 - 单 server 隔离边界
            logger.error(f"MCP server '{server_name}' 装配失败: {type(exc).__name__}: {exc}")
            entry["error"] = f"{type(exc).__name__}: {exc}"
        report[server_name] = entry

    return report


async def close_mcp_providers(registry: ToolRegistry) -> None:
    """关闭所有仍连接着的 MCP Provider（优雅停机用；幂等）。

    遍历 registry 的 provider，对 McpToolProvider 实例调用 close。
    """
    # 通过 registry 的内部 provider 列表访问（避免破坏封装——仅诊断用途
    # 的关闭钩子，不新增公开 API）
    providers = registry._providers
    for prov in providers:
        if isinstance(prov, McpToolProvider):
            try:
                await prov.close()
            except Exception as exc:  # noqa: BLE001 - 关闭边界兜底
                logger.warning(f"关闭 MCP provider '{prov.name}' 异常: {type(exc).__name__}: {exc}")


__all__ = [
    # 配置
    "McpExternalConfig",
    "McpServerConfig",
    # 通道
    "McpClient",
    "McpToolProvider",
    # 映射
    "normalize_tool_name",
    "to_spec",
    "to_result",
    # 装配
    "bind_mcp_tools",
    "close_mcp_providers",
]
