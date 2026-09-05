"""
ConsoleInputCollector —— 控制台输入采集器（v2 / Wave 5 迁移）

迁移自 ``src/stages/input/collectors/console_input/console_input_collector.py``。
按 §1.46 + .omo/drafts/amaidesu-v2-migration.md §C：
- 继承 ``BaseCollector``（流型感知者）
- 支持命令：exit() / /gift / /sc / /guard / /help
- v2 主动推事件：start() 开后台任务读 stdin → emit room.message.* （volatile.v2）
- 保留 ``collect()`` AsyncIterator 出口兼容旧 InputCollectorManager

verbatim 边界：命令解析、NormalizedMessage 构造 —— 未改动。
"""

from __future__ import annotations

import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, AsyncIterator, Dict, List, Optional

from pydantic import Field

from src.modules.collectors.base import BaseCollector
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.types.base.normalized_message import NormalizedMessage

# 控制台输入循环异常后重试间隔（秒）
_ERROR_RETRY_INTERVAL_S = 1

# 控制台输入专用线程池：stdin 阻塞读独占线程，与默认线程池中的其他
# 阻塞任务（HTTP 同步调用、文件 IO 等）隔离——共享池线程被占满时，
# readline 任务会排队延迟调度，用户输入的行要多次回车才被消费。
_STDIN_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="console-stdin")

# data_type → (事件名, message_type, 是否需要 content)
_MESSAGE_TYPE_TO_EVENT = {
    "text": (CoreEvents.ROOM_MESSAGE_DANMAKU, "danmaku"),
    "gift": (CoreEvents.ROOM_MESSAGE_GIFT, "gift"),
    "super_chat": (CoreEvents.ROOM_MESSAGE_SUPER_CHAT, "super_chat"),
    "guard": (CoreEvents.ROOM_MESSAGE_ENTER, "enter"),
}


def _read_stdin_line_with_diag() -> str:
    """阻塞读一行 stdin（在专用线程池中执行）。"""
    return sys.stdin.readline()


