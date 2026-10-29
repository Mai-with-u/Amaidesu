"""
ToolOperations —— 工具运营面：注册表的只读观测视图 + 手动重连编排

``ToolRegistry`` 的公开接口保持窄面（注册/查询/调用/熔断委托）；给
Dashboard 工具页与运维动作消费的"运营面"摘到本类：

- 提供者目录视图（``list_providers``，工具页提供者卡片数据源）
- 健康快照（``tool_health_snapshot``，熔断器状态的 dashboard 形状）
- Dashboard 标注投影（``scoped_owner_of`` / ``provider_supports_reconnect``）
- 手动重连编排（``reconnect_provider``：重连 → 工具集刷新 → 探活复位熔断；
  失败报告携带机器可读的 ``reason`` 枚举，供 HTTP 层映射状态码）

只读依赖：构造注入 ``ToolRegistry``，不持有登记账目。依赖协议是**同包
内部协议**——本类与 ``ToolRegistry`` 同包，允许读其私有登记账目
（``_providers`` / ``_tools`` / ``_tool_owner`` / ``_visible_to`` /
``_categories`` / ``_health``），只读不写；写路径（注册/换血/停用）仍归
registry，重连编排内的工具集刷新与探活复位也经 registry 的公开方法
（``refresh_provider_tools`` / ``probe_tool`` / ``recover_tool``）执行。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.modules.logging import get_logger
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.registry import DEFAULT_VISIBLE_TO

if TYPE_CHECKING:
    from src.modules.tools.registry import ToolRegistry


logger = get_logger("ToolOperations")


class ToolOperations:
    """工具运营面视图与重连编排（``ToolRegistry`` 的只读伴生对象）。

    Attributes:
        registry: 只读依赖的工具注册表（构造注入；本类不修改其登记账目）
    """

    def __init__(self, registry: "ToolRegistry") -> None:
        self._registry = registry

    # -------------------- 提供者目录视图 --------------------

    def list_providers(self) -> List[Dict[str, Any]]:
        """列出全部已登记 Provider 的运营摘要（工具页提供者卡片数据源）。

        每项：``name`` / ``category``（自声明，未声明空串）/ ``tool_count``
        （当前登记工具数，含停用与熔断）/ ``disabled_count`` /
        ``supports_reconnect`` / ``last_error``（Provider 侧最近一次连接失败
        摘要，无则空串）/ ``switch``（提供者开关地址声明，如 Agent 私有
        MCP 的 ``{"file", "key"}``，未声明为 None）/ ``connected``（维护外部连接的
        Provider 的连接态，无连接概念为 None）。**0 工具的 Provider
        也在列**——降级登记（连接失败）的 Provider 靠这条在工具页保持
        可见、可手动重连。
        """
        registry = self._registry
        result: List[Dict[str, Any]] = []
        for p in registry._providers:
            owned = [name for name, owner in registry._tool_owner.items() if owner is p]
            if not owned:
                # 非 BaseToolProvider 注册无归属记录：按 spec.provider 名兜底
                owned = [name for name, (spec, _) in registry._tools.items() if spec.provider == p.name]
            switch = getattr(p, "switch_config", None)
            # 维护外部连接的 Provider 报告连接态（None = 没有连接概念）：工具页据此区分
            # "连着但不向 LLM 暴露工具"（如 VTS）与"没连上所以 0 工具"
            summary_fn = getattr(p, "connection_summary", None)
            connected: Optional[bool] = None
            if callable(summary_fn):
                try:
                    connected = bool(summary_fn().get("is_connected"))
                except Exception:  # noqa: BLE001 - 连接摘要读失败按未知处理
                    connected = None
            result.append(
                {
                    "name": p.name,
                    "category": registry._categories.get(p.name, ""),
                    "tool_count": len(owned),
                    "disabled_count": sum(1 for n in owned if n in registry._disabled),
                    "supports_reconnect": isinstance(p, BaseToolProvider) and bool(p.supports_reconnect),
                    "last_error": str(getattr(p, "last_error", "") or ""),
                    "switch": dict(switch) if isinstance(switch, dict) else None,
                    "connected": connected,
                }
            )
        return result

    # -------------------- 健康快照 --------------------

    def tool_health_snapshot(self) -> Dict[str, Dict[str, Any]]:
        """返回对 Dashboard 有意义的工具健康快照（委托熔断器状态）。"""
        return self._registry._health.tool_health_snapshot()

    # -------------------- Dashboard 标注投影 --------------------

    def scoped_owner_of(self, full_name: str) -> str:
        """按工具全名查归属 Agent（可见名单的单归属投影；无专属归属返回空串）。

        归属口径：可见名单恰为**单个**非默认 Agent 时视为"专属归属"（如
        只对 minecraft 可见的委派工具 → "minecraft"）；默认名单、``["*"]``
        全员、多 Agent 共享与未声明（落默认名单）都算无专属归属 → 空串。
        供 Dashboard 工具页标注归属；调用可见性不受影响（可见名单仍是
        唯一可见性事实源，本方法只是它的单归属投影）。
        """
        entries = self._registry._visible_to.get(full_name)
        if entries is not None and len(entries) == 1 and entries != DEFAULT_VISIBLE_TO and entries != ["*"]:
            return entries[0]
        return ""

    def provider_supports_reconnect(self, tool_name: str) -> bool:
        """按工具名查归属 Provider 是否支持手动重连（供 Dashboard 工具页渲染按钮用）。

        无归属 Provider / 归属对象非 ``BaseToolProvider`` / Provider 的
        ``supports_reconnect`` 为 False 任一条件触发即返回 False。未知工具名
        同样 False。不解名/字符串猜测，按 ``_tool_owner`` 直查——与探活的
        归属解析口径一致。
        """
        owner = self._registry._tool_owner.get(tool_name)
        if not isinstance(owner, BaseToolProvider):
            return False
        return bool(owner.supports_reconnect)

    # -------------------- 手动重连编排 --------------------

    async def reconnect_provider(self, provider_id: str) -> Dict[str, Any]:
        """手动重连指定 Provider；成功后对归属其工具的熔断器联动探活复位。

        行为：
        - ``provider_id`` = Provider.name（按登记列表线性查找）。
          provider.name 与 spec.provider 同值同源（注册期校验保证），外部
          （Dashboard 等）传提供者短名即可命中。
        - 失败报告携带机器可读的 ``reason`` 字段（``not_registered`` /
          ``not_supported`` / ``reconnect_failed``），中文文案保留在
          ``error``——HTTP 层按 reason 映射状态码，不做文案子串匹配。
        - 未找到 → ``{"ok": False, "reason": "not_registered", "error": ...}``
        - 找到但不支持重连（非 BaseToolProvider 或 ``supports_reconnect``
          为 False）→ ``{"ok": False, "reason": "not_supported", "error": ...}``
        - 找到且支持 → ``await provider.reconnect()``：
          - 失败 → ``{"ok": False, "reason": "reconnect_failed", "error": ...,
            "provider_id": <id>}``
          - 成功 → 经 registry 刷新工具集（降级登记补注册 / 清单换血），
            再反查归属工具逐个 ``probe_tool``，通过且 ``is_tripped`` 即
            ``recover_tool``；返回 ``{"ok": True, "provider_id": ...,
            "recovered": [<已复位工具名>], "still_tripped":
            [<探活未通过的熔断工具名>]}``。未熔断的工具不纳入报告
            （运营只需关心"恢复 + 仍未恢复"两个集合）。
        """
        registry = self._registry
        provider = next((p for p in registry._providers if p.name == provider_id), None)
        if provider is None:
            logger.warning(f"手动重连失败：未注册 Provider '{provider_id}'")
            return {"ok": False, "reason": "not_registered", "error": f"未注册 Provider: {provider_id}"}
        if not isinstance(provider, BaseToolProvider) or not provider.supports_reconnect:
            logger.warning(
                f"手动重连失败：Provider '{provider_id}'（class={type(provider).__name__}）"
                "不支持手动重连（无覆写 connect 或非 BaseToolProvider）"
            )
            return {
                "ok": False,
                "reason": "not_supported",
                "error": f"Provider '{provider_id}' 不支持手动重连",
            }

        logger.info(f"手动触发 Provider '{provider_id}' 重连")
        try:
            ok = bool(await provider.reconnect())
        except Exception as exc:  # noqa: BLE001 - 重连边界兜底，不上抛
            logger.error(
                f"Provider '{provider_id}' 重连异常: {type(exc).__name__}: {exc}",
                exc=True,
            )
            return {
                "ok": False,
                "reason": "reconnect_failed",
                "error": f"重连失败: {type(exc).__name__}: {exc}",
                "provider_id": provider_id,
            }
        if not ok:
            logger.warning(f"Provider '{provider_id}' 手动重连失败（provider.reconnect 返回 False）")
            return {
                "ok": False,
                "reason": "reconnect_failed",
                "error": "重连失败: provider.reconnect 返回 False",
                "provider_id": provider_id,
            }

        # 重连成功先刷新工具集（MCP 降级登记 0 工具 → 重连后补注册；server
        # 侧清单变化 → 换血），再对归属工具做熔断探活复位
        try:
            refresh = registry.refresh_provider_tools(provider)
        except Exception as exc:  # noqa: BLE001 - 刷新异常不推翻重连成果，报告携带原因
            logger.error(
                f"Provider '{provider_id}' 重连后工具集刷新异常: {type(exc).__name__}: {exc}",
                exc=True,
            )
            refresh = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        recovered: List[str] = []
        still_tripped: List[str] = []
        for tool_name in registry._tools_owned_by(provider):
            if not registry.is_tripped(tool_name):
                continue
            healthy = await registry.probe_tool(tool_name)
            if healthy and registry.recover_tool(tool_name):
                recovered.append(tool_name)
            else:
                still_tripped.append(tool_name)
        logger.info(
            f"Provider '{provider_id}' 重连成功，恢复 {len(recovered)} 个熔断工具"
            + (f"，仍有 {len(still_tripped)} 个未通过探活" if still_tripped else "")
            + (f"，工具集新增 {len(refresh.get('added', []))} 个" if refresh.get("ok") else "")
        )
        return {
            "ok": True,
            "provider_id": provider_id,
            "recovered": recovered,
            "still_tripped": still_tripped,
            "refreshed": {
                "added": refresh.get("added", []),
                "removed": refresh.get("removed", []),
                "count": refresh.get("count", 0),
            }
            if refresh.get("ok")
            else None,
        }


__all__ = ["ToolOperations"]
