"""
MaiCoreDecisionProvider - MaiCore决策提供者

职责:
- 将 NormalizedMessage 转换为 Intent
- 通过 WebSocket 与 MaiCore 通信
- 自己解析 MaiCore 响应为 Intent（支持 LLM 和规则两种方式）
"""

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any, Dict, Literal, Optional

from maim_message import MessageBase, RouteConfig, Router, TargetConfig
from pydantic import Field

from src.modules.di.context import ProviderContext
from src.modules.types import ActionType, EmotionType, Intent, IntentAction, SourceContext
from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger
from src.modules.types.base.decision_provider import DecisionProvider

from .router_adapter import RouterAdapter

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus
    from src.modules.types.base.normalized_message import NormalizedMessage


class MaiCoreDecisionProvider(DecisionProvider):
    """
    MaiCore 决策提供者

    通过 WebSocket 与 MaiCore 通信，将 NormalizedMessage 转换为 Intent。

    职责:
    - 决策逻辑 (decide)
    - 直接使用 Router 管理 WebSocket 连接
    - 使用 RouterAdapter 发送消息到 MaiCore
    - 自己解析 MaiCore 响应为 Intent（支持 LLM 和规则两种方式）

    配置示例:
        ```toml
        [decision.maicore]
        host = "localhost"
        port = 8000
        platform = "amaidesu"
        http_host = "localhost"
        http_port = 8080
        http_callback_path = "/callback"
        ```

    异步处理流程:
        1. decide() 被调用，发送消息到 MaiCore（fire-and-forget）
        2. MaiCore 响应到达时，_process_maicore_message 解析为 Intent（优先 LLM，失败时降级到规则）
        3. 通过 event_bus 发布 decision.intent 事件
    """

    class ConfigSchema(BaseProviderConfig):
        """MaiCore决策Provider配置Schema

        通过WebSocket与MaiCore通信进行决策。
        """

        type: Literal["maicore"] = "maicore"
        host: str = Field(default="localhost", description="MaiCore WebSocket服务器主机地址")
        port: int = Field(default=8000, description="MaiCore WebSocket服务器端口", ge=1, le=65535)
        platform: str = Field(default="amaidesu", description="平台标识符")
        http_host: Optional[str] = Field(default=None, description="HTTP服务器主机（可选）")
        http_port: Optional[int] = Field(default=None, description="HTTP服务器端口", ge=1, le=65535)
        http_callback_path: str = Field(default="/callback", description="HTTP回调路径")
        connect_timeout: float = Field(default=10.0, description="连接超时时间（秒）", gt=0)
        reconnect_interval: float = Field(default=5.0, description="重连间隔时间（秒）", gt=0)

        # Action 建议配置
        action_suggestions_enabled: bool = Field(default=False, description="是否启用 MaiBot Action 建议")
        action_confidence_threshold: float = Field(
            default=0.6, ge=0.0, le=1.0, description="Action 建议的最低置信度阈值"
        )
        action_cooldown_seconds: float = Field(default=5.0, gt=0.0, description="同一 Action 的最小间隔（秒）")
        max_suggested_actions: int = Field(default=3, ge=1, le=10, description="每条消息最大建议数量")

    @classmethod
    def get_registration_info(cls) -> Dict[str, Any]:
        """
        获取 Provider 注册信息

        用于显式注册模式，避免模块导入时的自动注册。
        """
        return {"layer": "decision", "name": "maicore", "class": cls, "source": "builtin:maicore"}

    def __init__(self, config: Dict[str, Any], context: "ProviderContext"):
        """
        初始化 MaiCoreDecisionProvider

        Args:
            config: 配置字典，包含:
                - host: MaiCore WebSocket 服务器主机
                - port: MaiCore WebSocket 服务器端口
                - platform: 平台标识符
                - http_host: (可选) HTTP 服务器主机
                - http_port: (可选) HTTP 服务器端口
                - http_callback_path: (可选) HTTP 回调路径，默认"/callback"
            context: 依赖注入上下文
        """
        super().__init__(config, context)

        self.provider_name = "maicore"

        # 使用 Pydantic Schema 验证配置
        self.typed_config = self.ConfigSchema(**config)
        self.logger = get_logger("MaiCoreDecisionProvider")

        # WebSocket 配置
        self.host = self.typed_config.host
        self.port = self.typed_config.port
        self.platform = self.typed_config.platform
        self.ws_url = f"ws://{self.host}:{self.port}/ws"

        # HTTP 配置
        self.http_host = self.typed_config.http_host
        self.http_port = self.typed_config.http_port
        self.http_callback_path = self.typed_config.http_callback_path

        # Router
        self._router: Optional[Router] = None
        self._router_task: Optional[asyncio.Task] = None

        # 组件
        self._router_adapter: Optional[RouterAdapter] = None

        # EventBus 引用（用于事件通知）
        self._event_bus: Optional["EventBus"] = None

    async def init(self) -> None:
        """
        初始化 MaiCoreDecisionProvider

        配置 Router 并创建 RouterAdapter。
        """
        self.logger.info("初始化 MaiCoreDecisionProvider...")

        # 配置 Router
        self._setup_router()

        if not self._router:
            self.logger.error("Router 初始化失败")
            raise RuntimeError("Router 初始化失败")

        # 创建 RouterAdapter
        self._router_adapter = RouterAdapter(self._router, self.event_bus)
        self._router_adapter.register_message_handler(self._handle_maicore_message)

        # 启动 WebSocket 连接
        await self.connect()

        self.logger.info("MaiCoreDecisionProvider 初始化完成")

    def _setup_router(self):
        """配置 maim_message Router"""
        route_config = RouteConfig(
            route_config={
                self.platform: TargetConfig(
                    url=self.ws_url,
                    token=None,
                )
            }
        )
        self._router = Router(route_config)
        self.logger.info(f"Router 配置完成，目标 MaiCore: {self.ws_url}")

    async def connect(self):
        """启动 WebSocket 连接 - 直接使用 Router"""
        if self._router:
            # 创建 Router 运行任务
            self._router_task = asyncio.create_task(self._router.run())
            self.logger.info("MaiCore Router 运行任务已创建")

            # 等待连接建立
            await asyncio.sleep(1)

            # 检查连接状态
            if self._router.check_connection(self.platform):
                self.logger.info("MaiCore WebSocket 连接初步建立")
            else:
                self.logger.warning("MaiCore WebSocket 连接尚未建立，Router 将在后台继续重试")

    async def disconnect(self):
        """断开 WebSocket 连接 - 直接使用 Router"""
        if self._router:
            # 停止 Router（会断开所有连接）
            await self._router.stop()
            self.logger.info("MaiCore Router 已停止")

        # 取消运行任务
        if self._router_task is not None and not self._router_task.done():
            self._router_task.cancel()
            try:
                await self._router_task
            except asyncio.CancelledError:
                pass

    def _normalized_to_message_base(self, normalized: "NormalizedMessage") -> Optional["MessageBase"]:
        """
        转换 NormalizedMessage 为 MessageBase

        将 NormalizedMessage 转换为 MaiCore 需要的 MessageBase 格式。

        Args:
            normalized: 标准化消息

        Returns:
            MessageBase 实例，如果转换失败返回 None
        """
        try:
            from maim_message import BaseMessageInfo, FormatInfo, MessageBase, Seg, UserInfo

            # 构建UserInfo
            # platform 字段是 MaiBot 存储消息时需要的（chat_info_user_platform）
            user_id = normalized.user_id or "unknown"
            nickname = normalized.metadata.get("user_nickname", normalized.source)
            user_info = UserInfo(platform=self.platform, user_id=user_id, user_nickname=nickname)

            # 构建FormatInfo（MaiBot 需要）
            format_info = FormatInfo(
                content_format=["text"],
                accept_format=["text"],
            )

            # 构建Seg（文本片段）
            seg = Seg(type="text", data=normalized.text)

            # 构建MessageBase
            # 统一使用 self.platform 作为平台标识（amaidesu），而非原始来源
            # 注意：raw_message 字段是 MaiBot 需要的原始消息内容
            # additional_config 包含 MaiBot 需要的额外信息
            # 注意：存储原始 message_id 以便在响应中查找对应的 Future
            message_id = f"normalized_{int(normalized.timestamp)}"
            additional_config = {
                "source": "amaidesu_provider",
                "original_platform": normalized.source,  # 保留原始平台信息
                "original_message_id": message_id,  # 存储原始 message_id 用于关联响应
            }
            message = MessageBase(
                message_info=BaseMessageInfo(
                    message_id=message_id,
                    platform=self.platform,
                    user_info=user_info,
                    time=normalized.timestamp,
                    format_info=format_info,
                    additional_config=additional_config,
                ),
                message_segment=seg,
                raw_message=normalized.text,  # 添加原始消息内容
            )

            return message
        except Exception as e:
            self.logger.error(f"转换为 MessageBase 失败: {e}", exc_info=True)
            return None

    async def decide(self, normalized_message: "NormalizedMessage") -> None:
        """
        发送消息到 MaiCore（fire-and-forget）

        Args:
            normalized_message: 标准化消息
        """
        if not self._is_router_connected:
            self.logger.warning("MaiCore 未连接，跳过消息发送")
            return

        message = self._normalized_to_message_base(normalized_message)
        if not message:
            self.logger.error("转换为 MessageBase 失败，无法发送消息")
            return

        if not self._router_adapter:
            self.logger.error("RouterAdapter 未初始化，无法发送消息")
            return

        try:
            await self._router_adapter.send(message)
            self.logger.debug(f"消息已发送至 MaiCore (id: {message.message_info.message_id})")
        except Exception as e:
            self.logger.error(f"发送消息到 MaiCore 时发生错误: {e}", exc_info=True)

    def _handle_maicore_message(self, message_data: Dict[str, Any]):
        """
        处理从 MaiCore 接收到的消息

        通过 EventBus 发布消息事件。

        Args:
            message_data: 消息数据（字典格式）
        """
        # 打印收到的完整消息（所有字段）
        self.logger.debug(f"收到 MaiCore 原始消息: {message_data}")
        # 在新任务中处理以避免阻塞
        asyncio.create_task(self._process_maicore_message(message_data))

    async def _process_maicore_message(self, message_data: Dict[str, Any]):
        """
        异步处理从 MaiCore 接收到的消息

        1. 解析为 MessageBase
        2. 调用 _parse_intent_from_maicore_response() 构造 Intent
        3. 通过 event_bus 发布 decision.intent 事件

        Args:
            message_data: 消息数据（字典格式）
        """
        try:
            # 从字典构建 MessageBase 对象
            message = MessageBase.from_dict(message_data)
        except Exception as e:
            self.logger.error(f"从 MaiCore 接收到的消息无法解析为 MessageBase 对象: {e}", exc_info=True)
            self.logger.debug(f"原始消息数据: {message_data}")
            return

        # 获取消息ID
        message_id = message.message_info.message_id
        response_text = self._extract_text_from_response(message)

        self.logger.debug(f"收到 MaiCore 消息: message_id={message_id}, text={response_text}")
        self.logger.debug(f"完整消息: {message_data}")

        # 检查消息是否是 MaiCore 的响应（有 message_segment 且有内容）
        seg = message.message_segment
        if not seg or not seg.data:
            self.logger.debug(f"收到无内容消息，忽略（message_id={message_id}）")
            return

        try:
            # 解析 MessageBase → Intent
            intent = self._parse_intent_from_maicore_response(message)
            self.logger.debug(f"Intent解析成功: {intent}")

            # 通过 event_bus 发布 decision.intent 事件
            await self._publish_intent(intent)

        except Exception as e:
            self.logger.error(f"解析 Intent 失败: {e}", exc_info=True)

    async def _publish_intent(self, intent: Intent) -> None:
        """通过 event_bus 发布 decision.intent.generated 事件"""
        from src.modules.events.names import CoreEvents
        from src.modules.events.payloads import IntentPayload

        if not self.event_bus:
            self.logger.error("EventBus 未初始化，无法发布事件")
            return

        provider_name = self.get_info().get("name", "maicore")

        await self.event_bus.emit(
            CoreEvents.DECISION_INTENT_GENERATED,
            IntentPayload.from_intent(intent, provider_name),
            source="MaiCoreDecisionProvider",
        )

        self.logger.debug("已发布 decision.intent.generated 事件")

    def _parse_intent_from_maicore_response(self, response: MessageBase) -> Intent:
        """
        从 MaiCore 响应解析 Intent

        优先使用 LLM 解析，失败时降级到规则解析。

        Args:
            response: MaiCore 返回的 MessageBase

        Returns:
            解析后的 Intent
        """
        # 提取文本
        text = self._extract_text_from_response(response)

        # 尝试使用 LLM 解析
        llm_service = self.context.llm_service
        if llm_service:
            try:
                return self._parse_with_llm(text, response, llm_service)
            except Exception as e:
                self.logger.warning(f"LLM 解析失败，降级到规则解析: {e}")

        # 降级到规则解析
        return self._parse_with_rules(text, response)

    def _extract_reply_to_id(self, message: MessageBase) -> Optional[str]:
        """
        从 MessageBase 中提取回复关联的原始消息 ID

        MaiCore 在消息间隔过长时会回复，回复消息的 Seg 中包含 type="reply" 的段，
        其 data 字段包含原始消息的 ID。

        Args:
            message: MaiCore 返回的 MessageBase

        Returns:
            原始消息 ID，如果没有 reply 段则返回 None
        """
        seg = message.message_segment
        if seg.type == "reply":
            return str(seg.data)
        return None

    def _extract_text_from_response(self, response: MessageBase) -> str:
        """
        从 MessageBase 提取文本内容

        Args:
            response: MaiCore 返回的 MessageBase

        Returns:
            提取的文本字符串
        """
        seg = response.message_segment
        if seg.type == "text":
            return seg.data
        else:
            # 非文本类型，返回类型+数据
            return f"[{seg.type}] {seg.data}"

    def _parse_with_llm(self, text: str, response: MessageBase, llm_service) -> Intent:
        """
        使用 LLM 解析 Intent

        Args:
            text: 提取的文本
            response: MaiCore 返回的 MessageBase
            llm_service: LLM 服务实例

        Returns:
            解析后的 Intent

        Raises:
            ValueError: 如果 LLM 返回的内容无法解析为 JSON
        """
        # 使用 decision/intent_parser prompt
        # 依赖注入的 prompt_service
        if not self.context or not self.context.prompt_service:
            raise ValueError("prompt_service 未注入，请检查 Provider 初始化配置")
        prompt_manager = self.context.prompt_service
        prompt = prompt_manager.render("decision/intent_parser", text=text)

        # 调用 LLM
        llm_manager = llm_service
        llm_result = llm_manager.chat_fast(prompt)

        if not llm_result.success:
            raise ValueError(f"LLM 调用失败: {llm_result.error}")

        content = llm_result.content

        # 完整的 JSON 清理逻辑
        # 1. 去掉外层 ```json / ``` 代码块
        content = re.sub(r"^```json\s*", "", content.strip())
        content = re.sub(r"^```\s*", "", content.strip())
        content = re.sub(r"\s*```\s*$", "", content.strip())

        # 2. 截取第一个 { 到最后一个 }
        first_brace = content.find("{")
        last_brace = content.rfind("}")
        if first_brace == -1 or last_brace == -1:
            raise ValueError("LLM 返回内容中未找到 JSON 对象")
        content = content[first_brace : last_brace + 1]

        # 3. 去掉尾逗号
        content = re.sub(r",(\s*[}\]])", r"\1", content)

        # 解析 JSON
        try:
            intent_data = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"无法解析 LLM 返回的 JSON: {e}\n内容: {content}") from e

        # 构造 Intent
        response_text = intent_data.get("response_text", text)
        emotion_str = intent_data.get("emotion", "neutral")
        try:
            emotion = EmotionType(emotion_str)
        except ValueError:
            emotion = EmotionType.NEUTRAL

        # 解析 actions
        actions = []
        for action_data in intent_data.get("actions", []):
            try:
                action_type_str = action_data.get("type", "none")
                try:
                    action_type = ActionType(action_type_str)
                except ValueError:
                    action_type = ActionType.NONE

                action = IntentAction(
                    type=action_type,
                    params=action_data.get("params", {}),
                    priority=action_data.get("priority", 50),
                )
                actions.append(action)
            except Exception as e:
                self.logger.warning(f"解析 action 失败: {e}, action_data: {action_data}")

        # 构造 SourceContext
        source_context = SourceContext(
            source=response.message_info.platform if response.message_info else "maicore",
            data_type="text",
            user_id=response.message_info.user_info.user_id
            if response.message_info and response.message_info.user_info
            else None,
            user_nickname=response.message_info.user_info.user_nickname
            if response.message_info and response.message_info.user_info
            else None,
        )

        return Intent(
            original_text=text,
            response_text=response_text,
            emotion=emotion,
            actions=actions,
            source_context=source_context,
            metadata={
                "llm_model": llm_result.model,
                "parser": "llm",
            },
        )

    async def _parse_intent_locally(self, normalized_message: "NormalizedMessage") -> Intent:
        """
        本地解析 Intent（不等待 MaiCore 响应）

        发送消息到 MaiCore 后，直接使用本地 LLM 或规则进行意图解析。

        Args:
            normalized_message: 标准化消息

        Returns:
            解析后的 Intent
        """
        text = normalized_message.text

        # 尝试使用 LLM 解析
        llm_service = self.context.llm_service
        if llm_service:
            try:
                # 创建假的 MessageBase 用于 LLM 解析
                from maim_message import BaseMessageInfo, MessageBase, Seg, UserInfo

                user_id = normalized_message.user_id or "unknown"
                nickname = normalized_message.metadata.get("user_nickname", normalized_message.source)
                user_info = UserInfo(platform=self.platform, user_id=user_id, user_nickname=nickname)

                message = MessageBase(
                    message_info=BaseMessageInfo(
                        message_id=normalized_message.message_id or f"local_{int(normalized_message.timestamp)}",
                        platform=self.platform,
                        user_info=user_info,
                        time=normalized_message.timestamp,
                    ),
                    message_segment=Seg(type="text", data=text),
                )
                return self._parse_intent_from_maicore_response(message)
            except Exception as e:
                self.logger.warning(f"LLM 解析失败，降级到规则解析: {e}")

        # 降级到规则解析
        from maim_message import BaseMessageInfo, MessageBase, Seg, UserInfo

        user_id = normalized_message.user_id or "unknown"
        nickname = normalized_message.metadata.get("user_nickname", normalized_message.source)
        user_info = UserInfo(platform=self.platform, user_id=user_id, user_nickname=nickname)

        message = MessageBase(
            message_info=BaseMessageInfo(
                message_id=normalized_message.message_id or f"local_{int(normalized_message.timestamp)}",
                platform=self.platform,
                user_info=user_info,
                time=normalized_message.timestamp,
            ),
            message_segment=Seg(type="text", data=text),
        )
        return self._parse_with_rules(text, message)

    def _parse_with_rules(self, text: str, response: MessageBase) -> Intent:
        """
        使用规则解析 Intent

        简单的关键词匹配，保证最坏情况下也能给出合理的 emotion 和基础动作。

        Args:
            text: 提取的文本
            response: MaiCore 返回的 MessageBase

        Returns:
            解析后的 Intent
        """
        # 情感关键词表
        emotion_keywords = {
            EmotionType.HAPPY: ["开心", "哈哈", "太棒了", "好玩", "高兴", "快乐", "欢乐", "喜"],
            EmotionType.SAD: ["难过", "伤心", "哎", "可惜", "遗憾", "悲伤"],
            EmotionType.ANGRY: ["生气", "愤怒", "讨厌", "烦", "气死"],
            EmotionType.SURPRISED: ["哇", "天啊", "竟然", "真的吗", "没想到"],
            EmotionType.LOVE: ["爱", "喜欢", "谢谢", "感谢", "支持"],
            EmotionType.SHY: ["害羞", "不好意思"],
            EmotionType.EXCITED: ["激动", "兴奋"],
        }

        # 检测情感
        emotion = EmotionType.NEUTRAL
        for emo, keywords in emotion_keywords.items():
            if any(keyword in text for keyword in keywords):
                emotion = emo
                break

        # 动作规则
        actions = []

        # 感谢 → clap + 表情
        if any(word in text for word in ["感谢", "谢谢", "多谢"]):
            actions.append(IntentAction(type=ActionType.CLAP, params={}, priority=60))
            actions.append(IntentAction(type=ActionType.EXPRESSION, params={"name": "thank"}, priority=70))

        # 打招呼 → wave
        if any(word in text for word in ["你好", "大家好", "嗨", "哈喽"]):
            actions.append(IntentAction(type=ActionType.WAVE, params={}, priority=60))

        # 同意 → nod
        if any(word in text for word in ["是的", "对", "嗯", "好的", "可以"]):
            actions.append(IntentAction(type=ActionType.NOD, params={}, priority=50))

        # 不同意 → shake
        if any(word in text for word in ["不", "不是", "不行"]):
            actions.append(IntentAction(type=ActionType.SHAKE, params={}, priority=50))

        # 如果没有任何动作，添加低优先级眨眼
        if not actions:
            actions.append(IntentAction(type=ActionType.BLINK, params={}, priority=30))

        # 构造 SourceContext
        source_context = SourceContext(
            source=response.message_info.platform if response.message_info else "maicore",
            data_type="text",
            user_id=response.message_info.user_info.user_id
            if response.message_info and response.message_info.user_info
            else None,
            user_nickname=response.message_info.user_info.user_nickname
            if response.message_info and response.message_info.user_info
            else None,
        )

        return Intent(
            original_text=text,
            response_text=text,
            emotion=emotion,
            actions=actions,
            source_context=source_context,
            metadata={"parser": "rule_based"},
        )

    async def _safe_send_suggestion(self, intent: Intent) -> None:
        """
        安全发送 Action 建议（捕获异常）

        异步发送 MaiBot Action 建议，任何错误都不会影响主流程。

        Args:
            intent: 包含建议动作的 Intent 对象
        """
        try:
            if not self._router_adapter:
                self.logger.warning("RouterAdapter 未初始化，无法发送 Action 建议")
                return

            await self._router_adapter.send_action_suggestion(intent)
            self.logger.debug(f"Action 发送成功: {len(intent.actions)} 个动作")
        except Exception as e:
            self.logger.warning(f"发送 Action 建议失败: {e}", exc_info=True)

    async def cleanup(self) -> None:
        """
        清理资源

        断开连接并清理所有资源。
        """
        self.logger.info("清理 MaiCoreDecisionProvider...")

        await self.disconnect()
        self.logger.info("MaiCoreDecisionProvider 已清理")

    @property
    def is_connected(self) -> bool:
        """获取连接状态"""
        return self._is_router_connected

    @property
    def _is_router_connected(self) -> bool:
        """检查 Router 是否已连接"""
        if self._router:
            return self._router.check_connection(self.platform)
        return False

    @property
    def router(self) -> Optional[Router]:
        """获取 Router 实例"""
        return self._router

    def get_info(self) -> Dict[str, Any]:
        """
        获取 Provider 信息

        Returns:
            Provider 信息字典
        """
        return {
            "name": "MaiCoreDecisionProvider",
            "version": "1.0.0",
            "host": self.host,
            "port": self.port,
            "platform": self.platform,
            "http_host": self.http_host,
            "http_port": self.http_port,
            "is_connected": self.is_connected,
        }

    def get_statistics(self) -> Dict[str, Any]:
        """
        获取运行时统计信息

        Returns:
            统计信息字典
        """
        return {
            "is_connected": self.is_connected,
            "router_running": self._router_task is not None and not self._router_task.done(),
        }