class ConsoleInputCollector(BaseCollector):
    """
    控制台输入采集器

    从标准输入读取文本，支持命令处理(exit, gift, sc, guard)。
    直接构造 NormalizedMessage，无需中间数据结构。
    """

    name = "console_input"
    description = "从控制台读取用户输入，支持文本与测试命令（/gift / /sc / /guard）"

    class ConfigSchema(BaseConfig):
        """控制台输入采集器配置"""

        user_id: str = Field(default="console_user", description="用户ID")
        user_nickname: str = Field(default="控制台", description="用户昵称")

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        event_bus: Optional[EventBus] = None,
    ):
        super().__init__(event_bus=event_bus)
        self.config = config or {}
        self.logger = get_logger(self.__class__.__name__)

        self.typed_config = self.ConfigSchema.from_dict(self.config)
        self.user_id = self.typed_config.user_id
        self.user_nickname = self.typed_config.user_nickname

        self.logger.info(f"ConsoleInputCollector初始化完成 (user: {self.user_nickname})")

        self.is_started: bool = False
        # v2 主动推事件：后台输入循环任务
        self._input_task: Optional[asyncio.Task] = None
        self._input_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 旧 InputCollectorManager 兼容接口
    # ------------------------------------------------------------------

    def stream(self) -> AsyncIterator[NormalizedMessage]:
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
        """启动：开后台任务循环 stdin → emit room.message.*（v2 主动推）。"""
        async with self._input_lock:
            if self.is_started:
                return
            import os as _os

            if _os.environ.get("TERM_PROGRAM") == "vscode":
                self.logger.warning(
                    "检测到 VSCode 集成终端：其 shell integration 会向 stdin 注入命令流，"
                    "干扰控制台输入（丢字符/丢行）。建议改用 Windows Terminal 或独立 PowerShell 窗口运行。"
                )
            self.is_started = True
            self._input_task = asyncio.create_task(self._run_input_loop())
            self.logger.info("控制台输入后台循环已启动")

    async def stop(self) -> None:
        """停止：取消后台循环任务。"""
        async with self._input_lock:
            if not self.is_started:
                return
            self.is_started = False
            task = self._input_task
            self._input_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.logger.info("控制台输入循环已停止")

    async def cleanup(self) -> None:
        """清理资源"""
        await self.stop()
        self.logger.info("ConsoleInputCollector cleanup完成")

    # ------------------------------------------------------------------
    # v2 主动推事件：后台输入循环
    # ------------------------------------------------------------------

    async def _run_input_loop(self) -> None:
        """后台任务：实时读 stdin，构造 NormalizedMessage 并 emit 语义事件。"""
        loop = asyncio.get_event_loop()
        self.logger.info("控制台输入已准备就绪。输入 'exit()' 来停止。")

        while self.is_started:
            try:
                line = await loop.run_in_executor(_STDIN_EXECUTOR, _read_stdin_line_with_diag)
                text = line.strip()

                if not text:
                    continue

                if text.lower() == "exit()":
                    self.logger.info("收到 'exit()' 命令，正在停止...")
                    break

                if text.startswith("/"):
                    message = await self._handle_command(text)
                    if message:
                        await self._emit_semantic_event(message)
                else:
                    message = NormalizedMessage(
                        text=text,
                        source=self.name,
                        data_type="text",
                        importance=0.5,
                        timestamp_ms=now_ms(),
                        raw=None,
                        user_id=self.user_id,
                        user_nickname=self.user_nickname,
                        platform="console",
                    )
                    await self._emit_semantic_event(message)

            except asyncio.CancelledError:
                self.logger.info("控制台输入循环被取消")
                break
            except Exception as e:
                self.logger.error(f"控制台输入循环出错: {e}", exc_info=True)
                await asyncio.sleep(_ERROR_RETRY_INTERVAL_S)

        self.logger.info("控制台输入循环结束")

    async def _emit_semantic_event(self, normalized_msg: NormalizedMessage) -> None:
        """构造 RoomMessagePayload 并 emit 对应 room.message.* 事件（v2）。"""
        mapping = _MESSAGE_TYPE_TO_EVENT.get(normalized_msg.data_type)
        if mapping is None:
            self.logger.debug(f"未知 data_type '{normalized_msg.data_type}'，跳过 emit")
            return
        event_name, message_type = mapping

        payload = RoomMessagePayload(
            live_session_id="console",
            message_type=message_type,  # type: ignore[arg-type]
            user=RoomMessageUser(
                id=str(normalized_msg.user_id or "console_user"),
                name=str(normalized_msg.user_nickname or "控制台"),
            ),
            content=normalized_msg.text or "",
            timestamp_ms=normalized_msg.timestamp_ms,
        )
        await self.emit_event(event_name, payload)
        self.logger.info(f"已 emit {event_name} (content='{normalized_msg.text[:40]}')")

    # ------------------------------------------------------------------
    # 旧 collect() 生成器兼容（外部消费者不存在时保留接口）
    # ------------------------------------------------------------------

    async def collect(self) -> AsyncIterator[NormalizedMessage]:
        """
        启动控制台输入，直接返回 NormalizedMessage 流（旧兼容出口）

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
                    line = await loop.run_in_executor(_STDIN_EXECUTOR, _read_stdin_line_with_diag)
                    text = line.strip()

                    if not text:
                        continue

                    if text.lower() == "exit()":
                        self.logger.info("收到 'exit()' 命令，正在停止...")
                        break

                    if text.startswith("/"):
                        message = await self._handle_command(text)
                        if message:
                            yield message
                    else:
                        yield NormalizedMessage(
                            text=text,
                            source=self.name,
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
                    await asyncio.sleep(_ERROR_RETRY_INTERVAL_S)

            self.logger.info("控制台输入循环结束")

        finally:
            self.is_started = False

    async def _handle_command(self, cmd_line: str) -> Optional[NormalizedMessage]:
        parts = cmd_line[1:].strip().split()
        if not parts:
            return None

        cmd_name = parts[0].lower()
        args = parts[1:]

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

        if cmd_name == "gift":
            return await self._create_gift_message(args)

        if cmd_name == "sc":
            return await self._create_sc_message(args)

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
            source=self.name,
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
            source=self.name,
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
            source=self.name,
            data_type="guard",
            importance=importance,
            timestamp_ms=now_ms(),
            raw=None,
            user_id=self.user_id,
            user_nickname=username,
            platform="console",
        )
