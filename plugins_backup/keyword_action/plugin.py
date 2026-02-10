# src/plugins/keyword_action/plugin.py
import asyncio
import importlib
import time
from typing import Any, Dict, List

from maim_message import MessageBase
from src.core.amaidesu_core import AmaidesuCore
from src.core.plugin_manager import BasePlugin


class KeywordActionPlugin(BasePlugin):
    """
    监听消息中的关键词，并根据配置动态执行相应的动作脚本。
    """

    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        self.config = self.plugin_config

        if not self.config.get("enabled", True):
            self.logger.warning("KeywordActionPlugin 在配置中被禁用。")
            return

        # 加载动作配置
        self.actions = self.config.get("actions", [])
        self.global_cooldown = self.config.get("global_cooldown", 1.0)

        # 状态追踪
        self.last_triggered_times: Dict[str, float] = {}  # Key: action_name, Value: timestamp

        self.logger.info(f"成功加载 {len(self.actions)} 个动作规则。")

    async def setup(self):
        await super().setup()
        if not self.config.get("enabled", True):
            return

        # 注册通配符处理器，监听所有消息
        self.core.register_websocket_handler("*", self.handle_message)
        self.logger.info("已注册消息处理器 handle_message。")

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
                # 传递 core 实例和原始消息给脚本
                await action_module.execute(self.core, message)
            else:
                self.logger.error(f"动作脚本 '{script_name}' 中未找到可执行的 'execute' 函数。")

        except ImportError:
            self.logger.error(f"无法找到或导入动作脚本: {script_name} (路径: {module_path})")
        except Exception as e:
            self.logger.error(f"执行动作脚本 '{script_name}' 时发生错误: {e}", exc_info=True)


# 插件入口点
plugin_entrypoint = KeywordActionPlugin
