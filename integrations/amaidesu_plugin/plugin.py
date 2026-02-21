"""
Amaidesu 动作控制插件

用于 MaiBot 插件系统，通过 HTTP API 控制 Amaidesu 的动作和情绪。
由 MaiBot 的 LLM 决策判断何时触发动作。

安装：将此文件夹复制到 MaiBot 的 plugins 目录下
配置：在 config 目录下的对应配置文件中启用插件
"""

import httpx
from typing import List, Tuple, Type, Optional

from src.plugin_system import (
    BasePlugin,
    register_plugin,
    BaseAction,
    BaseCommand,
    ComponentInfo,
    ActionActivationType,
    ConfigField,
)
from src.common.logger import get_logger

logger = get_logger("amaidesu_action_plugin")

# ============ 配置 ============
AMAIDESU_API_URL = "http://127.0.0.1:60214/api/v1/maibot/action"
DEFAULT_TIMEOUT = 10


# ============ Action 组件 ============


class AmaidesuAction(BaseAction):
    """Amaidesu 动作控制 Action

    当 LLM 判断需要控制 Amaidesu 虚拟形象的动作或情绪时激活此 Action。
    由 MaiBot 的决策系统自动判断触发时机。
    """

    action_name = "amaidesu_action"
    action_description = "控制 Amaidesu 虚拟形象的动作和情绪"
    activation_type = ActionActivationType.ALWAYS

    action_parameters = {
        "action_type": "动作类型 (hotkey/expression/motion)，为空则只设置情绪",
        "action_value": "具体的动作值，如热键名称、表情名称、动作名称",
        "emotion": "情绪类型 (happy/neutral/sad/angry/excited/shy/surprised/confused)",
        "priority": "优先级 0-100，默认为 50",
        "text": "可选的回复文本",
    }
    action_require = [
        "需要控制 Amaidesu 虚拟形象做动作时使用",
        "需要改变 Amaidesu 情绪时使用",
    ]
    associated_types = ["text"]

    async def execute(self) -> Tuple[bool, str]:
        """执行动作控制"""
        action_type = self.action_data.get("action_type")
        action_value = self.action_data.get("action_value")
        emotion = self.action_data.get("emotion")
        priority = self.action_data.get("priority", 50)
        text = self.action_data.get("text")

        payload = {"priority": priority}

        if action_type and action_value:
            payload["action"] = action_type
            payload["action_params"] = {action_type: action_value}

        if emotion:
            payload["emotion"] = emotion

        if text:
            payload["text"] = text

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.post(AMAIDESU_API_URL, json=payload)
                response.raise_for_status()
                result = response.json()

                if result.get("success"):
                    logger.info(f"Amaidesu 动作执行成功: {payload}")
                    return True, "动作执行成功"
                else:
                    error_msg = result.get("error", "未知错误")
                    logger.error(f"Amaidesu 动作执行失败: {error_msg}")
                    return False, error_msg

        except httpx.ConnectError:
            logger.error(f"无法连接到 Amaidesu ({AMAIDESU_API_URL})")
            return False, "连接失败"
        except httpx.TimeoutException:
            logger.error("请求超时")
            return False, "请求超时"
        except Exception as e:
            logger.error(f"执行失败: {str(e)}")
            return False, str(e)


# ============ Command 组件（用于调试/手动控制）===========


