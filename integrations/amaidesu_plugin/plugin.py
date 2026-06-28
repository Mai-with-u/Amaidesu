"""
Amaidesu 动作控制插件 - MaiBot SDK v2

新结构化 Intent + Capability 接口:
- `amaidesu_action` Tool: 触发 action(action_name=`<handler>.<local>`) + emotion + text
- `amaidesu_query_capabilities` Tool: 查询全限定 action 列表
- `amaidesu_query_handlers` Tool: 查询合法 handler 名列表
- 启动 fetch `/api/v1/handlers` 缓存,预验证 action_name 前缀
- `/amaidesu` Command: 重写为新 payload 格式
"""

import asyncio
from typing import Any, Optional

import httpx
from pydantic import Field
from maibot_sdk import (
    MaiBotPlugin,
    Tool,
    Command,
    HookHandler,
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

    base_url: str = Field(
        default="http://127.0.0.1:60214",
        description="Amaidesu HTTP API 基地址(无尾部 /)",
    )
    action_path: str = Field(
        default="/api/v1/maibot/action",
        description="触发 Intent 端点",
    )
    handlers_path: str = Field(
        default="/api/v1/handlers",
        description="查询合法 handler 名端点",
    )
    capabilities_path: str = Field(
        default="/api/v1/capabilities",
        description="查询全限定 action 列表端点",
    )
    timeout: int = Field(default=10, description="请求超时时间(秒)")
    startup_fetch_retries: int = Field(default=3, description="启动 fetch /handlers 重试次数")
    startup_fetch_backoff: float = Field(default=1.0, description="启动 fetch 重试退避基础(秒,指数)")


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
    """Amaidesu 动作控制插件(结构化 Intent + Capability 查询)。"""

    config_model = AmaidesuPluginConfig

    def __init__(self) -> None:
        super().__init__()
        # 合法 handler 名缓存,启动时 fetch 填充;None 表示未启用预验证(dashboard 不可达)
        self._valid_handlers: Optional[set[str]] = None

    async def on_load(self) -> None:
        self.ctx.logger.info("AmaidesuPlugin 已加载")
        await self._prefetch_handlers()

    async def on_unload(self) -> None:
        self.ctx.logger.info("AmaidesuPlugin 已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        self.ctx.logger.info(f"AmaidesuPlugin 配置已更新 (scope={scope}, version={version})")
        await self._prefetch_handlers()

    # ===== 启动预 fetch =====

    async def _prefetch_handlers(self) -> None:
        """从 Dashboard 拉取合法 handler 名,缓存到 `self._valid_handlers`。

        try/except + 重试 + 降级:dashboard 未启动时,`_valid_handlers` 保持 None,
        `amaidesu_action` 不做前缀预验证(LLM 自行控制)。"""
        url = self.config.api.base_url.rstrip("/") + self.config.api.handlers_path
        retries = int(self.config.api.startup_fetch_retries)
        backoff = float(self.config.api.startup_fetch_backoff)

        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.config.api.timeout) as client:
                    response = await client.get(url)
                if response.status_code == 200:
                    data = response.json()
                    handlers = data.get("handlers", [])
                    self._valid_handlers = set(handlers)
                    self.ctx.logger.info(f"启动 fetch /handlers 成功: {len(self._valid_handlers)} 个合法 handler")
                    return
                self.ctx.logger.warning(f"启动 fetch /handlers 收到 HTTP {response.status_code}(attempt {attempt + 1})")
            except Exception as e:
                self.ctx.logger.warning(
                    f"启动 fetch /handlers 失败 (attempt {attempt + 1}/{retries + 1}): {type(e).__name__}: {e}"
                )
            if attempt < retries:
                await asyncio.sleep(backoff * (2**attempt))

        self.ctx.logger.warning("启动 fetch /handlers 最终失败,amaidesu_action 关闭前缀预验证(降级)")
        self._valid_handlers = None

    def _validate_action_prefix(self, action_name: str) -> Optional[str]:
        """如果启用了预验证,检查 `action_name` 前缀是否在合法 handler 集中。

        返回 None 表示通过,返回字符串表示错误消息。
        """
        if self._valid_handlers is None:
            return None
        if not action_name or "." not in action_name:
            return f"action_name '{action_name}' 不是 handler-qualified 格式(需要 `<handler>.<action>`)"
        prefix = action_name.split(".", 1)[0]
        if prefix not in self._valid_handlers:
            return f"未知 handler 标识符: '{prefix}', 可用: {sorted(self._valid_handlers)}"
        return None

    # ===== 提示词覆盖(Hook)=====

    def _override_system_message(self, messages: list, content: str) -> list:
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
        cfg = self.config.replyer_override
        if not cfg.enabled or not cfg.system_prompt_content:
            return {"success": True}
        try:
            modified = self._override_system_message(messages or [], cfg.system_prompt_content)
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
        cfg = self.config.planner_override
        if not cfg.enabled or not cfg.system_prompt_content:
            return {"success": True}
        try:
            modified = self._override_system_message(messages or [], cfg.system_prompt_content)
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

    # ===== Tools =====

    @Tool(
        "amaidesu_action",
        description=(
            "触发 Amaidesu 虚拟形象的结构化 action / emotion / 文本。"
            "action_name 必须是 handler-qualified 形式 `<handler>.<action>`(如 `warudo.wave`)。"
            "可省略 action/emotion 中任一项,但至少需要提供 action / emotion / text 之一。"
        ),
        activation_type=ActivationType.ALWAYS,
        parameters=[
            ToolParameterInfo(
                name="action_name",
                param_type=ToolParamType.STRING,
                description="全限定 action 名,格式 `<handler>.<local_action>`(如 `warudo.wave`)",
                required=False,
            ),
            ToolParameterInfo(
                name="action_parameters",
                param_type=ToolParamType.STRING,
                description='动作参数 JSON 字符串,如 `{"duration_ms": 1500}`(可选)',
                required=False,
            ),
            ToolParameterInfo(
                name="emotion_name",
                param_type=ToolParamType.STRING,
                description="情绪枚举名,如 `happy` / `neutral` / `sad`",
                required=False,
            ),
            ToolParameterInfo(
                name="emotion_intensity",
                param_type=ToolParamType.FLOAT,
                description="情绪强度 [0.0, 1.0],默认 0.5",
                required=False,
            ),
            ToolParameterInfo(
                name="text",
                param_type=ToolParamType.STRING,
                description="可选附带文本(对 TTS 朗读)",
                required=False,
            ),
        ],
    )
    async def handle_action(self, stream_id="", **kwargs) -> dict[str, str]:
        action_name = kwargs.get("action_name")
        if action_name and isinstance(action_name, str):
            action_name = action_name.strip()
        action_parameters_raw = kwargs.get("action_parameters")
        action_parameters: dict[str, Any] = {}
        if action_parameters_raw:
            import json

            try:
                parsed = (
                    json.loads(action_parameters_raw)
                    if isinstance(action_parameters_raw, str)
                    else action_parameters_raw
                )
                if isinstance(parsed, dict):
                    action_parameters = parsed
            except Exception as e:
                self.ctx.logger.warning(f"action_parameters JSON 解析失败,忽略: {e}")
        emotion_name = kwargs.get("emotion_name")
        emotion_intensity = kwargs.get("emotion_intensity", 0.5)
        text = kwargs.get("text")

        if not any([action_name, emotion_name, text]):
            return {"content": "至少需要提供 action_name、emotion_name 或 text 之一"}

        if action_name:
            err = self._validate_action_prefix(action_name)
            if err:
                return {"content": err}

        payload: dict[str, Any] = {
            "text": text,
            "action": {"name": action_name, "parameters": action_parameters} if action_name else None,
            "emotion": {"name": emotion_name, "intensity": emotion_intensity} if emotion_name else None,
        }

        try:
            url = self.config.api.base_url.rstrip("/") + self.config.api.action_path
            async with httpx.AsyncClient(timeout=self.config.api.timeout) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                result = response.json()
                if result.get("success"):
                    self.ctx.logger.info(f"Amaidesu 动作已执行: action={action_name} emotion={emotion_name}")
                    return {"content": f"动作执行成功(intent_id={result.get('intent_id', '?')})"}
                return {"content": f"动作执行失败: {result.get('error', '未知错误')}"}
        except httpx.ConnectError:
            return {"content": f"连接 Amaidesu 失败({self.config.api.base_url}),请确认服务已启动"}
        except httpx.TimeoutException:
            return {"content": "请求 Amaidesu 超时"}
        except Exception as e:
            return {"content": f"动作执行失败: {e}"}

    @Tool(
        "amaidesu_query_capabilities",
        description="查询 Amaidesu Output 阶段所有 handler 暴露的 action 列表(全限定名 `<handler>.<action>`,带 parameters 描述)。",
        activation_type=ActivationType.ALWAYS,
        parameters=[],
    )
    async def handle_query_capabilities(self, stream_id: str = "", **kwargs) -> dict[str, str]:
        url = self.config.api.base_url.rstrip("/") + self.config.api.capabilities_path
        try:
            async with httpx.AsyncClient(timeout=self.config.api.timeout) as client:
                response = await client.get(url)
                response.raise_for_status()
                return {"content": response.text}
        except Exception as e:
            return {"content": f'{{"error": "query_capabilities 失败: {type(e).__name__}: {e}"}}'}

    @Tool(
        "amaidesu_query_handlers",
        description="查询 Amaidesu Output 阶段所有已注册的合法 handler 名(用于构造 action_name 前缀)。",
        activation_type=ActivationType.ALWAYS,
        parameters=[],
    )
    async def handle_query_handlers(self, stream_id: str = "", **kwargs) -> dict[str, str]:
        url = self.config.api.base_url.rstrip("/") + self.config.api.handlers_path
        try:
            async with httpx.AsyncClient(timeout=self.config.api.timeout) as client:
                response = await client.get(url)
                response.raise_for_status()
                return {"content": response.text}
        except Exception as e:
            return {"content": f'{{"error": "query_handlers 失败: {type(e).__name__}: {e}"}}'}

    # ===== Commands =====

    @Command(
        "amaidesu",
        description="手动控制 Amaidesu 动作和情绪(新结构化 payload)",
        pattern=(
            r"^/amaidesu\s+(?P<handler>\w+)\.(?P<action>\w+)"
            r"|^/amaidesu\s+emotion\s+(?P<emotion>\w+)"
            r"|^/amaidesu\s+list"
        ),
    )
    async def handle_amaidesu_command(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, bool]:
        matched_raw = kwargs.get("matched_groups")
        matched: dict[str, Any] = matched_raw if isinstance(matched_raw, dict) else {}

        payload: dict[str, Any] = {}

        handler_name = matched.get("handler")
        action_local = matched.get("action")
        emotion_name = matched.get("emotion")
        has_list = "list" in matched

        if handler_name and action_local:
            action_name = f"{handler_name}.{action_local}"
            err = self._validate_action_prefix(action_name)
            if err:
                await self.ctx.send.text(f"❌ {err}", stream_id)
                return True, err, True
            payload["action"] = {"name": action_name, "parameters": {}}
        elif emotion_name:
            payload["emotion"] = {"name": str(emotion_name).lower(), "intensity": 0.5}
        elif has_list:
            msg = (
                "📋 请改用 `amaidesu_query_capabilities` / `amaidesu_query_handlers` Tool 获取动态能力列表\n"
                "旧的硬编码 `list` 已废弃(避免与实际能力漂移)"
            )
            await self.ctx.send.text(msg, stream_id)
            return True, msg, True
        else:
            msg = "❌ 用法: `/amaidesu <handler>.<action>` 或 `/amaidesu emotion <name>`"
            await self.ctx.send.text(msg, stream_id)
            return True, msg, True

        try:
            url = self.config.api.base_url.rstrip("/") + self.config.api.action_path
            async with httpx.AsyncClient(timeout=self.config.api.timeout) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                result = response.json()
                if result.get("success"):
                    intent_id = result.get("intent_id", "?")
                    msg = f"✅ 执行成功! (intent_id={intent_id})"
                else:
                    msg = f"❌ 执行失败: {result.get('error', '未知错误')}"
        except httpx.ConnectError:
            msg = f"❌ 无法连接 Amaidesu ({self.config.api.base_url})"
        except httpx.TimeoutException:
            msg = "❌ 请求超时"
        except Exception as e:
            msg = f"❌ 执行失败: {e}"

        await self.ctx.send.text(msg, stream_id)
        return True, msg, True

    @Command("amaidestatus", description="查询 Amaidesu 连接状态", pattern=r"^/amaidestatus$")
    async def handle_status_command(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, bool]:
        try:
            url = self.config.api.base_url.rstrip("/") + "/api/v1/system/status"
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(url)
                msg = (
                    "✅ Amaidesu 连接正常"
                    if response.status_code == 200
                    else f"⚠️ Amaidesu 响应异常: {response.status_code}"
                )
        except httpx.ConnectError:
            msg = f"❌ 无法连接 Amaidesu ({self.config.api.base_url})"
        except Exception as e:
            msg = f"❌ 查询失败: {e}"
        await self.ctx.send.text(msg, stream_id)
        return True, msg, True


# ============ Factory Function ============


def create_plugin() -> AmaidesuPlugin:
    return AmaidesuPlugin()
