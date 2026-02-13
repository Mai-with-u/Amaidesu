"""
OBS Control OutputProvider - OBS控制Provider

职责:
- 通过WebSocket连接到OBS
- 支持文本显示（包含逐字打印效果）
- 场景切换
- 源设置控制
"""

import asyncio
from typing import TYPE_CHECKING, Any, Dict, Optional

from pydantic import Field, field_validator

if TYPE_CHECKING:
    from src.modules.types import Intent

from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import (
    OBSSetSourceVisibilityPayload,
    OBSSendTextPayload,
    OBSSwitchScenePayload,
)
from src.modules.logging import get_logger
from src.modules.types.base.output_provider import OutputProvider

try:
    import obsws_python as obs
except ImportError:
    obs = None


class ObsControlOutputProvider(OutputProvider):
    """OBS控制Provider

    功能:
    - 文本显示: 发送文本到OBS文本源
    - 逐字效果: 打字机效果显示文本
    - 场景切换: 切换OBS场景
    - 源控制: 控制源的可见性、设置等
    """

    class ConfigSchema(BaseProviderConfig):
        """OBS控制输出Provider配置"""

        type: str = "obs_control"

        # OBS连接配置
        host: str = Field(default="localhost", description="OBS WebSocket主机地址")
        port: int = Field(default=4455, ge=1, le=65535, description="OBS WebSocket端口")
        password: Optional[str] = Field(default=None, description="OBS WebSocket密码")
        text_source_name: str = Field(default="text", description="文本源名称")

        # 逐字效果配置
        typewriter_enabled: bool = Field(default=False, description="是否启用逐字显示效果")
        typewriter_speed: float = Field(default=0.1, ge=0.01, le=2.0, description="每个字符间隔秒数")
        typewriter_delay: float = Field(default=0.5, ge=0.0, le=10.0, description="完整显示后延迟秒数")

        # 连接测试配置
        test_on_connect: bool = Field(default=True, description="连接时是否发送测试消息")

        @field_validator("password")
        @classmethod
        def validate_password(cls, v: Optional[str]) -> Optional[str]:
            """验证密码配置"""
            # 密码可以为空（OBS未设置密码时），但需要记录警告
            if v is None or v == "":
                # 这里只返回，警告在初始化时记录
                return v
            return v

    def __init__(self, config: Dict[str, Any]):
        """
        初始化OBS Control Provider

        Args:
            config: 配置字典，包含:
                - host: OBS WebSocket主机地址 (默认: localhost)
                - port: OBS WebSocket端口 (默认: 4455)
                - password: OBS WebSocket密码 (可选)
                - text_source_name: 文本源名称 (默认: "text")
                - typewriter_enabled: 是否启用逐字效果 (默认: False)
                - typewriter_speed: 逐字速度 (默认: 0.1)
                - typewriter_delay: 显示后延迟 (默认: 0.5)
                - test_on_connect: 连接时测试 (默认: True)
        """
        super().__init__(config)
        self.logger = get_logger("ObsControlOutputProvider")

        # 使用 ConfigSchema 验证配置，获得类型安全的配置对象
        try:
            self.typed_config = self.ConfigSchema(**config)
        except Exception as e:
            self.logger.error(f"配置验证失败: {e}")
            raise

        # OBS连接配置
        self.host = self.typed_config.host
        self.port = self.typed_config.port
        self.password = self.typed_config.password
        self.text_source_name = self.typed_config.text_source_name

        # 逐字效果配置
        self.typewriter_enabled = self.typed_config.typewriter_enabled
        self.typewriter_speed = self.typed_config.typewriter_speed
        self.typewriter_delay = self.typed_config.typewriter_delay

        # 连接测试配置
        self.test_on_connect = self.typed_config.test_on_connect

        # OBS连接实例
        self.obs_connection: Optional["obs.ReqClient"] = None
        self.is_connected: bool = False

        # 密码检查
        if not self.password:
            self.logger.warning("OBS WebSocket密码未配置，如果OBS设置了密码则无法连接")

        self.logger.info(
            f"OBS控制Provider初始化完成 - {self.host}:{self.port}, "
            f"逐字效果: {'启用' if self.typewriter_enabled else '禁用'}"
        )

    async def init(self):
        """初始化 Provider"""
        if obs is None:
            self.logger.error("obsws-python库未安装，请运行: uv add obsws-python")
            raise RuntimeError("obsws-python库未安装")

        # 连接OBS
        await self._connect_obs()

        # 注册事件监听（用于外部直接调用，不属于 OUTPUT_INTENT 事件）
        self.event_bus.on(CoreEvents.OBS_SEND_TEXT, self._handle_send_text_event, OBSSendTextPayload)
        self.event_bus.on(CoreEvents.OBS_SWITCH_SCENE, self._handle_switch_scene_event, OBSSwitchScenePayload)
        self.event_bus.on(
            CoreEvents.OBS_SET_SOURCE_VISIBILITY,
            self._handle_set_source_visibility_event,
            OBSSetSourceVisibilityPayload,
        )

    async def execute(self, intent: "Intent"):
        """
        执行意图

        从Intent中提取文本并发送到OBS

        Args:
            intent: 决策意图，包含response_text和actions
        """
        if not self.is_connected or not self.obs_connection:
            self.logger.warning("OBS未连接，跳过渲染")
            return

        # 从多个可能的来源提取文本
        text = None

        # 1. 从回复文本提取
        if intent.response_text:
            text = intent.response_text

        # 2. 从元数据提取（兼容旧插件）
        if not text and "obs_text" in intent.metadata:
            text = intent.metadata["obs_text"]

        # 3. 从动作提取（支持特殊动作）
        if not text:
            for action in intent.actions:
                # action.type 已经是字符串值（由于 use_enum_values=True）
                action_type = action.type if isinstance(action.type, str) else action.type.value
                if action_type == "obs_send_text" or action_type == "custom":
                    if "text" in action.params:
                        text = action.params["text"]
                        break

        if not text:
            self.logger.debug("没有需要显示的文本内容")
            return

        # 发送文本到OBS
        await self._send_text_to_obs(text)

    async def _connect_obs(self) -> bool:
        """
        连接OBS WebSocket

        Returns:
            bool: 连接是否成功
        """
        if not obs:
            self.logger.error("obsws-python库未安装")
            return False

        try:
            # 创建连接（obsws-python的ReqClient是同步的）
            self.obs_connection = obs.ReqClient(
                host=self.host, port=self.port, password=self.password if self.password else None
            )

            # 测试连接
            if self.test_on_connect:
                await self._send_test_message()

            self.is_connected = True
            self.logger.info(f"成功连接到OBS WebSocket: {self.host}:{self.port}")
            return True

        except Exception as e:
            self.logger.error(f"连接OBS失败: {e}")
            self.logger.error("请检查: 1) OBS是否开启WebSocket服务; 2) 端口和密码是否正确; 3) 网络是否通畅")
            self.is_connected = False
            self.obs_connection = None
            return False

    async def _send_test_message(self):
        """发送测试消息"""
        test_message = "OBS控制Provider已成功连接"

        try:
            if self.typewriter_enabled:
                await self._send_typewriter_effect(test_message)
                self.logger.info("已使用逐字效果发送测试消息")
            else:
                await self._set_text_source(test_message)
                self.logger.info(f"已发送测试消息到OBS文本源 '{self.text_source_name}'")

        except Exception as e:
            self.logger.error(f"发送测试消息失败: {e}")
            self.logger.warning(f"请确认OBS中存在名为 '{self.text_source_name}' 的文本源")
            raise

    async def _send_text_to_obs(self, text: str, typewriter: Optional[bool] = None):
        """
        发送文本到OBS

        Args:
            text: 要发送的文本
            typewriter: 是否使用逐字效果，None则使用配置默认值
        """
        if not self.is_connected or not self.obs_connection:
            self.logger.warning("OBS未连接，无法发送文本")
            return False

        try:
            use_typewriter = self.typewriter_enabled if typewriter is None else typewriter

            if use_typewriter:
                await self._send_typewriter_effect(text)
            else:
                await self._set_text_source(text)
                self.logger.debug(f"已发送文本到OBS: {text[:50]}{'...' if len(text) > 50 else ''}")

            return True

        except Exception as e:
            self.logger.error(f"发送文本到OBS失败: {e}")
            return False

    async def _set_text_source(self, text: str):
        """
        设置OBS文本源内容

        Args:
            text: 文本内容
        """
        if not self.obs_connection:
            raise RuntimeError("OBS未连接")

        # obsws_python的set_input_settings是同步方法
        self.obs_connection.set_input_settings(
            self.text_source_name,
            {"text": text},
            True,  # overlay表示覆盖设置
        )

    async def _send_typewriter_effect(self, text: str):
        """
        发送逐字显示效果

        Args:
            text: 要显示的文本
        """
        if not self.obs_connection:
            raise RuntimeError("OBS未连接")

        try:
            # 先清空文本
            await self._set_text_source("")

            # 逐字显示
            current_text = ""
            for char in text:
                current_text += char
                await self._set_text_source(current_text)
                await asyncio.sleep(self.typewriter_speed)

            # 显示完整文本后等待
            await asyncio.sleep(self.typewriter_delay)

            self.logger.debug(f"已逐字显示文本: {text[:50]}{'...' if len(text) > 50 else ''}")

        except Exception as e:
            self.logger.error(f"逐字效果执行失败: {e}")
            # 失败时直接显示完整文本
            await self._set_text_source(text)

    async def switch_scene(self, scene_name: str) -> bool:
        """
        切换OBS场景

        Args:
            scene_name: 场景名称

        Returns:
            bool: 是否成功
        """
        if not self.is_connected or not self.obs_connection:
            self.logger.warning("OBS未连接，无法切换场景")
            return False

        try:
            # obsws_python的set_current_program_scene是同步方法
            self.obs_connection.set_current_program_scene(scene_name)
            self.logger.info(f"已切换到场景: {scene_name}")
            return True

        except Exception as e:
            self.logger.error(f"切换场景失败: {e}")
            return False

    async def set_source_visibility(self, source_name: str, visible: bool) -> bool:
        """
        设置源的可见性

        Args:
            source_name: 源名称
            visible: 是否可见

        Returns:
            bool: 是否成功
        """
        if not self.is_connected or not self.obs_connection:
            self.logger.warning("OBS未连接，无法设置源可见性")
            return False

        try:
            # obsws_python的set_source_enabled是同步方法
            self.obs_connection.set_source_enabled(source_name, visible)
            self.logger.debug(f"已设置源 '{source_name}' 可见性: {visible}")
            return True

        except Exception as e:
            self.logger.error(f"设置源可见性失败: {e}")
            return False

    async def _handle_send_text_event(self, payload: OBSSendTextPayload):
        """
        处理发送文本事件（供外部调用）

        Args:
            payload: OBS 发送文本事件 Payload
        """
        text = payload.text

        if text:
            await self._send_text_to_obs(text, None)
        else:
            self.logger.warning("事件数据中未包含文本内容")

    async def _handle_switch_scene_event(self, payload: OBSSwitchScenePayload):
        """
        处理切换场景事件

        Args:
            payload: OBS 切换场景事件 Payload
        """
        scene_name = payload.scene_name
        if scene_name:
            await self.switch_scene(scene_name)
        else:
            self.logger.warning("事件数据中未包含场景名称")

    async def _handle_set_source_visibility_event(self, payload: OBSSetSourceVisibilityPayload):
        """
        处理设置源可见性事件

        Args:
            payload: OBS 设置源可见性事件 Payload
        """
        source_name = payload.source_name
        visible = payload.visible

        if source_name:
            await self.set_source_visibility(source_name, visible)
        else:
            self.logger.warning("事件数据中未包含源名称")

    async def cleanup(self):
        """清理资源"""
        # 取消事件监听（OBS特有的事件，不属于 OUTPUT_INTENT）
        if self.event_bus:
            self.event_bus.off(CoreEvents.OBS_SEND_TEXT, self._handle_send_text_event)
            self.event_bus.off(CoreEvents.OBS_SWITCH_SCENE, self._handle_switch_scene_event)
            self.event_bus.off(CoreEvents.OBS_SET_SOURCE_VISIBILITY, self._handle_set_source_visibility_event)

        # 断开OBS连接
        if self.obs_connection:
            try:
                self.obs_connection.disconnect()
                self.logger.info("已断开OBS WebSocket连接")
            except Exception as e:
                self.logger.error(f"断开OBS连接时出错: {e}")
            finally:
                self.obs_connection = None
                self.is_connected = False
