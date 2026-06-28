"""
Warudo Handler - Warudo 虚拟形象控制 Handler

职责:
- 通过 WebSocket 连接到 Warudo
- 接收 Intent 事件并适配为 Warudo 参数
- 发送表情、状态等指令到 Warudo
- 口型同步、眨眼、眼球移动等自动化动作
- 心情状态管理
- 字幕系统管理(HTML + WebSocket)
- 自动重连支持

重构自 plugins_backup/warudo/(commit 78a0c46)旧插件,适配新 3 阶段架构。
"""

import asyncio
import json
from typing import TYPE_CHECKING, Any, Dict, Optional

from pydantic import BaseModel, Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.streaming.audio_stream_channel import (
    AudioStreamChannel,
    BackpressureStrategy,
    SubscriberConfig,
)
from src.stages.output.handlers.avatar.base import AvatarHandlerBase
from src.stages.output.handlers.avatar.warudo.lip_sync_subscriber import (
    WarudoLipSyncSubscriber,
)
from src.stages.output.handlers.avatar.warudo.state.mood_manager import MoodManager
from src.stages.output.handlers.avatar.warudo.state.warudo_state_manager import (
    WarudoStateManager,
)
from src.stages.output.handlers.avatar.warudo.subtitle.subtitle_manager import (
    WarudoSubtitleManager,
)
from src.stages.output.handlers.avatar.warudo.tasks.blink_task import BlinkTask
from src.stages.output.handlers.avatar.warudo.tasks.shift_task import ShiftTask
from src.stages.output.handlers.avatar.warudo.tasks.talking_head_task import (
    TalkingHeadTask,
)
from src.stages.output.handlers.avatar.warudo.tasks.throw_fish_task import (
    ThrowFishTask,
)
from src.stages.output.handlers.avatar.warudo.tasks.typing_action_task import (
    TypingActionTask,
)
from src.stages.output.handlers.avatar.warudo.warudo_sender import ActionSender
from src.stages.output.registry import handler

if TYPE_CHECKING:
    from src.modules.types import Intent


# 软降级:websockets 库可能未安装
try:
    import websockets  # type: ignore

    WEBSOCKETS_AVAILABLE = True
except ImportError:
    websockets = None  # type: ignore
    WEBSOCKETS_AVAILABLE = False


