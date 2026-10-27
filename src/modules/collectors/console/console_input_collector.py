"""
ConsoleInputCollector —— 控制台输入采集器

- 继承 ``BaseCollector``（流型感知者）
- 支持命令：exit() / /gift / /sc / /guard / /help
- 主动推事件：start() 开后台任务读 stdin → emit room.message.*（volatile）
"""

from __future__ import annotations

import asyncio
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, AsyncIterator, Dict, List, Optional

from pydantic import Field

from src.modules.collectors.base import BaseCollector
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.payloads.room import (
    GiftInfo,
    GuardInfo,
    RoomMessagePayload,
    RoomMessageUser,
    SuperChatInfo,
)
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms
from src.modules.types.guard_levels import GUARD_LEVEL_NAMES

# 控制台输入循环异常后重试间隔（秒）
_ERROR_RETRY_INTERVAL_S = 1

# 控制台输入专用线程池：stdin 阻塞读独占线程，与默认线程池中的其他
# 阻塞任务（HTTP 同步调用、文件 IO 等）隔离——共享池线程被占满时，
# readline 任务会排队延迟调度，用户输入的行要多次回车才被消费。
_STDIN_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="console-stdin")


def _read_stdin_line_with_diag() -> str:
    """阻塞读一行 stdin（在专用线程池中执行）。"""
    return sys.stdin.readline()


def _stdin_interactive() -> bool:
    """stdin 是否为可交互终端。

    重定向/管道场景下守护进程化的运行方式会让宿主 shell 的命令文本残留在
    stdin 缓冲里被当观众弹幕发出去（历史上出现过 PowerShell 激活命令落库），
    EOF 后 readline() 恒返空串还会导致忙转——非交互 stdin 一律不启动输入循环。
    """
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        return False


# 本采集器的平台标识（身份键组成部分，装配期常量）：console 是调试保留字，
# 与真实平台数据在 (platform, user_id) 身份键上隔离，避免测试用户污染真实观众
_PLATFORM = "console"

# 调试命令的舰名 → 等级映射（B 站语义：1 总督 / 2 提督 / 3 舰长）
_GUARD_NAME_TO_LEVEL: dict[str, int] = {name: level for level, name in GUARD_LEVEL_NAMES.items()}