class AmaidesuCommand(BaseCommand):
    """Amaidesu 控制命令

    用于手动调试控制 Amaidesu 的动作和情绪。
    """

    command_name = "amaidesu"
    command_description = "手动控制 Amaidesu 虚拟形象的动作和情绪（仅调试用）"

    # 命令格式: /amaidesu <action|emotion> <value>
    # 示例:
    #   /amaidesu hotkey smile
    #   /amaidesu emotion happy
    command_pattern = r"^/amaidesu\s+(\w+)\s*(\w*)"

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        """执行命令"""
        import re

        # 解析命令参数
        match = re.match(self.command_pattern, self.message.raw_message)
        if not match:
            await self.send_text(
                "❌ 命令格式错误\n用法: /amaidesu <action|emotion|hotkey> <value>\n示例:\n  /amaidesu hotkey smile\n  /amaidesu emotion happy"
            )
            return True, "命令格式错误", 1

        cmd_type = match.group(1).lower()
        cmd_value = match.group(2).lower() if match.group(2) else None

        payload = {"priority": 50}

        if cmd_type == "action" or cmd_type == "hotkey":
            if not cmd_value:
                await self.send_text("❌ 请指定动作值\n可用热键: smile, wave, nod, clap, dance, think, bow")
                return True, "缺少动作值", 1
            payload["action"] = "hotkey"
            payload["action_params"] = {"hotkey": cmd_value}

        elif cmd_type == "emotion" or cmd_type == "feeling":
            if not cmd_value:
                await self.send_text(
                    "❌ 请指定情绪类型\n可用情绪: happy, neutral, sad, angry, excited, shy, surprised, confused"
                )
                return True, "缺少情绪值", 1
            payload["emotion"] = cmd_value

        elif cmd_type == "list":
            # 列出可用动作和情绪
            await self.send_text(
                "📋 可用命令:\n\n"
                "热键动作:\n  /amaidesu hotkey <动作>\n"
                "  可用: smile, wave, nod, clap, dance, think, bow, point\n\n"
                "情绪设置:\n  /amaidesu emotion <情绪>\n"
                "  可用: happy, neutral, sad, angry, excited, shy, surprised, confused"
            )
            return True, "列出可用命令", 1

        else:
            await self.send_text(f"❌ 未知命令类型: {cmd_type}\n可用: action, emotion, list")
            return True, "未知命令类型", 1

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.post(AMAIDESU_API_URL, json=payload)
                response.raise_for_status()
                result = response.json()

                if result.get("success"):
                    intent_id = result.get("intent_id", "")
                    await self.send_text(f"✅ 执行成功! (ID: {intent_id})")
                    return True, "执行成功", 1
                else:
                    error_msg = result.get("error", "未知错误")
                    await self.send_text(f"❌ 执行失败: {error_msg}")
                    return True, error_msg, 1

        except httpx.ConnectError:
            await self.send_text(f"❌ 无法连接到 Amaidesu\n地址: {AMAIDESU_API_URL}")
            return True, "连接失败", 1
        except httpx.TimeoutException:
            await self.send_text("❌ 请求超时")
            return True, "请求超时", 1
        except Exception as e:
            await self.send_text(f"❌ 执行失败: {str(e)}")
            return True, str(e), 1


class AmaidesuStatusCommand(BaseCommand):
    """查询 Amaidesu 状态命令"""

    command_name = "amaidestatus"
    command_description = "查询 Amaidesu 连接状态"
    command_pattern = r"^/amaidestatus$"

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        """执行状态查询"""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(AMAIDESU_API_URL.replace("/action", "/status"))
                if response.status_code == 200:
                    await self.send_text("✅ Amaidesu 连接正常")
                else:
                    await self.send_text(f"⚠️ Amaidesu 响应异常: {response.status_code}")
        except httpx.ConnectError:
            await self.send_text(f"❌ 无法连接到 Amaidesu\n地址: {AMAIDESU_API_URL}")
        except Exception as e:
            await self.send_text(f"❌ 查询失败: {str(e)}")

        return True, None, 1


# ============ 插件注册 ============


@register_plugin
class AmaidesuActionPlugin(BasePlugin):
    """Amaidesu 动作控制插件

    用于控制 Amaidesu 虚拟形象的动作和情绪。
    由 MaiBot 的 LLM 决策系统自动判断触发时机。
    """

    plugin_name = "amaidesu"
    enable_plugin = False
    dependencies = []
    python_dependencies = ["httpx"]
    config_file_name = "config.toml"

    config_section_descriptions = {
        "plugin": "插件基本信息",
        "api": "API 配置",
    }

    config_schema = {
        "plugin": {
            "config_version": ConfigField(type=str, default="1.0.0", description="配置文件版本"),
            "enabled": ConfigField(type=bool, default=False, description="是否启用插件"),
        },
        "api": {
            "url": ConfigField(
                type=str,
                default="http://127.0.0.1:60214/api/v1/maibot/action",
                description="Amaidesu API 地址",
            ),
            "timeout": ConfigField(type=int, default=10, description="请求超时时间(秒)"),
        },
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        return [
            (AmaidesuAction.get_action_info(), AmaidesuAction),
            (AmaidesuCommand.get_command_info(), AmaidesuCommand),
            (AmaidesuStatusCommand.get_command_info(), AmaidesuStatusCommand),
        ]
