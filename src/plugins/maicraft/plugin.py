from typing import Dict, Any, Optional
from src.core.plugin_manager import BasePlugin
from src.core.amaidesu_core import AmaidesuCore
from maim_message import MessageBase
from .command_parser import CommandParser
from .action_registry import ActionRegistry
from .actions import ActionDiscoverer
from .impl.action_executor_interface import ActionExecutor
from .impl.log_action_executor import LogActionExecutor


class MaicraftPlugin(BasePlugin):
    """
    Maicraft 弹幕互动游戏插件。

    负责接收命令转发管道转发的命令，解析并执行相应的游戏行动。
    支持多种命令映射到同一个行动，以及不同的行动执行器实现。
    """

    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)

        self.config = self.plugin_config
        self.enabled = self.config.get("enabled", True)

        if not self.enabled:
            self.logger.warning("Maicraft插件在配置中被禁用")
            return

        # 初始化组件
        self.command_parser = CommandParser()
        self.action_registry = ActionRegistry()
        self.action_discoverer = ActionDiscoverer()
        self.action_executor: Optional[ActionExecutor] = None

        # 初始化执行器
        self._initialize_executor()

        # 注册行动
        self._register_actions()

        self.logger.info("Maicraft插件初始化完成")

    def _initialize_executor(self):
        """初始化行动执行器"""
        executor_type = self.config.get("executor_type", "log").lower()

        if executor_type == "log":
            self.action_executor = LogActionExecutor()
        else:
            self.logger.warning(f"未知的执行器类型: {executor_type}，使用默认的日志执行器")

        self.action_executor = LogActionExecutor()

        self.logger.info(f"使用行动执行器: {executor_type}")

    def _register_actions(self):
        """注册所有支持的行动"""
        # 发现所有可用的动作类
        available_actions = self.action_discoverer.discover_actions()
        if not available_actions:
            self.logger.warning("未发现任何可用的动作类")
            return

        # 获取命令映射配置
        command_mappings = self.config.get("command_mappings", {})

        # 注册命令映射
        registered_commands = []
        for command, action_id in command_mappings.items():
            if (action_class := available_actions.get(action_id)) is not None:
                self.action_registry.register_action(command, action_class)
                registered_commands.append(f"{command}({action_id})")
                self.logger.debug(f"注册命令映射: '{command}' -> {action_class.__name__}({action_id})")
            else:
                self.logger.warning(f"未找到动作类: '{action_id}'，跳过命令 '{command}' 的注册")

        if registered_commands:
            self.logger.info(f"已注册命令映射: {registered_commands}")
        else:
            self.logger.warning("未找到有效的命令映射配置")

        # 输出发现的动作信息
        self.logger.info(f"发现的可用动作: {list(available_actions.keys())}")

    async def setup(self):
        """设置插件"""
        await super().setup()

        if not self.enabled:
            return

        try:
            # 初始化执行器
            if self.action_executor:
                success = await self.action_executor.initialize()
                if not success:
                    self.logger.error("行动执行器初始化失败")
                    self.enabled = False
                    return

            # 向AmaidesuCore注册命令处理服务
            self.core.register_service("maicraft_command_handler", self.handle_command)
            self.logger.info("Maicraft命令处理服务已注册")

            # 输出支持的命令列表
            supported_commands = self.action_registry.get_supported_commands()
            self.logger.info(f"支持的命令: {supported_commands}")

        except Exception as e:
            self.logger.error(f"设置Maicraft插件时出错: {e}", exc_info=True)
            self.enabled = False

    async def cleanup(self):
        """清理插件资源"""
        if self.action_executor:
            await self.action_executor.cleanup()

        self.action_registry.clear()
        self.logger.info("Maicraft插件清理完成")
        await super().cleanup()

    async def handle_command(self, message: MessageBase) -> bool:
        """
        处理命令消息。

        Args:
            message: 包含命令的完整消息对象

        Returns:
            是否成功处理命令
        """
        if not self.enabled:
            return False

        try:
            # 从消息中提取文本内容
            if not message.message_segment or message.message_segment.type != "text":
                self.logger.debug("消息不包含文本内容")
                return False

            data = str(message.message_segment.data)
            message_text = data.strip()
            if not message_text:
                self.logger.debug("消息文本为空")
                return False

            # 解析命令
            command = self.command_parser.parse_command(message_text, message)
            if not command:
                self.logger.debug(f"无法解析命令: '{message_text}'")
                return False

            # 检查是否支持该命令
            if not self.action_registry.is_supported_command(command.name):
                self.logger.debug(f"不支持的命令: '{command.name}'")
                return False

            # 创建行动实例
            action = self.action_registry.create_action(command.name)
            if not action:
                self.logger.error(f"无法创建行动实例: '{command.name}'")
                return False

            # 准备行动参数
            params = {"args": command.args}

            # 验证参数
            if not action.validate_params(params):
                self.logger.error(f"行动参数验证失败: 命令='{command.name}', 参数={params}")
                return False

            # 执行行动
            if not self.action_executor:
                self.logger.error(f"行动执行器未初始化，无法执行命令: '{command.name}'")
                return False

            success = await action.execute(params, self.action_executor)
            if success:
                self.logger.info(f"成功执行命令: '{command.name}' -> 行动: '{action.get_action_id()}'")
            else:
                self.logger.warning(f"执行命令失败: '{command.name}'")

            return success

        except Exception as e:
            self.logger.error(f"处理命令时出错: '{message_text}', 错误: {e}", exc_info=True)
            return False


# 插件入口点
plugin_entrypoint = MaicraftPlugin
