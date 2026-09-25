"""现有 MaiCraft 四工具协议：设计操作核实终态，施工由父级独立受理。"""

import asyncio
import hashlib
import json
from typing import Any, Protocol

from src.agents.minecraft.builder.config import MinecraftBuilderConfig
from src.agents.minecraft.builder.models import BuildCatalog
from src.agents.minecraft.readback import has_references, read_value


class SceneInvoker(Protocol):
    """复用父 Agent 注册的 MCP 调用，不另建 Mod 连接。"""

    async def __call__(self, name: str, arguments: dict[str, Any], *, source: str) -> dict[str, Any]: ...


def scene_goal(
    operation: str, parameters: dict[str, Any], catalog: BuildCatalog, context: dict[str, Any]
) -> dict[str, Any]:
    """操作由代码确定；场景引用固定锚点，不把父级上下文当成移动场景的权限。"""
    design_operations = {
        "create_scene",
        "update_scene",
        "get_scene_info",
        "get_object_info",
        "get_component_info",
        "preview",
        "export_scene",
    }
    if operation not in design_operations and operation != "build":
        raise ValueError("未授予的建模操作")
    params = {
        **parameters,
        "operation": operation,
        "expected_capability_revision": catalog.revision,
        "expected_design_schema_revision": catalog.design_schema_revision,
    }
    goal: dict[str, Any] = {
        "ability": "maicraft:build" if operation == "build" else "maicraft:design_build",
        "outcome": "执行建筑施工" if operation == "build" else "检查或保存建筑设计",
        "parameters": params,
    }
    if operation == "create_scene":
        goal["target"] = context.get("target", {"kind": "current_place"})
    if operation == "build":
        # 只传递父 Agent 明确声明的施工政策，不允许 context 覆盖设计、操作或版本。
        for name in ("replace_existing", "material_policy", "protected_labels"):
            if name in context:
                params[name] = context[name]
        params.setdefault("replace_existing", False)
    return goal


class MinecraftSceneProtocol:
    """设计也返回受理任务号，必须读取 terminal.result 才能相信场景已保存。"""

    def __init__(self, config: MinecraftBuilderConfig, invoke: SceneInvoker) -> None:
        self._config = config
        self._invoke = invoke

    async def design_operation(
        self,
        operation: str,
        parameters: dict[str, Any],
        catalog: BuildCatalog,
        context: dict[str, Any],
        *,
        request_key: str,
    ) -> dict[str, Any]:
        """同一设计操作重试沿用稳定键，编辑不同版本或不同内容时自动分键。"""
        if operation == "build":
            raise ValueError("设计 Agent 不能发起施工")
        goal = scene_goal(operation, parameters, catalog, context)
        fingerprint = hashlib.sha256(json.dumps(goal, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        receipt = await self._invoke(
            self._config.execute_tool,
            {"goal": goal, "request_key": f"{request_key}:design:{fingerprint}"},
            source="minecraft-builder-react",
        )
        task_id = receipt.get("task_id")
        if receipt.get("accepted") is not True or not isinstance(task_id, str) or not task_id:
            raise ValueError("Mod 未返回设计操作任务号，不能把受理失败当作已保存设计")
        while True:
            snapshot = await self._invoke(
                self._config.task_tool, {"action": "get", "task_id": task_id}, source="minecraft-builder-react"
            )
            if snapshot.get("task_id") != task_id:
                raise ValueError("Mod 返回了其他任务的快照")
            state = snapshot.get("state")
            terminal = snapshot.get("terminal")
            if isinstance(terminal, dict):
                # 当前摘要足够显示进度；设计交付须核对完整版本和施工标记，缺省略值时只读取原任务证据。
                result = (
                    await self._terminal_result(task_id, state) if has_references(terminal) else terminal.get("result")
                )
                if not isinstance(result, dict):
                    raise ValueError("Mod 终态缺少结构化 result")
                if state != "success" or result.get("success") is not True:
                    return {
                        "valid": False,
                        "stage": "mod_compile",
                        "task_id": task_id,
                        "errors": [
                            {"message": result.get("message", "设计操作失败"), "details": result.get("data", {})}
                        ],
                    }
                data = result.get("data")
                if not isinstance(data, dict) or data.get("construction_started") is not False:
                    raise ValueError("设计操作未明确证明没有开始施工")
                if (
                    data.get("capability_revision") != catalog.revision
                    or data.get("design_schema_revision") != catalog.design_schema_revision
                ):
                    raise ValueError("Mod 设计结果缺少匹配的版本依据，需要重新校验")
                if not isinstance(data.get("scene_id"), str) or not data["scene_id"]:
                    raise ValueError("Mod 设计结果没有场景编号")
                coverage = (
                    ["saved_model"]
                    if operation.startswith("get_")
                    else ["model_contract", "voxel_compilation", "block_states"]
                )
                return {
                    **data,
                    "valid": True,
                    "artifact_ref": data["scene_id"],
                    "task_id": task_id,
                    "stage": "scene_inspection" if operation.startswith("get_") else "mod_compile",
                    "coverage": coverage,
                }
            if state not in {"pending", "running"}:
                return {
                    "valid": False,
                    "stage": "mod_compile",
                    "task_id": task_id,
                    "errors": [{"message": f"设计操作未完成：{state}", "details": snapshot}],
                }
            # 等待的是已受理设计操作，整个循环受外层设计任务总预算和取消控制。
            await asyncio.sleep(self._config.operation_poll_interval_ms / 1000)

    async def _terminal_result(self, task_id: str, state: str) -> dict[str, Any]:
        """找回已结束设计的确切结果，分页只走 get，不因丢失上下文再次执行设计或施工。"""

        async def page(path: str, offset: int) -> dict[str, Any]:
            response = await self._invoke(
                self._config.task_tool,
                {"action": "get", "task_id": task_id, "path": path, "offset": offset, "limit": 20},
                source="minecraft-builder-react",
            )
            if (
                response.get("task_id") != task_id
                or response.get("state") != state
                or not isinstance(response.get("detail"), dict)
            ):
                raise ValueError("设计证据不属于同一个已结束任务")
            return response["detail"]

        result = await read_value(page, "/terminal/result")
        if not isinstance(result, dict):
            raise ValueError("设计任务没有完整的结果对象")
        return result
