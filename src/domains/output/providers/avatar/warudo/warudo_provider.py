"""
Warudo OutputProvider - Warudo虚拟形象控制Provider

职责:
- 通过WebSocket连接到Warudo
- 接收Intent事件并适配为Warudo参数
- 发送表情、状态等指令到Warudo
"""

from typing import Dict, Any, Optional
from pydantic import Field

from src.core.types import EmotionType, ActionType
from src.domains.output.providers.avatar.base import AvatarProviderBase
from src.core.events.names import CoreEvents
from src.core.utils.logger import get_logger
from src.services.config.schemas.schemas.base import BaseProviderConfig


class WarudoOutputProvider(AvatarProviderBase):
    """Warudo虚拟形象控制Provider"""

    class ConfigSchema(BaseProviderConfig):
        """Warudo输出Provider配置"""

        type: str = "warudo"

        # WebSocket配置
        ws_host: str = Field(default="localhost", description="WebSocket主机地址")
        ws_port: int = Field(default=19190, ge=1, le=65535, description="WebSocket端口")

    # Warudo 特定的参数映射（需要根据实际 API 调整）
    EMOTION_MAP: Dict[EmotionType, Dict[str, float]] = {
        EmotionType.HAPPY: {"mouthSmile": 1.0},
        EmotionType.SAD: {"mouthSad": 1.0},
        EmotionType.ANGRY: {"eyebrowAngry": 1.0},
        EmotionType.SURPRISED: {"eyeSurprised": 1.0},
        EmotionType.SHY: {"cheekBlush": 0.8},
        EmotionType.LOVE: {"heart": 1.0},
        EmotionType.NEUTRAL: {},
    }

    ACTION_HOTKEY_MAP: Dict[ActionType, str] = {
        ActionType.WAVE: "wave",
        ActionType.NOD: "nod",
        ActionType.SHAKE: "shake",
        ActionType.CLAP: "clap",
        ActionType.BLINK: "blink",
    }

    def __init__(self, config: Dict[str, Any]):
        """
        初始化Warudo Provider

        Args:
            config: 配置字典，包含:
                - ws_host: WebSocket主机地址 (默认: localhost)
                - ws_port: WebSocket端口 (默认: 19190)
        """
        super().__init__(config)
        self.logger = get_logger("WarudoOutputProvider")

        # 使用 ConfigSchema 验证配置
        self.typed_config = self.ConfigSchema(**config)

        # WebSocket配置
        self.ws_host = self.typed_config.ws_host
        self.ws_port = self.typed_config.ws_port
        self.websocket = None

    async def _connect(self) -> None:
        """连接到Warudo WebSocket服务器"""
        try:
            import websockets

            uri = f"ws://{self.ws_host}:{self.ws_port}"
            self.websocket = await websockets.connect(uri)
            self._is_connected = True
            self.logger.info(f"已连接到Warudo: {uri}")
        except Exception as e:
            self.logger.error(f"连接Warudo失败: {e}")
            self._is_connected = False
            raise

    async def _disconnect(self) -> None:
        """断开Warudo WebSocket连接"""
        if self.websocket:
            try:
                await self.websocket.close()
                self.logger.info("Warudo WebSocket连接已关闭")
            except Exception as e:
                self.logger.error(f"关闭Warudo连接失败: {e}")
            finally:
                self.websocket = None
                self._is_connected = False

    def _adapt_intent(self, intent: Any) -> Dict[str, Any]:
        """
        将Intent适配为Warudo参数

        Args:
            intent: Intent对象，包含emotion和actions

        Returns:
            Warudo参数字典
        """
        # 获取情感对应的表情参数
        expressions = self.EMOTION_MAP.get(intent.emotion, {}).copy()

        # 获取动作对应的热键
        hotkeys = []
        for action in intent.actions:
            hotkey_id = self.ACTION_HOTKEY_MAP.get(action.type)
            if hotkey_id:
                hotkeys.append(hotkey_id)

        return {
            "expressions": expressions,
            "hotkeys": hotkeys,
        }

    async def _render_internal(self, parameters: Dict[str, Any]) -> None:
        """
        内部渲染逻辑：发送参数到Warudo

        Args:
            parameters: Warudo参数字典
        """
        if not self._is_connected or not self.websocket:
            self.logger.warning("Warudo未连接，跳过渲染")
            return

        try:
            # 发送表情参数
            expressions = parameters.get("expressions", {})
            if expressions:
                await self._send_expressions(expressions)

            # 发送热键
            hotkeys = parameters.get("hotkeys", [])
            for hotkey in hotkeys:
                await self._send_hotkey(hotkey)

            self.logger.debug(f"Warudo渲染完成: {parameters}")
        except Exception as e:
            self.logger.error(f"Warudo渲染失败: {e}")
            raise

    async def _send_expressions(self, expressions: Dict[str, float]) -> None:
        """
        发送表情参数到Warudo

        Args:
            expressions: 表情参数字典
        """
        # TODO: 实现实际的Warudo表情发送逻辑
        # STATUS: PENDING - Warudo API待调研
        message = {
            "type": "expression",
            "data": expressions
        }
        await self.websocket.send_json(message)
        self.logger.debug(f"发送表情: {expressions}")

    async def _send_hotkey(self, hotkey_id: str) -> None:
        """
        发送热键到Warudo

        Args:
            hotkey_id: 热键ID
        """
        # TODO: 实现实际的Warudo热键发送逻辑
        # STATUS: PENDING - Warudo API待调研
        message = {
            "type": "hotkey",
            "data": {"id": hotkey_id}
        }
        await self.websocket.send_json(message)
        self.logger.debug(f"发送热键: {hotkey_id}")
