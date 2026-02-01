import asyncio
import sys
import time
from typing import Dict, Any, Optional, List, Union
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.event_bus import EventBus

# --- Dependency Check & TOML ---
try:
    import tomllib
except ModuleNotFoundError:
    try:
        import toml as tomllib
    except ImportError:
        print("依赖缺失: 请运行 'pip install toml' 来加载 Console Input 插件配置。", file=sys.stderr)
        tomllib = None

from src.core.data_types.raw_data import RawData
from maim_message import MessageBase, BaseMessageInfo, UserInfo, GroupInfo, Seg, FormatInfo, TemplateInfo
from src.utils.logger import get_logger


class ConsoleInputPlugin:
    """
    通过控制台接收用户输入并发送消息的插件

    迁移到新的Plugin接口：
    - 不继承BasePlugin
    - 实现Plugin协议
    - 通过event_bus和config进行依赖注入
    - 返回Provider列表（此插件不返回Provider，返回空列表）
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.enabled = True
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info(f"初始化插件: {self.__class__.__name__}")

        self.event_bus: Optional["EventBus"] = None
        self.platform = config.get("platform", "amaidesu")

        # --- Dependency Check ---
        if tomllib is None:
            self.logger.error("缺少 TOML 依赖，Console Input 插件禁用。")
            self.enabled = False
            return

        # --- Load Message Config Defaults from plugin's config.toml ---
        self.message_config = self.config.get("message_config", {})
        if not self.message_config:
            self.logger.warning("在 console_input/config.toml 中未找到 [message_config] 配置段，将使用硬编码默认值。")
            self.message_config = {
                "user_id": "console_user_fallback",
                "user_nickname": "控制台",
                "user_cardname": "Fallback",
                "enable_group_info": False,
                "group_id": 0,
                "group_name": "default",
                "content_format": ["text"],
                "accept_format": ["text"],
                "enable_template_info": False,
                "template_name": "default",
                "template_default": False,
                "additional_config": {},
            }
        else:
            self.logger.info("已加载来自 console_input/config.toml 的 [message_config]。")

        # --- Prompt Context Tags ---
        self.context_tags: Optional[List[str]] = self.message_config.get("context_tags")
        if not isinstance(self.context_tags, list):
            if self.context_tags is not None:
                self.logger.warning(
                    f"Config 'context_tags' in [message_config] is not a list ({type(self.context_tags)}), will fetch all context."
                )
            self.context_tags = None
        elif not self.context_tags:
            self.logger.info("'context_tags' in [message_config] is empty, will fetch all context.")
            self.context_tags = None
        else:
            self.logger.info(f"Will fetch context with tags: {self.context_tags}")

        # --- Load Template Items Separately (if enabled and exists within config) ---
        self.template_items = None
        if self.message_config.get("enable_template_info", False):
            self.template_items = self.message_config.get("template_items", {})
            if not self.template_items:
                self.logger.warning("配置启用了 template_info，但在 message_config 中未找到 template_items。")

        self._input_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    async def setup(self, event_bus: "EventBus", config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        此插件不返回Provider列表，因为它是输入处理插件，直接通过event_bus发送消息

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            空列表（此插件不返回Provider）
        """
        self.event_bus = event_bus
        self.logger.info("启动控制台输入监听任务...")

        if not self.enabled:
            self.logger.warning("Console Input 插件未启用，不启动监听任务。")
            return []

        self._stop_event.clear()
        self._input_task = asyncio.create_task(self._input_loop(), name="ConsoleInputLoop")

        return []

    async def cleanup(self):
        """停止控制台输入任务。"""
        self.logger.info("请求停止 Console Input 插件...")
        self._stop_event.set()

        if self._input_task and not self._input_task.done():
            self.logger.info("正在等待控制台输入任务结束 (最多 2 秒)...")
            try:
                await asyncio.wait_for(self._input_task, timeout=2.0)
            except asyncio.TimeoutError:
                self.logger.warning("控制台输入任务在超时后仍未结束，将强制取消。")
                self._input_task.cancel()
            except asyncio.CancelledError:
                self.logger.info("控制台输入任务已被取消。")
            except Exception as e:
                self.logger.error(f"等待控制台输入任务结束时出错: {e}", exc_info=True)

        self.logger.info("Console Input 插件清理完成。")

    async def _input_loop(self):
        """异步循环以读取控制台输入。"""
        self.logger.info("控制台输入已准备就绪。输入 'exit()' 来停止，'/help' 查看可用命令。")
        loop = asyncio.get_event_loop()
        while not self._stop_event.is_set():
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                text = line.strip()

                if not text:
                    continue  # Ignore empty lines
                if text.lower() == "exit()":
                    self.logger.info("收到 'exit()' 命令，正在停止...")
                    self._stop_event.set()
                    break
                if self._stop_event.is_set():  # Check again after potential blocking read
                    break

                if self.event_bus:
                    # 创建RawData并发送标准事件
                    raw_data = RawData(
                        content=text,
                        source="console",
                        data_type="text",
                        metadata={
                            "user_id": self.message_config.get("user_id", "console_user"),
                            "user_nickname": self.message_config.get("user_nickname", "控制台"),
                        },
                    )
                    await self.event_bus.emit(
                        "perception.raw_data.generated",
                        {"data": raw_data, "source": "console"},
                        source="ConsoleInputPlugin",
                    )

            except asyncio.CancelledError:
                self.logger.info("控制台输入循环被取消。")
                break
            except Exception as e:
                self.logger.error(f"控制台输入循环出错: {e}", exc_info=True)
                await asyncio.sleep(1)
        self.logger.info("控制台输入循环结束。")

    async def _handle_command(self, cmd_line: str) -> Optional[Union[MessageBase, List[MessageBase]]]:
        """处理命令行输入"""
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
/gift [用户名] [礼物名] [数量] - 发送虚假礼物消息（数量>1时会发送多次独立消息）
/sc [用户名] [内容...] - 发送虚假醒目留言
/guard [用户名] [等级] - 发送虚假大航海开通消息

