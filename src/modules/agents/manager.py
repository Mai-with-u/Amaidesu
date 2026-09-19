"""
AgentManager —— 框架 Agent 统一管理

- 注册 Agent（按名；重复 → 跳过后注册）
- 统一启动 / 停止 / cleanup
- 心跳 + 监控 + 可重建性（崩溃重启前提）

工具聚合责任边界：
- Agent 子类在 ``_on_start`` / ``_register_tools`` 内部构造 ToolProvider 并调用
  ``registry.register_provider(...)``（所有权内聚到 Agent 包内）。
- AgentManager 仅提供 ``audit_tools(registry)`` 纯只读审计：列出 Agent 已声明
  但 registry 未注册的工具名，便于组合根在启动后日志告警。
- 不提供 ``register_all_tools`` / ``collect_tool_specs`` —— 框架侧合成占位
  实现桥接会污染真实注册路径，违反 Agent 主体性。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.modules.agents.base import AgentState, BaseAgent
from src.modules.config.core_schemas import AgentSupervisorConfig
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.tools.registry import ToolRegistry

logger = get_logger("AgentManager")


@dataclass(slots=True)
class AgentRegistration:
    """Agent 注册项（name → agent 实例 + 元数据）"""

    name: str
    agent: BaseAgent
    description: str = ""
    registered_at_ms: int = 0
    spec_provider: str = "framework"  # 工具来源溯源标记


class AgentManager:
    """统一管理：注册 / 启动 / 监控 / 重启所有 Agent。

    Attributes:
        tool_registry: 构造时注入的默认 ``ToolRegistry``；传给
            ``enable_agent`` 时如未显式覆盖则用此成员（Dashboard 动态启停
            场景可能不传 tool_registry，回退到成员避免 Agent 拿 None）。
        memory: 构造时注入的默认 ``MemoryProvider`` 实例；Agent（如
            ``StreamerAgent``）构造时需要它做观众画像/长记忆读写。Dashboard
            动态启用 Agent 时如未显式传 ``memory``，回退到此成员。
    """

    def __init__(
        self,
        *,
        tool_registry: Optional[ToolRegistry] = None,
        memory: Optional[Any] = None,
        supervisor_config: Optional[AgentSupervisorConfig] = None,
    ) -> None:
        self._agents: Dict[str, AgentRegistration] = {}
        # enable 时记住的构造入参（name → kwargs 字典，含 config 与基建透传）；
        # rebuild 按它经同一构造路径重建实例
        self._enable_args: Dict[str, Dict[str, Any]] = {}
        self._tool_registry = tool_registry
        self._memory = memory
        self._lock = asyncio.Lock()
        # ----- 守护（心跳巡检 + 自动重建 + 风暴保护）-----
        self._supervisor_config = supervisor_config if supervisor_config is not None else AgentSupervisorConfig()
        self._supervisor_task: Optional["asyncio.Task[None]"] = None
        # name → 时间窗内重建失败时刻列表（毫秒时间戳；风暴保护计数）
        self._rebuild_failures: Dict[str, List[int]] = {}
        # 达到失败上限后停止自动重建的 Agent 名（人工介入前不再重试）
        self._supervisor_quarantined: set[str] = set()

    # -------------------- 注册 --------------------

    def register(
        self,
        agent: BaseAgent,
        *,
        description: Optional[str] = None,
        spec_provider: str = "framework",
    ) -> bool:
        """注册一个 Agent。

        Args:
            agent: BaseAgent 实例
            description: 显示描述（默认用 agent.description）
            spec_provider: 工具来源溯源（"framework" / Agent 名；"mcp" 预留枚举值，暂无实现）
        """
        if not agent.name:
            logger.warning(
                f"Agent 缺少 name（class={type(agent).__name__}），由 AgentManager 注册时显式拒绝；"
                f"请在子类显式声明 name = 'xxx'"
            )
            return False
        if agent.name in self._agents:
            logger.debug(f"Agent '{agent.name}' 已注册，跳过")
            return False

        self._agents[agent.name] = AgentRegistration(
            name=agent.name,
            agent=agent,
            description=description or agent.description,
            registered_at_ms=now_ms(),
            spec_provider=spec_provider,
        )
        logger.info(f"Agent '{agent.name}' 已注册 (spec_provider={spec_provider})")
        return True

    def unregister(self, name: str) -> bool:
        """从管理器移除（仅当已停止时）。"""
        reg = self._agents.get(name)
        if reg is None:
            return False
        if reg.agent.state not in (AgentState.STOPPED, AgentState.CREATED, AgentState.ERRORED):
            logger.warning(f"Agent '{name}' 未停止，不能 unregister（state={reg.agent.state}）")
            return False
        self._agents.pop(name, None)
        logger.info(f"Agent '{name}' 已从管理器移除")
        return True

    # -------------------- 查询 --------------------

    def list_agents(self) -> List[str]:
        return list(self._agents.keys())

    def list_running(self) -> List[str]:
        return [name for name, reg in self._agents.items() if reg.agent.state == AgentState.RUNNING]

    @property
    def descriptions(self) -> Dict[str, str]:
        """注册名 → 描述字典（dashboard 组件清单等展示面消费）。"""
        return {name: reg.description for name, reg in self._agents.items()}

    # -------------------- 动态启停（Dashboard 组件管理调用） --------------------

    def get_agent_by_name(self, name: str) -> Optional[BaseAgent]:
        """按注册名查找已加载的 Agent 实例（段名=注册名）。

        本类唯一的查名 API（外部统一走此入口）。
        """
        reg = self._agents.get(name)
        return reg.agent if reg is not None else None

    @property
    def tool_registry(self) -> Optional[ToolRegistry]:
        """默认工具注册中心（构造时注入；可能为 None）。"""
        return self._tool_registry

    async def start_agent(self, name: str) -> bool:
        """启动（或重启）单个已注册 Agent。"""
        reg = self._agents.get(name)
        if reg is None:
            return False
        if reg.agent.state == AgentState.RUNNING:
            return True
        try:
            await reg.agent.start()
        except Exception as exc:  # noqa: BLE001 - 边界
            logger.exception(f"Agent '{name}' 单实例启动失败: {exc}")
            return False
        return reg.agent.state == AgentState.RUNNING

    async def stop_agent(self, name: str) -> bool:
        """停止单个 Agent：调用 stop()（不 unregister）。"""
        reg = self._agents.get(name)
        if reg is None:
            return False
        try:
            await reg.agent.stop()
        except Exception as exc:  # noqa: BLE001 - 边界
            logger.exception(f"Agent '{name}' 单实例停止失败: {exc}")
            return False
        return True

    async def enable_agent(
        self,
        name: str,
        config: Optional[dict] = None,
        *,
        llm_manager: Optional[object] = None,
        prompt_manager: Optional[object] = None,
        event_bus: Optional[object] = None,
        tool_registry: Optional[ToolRegistry] = None,
        memory: Optional[Any] = None,
        thinking_sink: Optional[Any] = None,
        speech_config: Optional[Dict[str, Any]] = None,
        tts_engine: Optional[Any] = None,
        subtitle_service: Optional[Any] = None,
        session_manager: Optional[Any] = None,
        context_assembler_config: Optional[Any] = None,
        task_tracker: Optional[Any] = None,
    ) -> bool:
        """动态启用 Agent：实例化（配置段名）→ 注册 → 启动。

        段名 = 注册名：实例 name 与段名不一致时对齐为段名（生产子类均
        显式声明 name，不会触发；见下方对齐处注释），保证
        list_agents()/get_agent_by_name() 与配置 enabled 列表一致。

        构造经 ``factory.instantiate_agent`` 单一构造路径；基础设施参数
        （speech/tts/subtitle/session/thinking/task_tracker 等）按需透传，
        Dashboard 场景无对应基建时保持 None（Agent 各自降级）。

        ``tool_registry`` / ``memory`` 未显式传入时回退到 ``__init__`` 成员；
        Dashboard 动态启停场景一般不传这两个，回退保证 Agent 不再拿到 None。
        """
        from src.modules.agents.factory import instantiate_agent

        effective_registry = tool_registry if tool_registry is not None else self._tool_registry
        effective_memory = memory if memory is not None else self._memory

        instance = instantiate_agent(
            name,
            config or {},
            llm_manager=llm_manager,
            prompt_manager=prompt_manager,
            event_bus=event_bus,
            tool_registry=effective_registry,
            memory=effective_memory,
            thinking_sink=thinking_sink,
            speech_config=speech_config,
            tts_engine=tts_engine,
            subtitle_service=subtitle_service,
            session_manager=session_manager,
            context_assembler_config=context_assembler_config,
            task_tracker=task_tracker,
        )
        if instance is None:
            logger.warning(f"未实现的 Agent: {name}")
            return False
        # 触发条件：工厂产出实例的 name 与段名不一致。生产三子类均显式
        # 声明 name 类属性（与段名一致）不会触发；测试替身/未来子类漂移
        # 时兜底对齐，保证 list_agents()/get_agent_by_name() 与 enabled
        # 配置列表一致。
        if instance.name != name:
            instance.name = name
        if not self.register(instance):
            return False
        # 人工重新启用达限隔离的 Agent 时清除风暴保护记录（重新给出重建
        # 机会）；rebuild 的内部重试路径 name 不在隔离集，失败计数不被误清
        if name in self._supervisor_quarantined:
            self._supervisor_quarantined.discard(name)
            self._rebuild_failures.pop(name, None)
        # 记住构造入参（重建入口 rebuild 依赖；回退成员后的有效值不回填——
        # 重建时 enable_agent 会再走一次同样的回退逻辑）
        self._enable_args[name] = {
            "config": config,
            "llm_manager": llm_manager,
            "prompt_manager": prompt_manager,
            "event_bus": event_bus,
            "tool_registry": tool_registry,
            "memory": memory,
            "thinking_sink": thinking_sink,
            "speech_config": speech_config,
            "tts_engine": tts_engine,
            "subtitle_service": subtitle_service,
            "session_manager": session_manager,
            "context_assembler_config": context_assembler_config,
            "task_tracker": task_tracker,
        }
        return await self.start_agent(name)

    async def disable_agent(self, name: str) -> bool:
        """动态停用 Agent：停止 → 摘 provider → 关 MCP → unregister。

        provider 摘除与 MCP 客户端关闭主要由 Agent 自身 ``stop()`` 路径完成；
        此处兜底再调一次（均幂等），覆盖未接入清理契约的旧 Agent 子类。
        """
        if not await self.stop_agent(name):
            return False
        reg = self._agents.get(name)
        if reg is not None:
            try:
                reg.agent.unregister_tool_providers()
                await reg.agent.close_mcp_clients()
            except Exception as exc:  # noqa: BLE001 - 清理兜底边界
                logger.warning(f"Agent '{name}' 停用清理异常: {type(exc).__name__}: {exc}")
        self.unregister(name)
        self._enable_args.pop(name, None)
        logger.info(f"Agent '{name}' 已动态停用")
        return True

    async def rebuild(self, name: str) -> bool:
        """重建 Agent（控制面 restart / 心跳自愈复用）：完整清理 → 同路径重建 → 启动。

        前置：该 Agent 此前经 ``enable_agent`` 启用（构造入参已记住）。流程：
        ``disable_agent``（stop + 摘 provider + 关 MCP + unregister）→ 按
        记住的入参再次 ``enable_agent``（与首次启用同走 factory 单一构造
        路径）→ start。未记住入参（非 enable 途径注册）时拒绝重建。
        """
        args = self._enable_args.get(name)
        if args is None:
            logger.warning(f"Agent '{name}' 无 enable 记录，无法重建（仅支持经 enable_agent 启用的 Agent）")
            return False
        # 重启计数跨实例继承：重建产出全新实例（计数归零），先把累计值读出
        old_reg = self._agents.get(name)
        carried_restart_count = old_reg.agent.restart_count if old_reg is not None else 0
        if not await self.disable_agent(name):
            logger.warning(f"Agent '{name}' 重建前清理失败，放弃重建")
            return False
        logger.info(f"Agent '{name}' 开始重建（stop → 重新构造 → start）")
        if not await self.enable_agent(name, **args):
            return False
        new_agent = self.get_agent_by_name(name)
        if new_agent is not None:
            # 累计次数 + 1 写到新实例（观测面跨重建连续）
            new_agent.carry_restart_count(carried_restart_count + 1)
        return True

    # -------------------- 守护（心跳巡检 + 自动重建） --------------------

    def is_agent_alive(self, name: str) -> Optional[bool]:
        """按守护配置的判死阈值查询 Agent 存活状态；未注册名返回 None。"""
        reg = self._agents.get(name)
        if reg is None:
            return None
        return reg.agent.is_alive(dead_threshold_ms=self._supervisor_config.dead_threshold_ms)

    def start_supervisor(self) -> None:
        """启动低频巡检后台任务（组合根在 start_all 之后调用；幂等）。"""
        if self._supervisor_config.check_interval_ms <= 0:
            logger.info("[agent_supervisor].check_interval_ms<=0：巡检关闭，不启动守护循环")
            return
        if self._supervisor_task is not None and not self._supervisor_task.done():
            return
        self._supervisor_task = asyncio.create_task(self._supervisor_loop(), name="agent-supervisor")
        cfg = self._supervisor_config
        logger.info(
            f"Agent 守护循环已启动（check_interval_ms={cfg.check_interval_ms}, "
            f"dead_threshold_ms={cfg.dead_threshold_ms}, max_rebuild_failures={cfg.max_rebuild_failures}"
            f"@{cfg.rebuild_failure_window_ms}ms）"
        )

    async def stop_supervisor(self) -> None:
        """停止巡检后台任务（停机路径调用；幂等）。"""
        task = self._supervisor_task
        self._supervisor_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # 预期取消路径
        logger.info("Agent 守护循环已停止")

    async def _supervisor_loop(self) -> None:
        """巡检主循环：周期性对活跃 Agent 做存活检查并自动重建死者。"""
        interval_s = self._supervisor_config.check_interval_ms / 1000
        while True:
            await asyncio.sleep(interval_s)
            try:
                await self._supervise_once()
            except Exception as exc:  # noqa: BLE001 - 单轮巡检失败不终止守护
                logger.warning(f"Agent 巡检单轮异常（忽略，下轮继续）: {type(exc).__name__}: {exc}", exc=True)

    async def _supervise_once(self) -> None:
        """单轮巡检：对 STARTING/RUNNING/PAUSED 的 Agent 判死，超时则自动重建。"""
        cfg = self._supervisor_config
        for name in self.list_agents():
            if name in self._supervisor_quarantined:
                continue  # 风暴保护：已达失败上限，停止重试
            reg = self._agents.get(name)
            if reg is None:
                continue
            # 巡检范围：应存活的 Agent（STARTING/RUNNING/PAUSED），加上
            # 重建失败遗留的 ERRORED（有 enable 记录 = 可重建，窗口内重试）；
            # 其余终态（STOPPED 等）不误判、不重建
            if reg.agent.state not in (AgentState.STARTING, AgentState.RUNNING, AgentState.PAUSED, AgentState.ERRORED):
                continue
            if reg.agent.state == AgentState.ERRORED and name not in self._enable_args:
                continue  # 非 enable 途径注册的 ERRORED 实例不可重建，交给人工
            if reg.agent.is_alive(dead_threshold_ms=cfg.dead_threshold_ms):
                continue  # 空闲但心跳正常 → 不干预
            logger.warning(
                f"Agent '{name}' 心跳超时（>{cfg.dead_threshold_ms}ms，"
                f"last_heartbeat_ms={reg.agent.heartbeat.last_heartbeat_ms}），尝试自动重建"
            )
            if await self.rebuild(name):
                self._rebuild_failures.pop(name, None)
                logger.info(f"Agent '{name}' 心跳超时后自动重建成功")
            else:
                self._record_rebuild_failure(name)

    def _record_rebuild_failure(self, name: str) -> None:
        """记录一次重建失败；时间窗内达上限 → 置 ERRORED + 停止自动重试。"""
        cfg = self._supervisor_config
        now = now_ms()
        window = cfg.rebuild_failure_window_ms
        failures = [t for t in self._rebuild_failures.setdefault(name, []) if now - t < window]
        failures.append(now)
        self._rebuild_failures[name] = failures
        logger.error(f"Agent '{name}' 自动重建失败（窗口内第 {len(failures)}/{cfg.max_rebuild_failures} 次）")
        if len(failures) < cfg.max_rebuild_failures:
            return
        self._supervisor_quarantined.add(name)
        logger.error(
            f"Agent '{name}' 在 {window}ms 窗口内重建失败 {len(failures)} 次，达到上限，"
            f"停止自动重建（防重启风暴）；需人工介入后重新 enable 恢复"
        )
        # 状态置 ERRORED：重建路径大概率已把实例停在 ERRORED/STOPPED；
        # 若实例仍在名册且状态非终态，强制标死以如实反映"不可自动恢复"。
        reg = self._agents.get(name)
        if reg is not None and reg.agent.state not in (AgentState.ERRORED, AgentState.STOPPED):
            reg.agent._state = AgentState.ERRORED  # noqa: SLF001 - manager 对自身名册的标死特权

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._agents

    # -------------------- 生命周期 --------------------

    async def start_all(self) -> None:
        """按注册顺序启动所有 Agent。"""
        for name, reg in self._agents.items():
            try:
                await reg.agent.start()
            except Exception as exc:  # noqa: BLE001 - 边界
                logger.exception(f"Agent '{name}' 启动失败: {exc}")

    async def stop_all(self) -> None:
        """按注册**逆序**停止所有 Agent（LIFO 风格）。"""
        for name in reversed(list(self._agents.keys())):
            reg = self._agents.get(name)
            if reg is None:
                continue
            try:
                await reg.agent.stop()
            except Exception as exc:  # noqa: BLE001 - 边界
                logger.exception(f"Agent '{name}' 停止失败: {exc}")

    async def cleanup_all(self) -> None:
        """清理所有 Agent 资源。"""
        for name, reg in self._agents.items():
            try:
                await reg.agent.cleanup()
            except Exception as exc:  # noqa: BLE001 - 边界
                logger.exception(f"Agent '{name}' cleanup 失败: {exc}")

    # -------------------- 工具审计 --------------------

    def audit_tools(self, registry: ToolRegistry) -> List[str]:
        """审计：列出 Agent 已声明但 registry 未注册的工具（纯只读）。

        遍历所有已注册 Agent 的 ``list_tools()`` 声明；对每个 spec 的
        **派生全名**（``spec.full_name``）：
        - 若 ``registry.has(full_name)`` 为 False，加入缺失列表。
        - 若同全名被 2+ 个 Agent 声明，记 warning，**不**在缺失列表中重复
          （重复声明对"是否已注册"无影响）。

        Args:
            registry: 工具注册中心（实际项目里的 ``ToolRegistry`` 实例）。

        Returns:
            排序后的缺失工具全名列表（每个全名至多出现一次）。

        防御：
        - 若 ``agent.list_tools()`` 抛异常 → log warning + skip 该 Agent（不污染审计）。
        - 若 ``list_tools()`` 返回 ``None`` → 跳过该 Agent（不计入）。
        """
        missing: List[str] = []
        # 跨 Agent 的同名声明追踪：全名 -> 首次声明的 Agent 注册名
        declared_by: Dict[str, str] = {}

        for agent_name, reg in self._agents.items():
            try:
                spec_iter = reg.agent.list_tools()
            except Exception as exc:  # noqa: BLE001 - 审计边界，失败不中断
                logger.warning(
                    f"Agent '{agent_name}' 的 list_tools() 抛异常，已跳过审计: {exc}",
                    exc=True,
                )
                continue

            if spec_iter is None:
                continue

            for spec in spec_iter:
                full_name = spec.full_name
                first_agent = declared_by.get(full_name)
                if first_agent is not None:
                    logger.warning(
                        f"工具 '{full_name}' 被多个 Agent 声明：'{first_agent}' 与 "
                        f"'{agent_name}'；审计按首次声明记，重复声明不再计入缺失列表"
                    )
                    continue
                declared_by[full_name] = agent_name
                if not registry.has(full_name):
                    missing.append(full_name)

        return sorted(missing)


__all__ = ["AgentManager", "AgentRegistration"]
