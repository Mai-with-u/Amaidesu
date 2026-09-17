"""观众命令路由：识别 + 安全闸 + 委派。

把散在 StreamerAgent 上的命令接线（6 个属性 + 分支方法）收成一个组件：
命令命中白名单 → 经工具注册表委派 `framework_delegate` 后短路；
白名单外 / 限频超限 → 静默丢弃（同样短路，不进决策链）；
未激活或非命令文本 → 返回 False 走原路径。

红线口径：本组件是代码直连的内部件——不是 LLM 可调工具、不进工具面。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.tools.models import ToolInvocation

from .command_parser import CommandParser
from .command_registry import CommandRegistry
from ..config import StreamerCommandConfig

__all__ = ["CommandRouter"]


class CommandRouter:
    """命令识别与委派（最小接线：玩法待扩展）。

    安全闸（全部 config 化，见 ``StreamerCommandConfig``）：
    - 白名单：mappings 无映射的命令静默丢弃
    - 限频：同一用户在 rate_window_ms 内最多 rate_max 条，超限静默丢弃
    - 危险同意默认关闭：instruction 只含映射的语义目标，不携带任何
      ``allow_harm`` / ``may_alter_terrain`` 等危险同意位（目标 Agent
      契约默认不传即关）
    """

    def __init__(
        self,
        config: StreamerCommandConfig,
        tool_registry: Optional[Any],
        logger=None,
    ) -> None:
        """``tool_registry`` 为鸭子注解（仅调 ``invoke``）——命令接线
        消费工具面但不属于工具面，不引入该类型依赖。"""
        self._logger = logger or get_logger("StreamerAgent.CommandRouter")
        self._tool_registry = tool_registry
        self._parser = CommandParser(command_prefix=config.prefix)
        self._registry = CommandRegistry()
        self._registry.load_from_config(dict(config.mappings))
        self._target_agent = config.target_agent
        self._rate_window_ms = config.rate_window_ms
        self._rate_max = config.rate_max
        # 限频状态：用户 id → 窗口内命令时间戳（毫秒）
        self._hits: Dict[str, List[int]] = {}
        self._logger.info(
            f"观众命令接线已激活: 前缀='{config.prefix}' "
            f"白名单={self._registry.get_supported_commands()} 目标='{config.target_agent or '自动'}'"
        )

    async def try_dispatch(self, msg: RoomMessagePayload) -> bool:
        """观众命令入口。返回 True 表示消息已被命令分支消费（含丢弃）。

        返回 False 表示非命令文本，调用方继续走决策链。
        """
        content = (msg.content or "").strip()
        if not self._parser.is_command(content):
            return False
        command = self._parser.parse_command(content, msg)
        if command is None:
            return False

        user_id = msg.user.id if msg.user else "unknown"
        # 白名单闸：映射表外一律不执行
        action = self._registry.get_action(command.name)
        if action is None:
            self._logger.debug(f"命令未在白名单，静默丢弃: /{command.name}（用户 {user_id}）")
            return True
        # 限频闸：窗口滑出后回收旧时间戳
        now = now_ms()
        hits = [ts for ts in self._hits.get(user_id, []) if now - ts < self._rate_window_ms]
        if len(hits) >= self._rate_max:
            self._hits[user_id] = hits
            self._logger.debug(f"命令限频命中，静默丢弃: /{command.name}（用户 {user_id}，窗口 {len(hits)} 条）")
            return True
        hits.append(now)
        self._hits[user_id] = hits

        # 危险同意位零携带：instruction 就是映射的语义目标原文。
        # 目标留空交给框架委派原语解析（当前唯一启用的游戏 Agent），
        # 主播侧不写死任何游戏名——换游戏只改 agents.enabled。
        if self._tool_registry is None:
            self._logger.warning(f"命令 /{command.name} 无法委派：工具注册表未注入")
            return True
        target = self._target_agent or ""
        invocation = ToolInvocation(
            tool_name="framework_delegate",
            arguments={"agent": target, "instruction": action},
            source="streamer",
        )
        result = await self._tool_registry.invoke(invocation)
        if not result.success:
            # 目标不在名册 / 拒收等受理失败：拒绝执行，不崩、不进决策链
            self._logger.warning(
                f"命令 /{command.name} 委派受理失败（目标 '{target or '自动'}'）: {result.error_message}"
            )
            return True
        self._logger.info(f"观众命令已委派: /{command.name} -> '{target or '自动'}'（用户 {user_id}）")
        return True