示例：
/gift 大佬观众 火箭 1     # 发送1次火箭
/gift 土豪观众 辣条 5     # 连续发送5次辣条（独立消息）
/sc SC大佬 这是一条测试醒目留言！
/guard 舰长大人 舰长
            """
            print(help_text)
            return None

        # 礼物命令
        elif cmd_name == "gift":
            return await self._create_gift_message(args)

        # 醒目留言命令
        elif cmd_name == "sc":
            return await self._create_sc_message(args)

        # 大航海命令
        elif cmd_name == "guard":
            return await self._create_guard_message(args)

        else:
            print(f"未知命令: {cmd_name}。输入 '/help' 查看可用命令。")
            return None

    async def _create_gift_message(self, args: List[str]) -> Optional[Union[MessageBase, List[MessageBase]]]:
        """创建礼物消息"""
        # 默认参数
        username = args[0] if len(args) > 0 else "测试用户"
        gift_name = args[1] if len(args) > 1 else "辣条"
        gift_count = args[2] if len(args) > 2 else "1"

        try:
            count = int(gift_count)
        except ValueError:
            print(f"礼物数量必须是数字，当前输入: {gift_count}")
            return None

        if count <= 0:
            print(f"礼物数量必须大于0，当前输入: {count}")
            return None

        messages = []
        base_timestamp = time.time()
        user_id = f"test_gift_{hash(username) % 10000}"

        print(f"💝 发送礼物测试: {username} -> {count}个{gift_name} (将发送{count}次)")

        # 创建指定数量的独立礼物消息
        for i in range(count):
            timestamp = base_timestamp + i * 0.01  # 每个消息间隔0.1秒的时间戳
            message_id = f"test_gift_{int(timestamp * 1000)}_{i}"

            # User Info
            user_info = UserInfo(
                platform=self.platform, user_id=user_id, user_nickname=username, user_cardname=username
            )

            # Group Info (可选)
            group_info: Optional[GroupInfo] = None
            if self.message_config.get("enable_group_info", False):
                group_info = GroupInfo(
                    platform=self.platform,
                    group_id=self.message_config.get("group_id", 0),
                    group_name=self.message_config.get("group_name", "default"),
                )

            # Format Info
            format_info = FormatInfo(content_format=["text", "gift"], accept_format=["text", "gift"])

            # Additional Config
            additional_config = {"source": "console_gift_test", "sender_name": username}

            # Message Info
            message_info = BaseMessageInfo(
                platform=self.platform,
                message_id=message_id,
                time=timestamp,
                user_info=user_info,
                group_info=group_info,
                template_info=None,
                format_info=format_info,
                additional_config=additional_config,
            )

            # Message Segment - 单个礼物消息
            message_segment = Seg(
                "seglist",
                [
                    Seg(type="gift", data=f"{gift_name}:1"),  # 每次都是1个
                    Seg("priority_info", {"message_type": "vip", "priority": 1}),
                ],
            )

            raw_message = f"{username} 送出了 1 个 {gift_name}"

            message = MessageBase(message_info=message_info, message_segment=message_segment, raw_message=raw_message)
            messages.append(message)

        # 如果只有一个礼物，返回单个消息；否则返回消息列表
        return messages[0] if count == 1 else messages

    async def _create_sc_message(self, args: List[str]) -> Optional[MessageBase]:
        """创建醒目留言消息"""
        # 默认参数
        username = args[0] if len(args) > 0 else "SC大佬"
        content = " ".join(args[1:]) if len(args) > 1 else "这是一条测试醒目留言！"

        data = f"{args[2]}:{args[1]}"

        timestamp = time.time()
        user_id = f"test_sc_{hash(username) % 10000}"
        message_id = f"test_sc_{int(timestamp * 1000)}"

        # User Info
        user_info = UserInfo(platform=self.platform, user_id=user_id, user_nickname=username, user_cardname=username)

        # Group Info (可选)
        group_info: Optional[GroupInfo] = None
        if self.message_config.get("enable_group_info", False):
            group_info = GroupInfo(
                platform=self.platform,
                group_id=self.message_config.get("group_id", 0),
                group_name=self.message_config.get("group_name", "default"),
            )

        # Format Info
        format_info = FormatInfo(content_format=["text"], accept_format=["text"])

        # Additional Config
        additional_config = {"source": "console_sc_test", "sender_name": username}

        # Message Info
        message_info = BaseMessageInfo(
            platform=self.platform,
            message_id=message_id,
            time=timestamp,
            user_info=user_info,
            group_info=group_info,
            template_info=None,
            format_info=format_info,
            additional_config=additional_config,
        )

        # Message Segment - 醒目留言
        message_segment = Seg(
            "seglist",
            [Seg(type="superchat", data=data), Seg("priority_info", {"message_type": "super_vip", "priority": 2})],
        )

        raw_message = f"{username} 发送了醒目留言：{content}"
        print(f"⭐ 发送醒目留言测试: {raw_message}")

        return MessageBase(message_info=message_info, message_segment=message_segment, raw_message=raw_message)

    async def _create_guard_message(self, args: List[str]) -> Optional[MessageBase]:
        """创建大航海开通消息"""
        # 默认参数
        username = args[0] if len(args) > 0 else "大航海"
        guard_level = args[1] if len(args) > 1 else "舰长"

        # 验证大航海等级
        valid_levels = ["舰长", "提督", "总督"]
        if guard_level not in valid_levels:
            print(f"大航海等级必须是以下之一: {valid_levels}，当前输入: {guard_level}")
            return None

        timestamp = time.time()
        user_id = f"test_guard_{hash(username) % 10000}"
        message_id = f"test_guard_{int(timestamp * 1000)}"

        # User Info
        user_info = UserInfo(platform=self.platform, user_id=user_id, user_nickname=username, user_cardname=username)

        # Group Info (可选)
        group_info: Optional[GroupInfo] = None
        if self.message_config.get("enable_group_info", False):
            group_info = GroupInfo(
                platform=self.platform,
                group_id=self.message_config.get("group_id", 0),
                group_name=self.message_config.get("group_name", "default"),
            )

        # Format Info
        format_info = FormatInfo(content_format=["text"], accept_format=["text"])

        # Additional Config
        additional_config = {"source": "console_guard_test", "sender_name": username}

        # Message Info
        message_info = BaseMessageInfo(
            platform=self.platform,
            message_id=message_id,
            time=timestamp,
            user_info=user_info,
            group_info=group_info,
            template_info=None,
            format_info=format_info,
            additional_config=additional_config,
        )

        # Message Segment - 大航海
        message_segment = Seg(
            "seglist",
            [
                Seg(type="text", data=f"开通了{guard_level}"),
                Seg("priority_info", {"message_type": "super_vip", "priority": 3}),
            ],
        )

        raw_message = f"{username} 开通了{guard_level}"
        print(f"⚓ 发送大航海测试: {raw_message}")

        return MessageBase(message_info=message_info, message_segment=message_segment, raw_message=raw_message)

    async def _create_console_message(self, text: str) -> MessageBase:
        """使用从 config.toml 加载的配置创建 MessageBase 对象。"""
        timestamp = time.time()
        cfg = self.message_config  # Use the loaded message config

        # --- User Info ---
        user_id_from_config = cfg.get("user_id", 0)  # Assume int from config, default to 0
        user_info = UserInfo(
            # platform=cfg.get("platform", "qq"),
            platform=self.platform,
            user_id=user_id_from_config,
            user_nickname=cfg.get("user_nickname", "ConsoleUser"),
            user_cardname=cfg.get("user_cardname", ""),
        )

        # --- Group Info (Conditional) ---
        group_info: Optional[GroupInfo] = None
        if cfg.get("enable_group_info", False):
            group_info = GroupInfo(
                platform=self.platform,
                group_id=cfg.get("group_id", 0),
                group_name=cfg.get("group_name", "default"),
            )

        # --- Format Info ---
        format_info = FormatInfo(
            content_format=cfg.get("content_format", ["text"]), accept_format=cfg.get("accept_format", ["text"])
        )

        # --- Template Info (Conditional & Modification) ---
        final_template_info_value = None
        if cfg.get("enable_template_info", False) and self.template_items:
            # 1. 获取原始模板项 (创建副本)
            modified_template_items = (self.template_items or {}).copy()

            # 4. 使用修改后的模板项构建最终结构
            final_template_info_value = TemplateInfo(
                template_name=cfg.get("template_name", "default"),
                template_items=modified_template_items,
                template_default=cfg.get("template_default", True),
            )
        # else: # 不需要模板或模板项为空时，final_template_info_value 保持 None

        # --- Additional Config ---
        additional_config = cfg.get("additional_config", {})
        additional_config["source"] = "console_input_plugin"
        additional_config["sender_name"] = user_info.user_nickname
        additional_config["maimcore_reply_probability_gain"] = 1

        # --- Base Message Info ---
        message_info = BaseMessageInfo(
            platform=self.platform,
            # Consider casting time to int for consistency, but optional for now
            message_id=f"console_{int(timestamp * 1000)}_{hash(text) % 10000}",
            time=timestamp,
            user_info=user_info,
            group_info=group_info,
            # 使用可能已修改的 template_info
            template_info=final_template_info_value,
            format_info=format_info,
            additional_config=additional_config,
        )

        # --- Message Segment ---
        # Segment type is usually fixed for console input
        message_segment = Seg(type="text", data=text)

        # --- Final MessageBase ---
        return MessageBase(message_info=message_info, message_segment=message_segment, raw_message=text)


def get_info(self) -> Dict[str, Any]:
    """获取插件信息"""
    return {
        "name": "ConsoleInput",
        "version": "1.0.0",
        "author": "Amaidesu Team",
        "description": "通过控制台接收用户输入并发送消息的插件",
        "category": "input",
        "api_version": "1.0",
    }


plugin_entrypoint = ConsoleInputPlugin
