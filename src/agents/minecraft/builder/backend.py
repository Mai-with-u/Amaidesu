"""借用 Minecraft 的 MCP 通道读取设计资料并调用明确绑定的新建造接口。"""

import json
from collections.abc import Callable
from typing import Any, Protocol

from jsonschema.validators import validator_for
from referencing import Registry

from src.agents.minecraft.builder.config import MinecraftBuilderConfig
from src.agents.minecraft.builder.models import BuildCatalog, BuildJob
from src.modules.tools.models import ToolInvocation, ToolSpec
from src.modules.tools.registry import ToolRegistry


class ResourceClient(Protocol):
    """借来的客户端只用于读资料，连接启停仍归父 Agent。"""

    async def list_resources(self) -> list[Any]: ...

    async def read_resource(self, uri: str) -> Any: ...


def json_text(value: Any) -> str:
    """设计与资料保留中文，提供确定且紧凑的上下文表示。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def validate_schema(schema: dict[str, Any], value: Any) -> list[dict[str, str]]:
    """本地先查格式；外部引用不联网获取，未提供的定义必须明确报错。"""
    dialect = validator_for(schema, default=None) if "$schema" in schema else validator_for(schema)
    if dialect is None:
        raise ValueError("Mod 使用了当前客户端不支持的 JSON Schema 方言")
    dialect.check_schema(schema)
    validator = dialect(schema, registry=Registry())
    return [
        {"path": "/" + "/".join(str(part) for part in error.absolute_path), "message": error.message}
        for error in list(validator.iter_errors(value))[:10]
    ]


class MinecraftBuilderBackend:
    """把新建造器协议集中在适配器中，不把建筑类型写进 Agent。"""

    def __init__(
        self,
        config: MinecraftBuilderConfig,
        registry: ToolRegistry,
        client: Callable[[], ResourceClient | None],
    ) -> None:
        self.config = config
        self._registry = registry
        self._client = client

    async def read_text(self, uri: str) -> str:
        """兼容 MCP 的内容列表与结果封装，拒绝空资料和超出预算的内容。"""
        client = self._client()
        if client is None:
            raise ValueError("Minecraft MCP 未连接，无法读取新建造器资料")
        raw = await client.read_resource(uri)
        contents = raw.get("contents", []) if isinstance(raw, dict) else getattr(raw, "contents", raw)
        if not isinstance(contents, (list, tuple)):
            raise ValueError(f"建造资源没有文本内容：{uri}")
        texts = [item.get("text") if isinstance(item, dict) else getattr(item, "text", None) for item in contents]
        if not texts or any(not isinstance(item, str) for item in texts):
            raise ValueError(f"建造资源不是受支持的文本内容：{uri}")
        text = "\n".join(texts)
        if not text.strip() or len(text) > self.config.max_resource_chars:
            raise ValueError(f"建造资源为空或超过字符预算：{uri}")
        return text

    async def prepare(self) -> tuple[BuildCatalog, dict[str, Any]]:
        """每次任务发现真实目录与能力，缺少新协议时不调用旧建造器兜底。"""
        client = self._client()
        if client is None:
            raise ValueError("Minecraft MCP 未连接，建造设计不可用")
        resources = await client.list_resources()
        uris = {str(item.get("uri", "") if isinstance(item, dict) else getattr(item, "uri", "")) for item in resources}
        if self.config.catalog_uri not in uris:
            raise ValueError(f"Mod 尚未发布建造目录 {self.config.catalog_uri}，需要接入新建造器协议")
        catalog = BuildCatalog.model_validate_json(await self.read_text(self.config.catalog_uri))
        schema = json.loads(await self.read_text(catalog.design_schema_uri))
        if not isinstance(schema, dict):
            raise ValueError("建造设计 Schema 必须是 JSON 对象")
        # 初始化即验证方言和 Schema，避免让模型在不可执行的契约上反复设计。
        validate_schema(schema, {})
        self.tool(self.config.validate_tool)
        return catalog, schema

    def tool(self, raw_name: str) -> ToolSpec:
        """按生产处配置的 Mod 原名取工具，不接受教材指定其他可执行入口。"""
        spec = next(
            (
                item
                for item in self._registry.list_tools(provider="maicraft", for_agent="minecraft")
                if item.name == raw_name
            ),
            None,
        )
        if spec is None or spec.kind != "sync":
            raise ValueError(f"新建造器工具尚不可用：{raw_name}")
        return spec

    async def invoke(self, raw_name: str, arguments: dict[str, Any], *, source: str) -> dict[str, Any]:
        """所有 Mod 调用仍经注册表，继承工具停用、熔断与调用记录。"""
        spec = self.tool(raw_name)
        if spec.parameters_schema:
            errors = validate_schema(spec.parameters_schema, arguments)
            if errors:
                raise ValueError(f"Mod 工具契约不兼容：{json_text(errors)}")
        result = await self._registry.invoke(
            ToolInvocation(tool_name=spec.full_name, arguments=arguments, source=source)
        )
        if not result.success or not isinstance(result.structured_content, dict):
            raise ValueError(result.error_message or "新建造器未返回结构化结果")
        payload = result.structured_content
        if payload.get("ok") is False or payload.get("success") is False:
            raise ValueError(str(payload.get("error") or "Mod 拒绝建造请求"))
        if len(json_text(payload)) > self.config.max_context_chars:
            raise ValueError("Mod 建造结果超过上下文预算")
        return payload

    async def validate(
        self, design: dict[str, Any], catalog: BuildCatalog, schema: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """先检查设计格式，再由 Mod 检查真实几何与游戏约束。"""
        errors = validate_schema(schema, design)
        if errors:
            return {"valid": False, "errors": errors, "stage": "schema"}
        return await self.invoke(
            self.config.validate_tool,
            {
                "design": design,
                "design_schema_revision": catalog.design_schema_revision,
                "capability_revision": catalog.revision,
                "context": context,
            },
            source="minecraft-builder-react",
        )

    async def execute(self, job: BuildJob) -> tuple[ToolSpec, dict[str, Any]]:
        """父 Agent 施工前核实能力版本，按同一幂等键提交已校验产物。"""
        if job.result is None:
            raise ValueError("设计尚未通过校验，不能施工")
        current = BuildCatalog.model_validate_json(await self.read_text(self.config.catalog_uri))
        result = job.result
        if (current.revision, current.design_schema_revision) != (
            result.capability_revision,
            result.design_schema_revision,
        ):
            raise ValueError("Mod 建造能力版本已变化，需要重新校验设计")
        spec = self.tool(self.config.execute_tool)
        # 工具调用可能在传输返回前已受理；异常时保留未明标记，禁止悄悄换图重建。
        job.execution_status = "unknown"
        payload = await self.invoke(
            self.config.execute_tool,
            {
                "artifact_ref": result.artifact_ref,
                "design_schema_revision": result.design_schema_revision,
                "capability_revision": result.capability_revision,
                "context": job.request.context,
                "request_key": job.task_id,
            },
            source="minecraft-react",
        )
        if payload.get("accepted") is not True or not isinstance(payload.get("task_id"), str) or not payload["task_id"]:
            raise ValueError("Mod 未返回施工受理任务号；结果不明时必须使用相同幂等键核实")
        return spec, payload
