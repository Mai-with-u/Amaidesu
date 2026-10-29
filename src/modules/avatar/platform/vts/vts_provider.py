"""
VTSProvider - VTS 虚拟形象工具集

ToolProvider 协议实现，把 VTS 能力封装为工具（LLM 主动调用的工具侧）：

- 引擎子件（``ExpressionController`` / ``HotkeyMatcher``）
  经 callback 解耦，可独立复用；口型渲染（``VtsLipSyncRenderer``）挂在共享
  分析器（``avatar.lipsync``）上。
- 暴露的工具（同语义跨平台同名同参数形状，契约见 ``avatar.protocol``）：
  - ``vts_set_expression``        - 设置情绪（17 枚举值 + 强度）
  - ``vts_list_preset_actions``   - 列出可演预设（VTS 热键目录）
  - ``vts_trigger_preset_action`` - 触发预设动作（未知名随结果返回目录）
  - ``vts_set_idle_enabled``      - 启停头部微动（待机微动已退役，面捕合成器接入后由它负责）

工具表随连接态动态变化（继承 ``ConnectionToolProvider`` 中间层）：未连接
时 ``list_tools`` 返回空（装配期降级登记 0 工具，Agent 不可见不可调）；
连接检测点调 ``mark_connected`` / ``mark_disconnected``，经接线回调驱动
注册表 ``refresh_provider_tools`` 补注册 / 摘除。

不注册为工具的 Python 方法只保留仍有真实调用点的（``set_parameter_value``
供 Dashboard 调试台参数直调），其余一概不设。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pydantic import Field

from src.modules.avatar.speech_binding import AvatarBindings
from src.modules.avatar.tool_common import (
    contract_tool_specs,
    dispatch_tool,
    scaled_emotion_weights,
    unknown_action_fail,
)
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.utils.backoff import ExponentialBackoff
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
    tool_result_fail,
    tool_result_ok,
)
from src.modules.tools.provider import ConnectionToolProvider, wire_connection_tools

from .expression_controller import ExpressionController
from .hotkey_matcher import HotkeyMatcher
from .lip_sync_renderer import VtsLipSyncRenderer

if TYPE_CHECKING:
    pass


# =============================================================================
# 工具的 JSON Schema 描述
# =============================================================================

_VTS_SET_IDLE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "description": "是否启用 idle 拟人动画"},
    },
    "required": ["enabled"],
}


# =============================================================================
# VTSProvider
# =============================================================================


class VTSProvider(ConnectionToolProvider):
    """VTS 虚拟形象 ToolProvider

    实现 ToolProvider 协议，编排各引擎子件。
    推荐通过 ``create_vts_provider(config, event_bus)``
    构造与 setup/cleanup 流程管理。
    """

    PROVIDER_NAME = "vts"

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category = "avatar"

    PARAM_MOUTH_SMILE = "MouthSmile"
    PARAM_MOUTH_OPEN = "MouthOpen"
    PARAM_EYE_OPEN_LEFT = "EyeOpenLeft"
    PARAM_EYE_OPEN_RIGHT = "EyeOpenRight"

    # VTS 断线自动重连的巡检节拍（秒）：覆盖 Amaidesu 先于 VTS 启动、VTS 中途
    # 重启两种场景；探活与换模跟随也吃这个节拍，长关时重连尝试另按退避压频
    _RECONNECT_INTERVAL_S = 5.0
    # 重连尝试的退避（VTS 长关时压住整点尝试与日志；连上即复位回起点）
    _RECONNECT_BACKOFF_BASE_MS = 5_000
    _RECONNECT_BACKOFF_CAP_MS = 60_000

    class ConfigSchema(BaseConfig):
        """VTS 配置（连接；口型分析调参在 ``[avatar.lipsync]`` 共享件段）

        TOML 段位：[avatar.platform.vts]（成员段直接铺键）。待机微动与常驻微笑基线已
        退役：形象的逐帧驱动由面捕合成器（``[avatar.tracking]``）负责。
        """

        type: str = Field(default="vts", title="段位标识")
        vts_host: str = Field(title="VTS 主机地址", default="localhost", description="VTS WebSocket 主机地址")
        vts_port: int = Field(title="VTS 端口", default=8001, ge=1, le=65535, description="VTS WebSocket 端口")

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
        lipsync_analyzer: Optional[Any] = None,
    ) -> None:
        # 配置
        self.config = config
        self.event_bus = event_bus
        # 共享口型分析器（装配注入；setup 时挂 VTS 渲染器，None = 不渲染口型）
        self.lipsync_analyzer = lipsync_analyzer
        self.logger = get_logger(self.__class__.__name__)

        # 配置（typed；空 dict = 全默认；失败 log+raise）
        try:
            self.typed_config = self.ConfigSchema.from_dict(config)
        except Exception as e:
            self.logger.error(f"配置验证失败: {e}")
            raise

        self.vts_host: str = self.typed_config.vts_host
        self.vts_port: int = self.typed_config.vts_port

        # 情绪 → VTS 参数映射（词表 17 值全覆盖；键取 Emotion.value 小写）。
        # 眉部统一写 Brows（一个参数同时控制双眉，实测 17 个模型里 11 个
        # 绑定了它）：分侧参数 BrowLeftY/BrowRightY 在真实模型上几乎没人
        # 绑定，写入成功但看不到效果；scared/confused/smug 三个情绪原来
        # 左右眉幅度不同，改用 Brows 后取较大值（左右眉不同的细节效果
        # 留待将来做情绪参数自定义时再补）。FaceAngry 保留（语义独特、
        # 写了不亏）；强度在 set_expression 中按线性缩放施加。
        self._emotion_map: Dict[str, Dict[str, float]] = {
            "neutral": {},
            "happy": {"MouthSmile": 0.8, "Brows": 0.6},
            "sad": {"MouthSmile": -0.4, "Brows": 0.15, "EyeOpenLeft": 0.6, "EyeOpenRight": 0.6},
            "angry": {"MouthSmile": -0.6, "FaceAngry": 0.9, "Brows": 0.2, "MouthOpen": 0.1},
            "surprised": {
                "EyeOpenLeft": 1.0,
                "EyeOpenRight": 1.0,
                "MouthOpen": 0.5,
                "Brows": 1.0,
            },
            "scared": {
                "EyeOpenLeft": 0.7,
                "EyeOpenRight": 0.7,
                "MouthOpen": 0.4,
                "Brows": 0.9,
                "FaceAngry": -0.4,
            },
            "disgusted": {
                "MouthX": -0.3,
                "TongueOut": 0.2,
                "FaceAngry": 0.4,
                "EyeOpenLeft": 0.5,
                "EyeOpenRight": 0.5,
                "CheekPuff": 0.2,
            },
            "shy": {"MouthSmile": 0.35, "EyeOpenLeft": 0.55, "EyeOpenRight": 0.55, "CheekPuff": 0.15},
            "embarrassed": {"MouthSmile": 0.2, "MouthX": 0.25, "EyeOpenLeft": 0.5, "EyeOpenRight": 0.65},
            "confused": {
                "EyeOpenLeft": 0.75,
                "EyeOpenRight": 0.95,
                "Brows": 0.95,
                "MouthX": 0.15,
            },
            "love": {"MouthSmile": 0.9, "EyeOpenLeft": 0.35, "EyeOpenRight": 0.35, "CheekPuff": 0.2},
            "excited": {
                "MouthSmile": 1.0,
                "EyeOpenLeft": 1.0,
                "EyeOpenRight": 1.0,
                "MouthOpen": 0.4,
                "Brows": 0.85,
            },
            "smug": {"MouthSmile": 0.5, "EyeOpenLeft": 0.3, "EyeOpenRight": 0.45, "Brows": 0.7},
            "serious": {
                "MouthSmile": -0.1,
                "Brows": 0.15,
                "EyeOpenLeft": 0.85,
                "EyeOpenRight": 0.85,
                "FaceAngry": 0.25,
            },
            "tired": {"EyeOpenLeft": 0.3, "EyeOpenRight": 0.25, "MouthOpen": 0.12, "Brows": 0.1},
            "crying": {
                "EyeOpenLeft": 0.2,
                "EyeOpenRight": 0.2,
                "MouthOpen": 0.35,
                "MouthSmile": -0.5,
                "Brows": 0.1,
            },
            "speechless": {
                "MouthSmile": 0.0,
                "MouthOpen": 0.06,
                "EyeOpenLeft": 0.8,
                "EyeOpenRight": 0.8,
                "Brows": 0.3,
            },
        }

        self._vts: Any = None
        self._vts_api_lock = asyncio.Lock()
        self._is_connecting = False
        self._reconnect_task: Optional[asyncio.Task] = None
        # 重连尝试退避：连续失败时按指数间隔跳过巡检整点（节拍保持 5s，
        # 只压重连这一路）；连上即复位
        self._reconnect_backoff = ExponentialBackoff(
            base_delay_ms=self._RECONNECT_BACKOFF_BASE_MS, cap_delay_ms=self._RECONNECT_BACKOFF_CAP_MS
        )
        self._reconnect_skip_polls = 0
        self._is_connected = False
        self._has_started = False
        # 换模跟随基线：上次解析链对应的模型名（空 = 未知，连接后记录）
        self._current_model_name: str = ""
        # 事件订阅挂载项句柄（setup 时逐项 attach，cleanup 时一次全量退订摘除）
        self._bindings: Optional[AvatarBindings] = None

        self.render_count = 0
        self.error_count = 0

        self.hotkey_matcher = HotkeyMatcher(
            logger_name=f"{self.__class__.__name__}.Hotkey",
            is_connected=lambda: self._is_connected,
            vts_request=self._make_vts_request_proxy(),
        )
        self.expression = ExpressionController(
            logger_name=f"{self.__class__.__name__}.Expression",
            is_connected=lambda: self._is_connected,
            vts_request=self._make_vts_request_proxy(),
        )

    # ===== ToolProvider 协议 =====

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    def _connected_tools(self) -> List[ToolSpec]:
        """连接建立后暴露的工具列表（预设目录走 list_preset_actions 结果，不进描述）。

        未连接时清单为空（基类 ``list_tools`` 降级返回空，Agent 工具表不含
        vts_*）；此处只负责连接后的全量声明，清单变化经连接态回调驱动注册
        表刷新补注册。
        """
        specs = contract_tool_specs(
            self.PROVIDER_NAME,
            set_expression="VTS 设置主播当前情绪（17 枚举值 + 强度，持续生效直至下次设置）",
            list_preset_actions="列出 VTS 可演的预设动作目录（连接后的热键清单；贴纸/道具已绑成热键的也在列）",
            trigger_preset_action="触发一个预设动作（动作名取自 vts_list_preset_actions 返回的目录）",
        )
        # set_idle_enabled 是 VTS 平台特有工具（非三平台契约面），声明就地追加
        specs.append(
            ToolSpec(
                name="set_idle_enabled",
                description="VTS 启停头部微动（没有动作时头部的轻微晃动）",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_VTS_SET_IDLE_SCHEMA,
            )
        )
        return specs

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """ToolProvider.invoke：分发到对应的 handler 方法。

        handlers 为零参闭包字典，每次 invoke 现构造（捕获本次参数解析结果）。
        """
        args = invocation.arguments or {}

        async def _set_idle_enabled_tool() -> ToolExecutionResult:
            # set_idle_enabled 是"同步动作 + 固定 ok 返回"的本地操作，包一层异步
            # 闭包适配 dispatch_tool 的统一 handler 形态（其余三个契约工具本体
            # 即异步方法，无需包装；不对称是有意的）
            self._set_idle_enabled(bool(args["enabled"]))
            return tool_result_ok("vts_set_idle_enabled", True)

        return await dispatch_tool(
            invocation,
            self.PROVIDER_NAME,
            {
                "vts_set_expression": lambda: self.set_expression(
                    str(args.get("emotion", "")),
                    float(args.get("intensity", 0.5)),
                ),
                "vts_list_preset_actions": self.list_preset_actions,
                "vts_trigger_preset_action": lambda: self.trigger_preset_action(str(args.get("action", ""))),
                "vts_set_idle_enabled": _set_idle_enabled_tool,
            },
            self.logger,
        )

    async def health_check(self) -> bool:
        """探活钩子（BaseToolProvider 契约：维护外部连接者必须真实检查）。

        委托 ``_vts_health_check`` 做一次真实 VTS 请求往返（3 秒超时）：
        连接可用才返回 True，断连 / 超时 / 异常一律 False。熔断工具的探活
        恢复与手动重连后的复位都走此判定——不得沿用基类默认（恒 True 会把
        已死连接上的熔断工具反复复位）。
        """
        return await self._vts_health_check()

    # ===== 生命周期 =====

    async def setup(self) -> None:
        """Provider 生命周期入口"""
        if self._has_started:
            self.logger.warning("VTSProvider 已启动，跳过重复 setup")
            return

        try:
            # 可选重型依赖延迟加载：pyvts 仅在 VTS 平台实际建立连接时导入，
            # 缺失按平台不可用降级，不阻断其他 avatar 平台
            import pyvts  # noqa: F401
            from pyvts import vts

            plugin_info = {
                "plugin_name": "Amaidesu_VTS_ToolProvider",
                "developer": "Wave 4 Implementation",
                # token 与其他运行时数据统一落 data/（该目录已整目录不入库）
                "authentication_token_path": "data/vts_token.txt",
                "vts_host": self.vts_host,
                "vts_port": self.vts_port,
            }
            vts_api_info = {
                "host": self.vts_host,
                "port": self.vts_port,
                "name": "VTubeStudioPublicAPI",
                "version": "1.0",
            }
            # 形参名必须是 plugin_info：pyvts 以 **kwargs 吞掉拼错的键并静默退回
            # 库默认（曾致插件身份显示为 pyvts/genteki、token 落盘根目录）
            self._vts = vts(plugin_info=plugin_info, vts_api_info=vts_api_info)
            self.logger.info("pyvts 实例创建成功")
        except ImportError:
            self.logger.error("pyvts 库不可用，VTSProvider 将被禁用")
            self._vts = None
            raise ImportError("pyvts library not available") from None

        await self._connect()

        # 启动断线自动重连循环
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        self._reconnect_task.set_name(f"{self.__class__.__name__}.reconnect_loop")

        # 事件订阅（被动响应业务事件）：情绪反射（streamer.speech）；口型渲染挂到共享
        # 分析器（说话时 MouthOpen 跟随信号）
        self._bindings = AvatarBindings(self.event_bus, self.logger)
        self._bindings.attach_emotion_reflection(self)
        if self.lipsync_analyzer is not None:
            self._bindings.attach_lip_renderer(
                self.lipsync_analyzer,
                VtsLipSyncRenderer(set_parameter=self._expression_set_param_proxy),
            )

        self._has_started = True

    async def cleanup(self) -> None:
        """清理资源"""
        if not self._has_started:
            return

        if self._bindings is not None:
            self._bindings.close()
            self._bindings = None

        await self._disconnect()
        self._has_started = False
        self.logger.info(f"{self.__class__.__name__} 已停止")

    # ===== 业务方法 =====

    async def set_parameter_value(self, parameter_name: str, value: float, weight: float = 1) -> bool:
        return await self.expression.set_parameter(parameter_name, value, weight)

    # ===== 契约方法（LLM 工具与自动情绪路径共用的渲染入口）=====

    async def set_expression(self, emotion: str, intensity: float) -> ToolExecutionResult:
        """设置当前情绪：查映射表 → 按强度线性缩放 → 批量写入 VTS 参数。

        强度语义：0.0 收回全部表情参数，1.0 为映射表完整幅度，线性内插；
        ``neutral`` 映射为空参数表（表情交回 idle/基线渲染）。映射表外的
        情绪名按失败结果返回（词表编译期已知，出现未知名即调用方契约破坏）。
        """
        resolved = scaled_emotion_weights(emotion, intensity, self._emotion_map)
        if resolved is None:
            self.error_count += 1
            return tool_result_fail("vts_set_expression", f"未知情绪 '{emotion}'（应为 17 枚举值之一）")
        factor, scaled = resolved
        success = await self.expression.set_multi_parameter(scaled, 1.0)
        if success:
            self.render_count += 1
        else:
            self.error_count += 1
        return tool_result_ok(
            "vts_set_expression",
            bool(success),
            {"emotion": emotion, "intensity": factor, "parameters": scaled},
        )

    async def list_preset_actions(self) -> ToolExecutionResult:
        """列出可演预设目录（VTS 热键清单；未连接/未加载时为空列表）。

        贴纸/道具在 VTS 内绑成热键后自然出现在目录中（``load_sticker``
        的承载方式），不再单列工具。
        """
        actions = [
            {"name": str(hotkey.get("name", "")), "type": str(hotkey.get("type", ""))}
            for hotkey in self.hotkey_matcher.hotkey_list
            if hotkey.get("name")
        ]
        return tool_result_ok("vts_list_preset_actions", True, {"actions": actions})

    async def trigger_preset_action(self, action: str) -> ToolExecutionResult:
        """触发一个预设动作（按名解析热键）；未知名把目录随失败结果返回。"""
        action = action.strip()
        resolved = self.hotkey_matcher.find_by_name(action) if action else None
        if not resolved:
            catalog = [a["name"] for a in (await self.list_preset_actions()).structured_content["actions"]]
            return unknown_action_fail("vts_trigger_preset_action", action, catalog)
        success = await self.hotkey_matcher.trigger_hotkey(resolved)
        return tool_result_ok("vts_trigger_preset_action", bool(success), {"action": action})

    async def trigger_hotkey(self, name: str = "", hotkey_id: str = "") -> bool:
        """触发热键：按热键名解析（``find_by_name``）优先，``hotkey_id`` 兜底。

        LLM 只能从工具描述中拿到热键名（VTS 内部 hotkeyID 是不透明 UUID），
        因此调用侧以 name 为主入口；name 解析失败且有 id 时按 id 重试。
        """
        if name:
            resolved = self.hotkey_matcher.find_by_name(name)
            if resolved:
                return await self.hotkey_matcher.trigger_hotkey(resolved)
            if not hotkey_id:
                self.logger.warning(f"VTS 热键名未匹配且无 hotkey_id 兜底: {name}")
                return False
            self.logger.warning(f"VTS 热键名未匹配，回退 hotkey_id: name={name}, id={hotkey_id}")
        if hotkey_id:
            return await self.hotkey_matcher.trigger_hotkey(hotkey_id)
        self.logger.warning("VTS 触发热键失败：name 与 hotkey_id 均为空")
        return False

    def _set_idle_enabled(self, enabled: bool) -> None:
        """头部微动开关：待机微动已退役，面捕合成器接入前这里没有可开关的动作。"""
        self.logger.info(f"头部微动开关（{enabled}）暂无效果：待机微动已退役，面捕合成器接入后生效")

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self.__class__.__name__,
            "is_connected": self._is_connected,
            "render_count": self.render_count,
            "error_count": self.error_count,
            "hotkey_count": len(self.hotkey_matcher.hotkey_list),
        }

    # ===== 内部辅助 =====

    async def _expression_set_param_proxy(self, parameter_name: str, value: float, weight: float = 1) -> bool:
        return await self.expression.set_parameter(parameter_name, value, weight)

    def _make_vts_request_proxy(self) -> Any:
        """创建可调用代理，所有 VTS API 调用都经过同一把 asyncio.Lock 串行化"""
        # 可选重型依赖延迟加载：pyvts 仅在真正构造请求对象时导入（见 setup 处同款说明）
        import pyvts

        handler = self
        vts_request_builder = pyvts.VTSRequest()

        class VTSRequestProxy:
            async def __call__(self, request: Dict[str, Any]) -> Dict[str, Any]:
                async with handler._vts_api_lock:
                    return await handler._vts.request(request)

            @property
            def vts_request(self) -> Any:
                return vts_request_builder

            def requestHotKeyList(self) -> Dict[str, Any]:
                return vts_request_builder.requestHotKeyList()

            def requestTriggerHotKey(self, **kwargs: Any) -> Dict[str, Any]:
                return vts_request_builder.requestTriggerHotKey(**kwargs)

        return VTSRequestProxy()

    async def _connect(self) -> None:
        if self._is_connecting or self._is_connected:
            return
        self._is_connecting = True
        try:
            if not self._vts:
                self.last_error = "pyvts 未初始化"
                self.logger.error("pyvts 未初始化")
                return

            self.logger.info(f"开始连接 VTS: {self.vts_host}:{self.vts_port}")
            await self._vts.connect()
            await self._vts.request_authenticate_token()
            await self._vts.request_authenticate()
            self._is_connected = True
            self.last_error = ""
            self.logger.info("VTS 连接成功")
            # 连接态换血：装配期降级登记的 0 工具在此补注册（回调由注册接线方提供）
            self.mark_connected()

            await self._reload_model_state()
            self._current_model_name = await self._query_current_model_name()
        except Exception as e:
            self.last_error = f"VTS 连接失败: {e}"
            self.logger.error(f"VTS 连接失败: {e}")
            self._is_connected = False
        finally:
            self._is_connecting = False

    async def _reload_model_state(self) -> None:
        """模型相关状态：热键重拉 + Live2D 参数清单记 debug 日志（连接建立与换模跟随共用）。"""
        await self.hotkey_matcher.load_hotkeys()

        try:
            live2d_names = await self.expression.list_live2d_parameters()
            if live2d_names:
                self.logger.debug(f"当前模型 Live2D 参数清单（{len(live2d_names)} 项）: {sorted(live2d_names)}")
        except Exception as e:
            self.logger.debug(f"拉取 Live2D 参数清单失败（忽略）: {e}")

    async def _query_current_model_name(self) -> str:
        """查询 VTS 当前加载的模型名（AvailableModelsRequest 一次含清单与加载态）。"""
        try:
            proxy = self._make_vts_request_proxy()
            response = await proxy(proxy.vts_request.BaseRequest(message_type="AvailableModelsRequest"))
            if response and response.get("messageType") == "AvailableModelsResponse":
                for model in response.get("data", {}).get("availableModels", []):
                    if model.get("modelLoaded"):
                        return str(model.get("modelName") or "")
        except Exception as e:
            self.logger.debug(f"查询当前模型名失败: {e}")
        return ""

    async def _follow_model_switch(self) -> None:
        """换模跟随：检测到模型切换后重跑解析链（热键/绑定/缩放/基线全部刷新）。

        触发用健康心跳内的模型名轮询而非 ModelLoadedEvent 订阅：pyvts 的
        request 是"单次 send+recv"形态、无常驻接收循环，并行接收任务会与
        请求路径抢包。轮询复用既有心跳节拍，AvailableModelsRequest 单请求
        同时返回清单与加载态；换模是 VTS/人侧的低频操作，5 秒级跟随足够。
        """
        current = await self._query_current_model_name()
        if not current or current == self._current_model_name:
            return
        self.logger.info(f"检测到 VTS 模型切换: {self._current_model_name or '(未知)'} → {current}，重跑解析链")
        self._current_model_name = current
        try:
            await self._reload_model_state()
        except Exception:
            self.logger.exception("换模后解析链重跑失败")

    async def _vts_health_check(self) -> bool:
        try:
            proxy = self._make_vts_request_proxy()
            response = await asyncio.wait_for(
                proxy(proxy.vts_request.requestParameterValue("FaceAngleX")),
                timeout=3.0,
            )
            return bool(response and response.get("messageType") == "ParameterValueResponse")
        except Exception:
            return False

    async def _attempt_reconnect(self) -> None:
        """未连接时的整点重连尝试：连上复位退避，失败按指数间隔排后续节拍。

        巡检节拍（探活 / 换模跟随）保持恒定；只有重连这一路按退避压频
        （5s→10s→20s→…→60s 封顶），VTS 长关时不再每个节拍都尝试并打日志。
        """
        if self._reconnect_skip_polls > 0:
            self._reconnect_skip_polls -= 1
            return
        self.logger.info("VTS 未连接，尝试自动重连...")
        await self._connect()
        if self._is_connected:
            self._reconnect_backoff.reset()
            self._reconnect_skip_polls = 0
        else:
            delay_ms = self._reconnect_backoff.next_delay_ms()
            self._reconnect_skip_polls = max(1, round(delay_ms / (self._RECONNECT_INTERVAL_S * 1000)))

    async def _reconnect_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._RECONNECT_INTERVAL_S)
                if self._is_connecting:
                    continue
                if not self._is_connected:
                    await self._attempt_reconnect()
                    continue
                if self._reconnect_skip_polls:
                    # 手动重连或外部恢复连上：清掉旧失败留下的退避窗口，下次断线从起点起算
                    self._reconnect_skip_polls = 0
                    self._reconnect_backoff.reset()
                if not await self._vts_health_check():
                    self.logger.warning("VTS 连接已断开（VTS 可能已重启），准备自动重连")
                    self._is_connected = False
                    # 连接态换血：摘除断连工具（Agent 工具表不再包含 vts_*）
                    self.mark_disconnected()
                    try:
                        await self._vts.close()
                    except Exception as e:
                        self.logger.debug(f"关闭旧 VTS 连接异常（忽略）: {e}")
                    continue
                # 连接健康时的换模跟随（与断线检测同一节拍）
                await self._follow_model_switch()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.exception(f"VTS 自动重连循环异常: {e}")

    async def _disconnect(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        self._reconnect_task = None

        if not self._is_connected or not self._vts:
            return
        try:
            await self._vts.close()
            self.logger.info("VTS 连接已关闭")
        except Exception as e:
            self.logger.warning(f"关闭 VTS 连接异常: {e}")
        finally:
            self._is_connected = False
            # 连接态换血：手动断开 / cleanup 前先摘除工具（走到这里必是 True→False 迁移）
            self.mark_disconnected()

    async def connect(self) -> bool:
        """手动建立 VTS 连接（手动重连的"建立"半步）。

        直接委托 ``_connect``（内部已带 ``_is_connecting`` / ``_is_connected``
        短路，无需重复判重）。返回 bool 以 ``_is_connected`` 为准——即便
        ``_connect`` 异常被内部 try 吞掉，此处的真值与 VTS WebSocket 实际
        状态一致。手动 connect 与后台 ``_reconnect_loop`` 属低频可接受并发
        场景，不强制互斥。

        手动断开（``disconnect``）会取消自动重连循环并置空任务引用；此处
        在已启动过的 Provider 上把循环拉回来，保证手动重连成败与否都不
        影响后续 VTS 恢复时的自动重连（循环自身按 ``_is_connected`` 分流，
        未连接时持续重试）。
        """
        self.logger.info("手动触发 VTS 连接")
        await self._connect()
        if self._has_started and (self._reconnect_task is None or self._reconnect_task.done()):
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())
            self._reconnect_task.set_name(f"{self.__class__.__name__}.reconnect_loop")
        return self._is_connected

    async def disconnect(self) -> bool:
        """手动断开 VTS 连接（手动重连的"断开"半步）。

        复用 ``_disconnect`` 的关闭逻辑：取消后台重连循环 →
        关 VTS WebSocket → 置 ``_is_connected=False``。区别于 ``cleanup``
        的是**不重置** ``_has_started``——手动断开不破坏 setup 语义，后续
        仍可再 connect；重连循环的恢复由 ``connect`` 负责（拉回循环），
        手动重连成败均不影响后续自动重连。返回 True 表达"断开动作已完成"。
        """
        self.logger.info("手动断开 VTS 连接")
        await self._disconnect()
        return True


# =============================================================================
# 工厂与注册辅助
# =============================================================================


def create_vts_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    lipsync_analyzer: Optional[Any] = None,
) -> VTSProvider:
    """构造 VTSProvider 实例（不启动，由调用方 setup）"""
    return VTSProvider(
        config=config,
        event_bus=event_bus,
        lipsync_analyzer=lipsync_analyzer,
    )


def register_vts_tools(
    registry: Any,
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    lipsync_analyzer: Optional[Any] = None,
    subtitle_service: Optional[Any] = None,
) -> VTSProvider:
    """构造 VTSProvider 并注册到 registry。返回 Provider 实例供调用方管理生命周期。

    注册接线：装配期未连接 → 降级登记 0 工具（中间层标准语义）；连接态
    回调经 ``wire_connection_tools`` 接入，驱动 ``registry.refresh_provider_tools``
    补注册 / 摘除，工具表始终与真实连接态一致。
    """
    provider = create_vts_provider(
        config=config,
        event_bus=event_bus,
        lipsync_analyzer=lipsync_analyzer,
    )
    registry.register_provider(provider)
    wire_connection_tools(provider, registry)
    return provider
