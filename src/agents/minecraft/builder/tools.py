"""建造工具经统一注册表观测，生产侧声明受众与实际可调用来源。"""

from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
logger = get_logger("MinecraftBuilderToolProvider")


class MinecraftBuilderToolProvider(BaseToolProvider):
    """把委派入口或单次设计工具注册进既有工具体系。"""

    category = "game"

    def __init__(self, name: str, sources: set[str]) -> None:
        self._name = name
        self._sources = frozenset(sources)
        self._specs: list[ToolSpec] = []
        self._handlers: dict[str, ToolHandler] = {}

    @property
    def name(self) -> str:
        return self._name

    def add(self, name: str, description: str, schema: dict[str, Any], handler: ToolHandler) -> ToolSpec:
        """名字由 ToolSpec 派生，避免 MCP 原名与注册名被手动拼接混淆。"""
        spec = ToolSpec(name=name, description=description, parameters_schema=schema, provider=self.name)
        self._specs.append(spec)
        self._handlers[spec.full_name] = handler
        return spec

    def list_tools(self) -> Iterable[ToolSpec]:
        return list(self._specs)

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """名单过滤之外再检查调用分发，防止设计模型编名调用父级动作。"""
        try:
            if invocation.source not in self._sources:
                raise ValueError(f"当前调用来源不能使用建造工具：{invocation.source}")
            handler = self._handlers.get(invocation.tool_name)
            if handler is None:
                raise ValueError(f"未知建造工具：{invocation.tool_name}")
            result = await handler(invocation.arguments)
            return ToolExecutionResult(tool_name=invocation.tool_name, success=True, structured_content=result)
        except Exception as exc:
            logger.warning(f"建造工具 {invocation.tool_name} 失败：{exc}", exc=True)
            return ToolExecutionResult(tool_name=invocation.tool_name, success=False, error_message=str(exc))


def object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """接口只表达设计动作，建筑部件参数从 Mod 资料注入。"""
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
