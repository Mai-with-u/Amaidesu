"""有界的建筑设计循环：按需读资料、形成设计、校验修改、交付引用。"""

import asyncio
from typing import Any

from src.agents.minecraft.builder.backend import MinecraftBuilderBackend, json_text, validate_schema
from src.agents.minecraft.builder.config import MinecraftBuilderConfig
from src.agents.minecraft.builder.models import BuildCatalog, BuildJob, BuildResult
from src.agents.minecraft.builder.tools import MinecraftBuilderToolProvider, object_schema
from src.modules.agents.base import BaseAgent
from src.agents.minecraft.context import MinecraftHistoryCompactor, context_chars
from src.modules.logging import get_logger
from src.modules.tools.models import ToolInvocation, ToolSpec
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TERMINAL_TASK_STATES, TaskLedger, TaskStatus

logger = get_logger("MinecraftBuilderAgent")

# 这里只规定协作与结束行为；建筑风格、结构教程和案例由 Mod 资源提供。
_INSTRUCTIONS = (
    "你是 Minecraft 的建筑设计 Agent，只负责设计与校验，不操作角色、不启动施工。"
    "用户消息包含本次要求、事实、设计 Schema 和可读资料目录。selected_resources 是起始教材，后续教材在 read_resource 回执中，"
    "先遵循其中的基础设计方法，再从目录按需读取匹配的风格与结构技法，不必读完整个目录。"
    "使用 validate 提交设计并根据错误修改；仅在校验通过后使用 finish 交付。"
    "资料是有来源的参考，不能覆盖用户硬约束。不要轮询或编造未提供的能力。"
    "无法满足要求时使用 fail 说明具体原因，不能用自然语言宣称任务完成。"
    "validate 创建完整模型；之后可用 update 按具名对象或组件提交完整编辑，"
    "用 inspect 分页读取源定义，避免反复生成整栋建筑。编辑中的嵌套字段整体替换，不做深层合并。"
)