@handler("warudo")
class WarudoHandler(AvatarHandlerBase):
    """Warudo 虚拟形象控制 Handler"""

    class ConfigSchema(BaseConfig):
        """Warudo 输出 Handler 配置"""

        type: str = "warudo"

        # WebSocket 配置
        ws_host: str = Field(default="localhost", description="WebSocket 主机地址")
        ws_port: int = Field(default=19190, ge=1, le=65535, description="WebSocket 端口")
        reconnect_delay_seconds: float = Field(default=5.0, ge=0.1, description="断线重连间隔")

        # Lip-sync 配置
        lip_sync_enabled: bool = Field(default=True, description="是否启用音频口型同步")
        lip_sync_sample_rate: int = Field(default=16000, ge=8000, le=48000, description="音频处理采样率")
        lip_sync_volume_threshold: float = Field(default=0.01, ge=0.0, le=1.0, description="元音检测音量阈值")
        lip_sync_smoothing_factor: float = Field(default=0.3, ge=0.0, le=1.0, description="元音强度平滑系数")
        lip_sync_sensitivity: float = Field(default=0.5, ge=0.0, le=1.0, description="元音检测灵敏度")

        # 字幕系统配置
        subtitle_enabled: bool = Field(default=True, description="是否启用字幕系统")
        subtitle_port: int = Field(default=8766, ge=1, le=65535, description="字幕 HTTP 端口")
        subtitle_show_status: bool = Field(default=False, description="字幕页面显示连接状态")

        # 小动画配置
        talking_head_enabled: bool = Field(default=True, description="是否启用说话头部动作")
        talking_head_interval: float = Field(default=0.1, ge=0.05, le=1.0, description="头部动作循环步进(秒)")

        throw_fish_cooldown: float = Field(default=5.0, ge=0.0, description="抛鱼触发冷却(秒)")

    # ==================== Warudo API 参数映射 ====================

    _EMOTION_KEYS = frozenset(
        {
            "happy",
            "sad",
            "angry",
            "surprised",
            "shy",
            "love",
            "neutral",
        }
    )

    class _WarudoActionParams(BaseModel):  # type: ignore[name-defined]  # noqa: F821
        duration_ms: int = Field(default=1500, ge=100, le=10000)

    _ACTION_PARAMS_SCHEMA: dict[str, type] = {
        "blink": _WarudoActionParams,
        "nod": _WarudoActionParams,
        "shake": _WarudoActionParams,
        "wave": _WarudoActionParams,
        "clap": _WarudoActionParams,
        "throw_fish": _WarudoActionParams,
        "throw_fish_big": _WarudoActionParams,
        "typing_on": _WarudoActionParams,
        "typing_off": _WarudoActionParams,
        "head_action": _WarudoActionParams,
    }

    def get_capabilities(self):
        from src.stages.output.capabilities import (
            ActionSpec,
            HandlerCapabilities,
            _pydantic_to_param_spec,
        )

        actions = [
            ActionSpec(
                name=local,
                description=f"Warudo {local} action",
                parameters=_pydantic_to_param_spec(cls),
            )
            for local, cls in self._ACTION_PARAMS_SCHEMA.items()
        ]
        return HandlerCapabilities(actions=actions)

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: EventBus,
        audio_stream_channel: Optional[AudioStreamChannel] = None,
    ):
        super().__init__(config, event_bus, audio_stream_channel)
        self.logger = get_logger(self.__class__.__name__)

        # 配置验证
        self.typed_config = self.ConfigSchema.from_dict(config)

        # Emotion -> Warudo 表情参数映射
        self._emotion_map: Dict[str, Dict[str, float]] = {
            "happy": {"mouthSmile": 1.0},
            "sad": {"mouthSad": 1.0},
            "angry": {"eyebrowAngry": 1.0},
            "surprised": {"eyeSurprised": 1.0},
            "shy": {"cheekBlush": 0.8},
            "love": {"heart": 1.0},
            "neutral": {},
        }

        # Action 三字典分类(替代旧的单一 _action_map)
        # 热键类:由 _send_hotkey 发送
        self._action_hotkey_map: Dict[str, str] = {
            "blink": "blink",
            "nod": "nod",
            "shake": "shake",
            "wave": "wave",
            "clap": "clap",
        }
        # 身体动作类:由 _send_action_internal 发送 body_action 类别
        self._action_body_map: Dict[str, str] = {
            "throw_fish": "throw_fish",
            "throw_fish_big": "throw_fish_big",
            "typing_on": "typing_on",
            "typing_off": "typing_off",
        }
        # 头部动作类:由 _send_action_internal 发送 head_action 类别
        self._action_head_map: Dict[str, str] = {
            "head_action": "head_action",
        }

        # 兼容旧字段
        self._action_map = self._action_hotkey_map

        # WebSocket 状态
        self.ws_host = self.typed_config.ws_host
        self.ws_port = self.typed_config.ws_port
        self.websocket = None
        self._connection_task: Optional[asyncio.Task] = None
        self._should_stop: bool = False
        self._first_connection: bool = True
        self._lip_sync_sub_id: Optional[str] = None

        # 单实例 ActionSender
        self._action_sender = ActionSender()

        # ==================== 子组件实例化 ====================

        async def send_action_callback(action: str, data: Any) -> bool:
            """发送动作到 Warudo(给子组件用)"""
            await self._send_action_internal(action, data)
            return True

        # 状态管理器(给心情、眨眼、眼球移动用)
        self.state_manager = WarudoStateManager(self.logger, send_action_callback)

        # 心情管理器
        self.mood_manager = MoodManager(self.state_manager, self.logger)

        # 眨眼任务
        self.blink_task = BlinkTask(self.state_manager, self.logger)

        # 眼球移动任务
        self.shift_task = ShiftTask(self.state_manager, self.logger)

        # 说话时头部动作任务(接收 send_action_callback 而非 WebSocket)
        self.talking_head_task: Optional[TalkingHeadTask] = None
        if self.typed_config.talking_head_enabled:
            self.talking_head_task = TalkingHeadTask(
                send_action_callback=send_action_callback,
                logger=self.logger,
                min_interval=self.typed_config.talking_head_interval,
            )

        # 抛鱼动画(单次触发)
        self.throw_fish_task = ThrowFishTask(
            send_action_callback=send_action_callback,
            logger=self.logger,
            cooldown_seconds=self.typed_config.throw_fish_cooldown,
        )

        # 打字动画任务
        self.typing_action_task = TypingActionTask(
            send_action_callback=send_action_callback,
            logger=self.logger,
        )

        # 字幕系统
        self.subtitle_manager: Optional[WarudoSubtitleManager] = None
        if self.typed_config.subtitle_enabled:
            self.subtitle_manager = WarudoSubtitleManager(
                port=self.typed_config.subtitle_port,
                show_status=self.typed_config.subtitle_show_status,
                logger=self.logger,
            )

        # 音频口型同步订阅器(只启用 lip_sync + audio_stream_channel 都存在时)
        self.lip_sync: Optional[WarudoLipSyncSubscriber] = None
        if self.typed_config.lip_sync_enabled and audio_stream_channel is not None:
            self.lip_sync = WarudoLipSyncSubscriber(
                state_manager=self.state_manager,
                logger_name=f"{self.__class__.__name__}.LipSync",
                sample_rate=self.typed_config.lip_sync_sample_rate,
                volume_threshold=self.typed_config.lip_sync_volume_threshold,
                smoothing_factor=self.typed_config.lip_sync_smoothing_factor,
                vowel_detection_sensitivity=self.typed_config.lip_sync_sensitivity,
                is_connected=lambda: self._is_connected,
                on_audio_start_hook=self.on_audio_start_proxy,
                on_audio_end_hook=self.on_audio_end_proxy,
            )

        # 当前心情状态(1-10)
        self.current_mood = {"joy": 5, "anger": 1, "sorrow": 1, "fear": 1}
        self.current_expression = "neutral"

        # 统计
        self.render_count = 0
        self.error_count = 0

        self.logger.info("WarudoHandler 初始化完成")

    # ==================== AvatarHandlerBase 抽象方法实现 ====================

    async def _adapt_intent(self, intent: "Intent") -> Optional[Dict[str, Any]]:
        """适配 Intent 为 Warudo 参数(三字典分类)。

        新 Intent 结构:
        - `intent.emotion` 是 `IntentEmotion` 对象,`name` 已是全局枚举值
        - `intent.action` 是 `IntentAction` 对象,`name` 是 handler 前缀化的 `<handler>.<action>`,
          这里只取点号后的本地名

        返回 None 表示跳过(本地 action 不被识别 / 参数校验失败)。
        """
        result: Dict[str, Any] = {
            "expressions": {},
            "hotkeys": [],
            "body_actions": [],
            "head_actions": [],
        }

        if intent.emotion is not None:
            emotion_str = intent.emotion.name
            if emotion_str in self._emotion_map:
                scale = float(intent.emotion.intensity)
                base = self._emotion_map[emotion_str]
                result["expressions"] = {k: v * scale for k, v in base.items()}
                self.logger.debug(f"情感映射: {emotion_str} (intensity={scale}) -> {result['expressions']}")

        if intent.action is not None:
            local_name = intent.action.name.split(".", 1)[-1]
            schema_cls = self._ACTION_PARAMS_SCHEMA.get(local_name)
            if schema_cls is None:
                self.logger.debug(f"action '{local_name}' 不在 warudo _ACTION_PARAMS_SCHEMA 中,跳过")
                return None
            try:
                schema_cls.model_validate(intent.action.parameters or {})
            except Exception as e:
                self.logger.warning(f"warudo action '{local_name}' 参数校验失败: {e}")
                return None

            if local_name in self._action_hotkey_map:
                result["hotkeys"].append(self._action_hotkey_map[local_name])
            elif local_name in self._action_body_map:
                result["body_actions"].append(self._action_body_map[local_name])
            elif local_name in self._action_head_map:
                result["head_actions"].append(self._action_head_map[local_name])
            else:
                return None

        return result

    async def _render_to_platform(self, params: Dict[str, Any]) -> None:
        """渲染到 Warudo 平台"""
        try:
            expressions = params.get("expressions", {})
            hotkeys = params.get("hotkeys", [])
            body_actions = params.get("body_actions", [])
            head_actions = params.get("head_actions", [])

            # 1. 应用表情参数
            for param_name, param_value in expressions.items():
                await self._send_expression(param_name, param_value)

            # 2. 触发热键
            for hotkey in hotkeys:
                await self._send_hotkey(hotkey)

            # 3. 身体动作
            for body_action in body_actions:
                await self._send_action_internal("body_action", body_action)

            # 4. 头部动作
            for head_action in head_actions:
                await self._send_action_internal("head_action", head_action)

            self.render_count += 1
        except Exception as e:
            self.logger.error(f"Warudo 渲染失败: {e}", exc_info=True)
            self.error_count += 1
            raise RuntimeError(f"Warudo 渲染失败: {e}") from e

    async def _connect(self) -> None:
        """连接到 Warudo WebSocket(并启动后台重连循环)"""
        if not WEBSOCKETS_AVAILABLE:
            self.logger.error("websockets 库未安装,无法连接 Warudo")
            return

        self._should_stop = False
        uri = f"ws://{self.ws_host}:{self.ws_port}"

        # 第一次同步连接(给 _is_connected 提供初始值)
        try:
            self.websocket = await websockets.connect(uri)  # type: ignore
            self._is_connected = True
            self.logger.info(f"已连接到 Warudo: {uri}")
        except Exception as e:
            self.logger.warning(f"首次连接 Warudo 失败({e}),将由后台重连循环处理")
            self._is_connected = False

        # 启动后台重连循环
        if not self._connection_task or self._connection_task.done():
            self._connection_task = asyncio.create_task(self._connection_loop(uri), name="Warudo_Reconnect")
            self.logger.info("Warudo WebSocket 后台重连任务已启动")

    async def _disconnect(self) -> None:
        """断开 Warudo WebSocket 连接"""
        # 1. 通知重连循环停止
        self._should_stop = True

        # 2. 取消音频订阅
        if self._lip_sync_sub_id and self.audio_stream_channel:
            try:
                await self.audio_stream_channel.unsubscribe(self._lip_sync_sub_id)
            except Exception as e:
                self.logger.error(f"取消 lip-sync 订阅失败: {e}")
            finally:
                self._lip_sync_sub_id = None

        # 3. 停止后台任务
        try:
            await self.blink_task.stop()
        except Exception as e:
            self.logger.error(f"停止眨眼任务失败: {e}")

        try:
            await self.shift_task.stop()
        except Exception as e:
            self.logger.error(f"停止眼球移动任务失败: {e}")

        if self.talking_head_task is not None:
            try:
                await self.talking_head_task.stop()
            except Exception as e:
                self.logger.error(f"停止 talking_head 任务失败: {e}")

        try:
            await self.typing_action_task.stop()
        except Exception as e:
            self.logger.error(f"停止 typing_action 任务失败: {e}")

        # 4. 停止状态监控
        try:
            self.state_manager.stop_monitoring()
        except Exception as e:
            self.logger.error(f"停止状态监控失败: {e}")

        # 5. 停止字幕服务器
        if self.subtitle_manager is not None:
            try:
                await self.subtitle_manager.stop_server()
            except Exception as e:
                self.logger.error(f"停止字幕服务器失败: {e}")

        # 6. 取消 WebSocket 重连循环
        if self._connection_task and not self._connection_task.done():
            self._connection_task.cancel()
            try:
                await asyncio.wait_for(self._connection_task, timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                self.logger.debug("WebSocket 重连任务已取消")
            finally:
                self._connection_task = None

        # 7. 关闭当前 WebSocket
        if self.websocket:
            try:
                await asyncio.wait_for(self.websocket.close(), timeout=2.0)
            except (asyncio.TimeoutError, Exception) as e:
                self.logger.warning(f"WebSocket 关闭异常: {e}")
            finally:
                self.websocket = None
                self._is_connected = False

        self._action_sender.set_websocket(None)

    # ==================== 连接管理 ====================

    async def _connection_loop(self, uri: str) -> None:
        """WebSocket 真重连循环(由 _should_stop 控制退出)"""
        self.logger.info("Warudo WebSocket 重连循环已启动")
        while not self._should_stop:
            try:
                # 第一次连接后或断线后重新连接
                if not self._is_connected or self.websocket is None or self.websocket.closed:
                    self.logger.info(f"尝试连接 Warudo: {uri}")
                    self.websocket = await websockets.connect(uri)  # type: ignore
                    self._is_connected = True
                    self._action_sender.set_websocket(self.websocket)
                    self.logger.info(f"已连接到 Warudo: {uri}")

                    # 首次连接初始化(只执行一次)
                    if self._first_connection:
                        self._first_connection = False
                        await self._on_first_connection_setup()

                # 保持连接直到断开
                if self.websocket and not self.websocket.closed:
                    await self.websocket.wait_closed()

            except asyncio.CancelledError:
                self.logger.debug("WebSocket 重连循环被取消")
                break
            except Exception as e:
                self.logger.error(f"WebSocket 连接异常: {e}")
            finally:
                if not self._should_stop:
                    self._is_connected = False
                    self.websocket = None
                    self._action_sender.set_websocket(None)
                    self.logger.debug(f"WebSocket 断开,{self.typed_config.reconnect_delay_seconds}秒后重连...")
                    try:
                        await asyncio.sleep(self.typed_config.reconnect_delay_seconds)
                    except asyncio.CancelledError:
                        break

        self.logger.info("Warudo WebSocket 重连循环已退出")

    async def _on_first_connection_setup(self) -> None:
        """首次连接成功后的初始化(启动后台任务、监控、订阅音频)"""
        # 1. 启动状态监控(关键修复:之前从未启动)
        try:
            self.state_manager.start_monitoring()
            self.logger.info("状态管理器监控已启动")
        except Exception as e:
            self.logger.error(f"启动状态监控失败: {e}")

        # 2. 启动眨眼任务(关键修复:之前从未启动)
        try:
            await self.blink_task.start()
            self.logger.info("眨眼任务已启动")
        except Exception as e:
            self.logger.error(f"启动眨眼任务失败: {e}")

        # 3. 启动眼球移动任务(关键修复:之前从未启动)
        try:
            await self.shift_task.start()
            self.logger.info("眼球移动任务已启动")
        except Exception as e:
            self.logger.error(f"启动眼球移动任务失败: {e}")

        # 4. 启动 talking_head 任务
        if self.talking_head_task is not None:
            try:
                await self.talking_head_task.start()
                self.logger.info("TalkingHead 任务已启动")
            except Exception as e:
                self.logger.error(f"启动 TalkingHead 任务失败: {e}")

        # 5. 启动 typing_action 任务
        try:
            await self.typing_action_task.start()
            self.logger.info("TypingAction 任务已启动")
        except Exception as e:
            self.logger.error(f"启动 TypingAction 任务失败: {e}")

        # 6. 启动字幕服务器
        if self.subtitle_manager is not None:
            try:
                await self.subtitle_manager.start_server()
                self.logger.info(f"字幕服务器已启动: http://localhost:{self.typed_config.subtitle_port}")
            except Exception as e:
                self.logger.error(f"启动字幕服务器失败: {e}")

        # 7. 订阅音频流(关键: lip-sync)
        if self.lip_sync is not None and self.audio_stream_channel is not None:
            try:
                self._lip_sync_sub_id = await self.audio_stream_channel.subscribe(
                    name="warudo_lip_sync",
                    on_audio_start=self.lip_sync.on_start,
                    on_audio_chunk=self.lip_sync.on_chunk,
                    on_audio_end=self.lip_sync.on_end,
                    config=SubscriberConfig(
                        queue_size=200,
                        backpressure_strategy=BackpressureStrategy.DROP_NEWEST,
                    ),
                )
                self.logger.info("已订阅 AudioStreamChannel 进行 lip-sync")
            except Exception as e:
                self.logger.error(f"订阅 AudioStreamChannel 失败: {e}")

    # ==================== 发送方法 ====================

    async def _send_action_internal(self, action: str, data: Any) -> None:
        """通过单实例 ActionSender 发送动作"""
        if not self._is_connected or self.websocket is None or self.websocket.closed:
            self.logger.warning(f"Warudo 未连接,无法发送动作: {action}")
            return
        try:
            self._action_sender.set_websocket(self.websocket)
            await self._action_sender.send_action(action, data)
        except Exception as e:
            self.logger.error(f"发送动作失败: {action}: {e}")

    async def _send_expression(self, param_name: str, param_value: float) -> None:
        """发送表情参数"""
        if not self._is_ready_to_send():
            self.logger.warning(f"Warudo 未连接,无法设置参数: {param_name} = {param_value}")
            return
        try:
            message = {"action": param_name, "data": param_value}
            if hasattr(self.websocket, "send_json"):
                await self.websocket.send_json(message)
            else:
                await self.websocket.send(json.dumps(message))
            self.logger.debug(f"设置 Warudo 参数: {param_name} = {param_value}")
        except Exception as e:
            self.logger.error(f"设置 Warudo 参数失败: {param_name}: {e}")

    async def _send_hotkey(self, hotkey_id: str) -> None:
        """发送热键"""
        if not self._is_ready_to_send():
            self.logger.warning(f"Warudo 未连接,无法触发热键: {hotkey_id}")
            return
        try:
            message = {"action": hotkey_id, "data": ""}
            if hasattr(self.websocket, "send_json"):
                await self.websocket.send_json(message)
            else:
                await self.websocket.send(json.dumps(message))
            self.logger.debug(f"触发热键: {hotkey_id}")
        except Exception as e:
            self.logger.error(f"触发热键失败: {hotkey_id}: {e}")

    def _is_ready_to_send(self) -> bool:
        """检查是否就绪可以发送(处理 mock 对象没有 closed 属性的情况)"""
        if not self._is_connected or self.websocket is None:
            return False
        closed = getattr(self.websocket, "closed", None)
        if closed is True:
            return False
        return True

    # ==================== 心情管理 ====================

    def update_mood(self, mood_data: Dict[str, Any]) -> bool:
        """更新心情状态"""
        has_changes = False
        for emotion in ["joy", "anger", "sorrow", "fear"]:
            new_value = max(1, min(10, int(mood_data.get(emotion, 5))))
            if self.current_mood[emotion] != new_value:
                self.current_mood[emotion] = new_value
                has_changes = True

        if has_changes:
            self.logger.debug(f"心情状态已更新: {self.current_mood}")
            self.mood_manager.update_mood(mood_data)

        return has_changes

    # ==================== 字幕推送 ====================

    async def push_subtitle(self, speech: str, user_name: str = "MaiBot") -> None:
        """推送完整字幕(一次性,one-shot 退化模式)"""
        if not self.subtitle_manager or not speech:
            return
        try:
            await self.subtitle_manager.start_generation(user_name)
            await self.subtitle_manager.add_chunk(speech)
            await self.subtitle_manager.complete_generation()
            self.logger.debug(f"字幕已推送: {speech[:50]}...")
        except Exception as e:
            self.logger.error(f"字幕推送失败: {e}")

    # ==================== 状态联动(供其他模块/测试调用) ====================

    async def start_talking(self) -> None:
        """开始说话(由 lip-sync on_start 回调或外部触发)"""
        if self.talking_head_task is not None:
            self.talking_head_task.is_talking = True
        self.state_manager.sight_state.set_state("camera", 1.0)
        await self._send_action_internal("loading", "")

    async def stop_talking(self) -> None:
        """停止说话(由 lip-sync on_end 回调或外部触发)"""
        if self.talking_head_task is not None:
            self.talking_head_task.is_talking = False
        self.state_manager.sight_state.set_state("camera", 0.0)

    async def on_audio_start_proxy(self) -> None:
        """lip-sync on_start 回调代理: 触发 start_talking"""
        await self.start_talking()

    async def on_audio_end_proxy(self) -> None:
        """lip-sync on_end 回调代理: 触发 stop_talking"""
        await self.stop_talking()

    # ==================== 统计信息 ====================

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self.__class__.__name__,
            "is_connected": self._is_connected,
            "render_count": self.render_count,
            "error_count": self.error_count,
            "current_mood": self.current_mood,
            "current_expression": self.current_expression,
            "lip_sync_enabled": self.lip_sync is not None,
            "lip_sync_subscribed": self._lip_sync_sub_id is not None,
            "subtitle_enabled": self.subtitle_manager is not None,
            "talking_head_running": self.talking_head_task.running if self.talking_head_task else False,
        }
