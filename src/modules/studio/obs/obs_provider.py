"""
OBSProvider - OBS 控制工具集

提供三个工具：
- ``obs_send_text``          - 发送文本到 OBS 文本源（含逐字效果）
- ``obs_switch_scene``       - 切换 OBS 场景
- ``obs_set_source_visibility`` - 控制源可见性

通过 ``obsws-python`` 控制 OBS Studio；依赖缺失时软降级。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, Optional

from pydantic import Field, field_validator

from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider

if TYPE_CHECKING:
    pass


# obsws-python 软降级
try:
    import obsws_python as obs
except ImportError:
    obs = None


_OBS_SEND_TEXT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "要发送的文本"},
        "typewriter": {"type": "boolean", "description": "是否启用逐字效果（默认按配置）"},
    },
    "required": ["text"],
}

_OBS_SWITCH_SCENE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "scene_name": {"type": "string", "description": "目标场景名称"},
    },
    "required": ["scene_name"],
}

_OBS_SET_VISIBILITY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "source_name": {"type": "string", "description": "OBS 源名称"},
        "visible": {"type": "boolean", "description": "是否可见"},
    },
    "required": ["source_name", "visible"],
}


class OBSProvider(BaseToolProvider):
    """OBS ToolProvider（send_text / switch_scene / set_source_visibility）"""

    PROVIDER_NAME = "obs"

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category = "studio"

    class ConfigSchema(BaseConfig):
        """OBS 控制配置"""

        type: str = "obs"
        host: str = Field(default="localhost", description="OBS WebSocket 主机地址")
        port: int = Field(default=4455, ge=1, le=65535, description="OBS WebSocket 端口")
        password: Optional[str] = Field(default=None, description="OBS WebSocket 密码")
        text_source_name: str = Field(default="text", description="文本源名称")
        typewriter_enabled: bool = Field(default=False, description="是否启用逐字显示效果")
        typewriter_speed: float = Field(default=0.1, ge=0.01, le=2.0, description="每个字符间隔秒数")
        typewriter_delay: float = Field(default=0.5, ge=0.0, le=10.0, description="完整显示后延迟秒数")
        test_on_connect: bool = Field(default=True, description="连接时是否发送测试消息")

        @field_validator("password")
        @classmethod
        def validate_password(cls, v: Optional[str]) -> Optional[str]:
            if v is None or v == "":
                return v
            return v

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
    ):
        self.config = config
        self.event_bus = event_bus
        self.logger = get_logger(self.__class__.__name__)

        try:
            self.typed_config = self.ConfigSchema.from_dict(config)
        except Exception as e:
            self.logger.error(f"配置验证失败: {e}")
            raise

        self.host = self.typed_config.host
        self.port = self.typed_config.port
        self.password = self.typed_config.password
        self.text_source_name = self.typed_config.text_source_name
        self.typewriter_enabled = self.typed_config.typewriter_enabled
        self.typewriter_speed = self.typed_config.typewriter_speed
        self.typewriter_delay = self.typed_config.typewriter_delay
        self.test_on_connect = self.typed_config.test_on_connect

        self.obs_connection: Any = None
        self.is_connected = False
        self._has_started = False

        if not self.password:
            self.logger.warning("OBS WebSocket 密码未配置，如果 OBS 设置了密码则无法连接")

    # ===== ToolProvider 协议 =====

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="send_text",
                description="OBS 发送文本到文本源（可选逐字效果）",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_OBS_SEND_TEXT_SCHEMA,
            ),
            ToolSpec(
                name="switch_scene",
                description="OBS 切换场景",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_OBS_SWITCH_SCENE_SCHEMA,
            ),
            ToolSpec(
                name="set_source_visibility",
                description="OBS 设置源可见性",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_OBS_SET_VISIBILITY_SCHEMA,
            ),
            ToolSpec(
                name="send_test",
                description="OBS 发送测试消息（启动时默认行为）",
                kind="sync",
                provider=self.PROVIDER_NAME,
            ),
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        args = invocation.arguments or {}
        try:
            n = invocation.tool_name
            if n == "obs_send_text":
                text = str(args.get("text", ""))
                if not text:
                    return _fail(n, "缺少 text 字段")
                typewriter = args.get("typewriter")
                success = await self._send_text_to_obs(text, typewriter if isinstance(typewriter, bool) else None)
                return _ok(n, success)
            if n == "obs_switch_scene":
                scene_name = str(args.get("scene_name", ""))
                if not scene_name:
                    return _fail(n, "缺少 scene_name 字段")
                success = await self.switch_scene(scene_name)
                return _ok(n, success)
            if n == "obs_set_source_visibility":
                source_name = str(args.get("source_name", ""))
                visible = bool(args.get("visible", True))
                if not source_name:
                    return _fail(n, "缺少 source_name 字段")
                success = await self.set_source_visibility(source_name, visible)
                return _ok(n, success)
            if n == "obs_send_test":
                await self._send_test_message()
                return _ok(n, True)
            return _fail(n, f"工具 '{invocation.tool_name}' 不属于 Provider '{self.PROVIDER_NAME}'")
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"OBS 工具 {invocation.tool_name} 调用异常: {exc}", exc_info=True)
            return _fail(invocation.tool_name, f"{type(exc).__name__}: {exc}")

    # ===== 生命周期 =====

    async def setup(self) -> None:
        if self._has_started:
            return
        if obs is None:
            raise RuntimeError("obsws-python库未安装，请运行: uv add obsws-python")
        await self._connect_obs()
        self._has_started = True

    async def cleanup(self) -> None:
        if not self._has_started:
            return
        if self.obs_connection:
            try:
                self.obs_connection.disconnect()
                self.logger.info("已断开 OBS WebSocket 连接")
            except Exception as e:
                self.logger.error(f"断开 OBS 连接时出错: {e}")
            finally:
                self.obs_connection = None
                self.is_connected = False
        self._has_started = False

    async def connect(self) -> bool:
        """手动建立 OBS 连接（手动重连的"建立"半步）。

        委托 ``_connect_obs``：底层已对 obsws-python 缺失 / 连接异常做了 try/
        except 兜底并返回 bool，本层不另捕。``_has_started`` 保持不变——
        手动 connect 假设 setup 已先完成（组合根装配期调过），属于 setup
        内部的连接刷新，**不**重置 ``_has_started`` 标志。
        """
        self.logger.info("手动触发 OBS 连接")
        ok = await self._connect_obs()
        if not ok:
            self.logger.warning("OBS 手动连接失败（详见上方连接错误日志）")
        return ok

    async def disconnect(self) -> bool:
        """手动断开 OBS 连接（手动重连的"断开"半步）。

        与 ``cleanup`` 不同：仅关闭 ``obs_connection`` 并清空本地句柄 + 置
        ``is_connected=False``，**不重置** ``_has_started``——保持 setup 语
        义（仍可再 connect）。obsws-python 的 ``disconnect`` 同步调用，做
        try/except 兜底（断连中或重复断连不阻断）。返回 True 表达"断开动
        作已完成"。
        """
        self.logger.info("手动断开 OBS 连接")
        if self.obs_connection:
            try:
                self.obs_connection.disconnect()
            except Exception as e:
                self.logger.warning(f"手动断开 OBS 连接异常（忽略）: {e}")
            finally:
                self.obs_connection = None
                self.is_connected = False
        return True

    # ===== 业务方法 =====

    async def switch_scene(self, scene_name: str) -> bool:
        if not self.is_connected or not self.obs_connection:
            self.logger.warning("OBS 未连接，无法切换场景")
            return False
        try:
            self.obs_connection.set_current_program_scene(scene_name)
            self.logger.info(f"已切换到场景: {scene_name}")
            return True
        except Exception as e:
            self.logger.error(f"切换场景失败: {e}")
            return False

    async def set_source_visibility(self, source_name: str, visible: bool) -> bool:
        if not self.is_connected or not self.obs_connection:
            self.logger.warning("OBS 未连接，无法设置源可见性")
            return False
        try:
            self.obs_connection.set_source_enabled(source_name, visible)
            self.logger.debug(f"已设置源 '{source_name}' 可见性: {visible}")
            return True
        except Exception as e:
            self.logger.error(f"设置源可见性失败: {e}")
            return False

    async def _send_text_to_obs(self, text: str, typewriter: Optional[bool] = None) -> bool:
        if not self.is_connected or not self.obs_connection:
            self.logger.warning("OBS 未连接，无法发送文本")
            return False
        try:
            use_typewriter = self.typewriter_enabled if typewriter is None else typewriter
            if use_typewriter:
                await self._send_typewriter_effect(text)
            else:
                await self._set_text_source(text)
                self.logger.debug(f"已发送文本到 OBS: {text[:50]}{'...' if len(text) > 50 else ''}")
            return True
        except Exception as e:
            self.logger.error(f"发送文本到 OBS 失败: {e}")
            return False

    async def _set_text_source(self, text: str) -> None:
        if not self.obs_connection:
            raise RuntimeError("OBS 未连接")
        self.obs_connection.set_input_settings(
            self.text_source_name,
            {"text": text},
            True,
        )

    async def _send_typewriter_effect(self, text: str) -> None:
        if not self.obs_connection:
            raise RuntimeError("OBS 未连接")
        try:
            await self._set_text_source("")
            current_text = ""
            for char in text:
                current_text += char
                await self._set_text_source(current_text)
                await asyncio.sleep(self.typewriter_speed)
            await asyncio.sleep(self.typewriter_delay)
            self.logger.debug(f"已逐字显示文本: {text[:50]}{'...' if len(text) > 50 else ''}")
        except Exception as e:
            self.logger.error(f"逐字效果执行失败: {e}")
            await self._set_text_source(text)

    async def _send_test_message(self) -> None:
        test_message = "OBS 控制 Provider 已成功连接"
        try:
            if self.typewriter_enabled:
                await self._send_typewriter_effect(test_message)
                self.logger.info("已使用逐字效果发送测试消息")
            else:
                await self._set_text_source(test_message)
                self.logger.info(f"已发送测试消息到 OBS 文本源 '{self.text_source_name}'")
        except Exception as e:
            self.logger.error(f"发送测试消息失败: {e}")
            self.logger.warning(f"请确认 OBS 中存在名为 '{self.text_source_name}' 的文本源")
            raise

    async def _connect_obs(self) -> bool:
        if not obs:
            self.logger.error("obsws-python库未安装")
            return False
        try:
            self.obs_connection = obs.ReqClient(
                host=self.host,
                port=self.port,
                password=self.password if self.password else None,
            )
            if self.test_on_connect:
                await self._send_test_message()
            self.is_connected = True
            self.logger.info(f"成功连接到 OBS WebSocket: {self.host}:{self.port}")
            return True
        except Exception as e:
            self.logger.error(f"连接 OBS 失败: {e}")
            self.logger.error("请检查: 1) OBS 是否开启 WebSocket 服务; 2) 端口和密码是否正确; 3) 网络是否通畅")
            self.is_connected = False
            self.obs_connection = None
            return False


def _ok(tool_name: str, success: bool, structured: Any = None) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name,
        success=bool(success),
        structured_content=structured,
        content="" if structured is None else str(structured),
    )


def _fail(tool_name: str, error_message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name,
        success=False,
        error_message=error_message,
    )


def create_obs_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
) -> OBSProvider:
    return OBSProvider(config=config, event_bus=event_bus)


def register_obs_tools(
    registry: Any,
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
) -> OBSProvider:
    provider = create_obs_provider(config=config, event_bus=event_bus)
    registry.register_provider(provider)
    return provider
