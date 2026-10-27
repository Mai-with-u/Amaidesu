"""
OBSProvider - OBS 控制工具集

提供三个工具：
- ``obs_send_text``          - 发送文本到 OBS 文本源（含逐字效果）
- ``obs_switch_scene``       - 切换 OBS 场景
- ``obs_set_source_visibility`` - 控制源可见性

通过 ``obs-websocket-py`` 控制 OBS Studio；依赖缺失时软降级。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Awaitable, Dict, Optional

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger, silence_third_party_logger
from src.modules.studio.obs import obs_client
from src.modules.studio.obs.obs_errors import describe_obs_error
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import ConnectionToolProvider, wire_connection_tools

if TYPE_CHECKING:
    pass

# 失败原因里指向的密码配置项（工具段在 tools.toml，与直播画面采集器的 [live_frame] 分开配置）
_PASSWORD_SETTING = "tools.toml 的 [tools.studio.obs.config].password"


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
        "source_name": {"type": "string", "description": "当前节目场景里的 OBS 来源名称（不含分组内的来源）"},
        "visible": {"type": "boolean", "description": "是否可见"},
    },
    "required": ["source_name", "visible"],
}


class OBSProvider(ConnectionToolProvider):
    """OBS ToolProvider（send_text / switch_scene / set_source_visibility / send_test）

    工具表随连接态动态（继承 ``ConnectionToolProvider`` 中间层）：未连接
    返回空（装配期降级登记 0 工具），连接成功 / 断开 / invoke 捕获连接类
    异常时经 ``mark_connected`` / ``mark_disconnected`` 换血工具表——断连后
    工具即时摘除，不再等熔断器事后兜底。
    """

    PROVIDER_NAME = "obs"

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category = "studio"

    class ConfigSchema(BaseConfig):
        """OBS 控制配置"""

        type: str = "obs"
        host: str = Field(default="localhost", title="OBS 主机地址", description="OBS WebSocket 主机地址")
        port: int = Field(default=4455, ge=1, le=65535, title="OBS 端口", description="OBS WebSocket 端口")
        password: str = Field(default="", title="OBS 密码", description="OBS WebSocket 密码（空串=无密码）")
        text_source_name: str = Field(default="text", title="文本源名称", description="要写入的 OBS 文本源名称")
        typewriter_enabled: bool = Field(default=False, title="逐字显示", description="是否启用逐字显示效果")
        typewriter_speed: float = Field(
            default=0.1, ge=0.01, le=2.0, title="逐字间隔", description="每个字符的间隔秒数"
        )
        typewriter_delay: float = Field(
            default=0.5, ge=0.0, le=10.0, title="显示后停留", description="完整显示后的停留秒数"
        )
        test_on_connect: bool = Field(
            default=True,
            title="连接自检",
            description="连接成功后是否发送一条测试消息验证通路",
            json_schema_extra={"x-ui-advanced": True},
        )

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        self.logger = get_logger(self.__class__.__name__)

        try:
            self.typed_config = self.ConfigSchema.from_dict(config)
        except Exception as e:
            self.logger.error(f"配置验证失败: {e}")
            raise

        self.obs_connection: Any = None
        self._has_started = False

        if not self.typed_config.password:
            self.logger.warning("OBS WebSocket 密码未配置，如果 OBS 设置了密码则无法连接")

    # ===== ToolProvider 协议 =====

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    def _connected_tools(self) -> list[ToolSpec]:
        """连接建立后暴露的全量工具清单（未连接时基类 list_tools 降级返回空）。"""
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

    # 分发表：键 = ToolSpec 声明名（与 _connected_tools 的 spec.name 同源，无第二套词汇）
    def _send_text_handler(self, args: Dict[str, Any]) -> ToolExecutionResult:
        text = str(args.get("text", ""))
        if not text:
            return _fail("send_text", "缺少 text 字段")
        typewriter = args.get("typewriter")
        return _wait(self._send_text_to_obs(text, typewriter if isinstance(typewriter, bool) else None), "send_text")

    def _switch_scene_handler(self, args: Dict[str, Any]) -> ToolExecutionResult:
        scene_name = str(args.get("scene_name", ""))
        if not scene_name:
            return _fail("switch_scene", "缺少 scene_name 字段")
        return _wait(self.switch_scene(scene_name), "switch_scene")

    def _set_visibility_handler(self, args: Dict[str, Any]) -> ToolExecutionResult:
        source_name = str(args.get("source_name", ""))
        if not source_name:
            return _fail("set_source_visibility", "缺少 source_name 字段")
        return _wait(self.set_source_visibility(source_name, bool(args.get("visible", True))), "set_source_visibility")

    def _send_test_handler(self, args: Dict[str, Any]) -> ToolExecutionResult:
        return _wait(self._send_test_message(), "send_test", success_value=True)

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        args = dict(invocation.arguments or {})
        try:
            handler = _TOOL_HANDLERS.get(invocation.tool_name)
            if handler is None:
                return _fail(
                    invocation.tool_name, f"工具 '{invocation.tool_name}' 不属于 Provider '{self.PROVIDER_NAME}'"
                )
            return await handler(self, args)
        except Exception as exc:  # noqa: BLE001
            self.logger.exception(f"OBS 工具 {invocation.tool_name} 调用异常: {exc}")
            # 连接类异常（连接被关闭 / 请求超时 / 管道断裂）：首次失败即摘除工具，
            # 不等熔断器兜底；业务性失败（场景/来源不存在等 OBSRequestError）不误摘
            if self._connected and _is_connection_error(exc):
                self.logger.warning("OBS 工具调用遇连接类异常，摘除断连工具")
                self.mark_disconnected()
            return _fail(invocation.tool_name, f"{type(exc).__name__}: {exc}")

    # ===== 生命周期 =====

    async def setup(self) -> None:
        if self._has_started:
            return
        if not obs_client.AVAILABLE:
            raise RuntimeError("obs-websocket-py库未安装，请运行: uv sync")
        await self._connect_obs()
        self._has_started = True

    async def cleanup(self) -> None:
        if not self._has_started:
            return
        if self.obs_connection:
            was_connected = self._connected
            try:
                self.obs_connection.disconnect()
                self.logger.info("已断开 OBS WebSocket 连接")
            except Exception as e:
                self.logger.error(f"断开 OBS 连接时出错: {e}")
            finally:
                self.obs_connection = None
                if was_connected:
                    self.mark_disconnected()
        self._has_started = False

    async def connect(self) -> bool:
        """手动建立 OBS 连接（手动重连的"建立"半步）。

        委托 ``_connect_obs``：底层已对 obs-websocket-py 缺失 / 连接异常做了 try/
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
        清空本地连接标志（``_connected=False``），**不重置** ``_has_started``——保持 setup 语
        义（仍可再 connect）。obs-websocket-py 的 ``disconnect`` 同步调用，做
        try/except 兜底（断连中或重复断连不阻断）。返回 True 表达"断开动
        作已完成"。
        """
        self.logger.info("手动断开 OBS 连接")
        if self.obs_connection:
            was_connected = self._connected
            try:
                self.obs_connection.disconnect()
            except Exception as e:
                self.logger.warning(f"手动断开 OBS 连接异常（忽略）: {e}")
            finally:
                self.obs_connection = None
                if was_connected:
                    self.mark_disconnected()
        return True

    # ===== 业务方法 =====

    async def switch_scene(self, scene_name: str) -> bool:
        if not self._connected or not self.obs_connection:
            self.logger.warning("OBS 未连接，无法切换场景")
            return False
        try:
            self.obs_connection.call(obs_client.obs_requests.SetCurrentProgramScene(sceneName=scene_name))
            self.logger.info(f"已切换到场景: {scene_name}")
            return True
        except Exception as e:
            # 场景名不对（OBS 回 600）或连接已断：翻译成原因后记录，工具调用返回失败
            missing = f"OBS 里找不到场景「{scene_name}」：场景名区分大小写，需与 OBS「场景」面板里的名字完全一致"
            self.logger.error(f"切换场景「{scene_name}」失败：{self._describe(e, not_found=missing)}")
            return False

    async def set_source_visibility(self, source_name: str, visible: bool) -> bool:
        if not self._connected or not self.obs_connection:
            self.logger.warning("OBS 未连接，无法设置源可见性")
            return False
        scene_name = ""
        try:
            # obs-websocket 5 没有全局的来源开关，显隐挂在场景项上：
            # 取当前节目场景（观众正看到的）→ 在其中找到该来源的场景项 → 设这个场景项的显隐
            scene_name = self.obs_connection.call(
                obs_client.obs_requests.GetCurrentProgramScene()
            ).getCurrentProgramSceneName()
            item_id = self.obs_connection.call(
                obs_client.obs_requests.GetSceneItemId(sceneName=scene_name, sourceName=source_name)
            ).getSceneItemId()
            self.obs_connection.call(
                obs_client.obs_requests.SetSceneItemEnabled(
                    sceneName=scene_name, sceneItemId=item_id, sceneItemEnabled=visible
                )
            )
            self.logger.debug(f"已设置场景「{scene_name}」里来源「{source_name}」的可见性: {visible}")
            return True
        except Exception as e:
            # 查找只看当前节目场景的顶层来源：别的场景里、或收在分组里的来源都会回 600
            missing = (
                f"当前节目场景「{scene_name}」里找不到来源「{source_name}」：来源名区分大小写，"
                "需与 OBS「来源」面板里的名字完全一致；收在分组里的来源找不到"
            )
            self.logger.error(f"设置来源「{source_name}」可见性失败：{self._describe(e, not_found=missing)}")
            return False

    async def _send_text_to_obs(self, text: str, typewriter: Optional[bool] = None) -> bool:
        if not self._connected or not self.obs_connection:
            self.logger.warning("OBS 未连接，无法发送文本")
            return False
        try:
            use_typewriter = self.typed_config.typewriter_enabled if typewriter is None else typewriter
            if use_typewriter:
                await self._send_typewriter_effect(text)
            else:
                await self._set_text_source(text)
                self.logger.debug(f"已发送文本到 OBS: {text[:50]}{'...' if len(text) > 50 else ''}")
            return True
        except Exception as e:
            self.logger.error(f"发送文本到 OBS 失败：{self._describe(e, not_found=self._text_source_missing())}")
            return False

    async def _set_text_source(self, text: str) -> None:
        if not self.obs_connection:
            raise RuntimeError("OBS 未连接")
        self.obs_connection.call(
            obs_client.obs_requests.SetInputSettings(
                inputName=self.typed_config.text_source_name,
                inputSettings={"text": text},
                overlay=True,
            )
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
                await asyncio.sleep(self.typed_config.typewriter_speed)
            await asyncio.sleep(self.typed_config.typewriter_delay)
            self.logger.debug(f"已逐字显示文本: {text[:50]}{'...' if len(text) > 50 else ''}")
        except Exception as e:
            # 逐字写入中途失败：记下原因后直接写全文；全文也写不进去时异常交给调用方记录
            reason = self._describe(e, not_found=self._text_source_missing())
            self.logger.error(f"逐字效果执行失败，改为直接显示全文：{reason}")
            await self._set_text_source(text)

    async def _send_test_message(self) -> None:
        test_message = "OBS 控制 Provider 已成功连接"
        try:
            if self.typed_config.typewriter_enabled:
                await self._send_typewriter_effect(test_message)
                self.logger.info("已使用逐字效果发送测试消息")
            else:
                await self._set_text_source(test_message)
                self.logger.info(f"已发送测试消息到 OBS 文本源 '{self.typed_config.text_source_name}'")
        except Exception as e:
            self.logger.error(f"发送测试消息失败：{self._describe(e, not_found=self._text_source_missing())}")
            raise

    async def _connect_obs(self) -> bool:
        if not obs_client.AVAILABLE:
            self.logger.error("obs-websocket-py库未安装")
            return False
        # obs-websocket-py 连接阶段会以 DEBUG/INFO 级打印连接参数，连不上时每次都打整段堆栈，
        # 且经标准库兜底处理器直出控制台；先压住，失败原因由本类翻译后经项目日志记录
        silence_third_party_logger("obswebsocket")
        try:
            client = obs_client.ObsClient(
                host=self.typed_config.host,
                port=self.typed_config.port,
                password=self.typed_config.password,
                timeout=10,
            )
            client.connect()
        except Exception as e:
            # 握手阶段失败（OBS 没开、没启用 WebSocket 服务器、密码不对等）：翻译原因后报连接失败
            self.logger.error(f"连接 OBS 失败：{self._describe(e, connected=False)}")
            self.obs_connection = None
            if self._connected:
                self.mark_disconnected()
            return False
        self.obs_connection = client
        if self.typed_config.test_on_connect:
            try:
                await self._send_test_message()
            except Exception:
                # 握手成功但测试消息发不出去（多半是文本源不存在，原因已由 _send_test_message 记录）：
                # 先断开这条半开的连接再报连接失败，不泄漏 WebSocket
                self.logger.warning("OBS 测试消息未发出（原因见上一条），断开本次连接")
                self._close_client(client)
                self.obs_connection = None
                if self._connected:
                    self.mark_disconnected()
                return False
        self.mark_connected()
        self.logger.info(f"成功连接到 OBS WebSocket: {self.typed_config.host}:{self.typed_config.port}")
        return True

    def _close_client(self, client: Any) -> None:
        """断开一条 OBS WebSocket 连接；断开阶段的失败只记录，不影响之后重连。"""
        try:
            client.disconnect()
        except Exception as e:
            self.logger.warning(f"断开 OBS 连接时出错（忽略）：{e}")

    def _text_source_missing(self) -> str:
        """文本源不存在时的原因：在 OBS 建同名文本源，或改配置指向已有的文本源。"""
        return (
            f"OBS 里找不到文本源「{self.typed_config.text_source_name}」：在 OBS 里新建同名的文本源，"
            "或把 tools.toml 的 [tools.studio.obs.config].text_source_name 改成已有文本源的名字（区分大小写）"
        )

    def _describe(self, error: BaseException, *, not_found: str = "", connected: bool = True) -> str:
        """把 OBS 失败翻译成能照着改的原因；默认按请求阶段（连接已建立）理解连接被关闭。"""
        return describe_obs_error(
            error,
            host=self.typed_config.host,
            port=self.typed_config.port,
            password_setting=_PASSWORD_SETTING,
            not_found=not_found,
            connected=connected,
        )


def _is_connection_error(exc: Exception) -> bool:
    """判定是否连接类异常（保守策略）：只认 websocket 连接关闭与底层管道断裂/超时——
    业务性失败（场景/来源不存在等 OBSRequestError）不误摘。
    按类名识别以兼容依赖缺失时的降级路径（同 obs_errors 的惯例）。
    """
    name = type(exc).__name__
    return "WebSocketConnectionClosedException" in name or isinstance(
        exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError)
    )


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


async def _wait(coro: Awaitable[Any], tool_name: str, *, success_value: Any = None) -> ToolExecutionResult:
    """等齐业务协程并按其 bool 返回包装成功结果（send_test 固定报成功）。"""
    return _ok(tool_name, success_value if success_value is not None else bool(await coro))


def _tool_full_name(declared_name: str) -> str:
    """按 ToolSpec.full_name 的唯一派生实现求调用全名（与 _connected_tools 的 spec 同源）。"""
    return ToolSpec(name=declared_name, description="", provider=OBSProvider.PROVIDER_NAME).full_name


# 分发表：键 = 工具调用全名（由 ToolSpec 声明名派生，与 _connected_tools 的 spec 一一对应）
_TOOL_HANDLERS = {
    _tool_full_name("send_text"): OBSProvider._send_text_handler,
    _tool_full_name("switch_scene"): OBSProvider._switch_scene_handler,
    _tool_full_name("set_source_visibility"): OBSProvider._set_visibility_handler,
    _tool_full_name("send_test"): OBSProvider._send_test_handler,
}


def create_obs_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    lipsync_analyzer: Optional[Any] = None,
) -> OBSProvider:
    return OBSProvider(config=config, event_bus=event_bus)


def register_obs_tools(
    registry: Any,
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    lipsync_analyzer: Optional[Any] = None,
    subtitle_service: Optional[Any] = None,
) -> OBSProvider:
    provider = create_obs_provider(config=config, event_bus=event_bus)
    registry.register_provider(provider)
    wire_connection_tools(provider, registry)
    return provider