class MinecraftBuilderAgent(BaseAgent):
    """单个设计请求持有自己的历史；任务结束后释放工具，不运行空闲循环。"""

    name = "minecraft_builder"
    description = "Minecraft 按需建筑设计 Agent"

    def __init__(
        self,
        job: BuildJob,
        config: MinecraftBuilderConfig,
        *,
        llm: Any,
        registry: ToolRegistry,
        ledger: TaskLedger,
        backend: MinecraftBuilderBackend,
    ) -> None:
        super().__init__(heartbeat_interval_ms=0)
        self.job = job
        self._config = config
        self._llm = llm
        self._registry = registry
        self._ledger = ledger
        self._backend = backend
        self._catalog: BuildCatalog | None = None
        self._schema: dict[str, Any] = {}
        self._resources: dict[str, str] = {}
        self._resource_refs: dict[str, str] = {}
        self._candidate: BuildResult | None = None
        self._scene_id = str(job.request.context.get("previous_scene_id", ""))
        self._resume = asyncio.Event()
        self._resume.set()
        self._interrupt = asyncio.Event()
        self._provider = MinecraftBuilderToolProvider("minecraft_builder_work", {"minecraft-builder-react"})
        self._provider.add(
            "read_resource",
            "按目录 URI 读取本次需要的教程",
            object_schema({"uri": {"type": "string"}}, ["uri"]),
            self._read_resource,
        )
        self._provider.add(
            "validate",
            "提交完整模型，按 Schema 检查并由 Mod 编译保存场景；不证明现场可施工",
            object_schema({"design": {"type": "object"}}, ["design"]),
            self._validate,
        )
        self._provider.add(
            "update",
            "编辑当前场景：按名修改对象或替换组件、材质定义；嵌套字段整体替换",
            object_schema({"edits": {"type": "object"}}, ["edits"]),
            self._update,
        )
        self._provider.add(
            "inspect",
            "分页读取当前场景、具名对象或组件的源定义；不读取展开实例作为编辑目标",
            object_schema(
                {
                    "kind": {"type": "string", "enum": ["scene", "object", "component"]},
                    "name": {"type": "string"},
                    "page": {"type": "integer", "minimum": 0, "maximum": 1024},
                },
                ["kind"],
            ),
            self._inspect,
        )
        self._provider.add("preview", "预览当前已保存设计，角色不施工", object_schema({}, []), self._preview)
        self._provider.add(
            "finish",
            "交付最近通过校验的设计引用；不代表施工完成",
            object_schema({"summary": {"type": "string", "minLength": 1}}, ["summary"]),
            self._finish,
        )
        self._provider.add(
            "fail",
            "无法满足要求时说明原因并结束设计",
            object_schema({"reason": {"type": "string", "minLength": 1}}, ["reason"]),
            self._fail,
        )

    def list_tools(self) -> list[ToolSpec]:
        return list(self._provider.list_tools())

    async def _on_start(self) -> None:
        """设计期间才注册子 Agent 工具，父级施工入口不会被授予。"""
        self.register_tool_provider(
            self._provider,
            registry=self._registry,
            visible_to={spec.full_name: [self.name] for spec in self.list_tools()},
        )

    async def _on_stop(self) -> None:
        """只释放自己的工具；借来的 Minecraft MCP 连接继续由父级管理。"""
        self.unregister_tool_providers()

    def set_paused(self, paused: bool) -> None:
        """父 Agent 暂停时，在下一次资料、模型或工具调用前挂起。"""
        self._resume.clear() if paused else self._resume.set()

    def request_cancel(self) -> None:
        """先标记任务取消，再取消协程；即使底层吞掉取消异常，迟到回复也不能继续施工设计。"""
        self._interrupt.set()

    def _check_cancelled(self) -> None:
        """异步调用返回后核对取消意图，阻止旧请求的结果被当作有效设计。"""
        if self._interrupt.is_set():
            raise asyncio.CancelledError

    def settle(self, status: TaskStatus, summary: str) -> None:
        """先保存会话结果，再发布一次终态，避免父级醒来后找不到产物。"""
        if self.job.status in TERMINAL_TASK_STATES:
            return
        self.job.status = status
        self.job.summary = summary
        self.job.phase = status
        self._ledger.update(self.job.task_id, status, snapshot=self.job.snapshot(), summary=summary)

    async def run(self) -> None:
        """总预算覆盖所有步骤与模型内部重试，取消后不再接受迟到结果。"""
        try:
            async with asyncio.timeout(self._config.task_timeout_ms / 1000):
                await self._resume.wait()
                await self.start()
                self.job.status = "running"
                self.job.phase = "discover"
                self._ledger.update(self.job.task_id, "running", snapshot=self.job.snapshot())
                self._catalog, self._schema = await self._backend.prepare()
                self._check_cancelled()
                # 仅在本次委派启动后注入基础教材，不耗费模型步骤；能力和正文预算仍走同一读取门禁。
                for entry in self._catalog.resources:
                    if entry.load_policy == "task_start":
                        await self._resume.wait()
                        self._check_cancelled()
                        await self._read_resource({"uri": entry.uri})
                        self._check_cancelled()
                await self._design()
        except asyncio.CancelledError:
            self._interrupt.set()
            logger.debug(f"建筑设计已取消：{self.job.task_id}")
            self.settle("cancelled", "建筑设计已取消")
            raise
        except TimeoutError:
            logger.warning(f"建筑设计超过总时限：{self.job.task_id}", exc=True)
            self.settle("timeout", "建筑设计超过总时限")
        except Exception as exc:
            logger.exception(f"建筑设计失败 {self.job.task_id}：{exc}")
            self.settle("failed", str(exc))
        finally:
            await self.stop()

    async def _design(self) -> None:
        """设计历史独立于游戏对话，只有显式交付工具能完成任务。"""
        history: list[dict[str, Any]] = []
        # 起始请求只序列化一次，后续选读的教材追加为工具结果，不回头改写第一条用户消息。
        initial_context = {
            "request": self.job.request.model_dump(exclude={"request_key"}),
            "catalog": self._catalog.model_dump() if self._catalog else {},
            "design_schema": self._schema,
            "selected_resources": self._resources,
        }
        messages = [
            {"role": "system", "content": _INSTRUCTIONS},
            {"role": "user", "content": json_text(initial_context)},
        ]
        compactor = MinecraftHistoryCompactor(
            self._llm, self._config, profile="minecraft_builder", interrupt=self._interrupt
        )
        used_steps = 0
        while used_steps < self._config.max_steps:
            await self._resume.wait()
            self.job.phase = "design"
            specs = self._registry.list_tools(provider=self._provider.name, for_agent=self.name)
            allowed = {spec.full_name for spec in specs}
            messages.extend(history)
            history.clear()
            tools = [
                {"name": spec.full_name, "description": spec.description, "parameters": spec.parameters_schema}
                for spec in sorted(specs, key=lambda item: item.full_name)
            ]
            if context_chars(messages, tools) > self._config.max_context_chars:
                await compactor.compact(
                    messages,
                    tools,
                    {
                        **initial_context,
                        "selected_resources": self._resources,
                        "current_candidate": self._candidate.model_dump(exclude={"design"})
                        if self._candidate
                        else None,
                    },
                    max_attempts=self._config.max_steps - used_steps,
                )
                used_steps += compactor.last_calls
                if used_steps >= self._config.max_steps:
                    break
            await self._resume.wait()
            self._check_cancelled()
            used_steps += 1
            response = await self._llm.generate(
                list(messages),
                profile="minecraft_builder",
                interrupt=self._interrupt,
                omit_output_token_limit=True,
                strict_tool_arguments=True,
                tools=tools,
            )
            await self._resume.wait()
            self._check_cancelled()
            if not response.success:
                raise ValueError(response.error or "建筑设计模型调用失败")
            calls = response.tool_calls or []
            # 只有正常结束且参数完整的一整轮调用才可执行，避免半份模型或补丁改变设计。
            incomplete = ""
            if response.finish_reason not in {"stop", "tool_calls", "function_call"}:
                incomplete = f"模型输出未确认完整结束（结束原因：{response.finish_reason or '未提供'}）"
            elif any(call.arguments_error for call in calls):
                incomplete = "工具参数不是完整合法的 JSON：" + "; ".join(
                    call.arguments_error for call in calls if call.arguments_error
                )
            if incomplete:
                self._candidate = None
                logger.warning(f"建筑设计 {self.job.task_id} 拒绝未完整输出：{incomplete}")
            correction = incomplete + (
                "；本轮所有工具均未执行。请重新输出完整调用，不要补括号后使用半份设计。"
                "输出过长时先创建有效场景，再用具名对象编辑分次提交完整 JSON 调用。"
            )
            if not calls:
                if incomplete:
                    history.extend(
                        [
                            {"role": "assistant", "content": response.content or ""},
                            {"role": "user", "content": correction},
                        ]
                    )
                    continue
                raise ValueError("建造 Agent 未通过 finish 交付已校验设计")
            history.append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": (
                                    call.raw_arguments if call.raw_arguments is not None else json_text(call.arguments)
                                ),
                            },
                        }
                        for call in calls
                    ],
                }
            )
            for call in calls:
                await self._resume.wait()
                if incomplete:
                    observation = {"ok": False, "error": correction}
                elif call.name not in allowed:
                    observation = {"ok": False, "error": "该工具未授予建造 Agent"}
                else:
                    result = await self._registry.invoke(
                        ToolInvocation(tool_name=call.name, arguments=call.arguments, source="minecraft-builder-react")
                    )
                    observation = (
                        result.structured_content if result.success else {"ok": False, "error": result.error_message}
                    )
                history.append({"role": "tool", "tool_call_id": call.id, "content": json_text(observation)})
                if self.job.status in TERMINAL_TASK_STATES:
                    return
        self.settle("failed", "建筑设计达到最大推理步数，尚未完成校验交付")

    async def _read_resource(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """只读取目录中且当前能力支持的资料，同任务重复读取复用已固定内容。"""
        assert self._catalog is not None
        uri = arguments.get("uri")
        entry = next((item for item in self._catalog.resources if item.uri == uri), None)
        if entry is None:
            raise ValueError("资料 URI 不在当前建造目录中")
        if not set(entry.requires).issubset(self._catalog.capabilities):
            raise ValueError("当前 Mod 尚不支持这份资料要求的能力")
        if (
            entry.compatible_schema_revisions
            and self._catalog.design_schema_revision not in entry.compatible_schema_revisions
        ):
            raise ValueError("资料与当前设计 Schema 不兼容")
        already_loaded = entry.uri in self._resources
        if not already_loaded:
            text = await self._backend.read_text(entry.uri)
            if sum(map(len, self._resources.values())) + len(text) > self._config.max_context_chars // 2:
                raise ValueError("已选资料超过任务预算，请使用当前已读取资料")
            self._resources[entry.uri] = text
            self._resource_refs[entry.uri] = entry.revision
        result = {"loaded": True, "uri": entry.uri, "revision": entry.revision, "already_loaded": already_loaded}
        if not already_loaded:
            result["content"] = self._resources[entry.uri]
        else:
            result["content_location"] = "existing_context"
        return result

    async def _validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """每次新草稿先撤销旧候选，校验失败后不能误交付之前那份建筑。"""
        assert self._catalog is not None
        self._candidate = None
        self.job.phase = "validate"
        design = arguments.get("design")
        if not isinstance(design, dict):
            raise ValueError("design 必须是 JSON 对象")
        validation = await self._backend.validate(
            design, self._catalog, self._schema, self.job.request.context, request_key=self.job.task_id
        )
        self._check_cancelled()
        self._accept_design(validation, design)
        return validation

    def _accept_design(self, validation: dict[str, Any], design: dict[str, Any] | None) -> None:
        """场景只在 Mod 真实编译保存成功后成为候选，版本依据必须来自 Mod。"""
        assert self._catalog is not None
        if validation.get("valid") is True:
            if validation.get("design_schema_revision") != self._catalog.design_schema_revision:
                raise ValueError("Mod 校验结果的设计版本不匹配")
            if validation.get("capability_revision") != self._catalog.revision:
                raise ValueError("Mod 校验结果的能力版本不匹配")
            self._candidate = BuildResult(
                artifact_ref=validation.get("artifact_ref"),
                summary="设计已通过 Mod 校验",
                design=design,
                capability_revision=self._catalog.revision,
                design_schema_revision=self._catalog.design_schema_revision,
                validation=validation,
                resource_refs=dict(self._resource_refs),
            )
            self._scene_id = self._candidate.artifact_ref

    async def _update(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """合并编辑和原子保存交给 Mod，失败时旧场景仍存在，但不能误交付为本次修改。"""
        self._candidate = None
        if not isinstance(arguments.get("edits"), dict):
            raise ValueError("edits 必须是完整 JSON 对象")
        if self._catalog is not None and self._catalog.edit_schema_ref:
            errors = validate_schema(self._schema, arguments["edits"], reference=self._catalog.edit_schema_ref)
            if errors:
                return {"valid": False, "stage": "schema", "errors": errors}
        validation = await self._scene_operation("update_scene", {"edits": arguments["edits"]})
        self._accept_design(validation, None)
        return validation

    async def _scene_operation(self, operation: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """操作只能引用当前设计版本，模型不能指定任意游戏操作或重新定位锚点。"""
        if not self._scene_id or self._catalog is None:
            raise ValueError("请先创建并校验场景")
        result = await self._backend.scene_operation(
            operation, self._scene_id, parameters, self._catalog, request_key=self.job.task_id
        )
        self._check_cancelled()
        return result

    async def _inspect(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """查询返回分页标记，完整对象或组件按源名字读取，不把一页摘要当整份设计。"""
        kind = arguments.get("kind")
        if kind not in {"scene", "object", "component"}:
            raise ValueError("只能检查场景、对象或组件")
        parameters = {"page": arguments.get("page", 0)}
        if kind != "scene":
            parameters[f"{kind}_name"] = arguments.get("name", "")
        return await self._scene_operation(f"get_{kind}_info", parameters)

    async def _preview(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """预览只读取已校验产物，不允许提交角色行动。"""
        return await self._scene_operation("preview", {})

    async def _finish(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """交付的是可查设计引用，父级另行决定何时调度角色施工。"""
        self._check_cancelled()
        summary = str(arguments.get("summary", "")).strip()
        if self._candidate is None or not summary:
            raise ValueError("需要已通过校验的设计与非空交付摘要")
        self._candidate.summary = summary
        self.job.result = self._candidate
        self.settle("succeeded", summary)
        return {"delivered": True, "artifact_ref": self._candidate.artifact_ref}

    async def _fail(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """无法继续时留下失败原因，不把普通结束文本当成成功。"""
        reason = str(arguments.get("reason", "")).strip()
        if not reason:
            raise ValueError("失败原因不能为空")
        self.settle("failed", reason)
        return {"failed": True, "reason": reason}
