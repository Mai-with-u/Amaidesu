# src/plugins/keyword_action/plugin.py
import asyncio
import importlib
import time
from typing import Any, Dict, List, Optional
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.event_bus import EventBus


from src.utils.logger import get_logger
from maim_message import MessageBase


class KeywordActionPlugin:
    """
    监听消息中的关键词，并根据配置动态执行相应的动作脚本。

    迁移到新的Plugin接口：
    - 不继承BasePlugin
    - 实现Plugin协议
    - 通过event_bus和config进行依赖注入
    - 不返回Provider（此插件处理消息，不返回Provider列表）
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info(f"初始化插件: {self.__class__.__name__}")

        self.event_bus: Optional["EventBus"] = None

        if not self.config.get("enabled", True):
            self.logger.warning("KeywordActionPlugin 在配置中被禁用。")
            return

        # 加载动作配置
        self.actions = self.config.get("actions", [])
        self.global_cooldown = self.config.get("global_cooldown", 1.0)

        # 状态追踪
        self.last_triggered_times: Dict[str, float] = {}

        self.logger.info(f"成功加载 {len(self.actions)} 个动作规则。")

    async def setup(self, event_bus: "EventBus", config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            空列表（此插件不返回Provider）
        """
        self.event_bus = event_bus
        self.logger.info("设置KeywordActionPlugin")

        if not self.config.get("enabled", True):
            return []

        # 监听所有消息
        event_bus.on("websocket.*", self.handle_message)
        self.logger.info("已注册消息处理器 handle_message。")

        return []

    async def handle_message(self, message: MessageBase):
        """处理传入的消息，检查是否有匹配的关键词动作。"""
        if (
            not message.message_segment
            or message.message_segment.type != "text"
            or not isinstance(message.message_segment.data, str)
        ):
            return

        text_content = message.message_segment.data.strip()
        if not text_content:
            return

        current_time = time.time()

        for action in self.actions:
            if not action.get("enabled", False):
                continue

            action_name = action.get("name", "未命名动作")
            cooldown = action.get("cooldown", self.global_cooldown)

            # 检查冷却时间
            last_triggered = self.last_triggered_times.get(action_name, 0)
            if current_time - last_triggered < cooldown:
                continue

            # 检查关键词
            keywords = action.get("keywords", [])
            match_mode = action.get("match_mode", "anywhere")

            if self._check_keywords(text_content, keywords, match_mode):
                self.logger.info(f"消息触发了动作 '{action_name}'。")
                self.last_triggered_times[action_name] = current_time

                # 异步执行动作脚本
                action_script = action.get("action_script")
                if action_script:
                    asyncio.create_task(self._execute_action_script(action_script, message))
                else:
                    self.logger.warning(f"动作 '{action_name}' 已触发，但未配置 action_script。")

                # 每个消息只触发第一个匹配的动作
                break

    def _check_keywords(self, text: str, keywords: List[str], mode: str) -> bool:
        """根据指定的匹配模式检查文本是否包含关键词。"""
        if mode == "exact":
            return text in keywords
        elif mode == "startswith":
            return any(text.startswith(kw) for kw in keywords)
        elif mode == "endswith":
            return any(text.endswith(kw) for kw in keywords)
        # 默认模式 "anywhere"
        else:
            return any(kw in text for kw in keywords)

    async def _execute_action_script(self, script_name: str, message: MessageBase):
        """动态加载并执行指定的动作脚本。"""
        try:
            module_path = f"plugins.keyword_action.actions.{script_name.replace('.py', '')}"

            # 使用 importlib 动态导入模块
            action_module = importlib.import_module(module_path)

            # 重新加载模块以确保每次都使用最新代码（适用于开发）
            importlib.reload(action_module)

            if hasattr(action_module, "execute"):
                self.logger.debug(f"正在执行动作脚本: {script_name}")
                # 注意：旧架构通过 core 传递服务，新架构应通过 event_bus 通信
                # 当前无 action scripts，此功能暂时禁用
                self.logger.warning("KeywordAction 脚本执行已禁用，请使用 EventBus 进行服务调用")
            else:
                self.logger.error(f"动作脚本 '{script_name}' 中未找到可执行的 'execute' 函数。")

        except ImportError:
            self.logger.error(f"无法找到或导入动作脚本: {script_name} (路径: {module_path})")
        except Exception as e:
            self.logger.error(f"执行动作脚本 '{script_name}' 时发生错误: {e}", exc_info=True)

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "KeywordAction",
            "version": "1.0.0",
            "author": "Amaidesu Team",
            "description": "监听消息中的关键词，并根据配置动态执行相应的动作脚本",
            "category": "processing",
            "api_version": "1.0",
        }


# 插件入口点
plugin_entrypoint = KeywordActionPlugin
