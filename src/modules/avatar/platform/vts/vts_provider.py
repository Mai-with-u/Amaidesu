"""
VTSProvider - VTS 虚拟形象工具集

ToolProvider 协议实现，把 VTS 能力封装为工具（LLM 主动调用的工具侧）：

- 形象的逐帧驱动由面捕合成器（``avatar.tracking``）负责：本适配器把一个 VTS 面捕
  驱动（``VtsTrackingDriver``）挂上去，合成器每帧算出通用通道，驱动换算成 VTS
  捕捉输入整组注入；本适配器管连接、授权、断线重连、换模跟随与热键；
- 连接走自带的插件接口客户端（``VtsClient``：请求按编号对回复，注入发完就走）；
- 暴露的工具（同语义跨平台同名同参数形状，契约见 ``avatar.protocol``）：
  - ``vts_set_expression``        - 设置情绪（交给面捕合成器，保持一段时间后回落）
  - ``vts_list_preset_actions``   - 列出可演预设（VTS 热键目录）
  - ``vts_trigger_preset_action`` - 触发预设动作（未知名随结果返回目录）
  - ``vts_set_idle_enabled``      - 启停头部微动（面捕合成器的头部漫步）

工具表随连接态动态变化（继承 ``ConnectionToolProvider`` 中间层）：未连接
时 ``list_tools`` 返回空（装配期降级登记 0 工具，Agent 不可见不可调）；
连接检测点调 ``mark_connected`` / ``mark_disconnected``，经接线回调驱动
注册表 ``refresh_provider_tools`` 补注册 / 摘除。

不注册为工具的 Python 方法只保留仍有真实调用点的（``set_parameter_value``
供 Dashboard 调试台临时覆盖某个 VTS 输入），其余一概不设。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from pydantic import Field

from src.modules.avatar.tool_common import (
    contract_tool_specs,
    dispatch_tool,
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
from src.modules.types.emotion_vocab import Emotion

from .hotkey_matcher import HotkeyMatcher
from .tracking_driver import VtsTrackingDriver
from .vts_client import VtsClient

if TYPE_CHECKING:
    pass


# =============================================================================
# 工具的 JSON Schema 描述
# =============================================================================

_VTS_SET_IDLE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "description": "是否启用头部微动"},
    },
    "required": ["enabled"],
}

# 插件身份：令牌与身份绑定，改名会让 VTS 重新弹授权窗；令牌与其他运行时数据统一落 data/
_PLUGIN_NAME = "Amaidesu_VTS_ToolProvider"
_PLUGIN_DEVELOPER = "Wave 4 Implementation"
_TOKEN_PATH = Path("data/vts_token.txt")

_EMOTIONS = frozenset(e.value for e in Emotion)


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

    # VTS 断线自动重连的巡检节拍（秒）：覆盖 Amaidesu 先于 VTS 启动、VTS 中途
    # 重启两种场景；探活与换模跟随也吃这个节拍，长关时重连尝试另按退避压频
    _RECONNECT_INTERVAL_S = 5.0
    # 重连尝试的退避（VTS 长关时压住整点尝试与日志；连上即复位回起点）
    _RECONNECT_BACKOFF_BASE_MS = 5_000
    _RECONNECT_BACKOFF_CAP_MS = 60_000

    class ConfigSchema(BaseConfig):
        """VTS 配置：连接 + 面捕驱动的模型校准（逐帧驱动的调参在 ``[avatar.tracking]``）

        TOML 段位：[avatar.platform.vts]（成员段直接铺键）。校准项回答"这个模型上，
        正常睁眼 / 中性嘴角 / 中性眉对应多大的 VTS 输入值"——同一个输入值在不同
        模型上的含义不同（实测桃濑日和的眼睛开合输入 1.0 已是瞪到最大），换模型时
        按观感调。
        """

        type: str = Field(default="vts", title="段位标识")
        vts_host: str = Field(title="VTS 主机地址", default="localhost", description="VTS WebSocket 主机地址")
        vts_port: int = Field(title="VTS 端口", default=8001, ge=1, le=65535, description="VTS WebSocket 端口")
        eye_open_rest: float = Field(
            title="正常睁眼的输入值",
            default=0.55,
            ge=0.05,
            le=1.0,
            description="模型上正常睁眼对应的 EyeOpen 输入（1.0 是睁到最大；眼睛显得瞪着就调小，显得困就调大）",
        )
        mouth_smile_neutral: float = Field(
            title="中性嘴角的输入值",
            default=0.5,
            ge=0.0,
            le=1.0,
            description="模型上嘴角不笑也不撇时对应的 MouthSmile 输入",
            json_schema_extra={"x-ui-advanced": True},
        )
        brow_neutral: float = Field(
            title="中性眉的输入值",
            default=0.5,
            ge=0.0,
            le=1.0,
            description="模型上眉毛不抬也不压时对应的眉高输入",
            json_schema_extra={"x-ui-advanced": True},
        )

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
        lipsync_analyzer: Optional[Any] = None,
        tracking: Optional[Any] = None,
        client_factory: Optional[Callable[..., Any]] = None,
        reconnect_interval_s: Optional[float] = None,
    ) -> None:
        """Args:
        tracking: 面捕合成器运行时（组合根共享一个）；None = 只剩热键与连接，不驱动形象。
        client_factory: VTS 客户端工厂（关键字参数同 :class:`VtsClient`；测试注入假连接）。
        reconnect_interval_s: 断线巡检节拍（秒；None = 默认 5 秒，测试调短）。
        """
        # 配置
        self.config = config
        self.event_bus = event_bus
        # 统一装配签名的占位：口型信号由面捕合成器自己接，本适配器不再挂渲染器
        self.lipsync_analyzer = lipsync_analyzer
        self._tracking = tracking
        self.logger = get_logger(self.__class__.__name__)

        # 配置（typed；空 dict = 全默认；失败 log+raise）
        try:
            self.typed_config = self.ConfigSchema.from_dict(config)
        except Exception as e:
            self.logger.error(f"配置验证失败: {e}")
            raise

        self.vts_host: str = self.typed_config.vts_host
        self.vts_port: int = self.typed_config.vts_port

        factory = client_factory or VtsClient
        self._client = factory(
            host=self.vts_host,
            port=self.vts_port,
            plugin_name=_PLUGIN_NAME,
            plugin_developer=_PLUGIN_DEVELOPER,
            token_path=_TOKEN_PATH,
            logger_name=f"{self.__class__.__name__}.Client",
        )
        # 面捕驱动：连接授权成功后打开，断开时关闭；挂在面捕合成器上逐帧注入
        self._driver = VtsTrackingDriver(
            self._client,
            eye_open_rest=self.typed_config.eye_open_rest,
            mouth_smile_neutral=self.typed_config.mouth_smile_neutral,
            brow_neutral=self.typed_config.brow_neutral,
        )
        self._reconnect_interval_s = reconnect_interval_s or self._RECONNECT_INTERVAL_S
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

        self.render_count = 0
        self.error_count = 0

        self.hotkey_matcher = HotkeyMatcher(
            logger_name=f"{self.__class__.__name__}.Hotkey",
            is_connected=lambda: self._is_connected,
            request=self._client.request,
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
            set_expression="VTS 设置主播当前情绪（17 枚举值 + 强度；浮现后保持一段时间再自然回落）",
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

        await self._connect()

        # 启动断线自动重连循环
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        self._reconnect_task.set_name(f"{self.__class__.__name__}.reconnect_loop")

        # 面捕驱动挂到面捕合成器上：发言、播放、口型都由合成器订阅，这里不再订阅事件
        if self._tracking is not None:
            await self._tracking.attach(self._driver)
        else:
            self.logger.warning("面捕合成器未装配：VTS 只剩热键与连接，形象不会被逐帧驱动")

        self._has_started = True

    async def cleanup(self) -> None:
        """清理资源"""
        if not self._has_started:
            return

        # 先摘下面捕驱动（摘下前发一帧静止姿态），再断开连接
        if self._tracking is not None:
            await self._tracking.detach(self._driver)

        await self._disconnect()
        self._has_started = False
        self.logger.info(f"{self.__class__.__name__} 已停止")

    # ===== 业务方法 =====

    async def set_parameter_value(self, parameter_name: str, value: float, weight: float = 1) -> bool:
        """调试台手动写一个 VTS 输入：临时盖过面捕合成器 3 秒（weight 为历史签名，忽略）。"""
        if not self._is_connected:
            return False
        self._driver.set_override(parameter_name, value)
        return True

    # ===== 契约方法（LLM 工具与自动情绪路径共用的渲染入口）=====

    async def set_expression(self, emotion: str, intensity: float) -> ToolExecutionResult:
        """设置当前情绪：交给面捕合成器，浮现后保持配置的时长再回落。

        合成器按调度规则处理：同族相近只延长、最短停留内排队、强惊吓可打断。
        映射表外的情绪名按失败结果返回（词表编译期已知，出现未知名即调用方契约破坏）。
        """
        if emotion not in _EMOTIONS:
            self.error_count += 1
            return tool_result_fail("vts_set_expression", f"未知情绪 '{emotion}'（应为 17 枚举值之一）")
        if self._tracking is None:
            return tool_result_fail("vts_set_expression", "面捕合成器未启用（avatar.toml [tracking].enabled）")
        factor = min(1.0, max(0.0, float(intensity)))
        outcome = self._tracking.express(emotion, factor)
        self.render_count += 1
        return tool_result_ok("vts_set_expression", True, {"emotion": emotion, "intensity": factor, "outcome": outcome})

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
        """启停头部微动（面捕合成器的头部漫步；未装配合成器时无事可做）。"""
        if self._tracking is not None:
            self._tracking.set_head_wander(enabled)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self.__class__.__name__,
            "is_connected": self._is_connected,
            "render_count": self.render_count,
            "error_count": self.error_count,
            "hotkey_count": len(self.hotkey_matcher.hotkey_list),
            "model": self._current_model_name,
            "tracking": self._tracking is not None,
            "emotion": self._tracking.current_emotion() if self._tracking is not None else "",
        }

    # ===== 内部辅助 =====

    async def _connect(self) -> None:
        if self._is_connecting or self._is_connected:
            return
        self._is_connecting = True
        try:
            self.logger.info(f"开始连接 VTS: {self.vts_host}:{self.vts_port}")
            await self._client.connect()
            self._is_connected = True
            self.last_error = ""
            self.logger.info("VTS 连接成功")
            # 连接态换血：装配期降级登记的 0 工具在此补注册（回调由注册接线方提供）
            self.mark_connected()

            await self._reload_model_state()
            self._current_model_name = await self._query_current_model_name()
            self._driver.set_active(True)
        except Exception as e:
            self.last_error = f"VTS 连接失败: {e}"
            self.logger.error(f"VTS 连接失败: {e}")
            self._is_connected = False
        finally:
            self._is_connecting = False

    async def _reload_model_state(self) -> None:
        """模型相关状态：热键重拉 + 输入范围刷新（连接建立与换模跟随共用）。"""
        await self.hotkey_matcher.load_hotkeys()
        try:
            self._driver.set_input_ranges(await self._query_input_ranges())
        except Exception as e:
            self.logger.warning(f"拉取 VTS 输入范围失败，面捕驱动暂不钳制: {e}")
            self._driver.set_input_ranges({})

    async def _query_input_ranges(self) -> Dict[str, Tuple[float, float]]:
        """VTS 可注入输入的原生范围 ``{名: (min, max)}``（驱动按它钳制、只发认识的输入）。"""
        data = await self._client.request("InputParameterListRequest", None)
        ranges: Dict[str, Tuple[float, float]] = {}
        for param in [*data.get("defaultParameters", []), *data.get("customParameters", [])]:
            try:
                ranges[str(param["name"])] = (float(param["min"]), float(param["max"]))
            except (KeyError, TypeError, ValueError):
                continue
        return ranges

    async def _query_current_model_name(self) -> str:
        """查询 VTS 当前加载的模型名（没加载模型或查询失败时为空串）。"""
        try:
            data = await self._client.request("CurrentModelRequest", None)
            return str(data.get("modelName") or "") if data.get("modelLoaded") else ""
        except Exception as e:
            self.logger.debug(f"查询当前模型名失败: {e}")
            return ""

    async def _follow_model_switch(self) -> None:
        """换模跟随：检测到模型切换后重跑解析链（热键/绑定/缩放/基线全部刷新）。

        触发用健康心跳内的模型名轮询而非订阅 VTS 的换模事件：轮询复用既有心跳
        节拍、不多开一路订阅；换模是 VTS/人侧的低频操作，5 秒级跟随足够。
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
        if not self._client.is_open:
            return False
        try:
            await self._client.request("APIStateRequest", None, timeout_s=3.0)
            return True
        except Exception:
            return False

    async def _send_rest_pose(self) -> None:
        """断开前发一帧静止姿态：VTS 停止注入后会一直保持最后的值（实测 10 秒不回落）。"""
        if self._tracking is None or not self._driver.ready():
            return
        try:
            await self._driver.apply(self._tracking.rest_frame())
        except Exception as e:
            self.logger.debug(f"断开前发送静止姿态失败（已忽略）: {e}")

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
            self._reconnect_skip_polls = max(1, round(delay_ms / (self._reconnect_interval_s * 1000)))

    async def _reconnect_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._reconnect_interval_s)
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
                    self._driver.set_active(False)
                    # 连接态换血：摘除断连工具（Agent 工具表不再包含 vts_*）
                    self.mark_disconnected()
                    await self._client.close()
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

        if not self._is_connected:
            return
        await self._send_rest_pose()
        self._driver.set_active(False)
        try:
            await self._client.close()
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
    tracking: Optional[Any] = None,
    client_factory: Optional[Callable[..., Any]] = None,
) -> VTSProvider:
    """构造 VTSProvider 实例（不启动，由调用方 setup）"""
    return VTSProvider(
        config=config,
        event_bus=event_bus,
        lipsync_analyzer=lipsync_analyzer,
        tracking=tracking,
        client_factory=client_factory,
    )


def register_vts_tools(
    registry: Any,
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    lipsync_analyzer: Optional[Any] = None,
    subtitle_service: Optional[Any] = None,
    tracking: Optional[Any] = None,
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
        tracking=tracking,
    )
    registry.register_provider(provider)
    wire_connection_tools(provider, registry)
    return provider
