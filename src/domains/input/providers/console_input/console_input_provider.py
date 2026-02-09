"""
ConsoleInputProvider - 控制台输入Provider

从控制台接收用户输入并生成RawData。
"""

import sys
import asyncio
from typing import AsyncIterator, Optional, List, Literal, Dict, Any

from pydantic import Field

from src.core.base.input_provider import InputProvider
from src.core.base.raw_data import RawData
from src.core.utils.logger import get_logger
from src.services.config.schemas.schemas.base import BaseProviderConfig


class ConsoleInputProvider(InputProvider):
    """
    控制台输入Provider

    从标准输入读取文本，支持命令处理(exit, gift, sc, guard)。
    """

    class ConfigSchema(BaseProviderConfig):
        """控制台输入Provider配置"""

        type: Literal["console_input"] = "console_input"
        user_id: str = Field(default="console_user", description="用户ID")
        user_nickname: str = Field(default="控制台", description="用户昵称")

    @classmethod
    def get_registration_info(cls) -> Dict[str, Any]:
        """
        获取 Provider 注册信息

        用于显式注册模式，避免模块导入时的自动注册。
        """
        return {"layer": "input", "name": "console_input", "class": cls, "source": "builtin:console_input"}

    def __init__(self, config: dict):
        """
        初始化ConsoleInputProvider

        Args:
            config: 配置字典
        """
        super().__init__(config)
        self.logger = get_logger("ConsoleInputProvider")

        # 使用 ConfigSchema 验证配置，获得类型安全的配置对象
        self.typed_config = self.ConfigSchema(**config)

        # 从类型安全的配置对象读取参数
        self.user_id = self.typed_config.user_id
        self.user_nickname = self.typed_config.user_nickname

        self.logger.info(f"ConsoleInputProvider初始化完成 (user: {self.user_nickname})")

    async def start(self) -> AsyncIterator[RawData]:
        """
        启动控制台输入，返回RawData流

        支持的命令:
        - exit(): 退出
        - /gift [用户名] [礼物名] [数量]: 发送礼物消息
        - /sc [用户名] [内容]: 发送醒目留言
        - /guard [用户名] [等级]: 发送大航海消息

        Yields:
            RawData: 原始数据
        """
        # 使用基类的 start() 方法，由 _collect_data() 实现数据采集逻辑
        async for data in super().start():
            yield data

    async def _collect_data(self) -> AsyncIterator[RawData]:
        """
        从标准输入采集数据

        Yields:
            RawData: 原始数据
        """
        loop = asyncio.get_event_loop()

        self.logger.info("控制台输入已准备就绪。输入 'exit()' 来停止。")

        while self.is_running:
            try:
                # 从标准输入读取
                line = await loop.run_in_executor(None, sys.stdin.readline)
                text = line.strip()

                if not text:
                    continue

                if text.lower() == "exit()":
                    self.logger.info("收到 'exit()' 命令，正在停止...")
                    # 设置 is_running 为 False，让循环退出
                    # 注意：父类的 start() 会在 finally 中再次设置，但这是安全的
                    break

                # 检查是否为命令
                if text.startswith("/"):
                    commands_data = await self._handle_command(text)
                    if commands_data is not None:
                        # 处理可能返回的多个数据（现在总是返回列表）
                        for data in commands_data:
                            yield data
                else:
                    # 创建普通文本数据
                    yield RawData(
                        content=text,
                        source="console",
                        data_type="text",
                        metadata={"user": self.user_nickname, "user_id": self.user_id},
                    )

            except asyncio.CancelledError:
                self.logger.info("控制台输入循环被取消")
                break
            except Exception as e:
                self.logger.error(f"控制台输入循环出错: {e}", exc_info=True)
                await asyncio.sleep(1)

        self.logger.info("控制台输入循环结束")

    async def stop(self):
        """停止输入"""
        # 使用基类的 stop() 方法
        await super().stop()

    async def cleanup(self):
        """清理资源"""
        # 调用父类的清理方法
        await super().cleanup()
        self.logger.info("ConsoleInputProvider cleanup完成")

    async def _handle_command(self, cmd_line: str) -> Optional[List[RawData]]:
        """
        处理命令行输入

        Args:
            cmd_line: 命令行

        Returns:
            RawData列表，None表示命令不需要生成数据
        """
        parts = cmd_line[1:].strip().split()
        if not parts:
            return None

        cmd_name = parts[0].lower()
        args = parts[1:]

        # 显示帮助
        if cmd_name == "help":
            help_text = """
可用命令：
/help - 显示此帮助信息
/gift [用户名] [礼物名] [数量] - 发送虚假礼物消息
/sc [用户名] [内容...] - 发送虚假醒目留言
/guard [用户名] [等级] - 发送虚假大航海开通消息
            """
            print(help_text)
            return None

        # 礼物命令
        elif cmd_name == "gift":
            return await self._create_gift_data(args)

        # 醒目留言命令
        elif cmd_name == "sc":
            return await self._create_sc_data(args)

        # 大航海命令
        elif cmd_name == "guard":
            return await self._create_guard_data(args)

        else:
            print(f"未知命令: {cmd_name}。输入 '/help' 查看可用命令。")
            return None

    async def _create_gift_data(self, args: List[str]) -> Optional[List[RawData]]:
        """创建礼物数据"""
        username = args[0] if len(args) > 0 else "测试用户"
        gift_name = args[1] if len(args) > 1 else "辣条"
        gift_count = int(args[2]) if len(args) > 2 and args[2].isdigit() else 1

        if gift_count <= 0:
            print(f"礼物数量必须大于0，当前输入: {gift_count}")
            return None

        data_list = []
        for i in range(gift_count):
            data = RawData(
                content={"type": "gift", "gift_name": gift_name, "count": 1, "user": username},
                source="console",
                data_type="gift",
                metadata={"user": username, "gift_name": gift_name, "count": 1, "index": i},
            )
            data_list.append(data)

        print(f"发送礼物测试: {username} -> {gift_count}个{gift_name}")
        return data_list

    async def _create_sc_data(self, args: List[str]) -> Optional[RawData]:
        """创建醒目留言数据"""
        username = args[0] if len(args) > 0 else "SC大佬"
        content = " ".join(args[1:]) if len(args) > 1 else "这是一条测试醒目留言！"

        data = RawData(
            content={"type": "superchat", "user": username, "content": content},
            source="console",
            data_type="superchat",
            metadata={"user": username, "content": content},
        )

        print(f"发送醒目留言测试: {username} - {content}")
        return [data]

    async def _create_guard_data(self, args: List[str]) -> Optional[List[RawData]]:
        """创建大航海数据"""
        username = args[0] if len(args) > 0 else "大航海"
        guard_level = args[1] if len(args) > 1 else "舰长"

        valid_levels = ["舰长", "提督", "总督"]
        if guard_level not in valid_levels:
            print(f"大航海等级必须是以下之一: {valid_levels}，当前输入: {guard_level}")
            return None

        data = RawData(
            content={"type": "guard", "user": username, "level": guard_level},
            source="console",
            data_type="guard",
            metadata={"user": username, "level": guard_level},
        )

        print(f"发送大航海测试: {username} 开通了{guard_level}")
        return [data]
