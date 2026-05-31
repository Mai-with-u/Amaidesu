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
from typing import Any

from maibot_sdk import (
    MaiBotPlugin,
    Tool,
    Command,
    HookHandler,
    Field,
    PluginConfigBase,
)
from maibot_sdk.types import (
    ActivationType,
    ErrorPolicy,
    HookMode,
    HookOrder,
    ToolParameterInfo,
    ToolParamType,
)


# ============ Config Classes ============


class PluginSectionConfig(PluginConfigBase):
    """插件基本信息配置"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default="2.0.0", description="配置版本")


class ApiConfig(PluginConfigBase):
    """API 配置"""

    __ui_label__ = "API"
    __ui_icon__ = "link"
    __ui_order__ = 1

    url: str = Field(
        default="http://127.0.0.1:60214/api/v1/maibot/action",
        description="Amaidesu HTTP API 地址",
    )
    timeout: int = Field(default=10, description="请求超时时间(秒)")


class ReplyerOverrideConfig(PluginConfigBase):
    """回复器提示词覆盖配置"""

    __ui_label__ = "回复器提示词覆盖"
    __ui_icon__ = "file-text"
    __ui_order__ = 2

    enabled: bool = Field(default=False, description="是否启用回复器提示词覆盖")
    system_prompt_content: str = Field(default="", description="覆盖的回复器系统提示词内容")


class PlannerOverrideConfig(PluginConfigBase):
    """规划器提示词覆盖配置"""

    __ui_label__ = "规划器提示词覆盖"
    __ui_icon__ = "brain"
    __ui_order__ = 3

    enabled: bool = Field(default=False, description="是否启用规划器提示词覆盖")
    system_prompt_content: str = Field(default="", description="覆盖的规划器系统提示词内容")


class AmaidesuPluginConfig(PluginConfigBase):
    """插件完整配置"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    replyer_override: ReplyerOverrideConfig = Field(default_factory=ReplyerOverrideConfig)
    planner_override: PlannerOverrideConfig = Field(default_factory=PlannerOverrideConfig)


# ============ Main Plugin Class ============


