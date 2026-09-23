"""父 Agent 拥有的设计任务入口与会话结果，串行调度角色施工。"""

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

from src.agents.minecraft.builder.agent import MinecraftBuilderAgent
from src.agents.minecraft.builder.backend import MinecraftBuilderBackend, ResourceClient
from src.agents.minecraft.builder.config import MinecraftBuilderConfig
from src.agents.minecraft.builder.models import BuildJob, BuildRequest
from src.agents.minecraft.builder.tools import MinecraftBuilderToolProvider, object_schema
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.logging import get_logger
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TERMINAL_TASK_STATES, TaskTracker

logger = get_logger("MinecraftBuilderController")


class MinecraftBuilderController:
    """一个活跃设计占用一个子 Agent；已结束任务留在当前游戏会话。"""

    def __init__(
        self,
        config: MinecraftBuilderConfig,
        *,
        llm: Any,
        registry: ToolRegistry,
        tracker: TaskTracker | None,
        client: Callable[[], ResourceClient | None],
        parent_task: Callable[[], str],
    ) -> None:
        self._config = config
        self._llm = llm
        self._registry = registry
        self._tracker = tracker
        self._parent_task = parent_task
        self._backend = MinecraftBuilderBackend(config, registry, client)
        self._jobs: dict[str, BuildJob] = {}
        self._active: MinecraftBuilderAgent | None = None
        self._worker: asyncio.Task[None] | None = None
        self._accepting = False
        self._paused = False
        self._lock = asyncio.Lock()
        self.provider = MinecraftBuilderToolProvider("minecraft_builder", {"minecraft-react"})
        self._request_spec = self.provider.add(
            "request",
            "异步委派建筑设计并立即返回任务号。intent=build 要求最终施工；design 只交付设计。",
            BuildRequest.model_json_schema(),
            self.request,
        )
        self.provider.add(
            "task",
            "读取设计状态、修改要求、取消或由 Minecraft 发起施工。设计成功不表示建筑已完成。",
            object_schema(
                {
                    "task_id": {"type": "string"},
                    "action": {"type": "string", "enum": ["status", "revise", "cancel", "execute"]},
                    "requirements": {"type": "string", "description": "revise 时补充的修改要求"},
                },
                ["task_id", "action"],
            ),
            self.manage,
        )

    def open(self) -> None:
        """父 Agent 启动时仅开放受理，尚不创建子 Agent 或读取资料。"""
        self._accepting = True

    def owns_tool(self, full_name: str) -> bool:
        """这些回执已在本域正确登记，父级不能再次当作 provider 任务追踪。"""
        return any(spec.full_name == full_name for spec in self.provider.list_tools())

    async def request(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """受理不等待 LLM 或 MCP；相同请求键重试返回原任务。"""
        async with self._lock:
            return self._submit(BuildRequest.model_validate(arguments))

    def _submit(self, request: BuildRequest, *, revision: int = 1) -> dict[str, Any]:
        """先登记账本再调度协程，保证极速完成也不会丢失受理关系。"""
        if not self._accepting or self._paused:
            raise ValueError("Minecraft 已停止或暂停，暂不接收建造设计")
        if self._tracker is None or self._llm is None:
            raise ValueError("建造设计需要模型与任务跟踪设施")
        if request.request_key:
            previous = next(
                (job for job in self._jobs.values() if job.request.request_key == request.request_key), None
            )
            if previous is not None:
                if previous.request != request:
                    raise ValueError("同一 request_key 不能用于不同设计要求")
                return self._receipt(previous)
        if self._worker is not None and not self._worker.done():
            raise ValueError(f"已有活跃设计 {self._active.job.task_id if self._active else ''}，请查询或修改该任务")
        self._prune()
        job = BuildJob(
            task_id=f"builder_{uuid.uuid4().hex}",
            request=request,
            parent_task_id=self._parent_task(),
            request_revision=revision,
        )
        self._jobs[job.task_id] = job
        self._tracker.ledger.register(
            task_id=job.task_id,
            provider=self.provider.name,
            tool=self._request_spec.full_name,
            initiator="minecraft",
            executor="minecraft_builder",
            source="agent",
            snapshot=job.snapshot(),
        )
        child = MinecraftBuilderAgent(
            job,
            self._config,
            llm=self._llm,
            registry=self._registry,
            ledger=self._tracker.ledger,
            backend=self._backend,
        )
        self._active = child
        self._worker = asyncio.create_task(self._run_child(child), name=job.task_id)
        return self._receipt(job)

    async def _run_child(self, child: MinecraftBuilderAgent) -> None:
        """任务结束后丢弃子 Agent 及教材历史，只在父会话中保留设计产物。"""
        try:
            await child.run()
        finally:
            if self._active is child:
                self._active = None

    @staticmethod
    def _receipt(job: BuildJob) -> dict[str, Any]:
        """重复受理仍可附带当前状态，但不会再次启动设计。"""
        return {"accepted": True, "task_id": job.task_id, "executor": "minecraft_builder", "status": job.status}

    async def manage(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """控制动作串行执行，避免修改、取消和施工同时操作同一设计。"""
        async with self._lock:
            if not self._accepting:
                raise ValueError("Minecraft 已停止")
            job = self._jobs.get(str(arguments.get("task_id", "")))
            if job is None:
                raise ValueError("当前会话没有该建造任务")
            action = arguments.get("action")
            if action == "status":
                return job.snapshot()
            if self._paused and action != "cancel":
                raise ValueError("Minecraft 已暂停，只能查询设计状态")
            if action == "execute":
                return await self._execute(job)
            if action not in {"cancel", "revise"}:
                raise ValueError("未知建造任务动作")
            if job.execution_status not in {"not_started", "cancelled", "failed", "timeout"}:
                raise ValueError("施工已开始或结果未明，请先通过 Mod 任务工具取消并核实，不能替换设计")
            if action == "revise" and (job.dismissed or job.superseded_by):
                raise ValueError("该请求已经取消或被新设计替代")
            requirements = str(arguments.get("requirements", "")).strip()
            if action == "revise" and not requirements:
                raise ValueError("修改设计需要补充 requirements")
            await self._cancel_worker(job)
            if action == "cancel":
                job.dismissed = True
                return job.snapshot()
            context = dict(job.request.context)
            if job.result is not None:
                # 修改使用 Mod 中的不可变场景，不把整栋 JSON 再交给父模型复制。
                context["previous_scene_id"] = job.result.artifact_ref
                anchor = job.result.validation.get("anchor")
                if isinstance(anchor, dict):
                    context["target"] = {"kind": "coordinates", "position": anchor}
            request = job.request.model_copy(
                update={
                    "requirements": f"{job.request.requirements}\n修改要求：{requirements}",
                    "context": context,
                    "request_key": "",
                }
            )
            receipt = self._submit(request, revision=job.request_revision + 1)
            job.superseded_by = receipt["task_id"]
            return receipt

    async def _execute(self, job: BuildJob) -> dict[str, Any]:
        """父级通过引用发起施工，不要求游戏模型复制设计 JSON。"""
        if job.dismissed or job.superseded_by or job.status != "succeeded" or job.request.intent != "build":
            raise ValueError("只能施工当前有效且要求建好的已交付设计")
        if job.execution_task_id:
            return {
                "accepted": True,
                "task_id": job.execution_task_id,
                "design_task_id": job.task_id,
                "status": job.execution_status,
            }
        assert self._tracker is not None
        # 一个角色一次只施工一份设计；可以并行设计，但不能让第二份施工抢占身体。
        busy = next(
            (
                other
                for other in self._jobs.values()
                if other is not job
                and other.execution_status
                not in {
                    "not_started",
                    "succeeded",
                    "failed",
                    "cancelled",
                    "timeout",
                }
            ),
            None,
        )
        if busy is not None:
            raise ValueError(f"另一份设计仍在施工或受理结果未明：{busy.task_id}，请先处理该施工任务")
        spec, payload = await self._backend.execute(job)
        job.execution_task_id = payload["task_id"]
        job.execution_status = "accepted"
        self._tracker.track(
            task_id=job.execution_task_id, provider=spec.provider, tool=spec.full_name, initiator="minecraft"
        )
        return {**payload, "design_task_id": job.task_id}

    def absorb(self, payload: TaskChangedPayload) -> None:
        """施工终态来自 Mod 的真实任务快照，不接受设计模型声称已经建成。"""
        for job in self._jobs.values():
            if job.execution_task_id == payload.task_id:
                job.execution_status = payload.status

    def pending_ids(self) -> set[str]:
        """设计完成后继续保留施工义务，供父 Agent 的交付门禁检查。"""
        return {task_id for job in self._jobs.values() if (task_id := job.pending_id())}

    def actionable_ids(self) -> set[str]:
        """设计完成但未开工、设计失败或施工待决策都需要父级处理，不能当作仍在后台运行。"""
        return {
            pending
            for job in self._jobs.values()
            if (pending := job.pending_id())
            and (
                job.status in {"failed", "timeout", "waiting_for_decision"}
                or job.status == "succeeded"
                and job.request.intent == "build"
                and job.execution_status not in {"accepted", "running"}
            )
        }

    def set_paused(self, paused: bool) -> None:
        """父子共享暂停边界，不创建额外轮询协程。"""
        self._paused = paused
        if self._active is not None:
            self._active.set_paused(paused)

    async def _cancel_worker(self, job: BuildJob) -> None:
        """取消到达首个调度点之前也必须写回终态，避免账本永久挂起。"""
        child, worker = self._active, self._worker
        if child is None or child.job is not job or worker is None:
            return
        if not worker.done():
            child.request_cancel()
            worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            logger.debug(f"建筑设计协程已收束：{job.task_id}")
        finally:
            child.settle("cancelled", "建筑设计已取消")
            self._active = None
            self._worker = None

    async def close(self) -> None:
        """父级先关闭受理并收束设计，随后才可释放共享 MCP。"""
        self._accepting = False
        async with self._lock:
            if self._active is not None:
                job = self._active.job
                await self._cancel_worker(job)
                job.dismissed = True

    def _prune(self) -> None:
        """只清理已交付或明确取消的历史，不驱逐仍承担施工义务的设计。"""
        finished = [
            key for key, job in self._jobs.items() if job.status in TERMINAL_TASK_STATES and not job.pending_id()
        ]
        for key in finished[: max(0, len(finished) - self._config.retained_jobs + 1)]:
            del self._jobs[key]
