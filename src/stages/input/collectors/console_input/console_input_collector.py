"""
ConsoleInputCollector - 控制台输入Collector

从控制台接收用户输入并直接构造 NormalizedMessage。
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, AsyncIterator, Dict, List, Literal, Optional

from pydantic import Field

from src.stages.input.registry import collector
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.types.base.normalized_message import NormalizedMessage


@collector("console_input")
class ConsoleInputCollector:
    """
    控制台输入Collector（重构后）

    从标准输入读取文本，支持命令处理(exit, gift, sc, guard)。
    直接构造 NormalizedMessage，无需中间数据结构。
    """

    class ConfigSchema(BaseConfig):
        """控制台输入Collector配置"""

        type: Literal["console_input"] = "console_input"
        user_id: str = Field(default="console_user", description="用户ID")
        user_nickname: str = Field(default="控制台", description="用户昵称")

    def __init__(self, config: Dict[str, Any], event_bus: EventBus):
        """
        初始化ConsoleInputCollector

        Args:
            config: 配置字典
            event_bus: 事件总线实例
        """
        self.config = config
        self.event_bus = event_bus
        self.logger = get_logger(self.__class__.__name__)

        # 使用 ConfigSchema 验证配置，获得类型安全的配置对象
        self.typed_config = self.ConfigSchema.from_dict(config)

        # 从类型安全的配置对象读取参数
        self.user_id = self.typed_config.user_id
        self.user_nickname = self.typed_config.user_nickname

        self.logger.info(f"ConsoleInputCollector初始化完成 (user: {self.user_nickname})")

        # 状态变量
        self.is_started: bool = False

    def stream(self) -> AsyncIterator[NormalizedMessage]:
        """
        返回 NormalizedMessage 数据流

        这是一个异步生成器，调用后会迭代 Collector 产生的数据。

        注意: 调用此方法前必须先调用 start() 启动 Collector。

        Yields:
            NormalizedMessage: 标准化消息

        Raises:
            RuntimeError: 如果 Collector 未启动
        """
        if not self.is_started:
            raise RuntimeError("Collector 未启动，请先调用 start()")

        async def _generate():
            try:
                async for message in self.collect():
                    yield message
            finally:
                self.is_started = False

        return _generate()

    async def start(self) -> None:
        """启动 Collector"""
        self.is_started = True

    async def stop(self) -> None:
        """停止 Collector"""
        self.is_started = False

    async def cleanup(self) -> None:
        """清理资源"""
        self.logger.info("ConsoleInputCollector cleanup完成")

    async def collect(self) -> AsyncIterator[NormalizedMessage]:
        """
        启动控制台输入，直接返回 NormalizedMessage 流

        支持的命令:
        - exit(): 退出
        - /gift [用户名] [礼物名] [数量]: 发送礼物消息
        - /sc [用户名] [内容]: 发送醒目留言
        - /guard [用户名] [等级]: 发送大航海消息

        Yields:
            NormalizedMessage: 标准化消息
        """
        self.is_started = True

        try:
            loop = asyncio.get_event_loop()
            self.logger.info("控制台输入已准备就绪。输入 'exit()' 来停止。")

            while self.is_started:
                try:
                    # 从标准输入读取
                    line = await loop.run_in_executor(None, sys.stdin.readline)
                    text = line.strip()

                    if not text:
                        continue

                    if text.lower() == "exit()":
                        self.logger.info("收到 'exit()' 命令，正在停止...")
                        break

                    # 检查是否为命令
                    if text.startswith("/"):
                        message = await self._handle_command(text)
                        if message:
                            yield message
                    else:
                        # 普通文本消息，直接构造 NormalizedMessage
                        yield NormalizedMessage(
                            text=text,
                            source="console",
                            data_type="text",
                            importance=0.5,
                            timestamp_ms=now_ms(),
                            raw=None,
                            user_id=self.user_id,
                            user_nickname=self.user_nickname,
                            platform="console",
                        )

                except asyncio.CancelledError:
                    self.logger.info("控制台输入循环被取消")
                    break
                except Exception as e:
                    self.logger.error(f"控制台输入循环出错: {e}", exc_info=True)
                    await asyncio.sleep(1)

            self.logger.info("控制台输入循环结束")

        finally:
            self.is_started = False

    async def _handle_command(self, cmd_line: str) -> Optional[NormalizedMessage]:
        """
        处理命令行输入

        Args:
            cmd_line: 命令行

        Returns:
            NormalizedMessage: 构造好的标准化消息，None 表示命令不需要生成数据
        """
        parts = cmd_line[1:].strip().split()
        if not parts:
            return None

        cmd_name = parts[0].lower()
        args = parts[1:]

        # 显示帮助
        if cmd_name == "help":
            help_text = """
