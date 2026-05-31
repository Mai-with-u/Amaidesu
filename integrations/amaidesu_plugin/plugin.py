"""
Amaidesu 动作控制插件 - MaiBot SDK v2

用于 MaiBot 插件系统，通过 HTTP API 控制 Amaidesu 的动作和情绪。
由 MaiBot 的 LLM 决策判断何时触发动作。

配置：
    [plugin]
    enabled = true
    config_version = "2.0.0"

    [api]
    url = "http://127.0.0.1:60214/api/v1/maibot/action"
    timeout = 10

    [prompt_override]
    enabled = true
    system_prompt_content = "..."
"""

import httpx
import re
from typing import Any

from maibot_sdk import (
    MaiBotPlugin,
    Tool,
    Command,
    HookHandler,
    Field,
    PluginConfigBase,
)
from maibot_sdk.types import ActivationType, HookMode, HookOrder, ErrorPolicy


# ============ Config Classes ============


class PluginSectionConfig(PluginConfigBase):
    """插件基本信息配置"""

    enabled: bool = Field(default=True)
    config_version: str = Field(default="2.0.0")


class ApiConfig(PluginConfigBase):
    """API 配置"""

    url: str = Field(default="http://127.0.0.1:60214/api/v1/maibot/action")
    timeout: int = Field(default=10)


class PromptOverrideConfig(PluginConfigBase):
    """提示词覆盖配置"""

    enabled: bool = Field(default=True)
    system_prompt_content: str = Field(default="")


