"""
Warudo OutputProvider - Warudo虚拟形象控制Provider

职责:
- 通过WebSocket连接到Warudo
- 接收Intent事件并适配为Warudo参数
- 发送表情、状态等指令到Warudo
- 口型同步、眨眼、眼球移动等自动化动作
- 心情状态管理
"""

import asyncio
import json
from typing import Any, Dict, Optional

from pydantic import Field

from src.domains.output.providers.avatar.base import AvatarProviderBase
from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger


class WarudoOutputProvider(AvatarProviderBase):
    """Warudo虚拟形象控制Provider"""

    class ConfigSchema(BaseProviderConfig):
        """Warudo输出Provider配置"""

        type: str = "warudo"

        # WebSocket配置
        ws_host: str = Field(default="localhost", description="WebSocket主机地址")
        ws_port: int = Field(default=19190, ge=1, le=65535, description="WebSocket端口")

    # ==================== Warudo API 参数映射 ====================

    # 情感映射（符合测试期望）
    EMOTION_MAP: Dict[str, Dict[str, float]] = {
        "happy": {"mouthSmile": 1.0},
        "sad": {"mouthSad": 1.0},
        "angry": {"eyebrowAngry": 1.0},
        "surprised": {"eyeSurprised": 1.0},
        "shy": {"cheekBlush": 0.8},
        "love": {"heart": 1.0},
        "neutral": {},
    }

    # 动作映射
    ACTION_HOTKEY_MAP: Dict[str, str] = {
        "blink": "blink",
        "nod": "nod",
        "shake": "shake",
        "wave": "wave",
        "clap": "clap",
    }

    def __init__(self, config: Dict[str, Any]):
        """
        初始化Warudo Provider

        Args:
            config: Provider配置字典
        """
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)

        # 使用 ConfigSchema 验证配置
        self.typed_config = self.ConfigSchema(**config)

        # WebSocket配置
        self.ws_host = self.typed_config.ws_host
        self.ws_port = self.typed_config.ws_port
        self.websocket = None
        self._connection_task: Optional[asyncio.Task] = None
        self._first_connection = True

        # 状态管理器 - 使用新版本（依赖注入模式）
        from .state.warudo_state_manager import WarudoStateManager

        # 创建发送动作的回调函数
        async def send_action_callback(action: str, data: dict):
            """发送动作到 Warudo"""
            await self._send_action_internal(action, data)

        self.state_manager = WarudoStateManager(self.logger, send_action_callback)

        # 心情管理器
        from .state.mood_manager import MoodManager

        self.mood_manager = MoodManager(self.state_manager, self.logger)

        # 眨眼任务
        from .tasks.blink_task import BlinkTask

        self.blink_task = BlinkTask(self.state_manager, self.logger)

        # 眼部移动任务
        from .tasks.shift_task import ShiftTask

        self.shift_task = ShiftTask(self.state_manager, self.logger)

        # 回复状态管理器
        from .tasks.reply_state import ReplyState

        self.reply_state = ReplyState(self.state_manager, self.logger)

        # 当前心情状态 (1-10)
        self.current_mood = {"joy": 5, "anger": 1, "sorrow": 1, "fear": 1}
        self.current_expression = "neutral"

        # 统计信息
        self.render_count = 0
        self.error_count = 0

        self.logger.info("WarudoOutputProvider初始化完成")

    # ==================== AvatarProviderBase 抽象方法实现 ====================

    def _adapt_intent(self, intent: Any) -> Dict[str, Any]:
        """
        适配 Intent 为 Warudo 参数

        Args:
            intent: 平台无关的 Intent

        Returns:
            Warudo参数字典，包含:
            - expressions: Dict[str, float] - 表情参数
            - hotkeys: List[str] - 热键ID列表
        """
        from src.modules.types import EmotionType

        result = {
            "expressions": {},
            "hotkeys": [],
        }

        # 1. 适配情感为表情参数
        emotion_str = intent.emotion.value if isinstance(intent.emotion, EmotionType) else str(intent.emotion)
        if emotion_str in self.EMOTION_MAP:
            result["expressions"] = self.EMOTION_MAP[emotion_str].copy()
            self.logger.debug(f"情感映射: {emotion_str} -> {result['expressions']}")

        # 2. 适配动作为热键
        for action in intent.actions:
            action_type_str = action.type.value if hasattr(action.type, "value") else str(action.type)

            # 如果动作参数中直接指定了热键ID
            if "hotkey_id" in action.params:
                result["hotkeys"].append(action.params["hotkey_id"])
            # 否则使用映射
            elif action_type_str in self.ACTION_HOTKEY_MAP:
                result["hotkeys"].append(self.ACTION_HOTKEY_MAP[action_type_str])

        self.logger.debug(f"Intent适配结果: expressions={result['expressions']}, hotkeys={result['hotkeys']}")
        return result

    async def _render_internal(self, params: Dict[str, Any]) -> None:
        """
        渲染到Warudo平台

        Args:
            params: _adapt_intent() 返回的Warudo参数字典
        """
        try:
            expressions = params.get("expressions", {})
            hotkeys = params.get("hotkeys", [])

            # 1. 应用表情参数
            for param_name, param_value in expressions.items():
                await self._send_expression(param_name, param_value)

            # 2. 触发热键
            for hotkey in hotkeys:
                await self._send_hotkey(hotkey)

            self.render_count += 1

        except Exception as e:
            self.logger.error(f"Warudo渲染失败: {e}", exc_info=True)
            self.error_count += 1
            raise RuntimeError(f"Warudo渲染失败: {e}") from e

    async def _connect(self) -> None:
        """连接到Warudo WebSocket服务器"""
        import websockets

        uri = f"ws://{self.ws_host}:{self.ws_port}"

        # 同步连接（用于测试）
        self.websocket = await websockets.connect(uri)
        self._is_connected = True
        self.logger.info(f"已连接到Warudo: {uri}")

        # 启动连接的后台任务（用于生产环境）
        if not self._connection_task or self._connection_task.done():
            self._connection_task = asyncio.create_task(self._connection_loop(uri), name="Warudo_Connect")
            self.logger.info("Warudo WebSocket连接任务已启动")

    async def _disconnect(self) -> None:
        """断开Warudo WebSocket连接"""
        # 停止眨眼任务
        await self.blink_task.stop()
        self.logger.info("眨眼任务已停止")

        # 停止眼部移动任务
        await self.shift_task.stop()
        self.logger.info("眼部移动任务已停止")

        # 停止状态监控
        self.state_manager.stop_monitoring()
        self.logger.info("状态管理器监控已停止")

        # 取消WebSocket连接任务
        if self._connection_task and not self._connection_task.done():
            self._connection_task.cancel()
            try:
                await asyncio.wait_for(self._connection_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                self.logger.debug("WebSocket连接任务已取消")

        # 关闭WebSocket连接
        if self.websocket:
            try:
                await asyncio.wait_for(self.websocket.close(), timeout=2.0)
                self.logger.info("Warudo WebSocket连接已关闭")
            except asyncio.TimeoutError:
                self.logger.warning("WebSocket关闭超时")
            finally:
                self.websocket = None
                self._is_connected = False

    # ==================== 连接管理 ====================

    async def _connection_loop(self, uri: str):
        """WebSocket连接循环（支持自动重连）"""
        from .warudo_sender import ActionSender

        action_sender = ActionSender()

        while True:
            try:
                # 等待现有连接关闭
                if self.websocket and not self.websocket.closed:
                    await self.websocket.wait_closed()

            except Exception as e:
                self.logger.error(f"WebSocket连接异常: {e}，5秒后重连...")
            finally:
                self._is_connected = False
                self.websocket = None
                action_sender.set_websocket(None)
                self.logger.info("WebSocket断开，但服务继续运行")
                await asyncio.sleep(5)

    # ==================== 辅助方法 ====================

    async def _send_action_internal(self, action: str, data: dict) -> None:
        """
        内部方法：发送动作到 Warudo（供状态管理器回调使用）

        Args:
            action: 动作名称
            data: 动作数据
        """
        if not self._is_connected or not self.websocket:
            self.logger.warning(f"Warudo未连接，无法发送动作: {action}")
            return

        try:
            from .warudo_sender import ActionSender

            action_sender = ActionSender()
            action_sender.set_websocket(self.websocket)
            await action_sender.send_action(action, data)
        except Exception as e:
            self.logger.error(f"发送动作失败: {action}: {e}")

    async def _send_expression(self, param_name: str, param_value: float) -> None:
        """
        发送表情参数到Warudo

        Args:
            param_name: 参数名
            param_value: 参数值
        """
        if not self._is_connected or not self.websocket:
            self.logger.warning(f"Warudo未连接，无法设置参数: {param_name} = {param_value}")
            return

        try:
            message = {"type": "expression", "data": {param_name: param_value}}
            # 使用 send_json（如果可用）或 send
            if hasattr(self.websocket, "send_json"):
                await self.websocket.send_json(message)
            else:
                await self.websocket.send(json.dumps(message))
            self.logger.debug(f"设置Warudo参数: {param_name} = {param_value}")
        except Exception as e:
            self.logger.error(f"设置Warudo参数失败: {param_name}: {e}")

    async def _send_hotkey(self, hotkey_id: str) -> None:
        """
        发送热键到Warudo

        Args:
            hotkey_id: 热键ID
        """
        if not self._is_connected or not self.websocket:
            self.logger.warning(f"Warudo未连接，无法触发热键: {hotkey_id}")
            return

        try:
            message = {"type": "hotkey", "data": {"id": hotkey_id}}
            # 使用 send_json（如果可用）或 send
            if hasattr(self.websocket, "send_json"):
                await self.websocket.send_json(message)
            else:
                await self.websocket.send(json.dumps(message))
            self.logger.debug(f"触发热键: {hotkey_id}")
        except Exception as e:
            self.logger.error(f"触发热键失败: {hotkey_id}: {e}")

    # ==================== 心情管理 ====================

    def update_mood(self, mood_data: Dict[str, Any]) -> bool:
        """
        更新心情状态

        Args:
            mood_data: 包含心情数据的字典，格式: {"joy": 5, "anger": 1, "sorrow": 1, "fear": 1}

        Returns:
            bool: 是否有变化发生
        """
        has_changes = False

        for emotion in ["joy", "anger", "sorrow", "fear"]:
            new_value = max(1, min(10, int(mood_data.get(emotion, 5))))
            if self.current_mood[emotion] != new_value:
                self.current_mood[emotion] = new_value
                has_changes = True

        if has_changes:
            self.logger.info(f"心情状态已更新: {self.current_mood}")
            # 更新心情管理器
            self.mood_manager.update_mood(mood_data)

        return has_changes

    # ==================== 回复状态管理 ====================

    async def start_talking(self) -> None:
        """开始说话状态"""
        await self.reply_state.start_talking()

    async def stop_talking(self) -> None:
        """停止说话状态"""
        await self.reply_state.stop_talking()

    # ==================== 统计信息 ====================

    def get_stats(self) -> Dict[str, Any]:
        """获取Provider统计信息"""
        return {
            "name": self.__class__.__name__,
            "is_connected": self._is_connected,
            "render_count": self.render_count,
            "error_count": self.error_count,
            "current_mood": self.current_mood,
            "current_expression": self.current_expression,
            "concurrent_rendering": False,
        }