可用的命令：
/help - 显示此帮助信息
/gift [用户名] [礼物名] [数量] - 发送虚假礼物消息
/sc [用户名] [内容...] - 发送虚假醒目留言
/guard [用户名] [等级] - 发送虚假大航海开通消息
            """
            print(help_text)
            return None

        # 礼物命令
        if cmd_name == "gift":
            return await self._create_gift_message(args)

        # 醒目留言命令
        if cmd_name == "sc":
            return await self._create_sc_message(args)

        # 大航海命令
        if cmd_name == "guard":
            return await self._create_guard_message(args)

        print(f"未知命令: {cmd_name}。输入 '/help' 查看可用命令。")
        return None

    async def _create_gift_message(self, args: List[str]) -> Optional[NormalizedMessage]:
        """创建礼物 NormalizedMessage"""
        username = args[0] if len(args) > 0 else "测试用户"
        gift_name = args[1] if len(args) > 1 else "辣条"
        gift_count = int(args[2]) if len(args) > 2 and args[2].isdigit() else 1

        if gift_count <= 0:
            print(f"礼物数量必须大于0，当前输入: {gift_count}")
            return None

        description = f"{username} 送出了 {gift_count} 个 {gift_name}"
        importance = min(0.3 + gift_count * 0.05, 1.0)

        print(f"发送礼物测试: {username} -> {gift_count}个{gift_name}")
        return NormalizedMessage(
            text=description,
            source="console",
            data_type="gift",
            importance=importance,
            timestamp_ms=now_ms(),
            raw=None,
            user_id=self.user_id,
            user_nickname=username,
            platform="console",
        )

    async def _create_sc_message(self, args: List[str]) -> Optional[NormalizedMessage]:
        """创建醒目留言 NormalizedMessage"""
        username = args[0] if len(args) > 0 else "SC大佬"
        content_text = " ".join(args[1:]) if len(args) > 1 else "这是一条测试醒目留言！"

        print(f"发送醒目留言测试: {username} - {content_text}")
        return NormalizedMessage(
            text=content_text,
            source="console",
            data_type="super_chat",
            importance=0.7,
            timestamp_ms=now_ms(),
            raw=None,
            user_id=self.user_id,
            user_nickname=username,
            platform="console",
        )

    async def _create_guard_message(self, args: List[str]) -> Optional[NormalizedMessage]:
        """创建大航海 NormalizedMessage"""
        username = args[0] if len(args) > 0 else "大航海"
        guard_level = args[1] if len(args) > 1 else "舰长"

        valid_levels = ["舰长", "提督", "总督"]
        if guard_level not in valid_levels:
            print(f"大航海等级必须是以下之一: {valid_levels}，当前输入: {guard_level}")
            return None

        description = f"{username} 开通了{guard_level}"
        importance_scores = {"总督": 1.0, "提督": 0.9, "舰长": 0.8}
        importance = importance_scores.get(guard_level, 0.8)

        print(f"发送大航海测试: {username} 开通了{guard_level}")
        return NormalizedMessage(
            text=description,
            source="console",
            data_type="guard",
            importance=importance,
            timestamp_ms=now_ms(),
            raw=None,
            user_id=self.user_id,
            user_nickname=username,
            platform="console",
        )