class ConsoleInputCollector(BaseCollector):
    """
    控制台输入采集器

    从标准输入读取文本，支持命令处理(exit, gift, sc, guard)。
    直接构造 RoomMessagePayload，无需中间数据结构。
    """

    name = "console_input"
    description = "从控制台读取用户输入，支持文本与测试命令（/gift / /sc / /guard）"

    class ConfigSchema(BaseConfig):
        """控制台输入采集器配置"""

        user_id: str = Field(default="console_user", title="用户 ID", description="控制台消息展示用的用户 ID")
        user_nickname: str = Field(default="控制台", title="用户昵称", description="控制台消息展示用的昵称")

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        super().__init__(event_bus=event_bus)
        self.config = config or {}
        self.logger = get_logger(self.__class__.__name__)

        self.typed_config = self.ConfigSchema.from_dict(self.config)
        self.user_id = self.typed_config.user_id
        self.user_nickname = self.typed_config.user_nickname

        self.logger.info(f"ConsoleInputCollector初始化完成 (user: {self.user_nickname})")

    async def _on_start(self) -> None:
        """环境检查通过后开后台任务循环 stdin → emit room.message.*（主动推）。"""
        if not _stdin_interactive():
            self.logger.warning(
                "stdin 不是交互终端（重定向/后台运行），控制台输入不启动；手动注入弹幕请改用 WebUI 直播控制台的输入条"
            )
            raise RuntimeError("stdin 不是交互终端，控制台输入采集器拒绝启动")
        if os.environ.get("TERM_PROGRAM") == "vscode":
            self.logger.warning(
                "检测到 VSCode 集成终端：其 shell integration 会向 stdin 注入命令流，"
                "干扰控制台输入（丢字符/丢行）。建议改用 Windows Terminal 或独立 PowerShell 窗口运行。"
            )
        await self._start_collect_task()

    async def _on_stop(self) -> None:
        """取消后台输入循环任务。"""
        await self._stop_collect_task()

    async def cleanup(self) -> None:
        """清理资源"""
        await self.stop()
        self.logger.info("ConsoleInputCollector cleanup完成")

    # ------------------------------------------------------------------
    # 主动推事件：采集循环（基类后台任务驱动唯一的 collect()）
    # ------------------------------------------------------------------

    async def collect(self) -> AsyncIterator[RoomMessagePayload]:
        """实时读 stdin，构造 RoomMessagePayload 并 emit 语义事件。

        支持的命令:
        - exit(): 退出
        - /gift [用户名] [礼物名] [数量]: 发送礼物消息
        - /sc [用户名] [内容]: 发送醒目留言
        - /guard [用户名] [等级]: 发送大航海消息

        Yields:
            RoomMessagePayload: 直播间行为流事件载荷
        """
        loop = asyncio.get_event_loop()
        self.logger.info("控制台输入已准备就绪。输入 'exit()' 来停止。")

        # 循环由取消驱动退出（stop 取消后台任务）；stdin EOF 时生成器自然结束，
        # 基类消费循环随之把运行态收束为 STOPPED
        while True:
            try:
                line = await loop.run_in_executor(_STDIN_EXECUTOR, _read_stdin_line_with_diag)
                text = line.strip()

                if not text:
                    if not line:
                        # readline 返回空串即 EOF（终端关闭/管道结束）：退出而非忙转
                        self.logger.info("stdin 已关闭（EOF），控制台输入循环退出")
                        break
                    continue

                if text.lower() == "exit()":
                    self.logger.info("收到 'exit()' 命令，正在停止...")
                    break

                if text.startswith("/"):
                    message = await self._handle_command(text)
                    if message:
                        await self.publish_room_message(message)
                        yield message
                else:
                    message = RoomMessagePayload(
                        message_type="danmaku",
                        platform=_PLATFORM,
                        user=RoomMessageUser(id=self.user_id, name=self.user_nickname),
                        content=text,
                        timestamp_ms=now_ms(),
                    )
                    await self.publish_room_message(message)
                    yield message

            except asyncio.CancelledError:
                self.logger.info("控制台输入循环被取消")
                break
            except Exception as e:
                self.logger.exception(f"控制台输入循环出错: {e}")
                await asyncio.sleep(_ERROR_RETRY_INTERVAL_S)

        self.logger.info("控制台输入循环结束")

    async def _handle_command(self, cmd_line: str) -> Optional[RoomMessagePayload]:
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

    async def _create_gift_message(self, args: List[str]) -> Optional[RoomMessagePayload]:
        """创建礼物事件载荷（调试数据无真实金额，金额记 0、币种留空——不进付费统计）"""
        username = args[0] if len(args) > 0 else "测试用户"
        gift_name = args[1] if len(args) > 1 else "辣条"
        gift_count = int(args[2]) if len(args) > 2 and args[2].isdigit() else 1

        if gift_count <= 0:
            print(f"礼物数量必须大于0，当前输入: {gift_count}")
            return None

        description = f"{username} 送出了 {gift_count} 个 {gift_name}"

        print(f"发送礼物测试: {username} -> {gift_count}个{gift_name}")
        return RoomMessagePayload(
            message_type="gift",
            platform=_PLATFORM,
            user=RoomMessageUser(id=self.user_id, name=username),
            content=description,
            gift=GiftInfo(name=gift_name, count=gift_count),
            timestamp_ms=now_ms(),
        )

    async def _create_sc_message(self, args: List[str]) -> Optional[RoomMessagePayload]:
        """创建醒目留言事件载荷（调试数据无真实金额，金额记 0、币种留空）"""
        username = args[0] if len(args) > 0 else "SC大佬"
        content_text = " ".join(args[1:]) if len(args) > 1 else "这是一条测试醒目留言！"

        print(f"发送醒目留言测试: {username} - {content_text}")
        return RoomMessagePayload(
            message_type="super_chat",
            platform=_PLATFORM,
            user=RoomMessageUser(id=self.user_id, name=username),
            content=content_text,
            sc=SuperChatInfo(total_price=0, currency=""),
            timestamp_ms=now_ms(),
        )

    async def _create_guard_message(self, args: List[str]) -> Optional[RoomMessagePayload]:
        """创建大航海开通事件载荷（上舰语义；调试数据无真实金额）"""
        username = args[0] if len(args) > 0 else "大航海"
        guard_level_name = args[1] if len(args) > 1 else "舰长"

        if guard_level_name not in _GUARD_NAME_TO_LEVEL:
            valid_levels = list(GUARD_LEVEL_NAMES.values())
            print(f"大航海等级必须是以下之一: {valid_levels}，当前输入: {guard_level_name}")
            return None

        description = f"{username} 开通了{guard_level_name}"

        print(f"发送大航海测试: {username} 开通了{guard_level_name}")
        return RoomMessagePayload(
            message_type="guard",
            platform=_PLATFORM,
            user=RoomMessageUser(id=self.user_id, name=username),
            content=description,
            guard=GuardInfo(guard_level=_GUARD_NAME_TO_LEVEL[guard_level_name], currency=""),
            timestamp_ms=now_ms(),
        )
