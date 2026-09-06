"""
Warudo ReplyState - 有限状态机

状态机范围:
- 只保留 is_talking(由 lip-sync 音频流 on_start/on_end 驱动)
- is_thinking/is_replying/is_viewing/start_internal_thinking/typing 等
  状态当前无事件源,标记为 [DECISION NEEDED],暂不实现。

被外部调用的入口:
- start_talking() - 由音频开始回调调用
- stop_talking() - 由音频结束回调调用
- deal_state(state: str) - 字符串状态入口,对无源状态记录 warning + TODO
"""

import logging
from typing import Any, Callable, Coroutine, Optional

from src.modules.logging import get_logger


class ReplyState:
    """回复状态管理器(有限版本)"""

    def __init__(
        self,
        state_manager: Any,
        logger: Optional[logging.Logger] = None,
        send_action_callback: Optional[Callable[[str, Any], Coroutine[Any, Any, bool]]] = None,
        talking_head: Any = None,
    ):
        """
        初始化回复状态管理器

        Args:
            state_manager: WarudoStateManager 实例
            logger: 日志器
            send_action_callback: 发送动作的回调(可选,用于 loading 动画)
            talking_head: TalkingHeadTask 实例(可选,用于联动 is_talking)
        """
        self.state_manager = state_manager
        self.logger = logger or get_logger("WarudoReplyState")
        self._send_action_callback = send_action_callback
        self._talking_head = talking_head

        self.is_talking: bool = False
        self.logger.info("ReplyState 已初始化(有限状态机版本)")

    async def start_talking(self) -> None:
        """开始说话状态(由 lip-sync on_start 触发)"""
        if self.is_talking:
            return  # 幂等
        self.is_talking = True
        self.logger.debug("开始说话状态")

        # 联动: talking_head 开启
        if self._talking_head is not None:
            try:
                self._talking_head.is_talking = True
            except Exception as e:
                self.logger.error(f"设置 talking_head.is_talking 失败: {e}")

        # 联动: 视线转向相机
        try:
            self.state_manager.sight_state.set_state("camera", 1.0)
        except Exception as e:
            self.logger.error(f"设置 sight_state 失败: {e}")

        # 联动: 清除 loading 动画
        if self._send_action_callback is not None:
            try:
                await self._send_action_callback("loading", "")
            except Exception as e:
                self.logger.error(f"清除 loading 动画失败: {e}")

    async def stop_talking(self) -> None:
        """停止说话状态(由 lip-sync on_end 触发)"""
        if not self.is_talking:
            return  # 幂等
        self.is_talking = False
        self.logger.debug("停止说话状态")

        # 联动: talking_head 关闭
        if self._talking_head is not None:
            try:
                self._talking_head.is_talking = False
            except Exception as e:
                self.logger.error(f"设置 talking_head.is_talking 失败: {e}")

        # 联动: 视线从相机移除
        try:
            self.state_manager.sight_state.set_state("camera", 0.0)
        except Exception as e:
            self.logger.error(f"重置 sight_state 失败: {e}")

    async def send_loading(self) -> None:
        """发送 loading 动画(由 deal_state("start_internal_thinking") 调用)"""
        if self._send_action_callback is None:
            return
        try:
            await self._send_action_callback("loading", "......")
        except Exception as e:
            self.logger.error(f"发送 loading 失败: {e}")

    async def deal_state(self, state: str) -> None:
        """
        处理状态数据(兼容旧接口)

        已知有源状态(实际可处理):
        - "start_talking" / "stop_talking" - 由外部直接调用 start_talking/stop_talking 即可

        [DECISION NEEDED] 无源状态(暂不实现):
        - "start_thinking" / "finish_thinking" / "start_replying" - 无事件源
        - "start_viewing" - 语义为抛鱼 + 视线切到弹幕,无事件源
        - "start_internal_thinking" - 语义为手机视线,无事件源
        - "typing" / "stop_typing" - 语义为打字动画,无事件源

        Args:
            state: 状态字符串
        """
        # 已实现的有源状态
        if state == "start_talking":
            await self.start_talking()
            return
        if state == "stop_talking":
            await self.stop_talking()
            return

        # [DECISION NEEDED] 无源状态:仅记录 warning,不抛异常
        unimplemented_states = {
            "start_thinking",
            "finish_thinking",
            "start_replying",
            "start_viewing",
            "start_internal_thinking",
            "typing",
            "stop_typing",
        }
        if state in unimplemented_states:
            self.logger.warning(
                f"状态 {state} 在新架构下无事件源,未实现 [DECISION NEEDED: 需要 MaiBotDecider 输出 decision.* 事件]"
            )
            return

        # 未知状态
        self.logger.warning(f"未知的 deal_state: {state}")
