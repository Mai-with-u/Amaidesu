"""
Maicraft Decision Provider

基于抽象工厂模式的弹幕互动游戏决策 Provider。
支持通过配置切换不同的动作实现系列（如 Log、MCP 等）。

架构要点:
- 订阅 CoreEvents.DATA_MESSAGE 事件
- 解析弹幕命令并生成游戏操作 Intent
- 通过 Intent.actions 传递动作给 Output Domain
- 不直接触发 Output Provider（遵守 3 域数据流规则）
"""

from typing import Dict, Literal, Optional

from pydantic import Field, ValidationError

from src.modules.di.context import ProviderContext
from src.modules.types import Intent
from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger
from src.modules.types import EmotionType
from src.modules.types.base.decision_provider import DecisionProvider

from .action_registry import ActionRegistry
from .action_types import MaicraftActionType
from .command_parser import CommandParser
from .config import MaicraftDecisionProviderConfig
from .factories import AbstractActionFactory, LogActionFactory, McpActionFactory


class MaicraftDecisionProvider(DecisionProvider):
    """
    Maicraft 弹幕互动游戏决策 Provider。

    负责接收标准化消息，解析命令并通过工厂创建游戏操作动作。

    生命周期:
    1. setup() - 初始化工厂，注册命令映射，订阅事件
    2. decide() - 处理 NormalizedMessage，生成 Intent
    3. cleanup() - 清理工厂资源

    配置示例:
    ```toml
    [providers.decision.maicraft]
    enabled = true
    factory_type = "log"  # 或 "mcp"
    command_mappings = { "chat" = "chat", "attack" = "attack" }
    ```
    """

    class ConfigSchema(BaseProviderConfig):
        """Maicraft决策Provider配置Schema

        基于抽象工厂模式的弹幕互动游戏决策Provider。
        支持通过配置切换不同的动作实现系列（如 Log、MCP 等）。
        """

        type: Literal["maicraft"] = "maicraft"
        factory_type: Literal["log", "mcp"] = Field(
            default="log", description="工厂类型：log（测试用）或 mcp（生产环境）"
        )
        command_mappings: Dict[str, str] = Field(
            default_factory=lambda: {
                "chat": "chat",
                "say": "chat",
                "聊天": "chat",
                "attack": "attack",
                "攻击": "attack",
            },
            description="命令映射配置，将中文命令映射到英文动作类型",
        )
        command_prefix: str = Field(default="/", description="命令前缀")
        mcp_server_url: Optional[str] = Field(default=None, description="MCP服务器URL（当factory_type为mcp时使用）")
        mcp_timeout: int = Field(default=30, description="MCP超时时间（秒）")
        verbose_logging: bool = Field(default=False, description="是否输出详细日志")

    def __init__(self, config: dict, context: "ProviderContext"):
        super().__init__(config, context)

        self.logger = get_logger(self.__class__.__name__)

        # 解析配置
        try:
            self.parsed_config = MaicraftDecisionProviderConfig(**config)
        except ValidationError as e:
            self.logger.error(f"配置验证失败: {e}")
            raise

        if not self.parsed_config.enabled:
            self.logger.warning("MaicraftDecisionProvider 在配置中被禁用")
            return

        # 初始化组件
        self.command_parser = CommandParser(command_prefix=self.parsed_config.command_prefix)
        self.action_registry = ActionRegistry()
        self.action_factory: Optional[AbstractActionFactory] = None

        # 初始化工厂
        self._initialize_factory()

        # 注册命令映射
        self._register_commands()

        self.logger.info("MaicraftDecisionProvider 初始化完成")

    def _initialize_factory(self) -> None:
        """初始化动作工厂"""
        factory_type = self.parsed_config.factory_type

        # 根据配置创建对应的工厂
        if factory_type == "log":
            self.action_factory = LogActionFactory()
        elif factory_type == "mcp":
            self.action_factory = McpActionFactory()
        else:
            self.logger.error(f"未知的工厂类型: {factory_type}，使用默认的 Log 工厂")
            self.action_factory = LogActionFactory()

        self.logger.info(f"使用动作工厂: {factory_type} ({self.action_factory.get_factory_type()})")

    def _register_commands(self) -> None:
        """注册所有支持的命令映射"""
        command_mappings = self.parsed_config.command_mappings

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
            for action_type in MaicraftActionType:
                commands = self.action_registry.get_commands_for_action_type(action_type)
                if commands:
                    self.logger.info(f"动作 '{action_type.value}' 的命令: {commands}")

        except Exception as e:
            self.logger.error(f"注册命令映射失败: {e}", exc_info=True)
            raise

    async def init(self) -> None:
        """初始化逻辑"""
        # 初始化工厂
        if self.action_factory:
            success = await self.action_factory.initialize()
            if not success:
                self.logger.error("动作工厂初始化失败")
                return

        self.logger.info("MaicraftDecisionProvider 设置完成")

    async def cleanup(self) -> None:
        """清理资源"""
        if self.action_factory:
            await self.action_factory.cleanup()

        self.action_registry.clear()
        self.logger.info("MaicraftDecisionProvider 清理完成")

    async def decide(self, message) -> None:
        """
        决策（异步）

        根据标准化消息生成决策结果，通过事件发布 Intent。

        Args:
            message: 标准化消息

        注意:
            - 如果不是命令，不发布事件
            - 如果是命令但不支持，不发布事件
            - 命令解析失败时，不发布事件
        """
        if not self.parsed_config.enabled:
            return

        try:
            # 提取消息文本
            text = self._extract_message_text(message)
            if not text:
                return

            # 解析命令
            command = self.command_parser.parse_command(text, message)
            if not command:
                # 不是命令，返回
                return

            self.logger.debug(f"收到命令: {command.name} (参数: {command.args})")

            # 检查是否支持该命令
            if not self.action_registry.is_supported_command(command.name):
                self.logger.debug(f"不支持的命令: '{command.name}'")
                return

            # 获取命令对应的动作类型
            action_type = self.action_registry.get_action_type(command.name)
            if not action_type:
                self.logger.error(f"无法获取命令的动作类型: '{command.name}'")
                return

            # 准备动作参数
            params = self._prepare_action_params(action_type, command.args)
            if not params:
                self.logger.error(f"准备动作参数失败: 命令='{command.name}', 参数={command.args}")
                return

            # 通过工厂创建对应的动作
            if not self.action_factory:
                self.logger.error("动作工厂未初始化")
                return

            action = self.action_factory.create_action(action_type, params, message)
            if not action:
                self.logger.error(f"创建动作实例失败: {action_type.value}")
                return

            # 生成 Intent
            intent = Intent(
                original_text=text,
                response_text=f"[游戏操作] {command.name} {' '.join(command.args)}",
                emotion=EmotionType.NEUTRAL,
                actions=[action],
                metadata={
                    "command": command.name,
                    "action_type": action_type.value,
                    "factory_type": self.action_factory.get_factory_type(),
                },
            )

            self.logger.info(
                f"成功生成游戏操作 Intent: 命令='{command.name}' -> "
                f"动作类型='{action_type.value}' -> "
                f"工厂={self.action_factory.get_factory_type()}"
            )

            # 发布 intent 事件
            await self._publish_intent(intent)

        except Exception as e:
            self.logger.error(f"处理命令时出错: {e}", exc_info=True)
            return

    def _extract_message_text(self, message) -> Optional[str]:
        """
        提取消息文本内容。

        Args:
            message: 标准化消息

        Returns:
            消息文本，如果提取失败则返回 None
        """
        try:
            # 从 NormalizedMessage 中提取文本
            if hasattr(message, "content") and message.content:
                return str(message.content).strip()
            elif hasattr(message, "text") and message.text:
                return str(message.text).strip()
            else:
                return None
        except Exception as e:
            self.logger.debug(f"提取消息文本失败: {e}")
            return None

    def _prepare_action_params(self, action_type: MaicraftActionType, args: list[str]) -> Optional[dict]:
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
            if action_type == MaicraftActionType.CHAT:
                # 聊天动作：将所有参数连接成消息
                message_text = " ".join(args).strip()
                if not message_text:
                    self.logger.error("聊天消息不能为空")
                    return None
                return {"message": message_text}

            elif action_type == MaicraftActionType.ATTACK:
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

    def _create_default_intent(self, message) -> Intent:
        """
        创建默认 Intent（无动作）。

        Args:
            message: 标准化消息

        Returns:
            默认 Intent
        """
        text = self._extract_message_text(message) or ""

        return Intent(
            original_text=text,
            response_text="",
            emotion=EmotionType.NEUTRAL,
            actions=[],  # 空动作列表
            metadata={"provider": "maicraft", "is_default": True},
        )

    async def _publish_intent(self, intent: Intent) -> None:
        """通过 event_bus 发布 decision.intent 事件"""
        from src.modules.events.names import CoreEvents
        from src.modules.events.payloads import IntentPayload

        if not self.event_bus:
            self.logger.error("EventBus 未初始化，无法发布事件")
            return

        await self.event_bus.emit(
            CoreEvents.DECISION_INTENT,
            IntentPayload.from_intent(intent, "maicraft"),
            source="MaicraftDecisionProvider",
        )

        self.logger.debug("已发布 decision.intent 事件")

    @classmethod
    def get_registration_info(cls) -> dict:
        """
        获取 Provider 注册信息。

        Returns:
            注册信息字典
        """
        return {
            "layer": "decision",
            "name": "maicraft",
            "class": cls,
            "source": "builtin:maicraft",
        }
