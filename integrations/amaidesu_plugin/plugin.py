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
from typing import Any, Dict, Optional

import httpx
from pydantic import Field
from maibot_sdk import (
    MaiBotPlugin,
    MessageGateway,
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

# MessageGateway 标识
AMAIDESU_GATEWAY_NAME = "amaidesu_gateway"
AMAIDESU_PLATFORM = "amaidesu"
# 不传 account_id —— SendService 出来的 RouteKey.account_id 是 None(消息
# additional_config 里没有 platform_io_account_id),bind 端如果传非空会 hash
# 不一致导致永远查不到 binding。装饰器上的 account_id="" 同样走 None。

# c72d1e5 默认回复器提示词模板,占位符由 MaiBot 端填充
_DEFAULT_REPLYER_PROMPT = """{knowledge_prompt}{tool_info_block}{extra_info_block}
{expression_habits_block}{memory_retrieval}{jargon_explanation}

你是一位正在直播的 VTuber {bot_name}，下面是直播间正在聊的内容，其中包含聊天记录和聊天中的图片
其中标注 {bot_name}(你) 的发言是你自己的发言，请注意区分:
{time_block}
{dialogue_prompt}

{reply_target_block}。
{planner_reasoning}
{identity}
{chat_prompt}作为 VTuber，请用生动有趣的方式回应观众弹幕，保持角色特点，
尽量简短一些。{keywords_reaction_prompt}
请注意把握弹幕内容，不要回复的太有条理。
{reply_style}
请注意不要输出多余内容(包括不必要的前后缀，冒号，括号，表情包，at或 @等 )，只输出发言内容就好。
最好一次对一个话题进行回复，免得啰嗦或者回复内容太乱。
现在，你说：
"""

# 默认规划器提示词(VTuber 直播决策模板;c72d1e5 时代无独立模板,这里给完整版本)
# 注意:Hook 触发时 messages 已被 MaiBot 端渲染,override 内容按原样替换原 system message,
# 因此占位符不会被填充,这里用静态文本而非 {bot_name} 等变量。
_DEFAULT_PLANNER_PROMPT = """你是 VTuber 直播间的聊天决策规划助手。

# 任务
分析当前直播间的弹幕和聊天内容，判断 VTuber 是否需要回复、如何回复，以及是否需要触发额外的工具动作。

# 决策原则
- 保持角色特点，不要打破沉浸感
- 主动回应有趣或高频的弹幕，而不是每条都回
- 不要重复回复相同话题，不要在对方话没说完时插嘴
- 如果有工具可以帮助表达情绪/动作，优先调用
- 信息不足时不要编造

# 可用工具
- reply():对用户发出可见回复
- query_memory():检索历史对话/长期偏好
- tool_search():从 deferred 池中解锁其他工具
- view_complex_message():展开复杂消息
- no_action():不应该回复时使用
- wait():等对方继续说完
- finish():结束这次规划

现在，请输出你对当前场景的分析，然后调用工具：
"""


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

    enabled: bool = Field(default=True, description="是否启用回复器提示词覆盖")
    system_prompt_content: str = Field(
        default=_DEFAULT_REPLYER_PROMPT,
        description="覆盖的回复器系统提示词内容(默认 c72d1e5 模板,占位符由 MaiBot 端填充)",
    )


class PlannerOverrideConfig(PluginConfigBase):
    """规划器提示词覆盖配置"""

    __ui_label__ = "规划器提示词覆盖"
    __ui_icon__ = "brain"
    __ui_order__ = 3

    enabled: bool = Field(default=True, description="是否启用规划器提示词覆盖")
    system_prompt_content: str = Field(
        default=_DEFAULT_PLANNER_PROMPT,
        description="覆盖的规划器系统提示词内容(默认 VTuber 直播决策模板,完整无占位符)",
    )


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
        # 上报 MessageGateway ready,MaiBot 会自动注册 platform="amaidesu" 的 send route
        await self._report_gateway_ready(True)

    async def on_unload(self) -> None:
        # 卸载前 unregister,避免 PluginSupervisor 残留 driver
        await self._report_gateway_ready(False)
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

    # ===== MessageGateway(出站)=====

    async def _report_gateway_ready(self, ready: bool) -> None:
        """向 MaiBot Host 上报 MessageGateway 运行时状态。

        - ready=True: Host 会创建 PluginPlatformDriver 并 `bind_send_route(RouteKey)`
        - ready=False: Host 会解绑并移除 driver

        关键:不传 account_id/scope,让 bind 端 RouteKey = (platform, account_id=None, scope=None),
        与 SendService 构造的 RouteKey(消息 additional_config 通常无 platform_io_account_id)一致。
        """
        try:
            # 不传 account_id/scope → update_state 默认 "" → _build_message_gateway_route_key 内部
            # 走 "or gateway_entry.account_id or None" → None → RouteKey(platform='amaidesu', account_id=None)
            ok = await self.ctx.gateway.update_state(
                gateway_name=AMAIDESU_GATEWAY_NAME,
                ready=ready,
                platform=AMAIDESU_PLATFORM,
                metadata={
                    "plugin_version": "2.0.0",
                    "api_base_url": self.config.api.base_url,
                    "expected_route_key": f"platform={AMAIDESU_PLATFORM}, account_id=None, scope=None",
                },
            )
            if ok:
                self.ctx.logger.info(
                    f"[OK] MessageGateway {AMAIDESU_GATEWAY_NAME} ready={ready} 上报成功 "
                    f"| 期望 RouteKey: (platform={AMAIDESU_PLATFORM}, account_id=None, scope=None) "
                    f"| 实际: 见 MaiBot 端 [runner_manager] bind_send_route DEBUG 日志"
                )
            else:
                self.ctx.logger.warning(f"[!!] MessageGateway {AMAIDESU_GATEWAY_NAME} ready={ready} 上报被 Host 拒绝")
        except Exception as e:
            self.ctx.logger.warning(f"[!!] MessageGateway ready 上报异常(忽略): {type(e).__name__}: {e}")

    def _extract_reply_text(self, message: Dict[str, Any]) -> str:
        """从 MaiBot 序列化消息字典中提取回复文本。

        优先使用 `processed_plain_text`(MaiBot 已处理过的纯文本),
        回退从 `raw_message` 段的 text 字段拼接。
        """
        plain = message.get("processed_plain_text")
        if isinstance(plain, str) and plain.strip():
            return plain.strip()

        raw = message.get("raw_message")
        if isinstance(raw, list):
            parts = []
            for seg in raw:
                if isinstance(seg, dict):
                    if seg.get("type") == "text":
                        data = seg.get("data")
                        if isinstance(data, dict):
                            text = data.get("text")
                            if isinstance(text, str):
                                parts.append(text)
                        elif isinstance(data, str):
                            parts.append(data)
            if parts:
                return "".join(parts).strip()
        return ""

    @MessageGateway(
        name=AMAIDESU_GATEWAY_NAME,
        route_type="send",
        platform=AMAIDESU_PLATFORM,
        protocol="amaidesu",
        description="Amaidesu 出站消息网关:MaiBot 决策回复后通过此通道转发到 Amaidesu HTTP API",
    )
    async def handle_amaidesu_outbound(
        self,
        message: Dict[str, Any],
        route: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """接收 MaiBot 出站消息(MaiBot → Amaidesu 回复),转发到 Amaidesu `/api/v1/maibot/action`。

        入站(Amaidesu → MaiBot)走 `MaiBotDecider` 的 `maim_message.Router`,
        此 gateway 仅负责出站,故 route_type="send"。
        """
        del route
        del metadata
        del kwargs

        text = self._extract_reply_text(message)
        if not text:
            self.ctx.logger.warning("MessageGateway 出站消息无文本内容,跳过")
            return {"success": False, "error": "消息无文本内容"}

        payload = {"text": text}
        url = self.config.api.base_url.rstrip("/") + self.config.api.action_path

        try:
            async with httpx.AsyncClient(timeout=self.config.api.timeout) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                result = response.json()
        except httpx.ConnectError as e:
            self.ctx.logger.error(f"MessageGateway 连接 Amaidesu 失败: {self.config.api.base_url}: {e}")
            return {"success": False, "error": f"连接 Amaidesu 失败: {e}"}
        except httpx.TimeoutException as e:
            self.ctx.logger.error(f"MessageGateway 请求 Amaidesu 超时: {e}")
            return {"success": False, "error": f"请求超时: {e}"}
        except httpx.HTTPStatusError as e:
            self.ctx.logger.error(
                f"MessageGateway Amaidesu 返回 HTTP {e.response.status_code}: {e.response.text[:200]}"
            )
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            self.ctx.logger.error(f"MessageGateway 转发失败: {type(e).__name__}: {e}")
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

        if not result.get("success"):
            err = result.get("error", "未知错误")
            self.ctx.logger.error(f"MessageGateway Amaidesu 处理失败: {err}")
            return {"success": False, "error": err}

        intent_id = result.get("intent_id", "?")
        self.ctx.logger.info(f"MessageGateway 出站成功: text='{text[:50]}...' intent_id={intent_id}")
        return {
            "success": True,
            "external_message_id": str(intent_id) if intent_id else "",
            "metadata": {"intent_id": intent_id, "text_length": len(text)},
        }

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