class AmaidesuPluginConfig(PluginConfigBase):
    """插件完整配置"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    prompt_override: PromptOverrideConfig = Field(default_factory=PromptOverrideConfig)


# ============ Main Plugin Class ============


class AmaidesuPlugin(MaiBotPlugin):
    """Amaidesu 动作控制插件

    用于控制 Amaidesu 虚拟形象的动作和情绪。
    由 MaiBot 的 LLM 决策系统自动判断触发时机。
    """

    config_model = AmaidesuPluginConfig

    async def on_load(self) -> None:
        """插件加载时调用"""
        self.logger.info("AmaidesuPlugin loaded")

    async def on_unload(self) -> None:
        """插件卸载时调用"""
        self.logger.info("AmaidesuPlugin unloaded")

    @HookHandler(
        "maisaka.replyer.before_model_request",
        name="amaidesu_prompt_override",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
        error_policy=ErrorPolicy.SKIP,
    )
    async def handle_prompt_override(self, messages=None, session_id="", **kwargs) -> dict[str, Any]:
        """Hook handler for prompt override

        当 enabled=True 且 system_prompt_content 非空时，
        替换 messages 列表中第一个 system 消息的内容。
        """
        # 1. If enabled=False or system_prompt_content is empty, return kwargs unchanged
        if not self.config.prompt_override.enabled:
            return kwargs
        if not self.config.prompt_override.system_prompt_content:
            return kwargs

        # 7. Wrap in try-except, on error: logger.error and return original kwargs
        try:
            # 5. Handle edge cases: messages is None/empty
            if messages is None:
                messages = []

            if not messages:
                # 4. If not found: insert new system message at index 0
                messages.insert(0, {"role": "system", "content": self.config.prompt_override.system_prompt_content})
                self.logger.info("Prompt override: inserted new system message (empty messages)")
                return {"messages": messages}

            # 2. Find first message with role="system" in messages list
            system_message_index = None
            for i, msg in enumerate(messages):
                if isinstance(msg, dict):
                    if msg.get("role") == "system":
                        system_message_index = i
                        break
                elif hasattr(msg, "role"):
                    if msg.role == "system":
                        system_message_index = i
                        break

            if system_message_index is not None:
                # 3. If found: replace its content
                if isinstance(messages[system_message_index], dict):
                    messages[system_message_index]["content"] = self.config.prompt_override.system_prompt_content
                else:
                    # Handle message object with role/content attributes
                    messages[system_message_index].content = self.config.prompt_override.system_prompt_content
                self.logger.info(f"Prompt override: replaced system message at index {system_message_index}")
            else:
                # 4. If not found: insert new system message at index 0
                messages.insert(0, {"role": "system", "content": self.config.prompt_override.system_prompt_content})
                self.logger.info("Prompt override: inserted new system message at index 0")

            # 5. Handle edge cases: multiple system messages - only override the first one
            # (already handled by break in loop above)

            # 6. Use logger.info to log the override
            self.logger.info(f"Prompt override applied, {len(messages)} messages in list")

            # 8. Return {"messages": modified_messages}
            return {"messages": messages}

        except Exception as e:
            self.logger.error(f"Prompt override error: {e}")
            return kwargs

    @Tool(
        "amaidesu_action",
        description="控制 Amaidesu 虚拟形象的动作和情绪",
        activation_type=ActivationType.ALWAYS,
    )
    async def handle_action(self, stream_id="", **kwargs) -> tuple[bool, str]:
        """Tool handler for action control

        从 kwargs 中提取 action_type, action_value, emotion, priority, text
        构建 payload 并 POST 到 API
        """
        action_type = kwargs.get("action_type")
        action_value = kwargs.get("action_value")
        emotion = kwargs.get("emotion")
        priority = kwargs.get("priority", 50)
        text = kwargs.get("text")

        payload: dict[str, Any] = {"priority": priority}

        if action_type and action_value:
            payload["action"] = action_type
            payload["action_params"] = {action_type: action_value}

        if emotion:
            payload["emotion"] = emotion

        if text:
            payload["text"] = text

        try:
            async with httpx.AsyncClient(timeout=self.config.api.timeout) as client:
                response = await client.post(self.config.api.url, json=payload)
                response.raise_for_status()
                result = response.json()

                if result.get("success"):
                    self.logger.info(f"Amaidesu action executed: {payload}")
                    return True, "动作执行成功"
                else:
                    error_msg = result.get("error", "未知错误")
                    self.logger.error(f"Amaidesu action failed: {error_msg}")
                    return False, error_msg

        except httpx.ConnectError:
            self.logger.error(f"Cannot connect to Amaidesu ({self.config.api.url})")
            return False, "连接失败"
        except httpx.TimeoutException:
            self.logger.error("Request timeout")
            return False, "请求超时"
        except Exception as e:
            self.logger.error(f"Action execution failed: {e}")
            return False, str(e)

    @Command("amaidesu", description="手动控制 Amaidesu 动作和情绪", pattern=r"^/amaidesu\s+(\w+)\s*(\w*)")
    async def handle_amaidesu_command(self, stream_id="", **kwargs) -> tuple[bool, str, bool]:
        """Command handler for manual control

        Parse command: /amaidesu <action|emotion|hotkey> <value>
        /amaidesu list -> show available commands
        """
        message = kwargs.get("message", {})
        raw_message = message.get("raw_message", "") if isinstance(message, dict) else str(message)

        match = re.match(r"^/amaidesu\s+(\w+)\s*(\w*)", raw_message)
        if not match:
            return (
                True,
                (
                    "❌ 命令格式错误\n"
                    "用法: /amaidesu <action|emotion|hotkey> <value>\n"
                    "示例:\n"
                    "  /amaidesu hotkey smile\n"
                    "  /amaidesu emotion happy"
                ),
                True,
            )

        cmd_type = match.group(1).lower()
        cmd_value = match.group(2).lower() if match.group(2) else None

        payload: dict[str, Any] = {"priority": 50}

        if cmd_type == "action" or cmd_type == "hotkey":
            if not cmd_value:
                return True, ("❌ 请指定动作值\n可用热键: smile, wave, nod, clap, dance, think, bow, point"), True
            payload["action"] = "hotkey"
            payload["action_params"] = {"hotkey": cmd_value}

        elif cmd_type == "emotion" or cmd_type == "feeling":
            if not cmd_value:
                return (
                    True,
                    ("❌ 请指定情绪类型\n可用情绪: happy, neutral, sad, angry, excited, shy, surprised, confused"),
                    True,
                )
            payload["emotion"] = cmd_value

        elif cmd_type == "list":
            return (
                True,
                (
                    "📋 可用命令:\n\n"
                    "热键动作:\n"
                    "  /amaidesu hotkey <动作>\n"
                    "  可用: smile, wave, nod, clap, dance, think, bow, point\n\n"
                    "情绪设置:\n"
                    "  /amaidesu emotion <情绪>\n"
                    "  可用: happy, neutral, sad, angry, excited, shy, surprised, confused"
                ),
                True,
            )

        else:
            return True, f"❌ 未知命令类型: {cmd_type}\n可用: action, emotion, list", True

        try:
            async with httpx.AsyncClient(timeout=self.config.api.timeout) as client:
                response = await client.post(self.config.api.url, json=payload)
                response.raise_for_status()
                result = response.json()

                if result.get("success"):
                    intent_id = result.get("intent_id", "")
                    return True, f"✅ 执行成功! (ID: {intent_id})", True
                else:
                    error_msg = result.get("error", "未知错误")
                    return True, f"❌ 执行失败: {error_msg}", True

        except httpx.ConnectError:
            return True, f"❌ 无法连接到 Amaidesu\n地址: {self.config.api.url}", True
        except httpx.TimeoutException:
            return True, "❌ 请求超时", True
        except Exception as e:
            return True, f"❌ 执行失败: {str(e)}", True

    @Command("amaidestatus", description="查询 Amaidesu 连接状态", pattern=r"^/amaidestatus$")
    async def handle_status_command(self, stream_id="", **kwargs) -> tuple[bool, str, bool]:
        """Command handler for status query"""
        try:
            status_url = self.config.api.url.replace("/action", "/status")
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(status_url)
                if response.status_code == 200:
                    return True, "✅ Amaidesu 连接正常", True
                else:
                    return True, f"⚠️ Amaidesu 响应异常: {response.status_code}", True
        except httpx.ConnectError:
            return True, f"❌ 无法连接到 Amaidesu\n地址: {self.config.api.url}", True
        except Exception as e:
            return True, f"❌ 查询失败: {str(e)}", True


# ============ Factory Function ============


def create_plugin() -> AmaidesuPlugin:
    """创建插件实例的工厂函数"""
    return AmaidesuPlugin()
