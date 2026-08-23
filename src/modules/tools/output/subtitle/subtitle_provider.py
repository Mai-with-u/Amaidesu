"""
SubtitleProvider - 字幕工具（Wave 4 拆分）

将原 ``SubtitleHandler`` 拆为两部分：
- ``SubtitleGuiService``（位于 ``subtitle_service``）：长驻 Tk 线程 + 字幕渲染后端
- ``SubtitleProvider``（本模块）：ToolProvider，暴露 ``push_subtitle(text)`` 工具

迁移策略（与 .omo/drafts/amaidesu-v2-migration.md A 段对齐）:
- ``SubtitleGuiService.start()`` / ``stop()`` 由组合根（main.py）管理
- ``SubtitleProvider`` 持有 ``SubtitleGuiService`` 引用，工具调用时转发入队
- 配置 schema 字段 verbatim 保留（CONFIG_VERSION 不动）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec

if TYPE_CHECKING:
    pass


_PUSH_SUBTITLE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "要显示的字幕文本"},
    },
    "required": ["text"],
}


class SubtitleProvider:
    """字幕 ToolProvider（依赖注入 SubtitleGuiService）"""

    PROVIDER_NAME = "subtitle"

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)

        # 延迟实例化：避免在 import 阶段拉起 Tk 线程
        self._service: Any = None

    def attach_service(self, service: Any) -> None:
        """由组合根注入 GUI 服务实例"""
        self._service = service

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="push_subtitle",
                description="向字幕 GUI 推送字幕文本（线程安全入队）",
                kind="sync",
                provider="builtin",
                parameters_schema=_PUSH_SUBTITLE_SCHEMA,
            ),
            ToolSpec(
                name="subtitle_clear",
                description="清空字幕 GUI 显示",
                kind="sync",
                provider="builtin",
            ),
            ToolSpec(
                name="subtitle_show_test",
                description="在字幕 GUI 显示一条测试消息",
                kind="sync",
                provider="builtin",
            ),
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        args = invocation.arguments or {}
        try:
            n = invocation.tool_name
            if n == "push_subtitle":
                text = str(args.get("text", ""))
                if not text:
                    return _fail(n, "缺少 text 字段")
                if self._service is None:
                    return _fail(n, "字幕 GUI 服务未附加")
                self._service.push_subtitle(text)
                return _ok(n, True)
            if n == "subtitle_clear":
                if self._service is None:
                    return _fail(n, "字幕 GUI 服务未附加")
                self._service._clear_content()
                return _ok(n, True)
            if n == "subtitle_show_test":
                if self._service is None:
                    return _fail(n, "字幕 GUI 服务未附加")
                self._service._show_test_message()
                return _ok(n, True)
            return _fail(n, f"工具 '{invocation.tool_name}' 不属于 Provider '{self.PROVIDER_NAME}'")
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"字幕工具 {invocation.tool_name} 调用异常: {exc}", exc_info=True)
            return _fail(invocation.tool_name, f"{type(exc).__name__}: {exc}")


def _ok(tool_name: str, success: bool, structured: Any = None) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name,
        success=bool(success),
        structured_content=structured,
        content="" if structured is None else str(structured),
    )


def _fail(tool_name: str, error_message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name,
        success=False,
        error_message=error_message,
    )


def create_subtitle_provider(config: Dict[str, Any]) -> SubtitleProvider:
    return SubtitleProvider(config=config)


def register_subtitle_tools(registry: Any, config: Dict[str, Any]) -> SubtitleProvider:
    provider = create_subtitle_provider(config)
    if hasattr(registry, "register_provider"):
        registry.register_provider(provider)
    else:
        for spec in provider.list_tools():
            registry.register(spec, provider.invoke)
    return provider
