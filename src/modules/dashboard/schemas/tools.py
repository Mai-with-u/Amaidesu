"""工具域契约（请求 + 响应）

工具提供者面板的写路径请求体（开关控制、调试调用）与读路径响应模型
（工具清单、开关结果、调试调用结果）；字段对照 ``services.tool_catalog``
的组装函数实际产出。
"""

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class ProviderControlRequest(BaseModel):
    """提供者/工具开关控制请求体。"""

    action: Literal["enable", "disable"]


class ToolInvokeRequest(BaseModel):
    """工具调试调用请求体（arguments 形状由目标工具的 parameters_schema 约定）。"""

    arguments: Dict[str, Any] = Field(default_factory=dict)


# ---- 响应模型（工具清单 / 开关 / 调试调用）----


class ParameterSpec(BaseModel):
    """单个工具参数的展示规格（``convert_parameters_schema`` 的产出）。

    description / default / minimum / maximum 仅在源 JSON Schema 声明时出现，
    清单端点以 ``response_model_exclude_unset`` 保持"声明才输出"的原样。
    """

    type: str = Field(description="控件类型（string/integer/number/boolean/json）")
    required: bool = Field(description="是否必填")
    description: Optional[str] = Field(default=None, description="参数描述（未声明时缺省）")
    default: Optional[Union[int, float, str, bool]] = Field(default=None, description="默认值（未声明时缺省）")
    minimum: Optional[Union[int, float]] = Field(default=None, description="数值下界（未声明时缺省）")
    maximum: Optional[Union[int, float]] = Field(default=None, description="数值上界（未声明时缺省）")


class ToolHealth(BaseModel):
    """工具熔断/历史健康快照（``format_tool_health`` 的产出）。"""

    state: str = Field(description="健康态（tripped=已熔断 / healthy=有失败历史但未熔断）")
    failure_count: int = Field(description="连续失败计数")
    last_error: str = Field(description="最近一次错误描述")
    tripped_at_ms: int = Field(description="熔断时刻（Unix epoch 毫秒；未熔断为 0）")


class ToolEntry(BaseModel):
    """工具清单中的单个工具条目（``build_action_entry`` + ``health`` 的产出）。

    ``result_event`` 仅异步工具（``kind="async"``）出现；``health`` 恒有键，
    无失败历史时为 null（前端按"无徽标"渲染）。
    """

    name: str = Field(description="工具裸名（展示用）")
    full_name: str = Field(description="注册表调用键（<provider>_<裸名>），调试调用/停用开关以此为标识")
    description: str = Field(description="工具描述")
    parameters: Dict[str, ParameterSpec] = Field(default_factory=dict, description="参数规格表（键 = 参数名）")
    provider: str = Field(description="归属提供者标识")
    kind: str = Field(description="同步/异步（sync/async）")
    category: str = Field(description="工具分类（注册表反查，可为空串）")
    disabled: bool = Field(description="是否已停用（disabled_tools 名单）")
    owner_agent: str = Field(description="归属限定 Agent（空串 = 通用工具）")
    supports_reconnect: bool = Field(description="归属 Provider 是否支持手动重连")
    result_event: Optional[str] = Field(default=None, description="异步工具的结果回传事件名（仅 async 工具）")
    health: Optional[ToolHealth] = Field(default=None, description="熔断/健康快照；无失败历史为 null")


class ToolListResponse(BaseModel):
    """工具清单端点响应。"""

    tools: List[ToolEntry] = Field(default_factory=list)


class ToolToggleResponse(BaseModel):
    """工具/提供者开关写回端点响应（``_toggle_message`` 的产出）。"""

    success: bool = Field(description="写回是否成功")
    enabled: bool = Field(description="写回后的开关目标态")
    message: str = Field(description="面向人的结果话术（落盘位置与生效方式）")


class ToolInvokeBlock(BaseModel):
    """调试调用结果的多模态块（``ResultBlock`` 的透出）。"""

    kind: str = Field(description="块类型（text/image）")
    text: str = Field(default="", description="文本内容（kind=text 时使用）")
    data: str = Field(default="", description="base64 数据（kind=image 时使用）")
    mime_type: str = Field(default="", description="MIME 类型（kind=image 时使用）")


class ToolInvokeResponse(BaseModel):
    """调试调用端点响应（``serialize_execution_result`` 的产出；恒全键）。"""

    success: bool = Field(description="调用是否成功（工具级失败也是 200 + false）")
    content: str = Field(default="", description="文本内容/历史摘要")
    blocks: List[ToolInvokeBlock] = Field(default_factory=list, description="多模态结果块")
    error_message: str = Field(default="", description="错误信息（成功为空串）")
    structured_content: Any = Field(default=None, description="结构化结果（保留原形态）")
    duration_ms: int = Field(default=0, description="执行耗时（毫秒）")
    timestamp_ms: int = Field(default=0, description="完成时刻（Unix epoch 毫秒）")