class AmaidesuPlugin(MaiBotPlugin):
    """Amaidesu 动作控制插件

    用于控制 Amaidesu 虚拟形象的动作和情绪。
    由 MaiBot 的 LLM 决策系统自动判断触发时机。
    """

    config_model = AmaidesuPluginConfig

    async def on_load(self) -> None:
        """插件加载时执行"""
        self.ctx.logger.info("AmaidesuPlugin 已加载")

    async def on_unload(self) -> None:
        """插件卸载时执行"""
        self.ctx.logger.info("AmaidesuPlugin 已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """配置热重载时执行"""
        self.ctx.logger.info(f"AmaidesuPlugin 配置已更新 (scope={scope}, version={version})")

    # ===== 提示词覆盖 =====

    def _override_system_message(self, messages: list, content: str) -> list:
        """替换消息列表中第一条 system 消息的内容

        Args:
            messages: 原始消息列表（不会被修改）
            content: 新的系统提示词内容

        Returns:
            修改后的新消息列表
        """
        modified = list(messages)

        if not modified:
            modified.insert(0, {"role": "system", "content": content})
            return modified

        for i, msg in enumerate(modified):
            is_system = False
            if isinstance(msg, dict):
                is_system = msg.get("role") == "system"
            elif hasattr(msg, "role"):
                is_system = msg.role == "system"

            if is_system:
                if isinstance(modified[i], dict):
                    modified[i] = {**modified[i], "content": content}
                else:
                    modified[i].content = content
                return modified

        # 未找到 system 消息，在开头插入
        modified.insert(0, {"role": "system", "content": content})
        return modified

    @HookHandler(
        "maisaka.replyer.before_model_request",
        name="amaidesu_replyer_prompt_override",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
        error_policy=ErrorPolicy.SKIP,
    )
    async def handle_replyer_prompt_override(self, messages=None, session_id="", **kwargs) -> dict[str, Any]:
        """回复器提示词覆盖 Hook

        替换回复器发送给 LLM 的系统提示词。
        """
        cfg = self.config.replyer_override
        if not cfg.enabled or not cfg.system_prompt_content:
            return {"success": True}

        try:
            modified = self._override_system_message(messages or [], cfg.system_prompt_content)
            self.ctx.logger.info(f"Replyer prompt override 已应用，消息列表共 {len(modified)} 条")
            return {
                "success": True,
                "action": "continue",
                "modified_kwargs": {
                    "messages": modified,
                    "session_id": session_id,
                    **kwargs,
                },
            }
        except Exception as e:
            self.ctx.logger.error(f"Replyer prompt override 错误: {e}")
            return {"success": False, "error_message": str(e)}

    @HookHandler(
        "maisaka.planner.before_request",
        name="amaidesu_planner_prompt_override",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
        error_policy=ErrorPolicy.SKIP,
    )
    async def handle_planner_prompt_override(self, messages=None, session_id="", **kwargs) -> dict[str, Any]:
        """规划器提示词覆盖 Hook

        替换规划器发送给模型的消息中的系统提示词。
        同时保留 kwargs 中的 tool_definitions 等其他字段。
        """
        cfg = self.config.planner_override
        if not cfg.enabled or not cfg.system_prompt_content:
            return {"success": True}

        try:
            modified = self._override_system_message(messages or [], cfg.system_prompt_content)
            self.ctx.logger.info(f"Planner prompt override 已应用，消息列表共 {len(modified)} 条")
            return {
                "success": True,
                "action": "continue",
                "modified_kwargs": {
                    "messages": modified,
                    "session_id": session_id,
                    **kwargs,
                },
            }
        except Exception as e:
            self.ctx.logger.error(f"Planner prompt override 错误: {e}")
            return {"success": False, "error_message": str(e)}

    @Tool(
        "amaidesu_action",
        description="控制 Amaidesu 虚拟形象的动作和情绪。适用于需要表达情感、做动作、触发热键等场景。",
        activation_type=ActivationType.ALWAYS,
        parameters=[
            ToolParameterInfo(
                name="action_type",
                param_type=ToolParamType.STRING,
                description="动作类型，如 hotkey（热键动作）、expression（表情）",
                required=False,
            ),
            ToolParameterInfo(
                name="action_value",
                param_type=ToolParamType.STRING,
                description="动作值，如 smile、wave、nod、clap、dance、think、bow、point",
                required=False,
            ),
            ToolParameterInfo(
                name="emotion",
                param_type=ToolParamType.STRING,
                description="情绪类型，如 happy、neutral、sad、angry、excited、shy、surprised、confused",
                required=False,
            ),
            ToolParameterInfo(
                name="priority",
                param_type=ToolParamType.INTEGER,
                description="优先级，0-100，默认 50",
                required=False,
            ),
            ToolParameterInfo(
                name="text",
                param_type=ToolParamType.STRING,
                description="附带文本内容",
                required=False,
            ),
        ],
    )
    async def handle_action(self, stream_id="", **kwargs) -> dict[str, str]:
        """Tool handler for action control

        从参数中提取 action_type, action_value, emotion, priority, text
        构建 payload 并 POST 到 Amaidesu API
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
                    self.ctx.logger.info(f"Amaidesu 动作已执行: {payload}")
                    return {"content": "动作执行成功"}
                else:
                    error_msg = result.get("error", "未知错误")
                    self.ctx.logger.error(f"Amaidesu 动作失败: {error_msg}")
                    return {"content": f"动作执行失败: {error_msg}"}

        except httpx.ConnectError:
            self.ctx.logger.error(f"无法连接到 Amaidesu ({self.config.api.url})")
            return {"content": "连接 Amaidesu 失败，请确认服务是否已启动"}
        except httpx.TimeoutException:
            self.ctx.logger.error("请求超时")
            return {"content": "请求 Amaidesu 超时"}
        except Exception as e:
            self.ctx.logger.error(f"动作执行失败: {e}")
            return {"content": f"动作执行失败: {e}"}

    @Command(
        "amaidesu",
        description="手动控制 Amaidesu 动作和情绪",
        pattern=r"^/amaidesu\s+(?P<cmd_type>\w+)\s*(?P<cmd_value>\w*)",
    )
    async def handle_amaidesu_command(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, bool]:
        """手动控制命令

        用法: /amaidesu <action|emotion|hotkey> <value>
        /amaidesu list -> 显示可用命令
        """
        matched_groups = kwargs.get("matched_groups")
        if not isinstance(matched_groups, dict):
            matched_groups = {}

        cmd_type = str(matched_groups.get("cmd_type", "")).lower()
        cmd_value = str(matched_groups.get("cmd_value", "")).lower() or None

        payload: dict[str, Any] = {"priority": 50}

        if cmd_type in ("action", "hotkey"):
            if not cmd_value:
                msg = "❌ 请指定动作值\n可用热键: smile, wave, nod, clap, dance, think, bow, point"
                await self.ctx.send.text(msg, stream_id)
                return True, msg, True
            payload["action"] = "hotkey"
            payload["action_params"] = {"hotkey": cmd_value}

        elif cmd_type in ("emotion", "feeling"):
            if not cmd_value:
                msg = "❌ 请指定情绪类型\n可用情绪: happy, neutral, sad, angry, excited, shy, surprised, confused"
                await self.ctx.send.text(msg, stream_id)
                return True, msg, True
            payload["emotion"] = cmd_value

        elif cmd_type == "list":
            msg = (
                "📋 可用命令:\n\n"
                "热键动作:\n"
                "  /amaidesu hotkey <动作>\n"
                "  可用: smile, wave, nod, clap, dance, think, bow, point\n\n"
                "情绪设置:\n"
                "  /amaidesu emotion <情绪>\n"
                "  可用: happy, neutral, sad, angry, excited, shy, surprised, confused"
            )
            await self.ctx.send.text(msg, stream_id)
            return True, msg, True

        else:
            msg = f"❌ 未知命令类型: {cmd_type}\n可用: action, emotion, list"
            await self.ctx.send.text(msg, stream_id)
            return True, msg, True

        try:
            async with httpx.AsyncClient(timeout=self.config.api.timeout) as client:
                response = await client.post(self.config.api.url, json=payload)
                response.raise_for_status()
                result = response.json()

                if result.get("success"):
                    intent_id = result.get("intent_id", "")
                    msg = f"✅ 执行成功! (ID: {intent_id})"
                    await self.ctx.send.text(msg, stream_id)
                    return True, msg, True
                else:
                    error_msg = result.get("error", "未知错误")
                    msg = f"❌ 执行失败: {error_msg}"
                    await self.ctx.send.text(msg, stream_id)
                    return True, msg, True

        except httpx.ConnectError:
            msg = f"❌ 无法连接到 Amaidesu\n地址: {self.config.api.url}"
            await self.ctx.send.text(msg, stream_id)
            return True, msg, True
        except httpx.TimeoutException:
            msg = "❌ 请求超时"
            await self.ctx.send.text(msg, stream_id)
            return True, msg, True
        except Exception as e:
            msg = f"❌ 执行失败: {e}"
            await self.ctx.send.text(msg, stream_id)
            return True, msg, True

    @Command("amaidestatus", description="查询 Amaidesu 连接状态", pattern=r"^/amaidestatus$")
    async def handle_status_command(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, bool]:
        """查询 Amaidesu 连接状态"""
        try:
            status_url = self.config.api.url.replace("/action", "/status")
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(status_url)
                if response.status_code == 200:
                    msg = "✅ Amaidesu 连接正常"
                else:
                    msg = f"⚠️ Amaidesu 响应异常: {response.status_code}"
                await self.ctx.send.text(msg, stream_id)
                return True, msg, True
        except httpx.ConnectError:
            msg = f"❌ 无法连接到 Amaidesu\n地址: {self.config.api.url}"
            await self.ctx.send.text(msg, stream_id)
            return True, msg, True
        except Exception as e:
            msg = f"❌ 查询失败: {e}"
            await self.ctx.send.text(msg, stream_id)
            return True, msg, True


# ============ Factory Function ============


def create_plugin() -> AmaidesuPlugin:
    """创建插件实例的工厂函数"""
    return AmaidesuPlugin()
