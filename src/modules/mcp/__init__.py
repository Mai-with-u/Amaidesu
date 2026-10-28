"""通用 MCP 外部工具源（通道层，不含任何具体 MCP server 的知识）

定位：
- 读取 ``[tools.mcp]`` 配置 → 为每个 MCP server 建立连接 →
  把 server 暴露的工具**全局注册**到 ToolRegistry
  （provider 默认 = server 名，如 maicraft → "maicraft"）
- 任何 Agent（主播 / 游戏）均可像普通 code agent 一样看到并调用这些工具
  （ToolRegistry 统一分发）
- **不包含 server 特定语义**：工具名的语义化（如 goal 组装、资源
  订阅解释）由消费方内容层负责，本模块只做通道
- 装配时连不上的 server 以 0 工具占位登记（工具页可见、可手动重连），
  后台退避重试；连上后自动补注册工具，无需重启

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

from functools import partial
from typing import Any, Callable, Dict, Optional

from src.modules.logging import get_logger
from src.modules.mcp.client import McpClient
from src.modules.mcp.config import McpExternalConfig, McpServerConfig
from src.modules.mcp.mapper import to_result, to_spec
from src.modules.mcp.provider import McpToolProvider
from src.modules.tools.registry import ToolRegistry

logger = get_logger("McpTools")

# 装配报告结构：{server_name: {"ok": bool, "tools": int, "error": str}}
BindReport = Dict[str, Dict[str, Any]]

# 按 (server 名, 配置) 构造客户端的工厂：装配入口的构造接缝，
# 测试注入进程内替身时经此传入，不替换公开类属性。
McpClientFactory = Callable[[str, McpServerConfig], McpClient]


def _default_client_factory(name: str, config: McpServerConfig) -> McpClient:
    return McpClient(name=name, config=config)


async def bind_mcp_tools(
    registry: ToolRegistry,
    raw_config: Optional[Dict[str, Any]] = None,
    *,
    provider: str = "",
    client_factory: Optional[McpClientFactory] = None,
) -> BindReport:
    """装配 MCP 外部工具源：连接所有配置的 server 并注册其工具。

    Args:
        registry: 目标 ToolRegistry（由组合根构造持有）
        raw_config: ``[tools.mcp].config`` 原始 dict
            （键 ``servers`` → {别名: McpServerConfig}）
        provider: 注册来源标记（默认 "" = 用 **server 名** 作为 provider 标识，
            如 maicraft → "maicraft"；也可显式传值覆盖，如 "game"）
        client_factory: 可选——客户端构造接缝（缺省构造真实 ``McpClient``；
            测试注入进程内替身用，不替换模块属性）

    Returns:
        ``{server_name: {"ok", "tools", "error", "degraded"}}`` 报告。
        单个 server 失败不影响其它 server（隔离边界）；连不上的 server
        ``degraded=True``：已占位登记并在后台重试，连上后自动补注册。

    Example:
        config = McpExternalConfig.parse_extra(tools_cfg)
        report = await bind_mcp_tools(registry, {"servers": {...}})
    """
    cfg = McpExternalConfig.parse_extra(raw_config)
    report: BindReport = {}
    make_client = client_factory or _default_client_factory

    for server_name in cfg.enabled_servers():
        server_cfg: McpServerConfig = cfg.servers[server_name]
        entry: Dict[str, Any] = {"ok": False, "tools": 0, "error": "", "degraded": False}
        try:
            client = make_client(server_name, server_cfg)
            prov = McpToolProvider(
                client=client,
                server_name=server_name,
                provider=provider or server_name,
            )
            try:
                count = await prov.setup()
            except Exception as exc:  # noqa: BLE001 - 装配异常按连接失败同径降级
                logger.warning(f"MCP server '{server_name}' 装配异常，按连接失败降级: {type(exc).__name__}: {exc}")
                count = 0
            new_count = registry.register_provider(prov)
            # 登记之后才挂连接态回调：初次 setup 时 provider 尚未登记，此刻刷新只会报
            # "未注册"。之后无论后台恢复、手动重连还是断开，连接跃迁都经这里换血。
            prov.on_connection_changed = partial(_on_connection_changed, registry, prov)
            if count == 0:
                # server 没起来是常态（如 MaiBot 晚于本进程启动）：0 工具占位登记，
                # 工具页可见、可手动重连；后台退避重试，连上即补注册，无需重启
                entry["degraded"] = True
                entry["error"] = "连接失败或 server 未暴露工具（已占位登记，后台自动重连）"
                prov.start_recovery()
            else:
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


def _on_connection_changed(registry: ToolRegistry, prov: McpToolProvider, connected: bool) -> None:
    """连接态跃迁后按 provider 当前清单换血登记：连上补注册、断开整体摘除。"""
    report = registry.refresh_provider_tools(prov)
    count = len(list(prov.list_tools()))
    if report.get("ok"):
        action = "补注册" if connected else "摘除"
        logger.info(f"MCP server '{prov.server_name}' 工具集已刷新（{action}，当前 {count} 个）")
    else:
        logger.warning(f"MCP server '{prov.server_name}' 工具集刷新失败: {report.get('error')}")


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
    "to_spec",
    "to_result",
    # 装配
    "bind_mcp_tools",
    "close_mcp_providers",
]
