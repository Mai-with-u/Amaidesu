"""
Maicraft 插件主模块

基于抽象工厂模式的弹幕互动游戏插件。
支持通过配置切换不同的动作实现系列（如 Log、MCP 等）。
"""

from typing import Dict, Any, Optional
from src.core.plugin_manager import BasePlugin
from src.core.amaidesu_core import AmaidesuCore
from maim_message import MessageBase
from .command_parser import CommandParser
from .action_registry import ActionRegistry
from .action_types import ActionType
from .factories import AbstractActionFactory, LogActionFactory, McpActionFactory


class MaicraftPlugin(BasePlugin):
    """
    Maicraft 弹幕互动游戏插件。

    负责接收命令，解析并通过工厂创建的动作执行相应的游戏操作。
    支持通过配置切换不同的动作实现系列。
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
        self.action_factory: Optional[AbstractActionFactory] = None

        # 初始化工厂
        self._initialize_factory()

        # 注册命令映射
        self._register_commands()

        self.logger.info("Maicraft插件初始化完成")

    def _initialize_factory(self):
        """初始化动作工厂"""
        factory_type = self.config.get("factory_type", "log").lower()

        # 根据配置创建对应的工厂
        if factory_type == "log":
            self.action_factory = LogActionFactory()
        elif factory_type == "mcp":
            self.action_factory = McpActionFactory()
        else:
            self.logger.error(f"未知的工厂类型: {factory_type}，使用默认的 Log 工厂")
            self.action_factory = LogActionFactory()

        self.logger.info(f"使用动作工厂: {factory_type} ({self.action_factory.get_factory_type()})")

    def _register_commands(self):
        """注册所有支持的命令映射"""
        # 获取命令映射配置
        command_mappings = self.config.get("command_mappings", {})

        if not command_mappings:
            self.logger.warning("未配置命令映射")
            return

        try:
            # 从配置加载命令映射
            self.action_registry.load_from_config(command_mappings)

            # 输出注册的命令信息
            supported_commands = self.action_registry.get_supported_commands()
            self.logger.info(f"已注册 {len(supported_commands)} 个命令: {supported_commands}")

            # 输出每个动作类型对应的命令
            for action_type in ActionType:
                commands = self.action_registry.get_commands_for_action_type(action_type)
                if commands:
                    self.logger.info(f"动作 '{action_type.value}' 的命令: {commands}")

        except Exception as e:
            self.logger.error(f"注册命令映射失败: {e}", exc_info=True)
            raise

    async def setup(self):
        """设置插件"""
        await super().setup()

        if not self.enabled:
            return

        try:
            # 初始化工厂
            if self.action_factory:
                success = await self.action_factory.initialize()
                if not success:
                    self.logger.error("动作工厂初始化失败")
                    self.enabled = False
                    return

            # 向 AmaidesuCore 注册命令处理服务
            self.core.register_service("maicraft_command_handler", self.handle_command)
            self.logger.info("Maicraft命令处理服务已注册")

        except Exception as e:
            self.logger.error(f"设置Maicraft插件时出错: {e}", exc_info=True)
            self.enabled = False

    async def cleanup(self):
        """清理插件资源"""
        if self.action_factory:
            await self.action_factory.cleanup()

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

            # 获取命令对应的动作类型
            action_type = self.action_registry.get_action_type(command.name)
            if not action_type:
                self.logger.error(f"无法获取命令的动作类型: '{command.name}'")
                return False

            # 通过工厂创建对应的动作实例
            action = self._create_action(action_type)
            if not action:
                self.logger.error(f"无法创建动作实例: {action_type.value}")
                return False

            # 准备动作参数
            params = self._prepare_action_params(action_type, command.args)
            if not params:
                self.logger.error(f"准备动作参数失败: 命令='{command.name}', 参数={command.args}")
                return False

            # 验证参数
            if not action.validate_params(params):
                self.logger.error(f"动作参数验证失败: 类型={action_type.value}, 参数={params}")
                return False

            # 执行动作
            success = await action.execute(params)
            if success:
                self.logger.info(
                    f"成功执行命令: '{command.name}' -> "
                    f"动作类型: '{action_type.value}' -> "
                    f"实现: {action.__class__.__name__}"
                )
            else:
                self.logger.warning(f"执行命令失败: '{command.name}'")

            return success

        except Exception as e:
            self.logger.error(f"处理命令时出错: '{message_text}', 错误: {e}", exc_info=True)
            return False

    def _create_action(self, action_type: ActionType):
        """
        通过工厂创建指定类型的动作实例。

        Args:
            action_type: 动作类型

        Returns:
            动作实例，如果创建失败则返回 None
        """
        if not self.action_factory:
            self.logger.error("动作工厂未初始化")
            return None

        try:
            # 根据动作类型调用工厂的对应方法
            if action_type == ActionType.CHAT:
                return self.action_factory.create_chat_action()
            elif action_type == ActionType.ATTACK:
                return self.action_factory.create_attack_action()
            else:
                self.logger.error(f"不支持的动作类型: {action_type}")
                return None

        except Exception as e:
            self.logger.error(f"创建动作实例失败: {action_type.value}, 错误: {e}", exc_info=True)
            return None

    def _prepare_action_params(self, action_type: ActionType, args: list[str]) -> Optional[Dict[str, Any]]:
        """
        根据动作类型和命令参数准备动作参数字典。

        Args:
            action_type: 动作类型
            args: 命令参数列表

        Returns:
            动作参数字典，如果准备失败则返回 None
        """
        try:
            # 根据不同的动作类型准备不同的参数
            if action_type == ActionType.CHAT:
                # 聊天动作：将所有参数连接成消息
                message = " ".join(args).strip()
                if not message:
                    self.logger.error("聊天消息不能为空")
                    return None
                return {"message": message}

            elif action_type == ActionType.ATTACK:
                # 攻击动作：第一个参数是生物名称
                if not args:
                    self.logger.error("攻击动作需要指定生物名称")
                    return None
                mob_name = args[0].strip()
                if not mob_name:
                    self.logger.error("生物名称不能为空")
                    return None
                return {"mob_name": mob_name}

            else:
                self.logger.error(f"未知的动作类型: {action_type}")
                return None

        except Exception as e:
            self.logger.error(f"准备动作参数时出错: {e}", exc_info=True)
            return None


# 插件入口点
plugin_entrypoint = MaicraftPlugin
