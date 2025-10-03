import asyncio
import math
from typing import Tuple

from src.chat.message_receive.message import MessageRecv, MessageRecvS4U
from maim_message.message_base import GroupInfo
from src.mais4u.database.message_storage import MessageStorage
from typing import Any
from src.utils.logger import get_logger
from src.mais4u.mais4u_chat.body_emotion_action_manager import action_manager
from src.mais4u.mais4u_chat.s4u_mood_manager import mood_manager
from src.mais4u.mais4u_chat.s4u_watching_manager import watching_manager
from src.mais4u.mais4u_chat.context_web_manager import get_context_web_manager
from src.mais4u.mais4u_chat.gift_manager import gift_manager

from .s4u_chat import get_s4u_chat_manager

logger = get_logger("chat")


class S4UMessageProcessor:
    """心流处理器，负责处理接收到的消息并计算兴趣度"""

    def __init__(self):
        """初始化心流处理器，创建消息存储实例"""
        self.storage = MessageStorage()
        # 单流模式：缓存唯一的 chat_stream 与 s4u_chat，简化多流管理
        self._chat_stream = None
        self._s4u_chat = None

    async def process_message(self, message: MessageRecvS4U, skip_gift_debounce: bool = False) -> None:
        """处理接收到的原始消息数据

        主要流程:
        1. 消息解析与初始化
        2. 消息缓冲处理
        3. 过滤检查
        4. 兴趣度计算
        5. 关系处理

        Args:
            message_data: 原始消息字符串
        """

        # 1. 消息解析与初始化
        groupinfo = message.message_info.group_info
        userinfo = message.message_info.user_info
        message_info = message.message_info

        # 单流模式：仅在首次初始化 chat_stream，之后复用
        if self._chat_stream is None:
            # 单流模式：构造一个最小可用的 ChatStream 替身对象
            class _SingleChatStream:
                def __init__(self, platform: str, user_info: Any, group_info: Any):
                    self.platform = platform
                    self.user_info = user_info
                    self.group_info = group_info
                    # 以平台+用户为stream_id，确保稳定唯一
                    self.stream_id = f"{platform}:{getattr(user_info, 'user_id', '0')}"

            self._chat_stream = _SingleChatStream(
                platform=message_info.platform,
                user_info=userinfo,
                group_info=groupinfo,
            )
        chat = self._chat_stream

        if await self.hadle_if_voice_done(message):
            return

        # 处理礼物消息，如果消息被暂存则停止当前处理流程
        if not skip_gift_debounce and not await self.handle_if_gift(message):
            return
        await self.check_if_fake_gift(message)

        await self.storage.store_message(message, chat)

        # 单流模式：仅在首次创建 S4UChat，之后复用
        if self._s4u_chat is None:
            self._s4u_chat = get_s4u_chat_manager().get_or_create_chat(chat)
        s4u_chat = self._s4u_chat

        await s4u_chat.add_message(message)

        await mood_manager.start()

        # 一系列llm驱动的前处理
        chat_mood = mood_manager.get_mood_by_chat_id(chat.stream_id)
        asyncio.create_task(chat_mood.update_mood_by_message(message))
        chat_action = action_manager.get_action_state_by_chat_id(chat.stream_id)
        asyncio.create_task(chat_action.update_action_by_message(message))
        # 视线管理：收到消息时切换视线状态
        chat_watching = watching_manager.get_watching_by_chat_id(chat.stream_id)
        await chat_watching.on_message_received()

        # 上下文网页管理：启动独立task处理消息上下文
        asyncio.create_task(self._handle_context_web_update(chat.stream_id, message))

        # 日志记录
        if message.is_gift:
            logger.info(f"[S4U-礼物] {userinfo.user_nickname} 送出了 {message.gift_name} x{message.gift_count}")
        else:
            logger.info(f"[S4U]{userinfo.user_nickname}:{message.processed_plain_text}")

    async def hadle_if_voice_done(self, message: MessageRecvS4U):
        if message.voice_done:
            s4u_chat = get_s4u_chat_manager().get_or_create_chat(message.chat_stream)
            s4u_chat.voice_done = message.voice_done
            return True
        return False

    async def check_if_fake_gift(self, message: MessageRecvS4U) -> bool:
        """检查消息是否为假礼物"""
        if message.is_gift:
            return False

        gift_keywords = ["送出了礼物", "礼物", "送出了", "投喂"]
        if any(keyword in message.processed_plain_text for keyword in gift_keywords):
            message.is_fake_gift = True
            return True

        return False

    async def handle_if_gift(self, message: MessageRecvS4U) -> bool:
        """处理礼物消息

        Returns:
            bool: True表示应该继续处理消息，False表示消息已被暂存不需要继续处理
        """
        if message.is_gift:
            # 定义防抖完成后的回调函数
            def gift_callback(merged_message: MessageRecvS4U):
                """礼物防抖完成后的回调"""
                # 创建异步任务来处理合并后的礼物消息，跳过防抖处理
                asyncio.create_task(self.process_message(merged_message, skip_gift_debounce=True))

            # 交给礼物管理器处理，并传入回调函数
            # 对于礼物消息，handle_gift 总是返回 False（消息被暂存）
            await gift_manager.handle_gift(message, gift_callback)
            return False  # 消息被暂存，不继续处理

        return True  # 非礼物消息，继续正常处理

    async def _handle_context_web_update(self, chat_id: str, message: MessageRecv):
        """处理上下文网页更新的独立task

        Args:
            chat_id: 聊天ID
            message: 消息对象
        """
        try:
            logger.debug(f"🔄 开始处理上下文网页更新: {message.message_info.user_info.user_nickname}")

            context_manager = get_context_web_manager()

            # 只在服务器未启动时启动（避免重复启动）
            if context_manager.site is None:
                logger.info("🚀 首次启动上下文网页服务器...")
                await context_manager.start_server()

            # 添加消息到上下文并更新网页
            await asyncio.sleep(1.5)

            await context_manager.add_message(chat_id, message)

            logger.debug(f"✅ 上下文网页更新完成: {message.message_info.user_info.user_nickname}")

        except Exception as e:
            logger.error(f"❌ 处理上下文网页更新失败: {e}", exc_info=True)
