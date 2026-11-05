"""测试用的 MaiCraft v1 替身：五个工具的统一回复、目标运行与事件流，行为照真实接口写。

本模块同时是 MinecraftAgent 测试的场景工厂单入口：``assemble_minecraft_agent`` 构造
未启动的 Agent（离线缺省装配），``make_minecraft_agent`` 在其上装配 MaiCraft 替身并启动
（直播中），全部 MinecraftAgent 测试经这两处拿实例，不各自手工装配。
"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from unittest.mock import AsyncMock, MagicMock

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.modules.events.payloads.game import GamePayload
from src.modules.llm.payload import Response, ToolCall
from src.modules.mcp.config import McpServerConfig
from src.modules.skills import SkillLibrary
from src.modules.tools.models import ResultBlock, ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.registry import ToolRegistry

# 只改记忆、只读分析的能力当场完成，其余在后台推进。
_ASIDE_ABILITIES = frozenset({"maicraft:remember"})

# observe(picture) 回的那张图（假 JPEG 的 base64）：够长，测试据此确认它没进历史和事件
PICTURE_BASE64 = base64.b64encode(bytes([0xFF, 0xD8, 0xFF]) + b"minecraft-picture" * 200).decode("ascii")

# make_minecraft_agent 的 llm 参数哨兵：显式传 None（不注入模型）与未传（按剧本建替身）要区分开
_LLM_UNSET = object()

# 离线私有 MCP 配置：不连真端口（本机正开着 MaiCraft 时，测试也不能连上真实的 8766）。
OFFLINE_MCP_CONFIG = McpServerConfig(enabled=False, url="http://127.0.0.1:8766/mcp")


def offline_config(events_wait_ms: Optional[int] = None) -> MinecraftConfig:
    """不连私有 MCP 的 Agent 配置；长轮询等待时长可调（事件流替身按它等）。"""
    kwargs: Dict[str, Any] = {"mcp": OFFLINE_MCP_CONFIG}
    if events_wait_ms is not None:
        kwargs["events_wait_ms"] = events_wait_ms
    return MinecraftConfig(**kwargs)


def fake_bus() -> MagicMock:
    """捕获 emit 的桩总线（emit 是 AsyncMock，断言读 await_args_list）。"""
    bus = MagicMock()
    bus.emit = AsyncMock()
    return bus


def ok(data: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, "data": data}


def error(code: str, message: str) -> Dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _resp(content: str = "", calls: Optional[List[ToolCall]] = None) -> Response:
    return Response(success=True, content=content, tool_calls=calls or [])


def _call(name: str, arguments: Dict[str, Any], call_id: str = "c1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


def _texts(messages: List[Dict[str, Any]]) -> str:
    return "\n".join(str(message.get("content") or "") for message in messages)


def _called(messages: List[Dict[str, Any]], tool: str) -> bool:
    """模型在这段历史里调用过这个工具没有（开局资料是宿主消息，不含工具调用）。"""
    return any(
        call["function"]["name"] == tool
        for message in messages
        if message.get("role") == "assistant"
        for call in message.get("tool_calls") or []
    )


async def _wait_until(condition: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if condition():
            return
        await asyncio.sleep(0.02)
    raise TimeoutError("条件在时限内未成立")


def _reports(bus: MagicMock) -> List[GamePayload]:
    return [
        call.args[1]
        for call in bus.emit.await_args_list
        if isinstance(call.args[1], GamePayload) and call.args[1].event_type == "report"
    ]


class FakeMaicraft(BaseToolProvider):
    """MaiCraft v1 的替身：observe / lookup / execute / task / events。

    目标运行按真实接口走：要动手的目标返回 running，测试用 ``finish`` / ``ask`` / ``pause``
    推进它，同时往事件流里追加对应事件；事件流按游标分页，没有新事件时等一小会儿再空手返回。
    """

    category = "mcp"
    name = "maicraft"

    def __init__(self) -> None:
        self.goals: Dict[int, Dict[str, Any]] = {}
        # 结束了的目标里折起来的试过的办法：默认结果里不给，按路径 /attempts 读回。
        self.folded_attempts: Dict[int, List[Dict[str, Any]]] = {}
        self.events: List[Dict[str, Any]] = []
        self.stream_id = "stream-1"
        self.calls: List[tuple[str, Dict[str, Any]]] = []
        # 角色所在的存档：reports_world 为 False 时扮演旧版 Mod（回复里没有 world 键）
        self.reports_world = False
        self.world: Optional[Dict[str, Any]] = None
        self._streams = 1
        self.self_view: Dict[str, Any] = {
            "position": {"x": 3.6, "y": 64.0, "z": -7.2, "dimension": "minecraft:overworld"},
            "facing": "南",
            "health": 18,
            "food": 15,
            "air": 300,
            "max_air": 300,
            "held": "minecraft:stone_axe",
            "armor": [],
            "effects": [],
            "inventory": [{"item": "minecraft:oak_log", "count": 6}],
            "control": "automation",
            "goal": {"doing": "空闲，没有主任务"},
        }
        self.scene_view: Dict[str, Any] = {
            "time": "白天",
            "weather": "晴",
            "biome": "minecraft:forest",
            "summary": "前方：e1（minecraft:cow，6格）",
            "entities": [
                {"id": "e1", "type": "minecraft:cow", "direction": "前方", "distance": 6, "hostile": False},
                {"id": "e2", "type": "minecraft:creeper", "direction": "后方", "distance": 90, "hostile": True},
                {
                    "id": "e3",
                    "type": "minecraft:player",
                    "name": "Steve",
                    "direction": "左侧",
                    "distance": 40,
                    "hostile": False,
                },
            ],
            # 远处不相干的生物，Mod 只按种类报只数
            "distant_entities": {"minecraft:bat": 12, "minecraft:squid": 30},
            "facilities": [{"id": "b1", "block": "minecraft:chest", "direction": "左侧", "distance": 4}],
        }
        # observe(world_memory)：记住的地点、圈的地盘、容器等记录；默认什么都没记过。
        # world_memory_error 非空时这一项读失败（如角色不在世界里），原因就是这句话
        self.world_memory_view: Dict[str, Any] = {"places": [], "regions": [], "records": []}
        self.world_memory_error = ""
        # observe(picture)：和真实 Mod 一样回两个内容块——JSON 文字在前、JPEG 图在后（base64 不进 JSON）
        self.picture_view: Dict[str, Any] = {
            "picture": {"width": 1280, "height": 720, "format": "image/jpeg", "labeled": True, "menu_included": False},
            "camera": {"direction": "前方", "compass": "北", "pitch": "平视", "first_person": True},
            "in_picture": [
                {"id": "b1", "block": "minecraft:chest", "at": [214, 560], "labeled": True},
                {"id": "e1", "type": "minecraft:cow", "at": [902, 410], "labeled": True},
                {
                    "id": "e9",
                    "type": "minecraft:item",
                    "at": [930, 600],
                    "labeled": False,
                    "not_labeled_because": "太挤",
                },
            ],
            "summary": "画面里看得见 3 个编号，标了 2 个。",
        }
        self.picture_image = PICTURE_BASE64
        self._next_id = 1
        self._new_event = asyncio.Event()

    # 工具清单与分发

    def list_tools(self) -> List[ToolSpec]:
        return [
            ToolSpec(
                name=name, description=name, parameters_schema={"type": "object"}, kind="sync", provider="maicraft"
            )
            for name in ("observe", "lookup", "execute", "goal", "events")
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        name = invocation.tool_name.removeprefix("maicraft_")
        arguments = dict(invocation.arguments or {})
        self.calls.append((name, arguments))
        reply = await getattr(self, f"_{name}")(arguments)
        if name == "observe" and arguments.get("what") == "picture" and reply["ok"]:
            # 真实 MCP 结果有两个内容块时宿主拿不到结构化结果，只有文字正文与内容块（宿主自己解析正文里的 JSON）
            text = json.dumps(reply, ensure_ascii=False)
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=True,
                content=text,
                blocks=[
                    ResultBlock(kind="text", text=text),
                    ResultBlock(kind="image", data=self.picture_image, mime_type="image/jpeg"),
                ],
            )
        if reply["ok"]:
            return ToolExecutionResult(tool_name=invocation.tool_name, success=True, structured_content=reply)
        return ToolExecutionResult(
            tool_name=invocation.tool_name,
            success=False,
            structured_content=reply,
            error_message=reply["error"]["message"],
            failure_kind="business",
        )

    def calls_to(self, name: str) -> List[Dict[str, Any]]:
        return [arguments for called, arguments in self.calls if called == name]

    # 五个工具

    async def _observe(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        what = arguments.get("what", "scene")
        if what == "picture":
            return ok(dict(self.picture_view))
        if what == "world_memory":
            if self.world_memory_error:
                return error("not_in_world", self.world_memory_error)
            return ok(dict(self.world_memory_view))
        view = dict(self.self_view if what == "self" else self.scene_view)
        if what == "self" and self.reports_world:
            view["world"] = self.world
        return ok(view)

    async def _lookup(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return ok({"abilities": [{"ability": "maicraft:gather"}, {"ability": "maicraft:remember"}]})

    async def _execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        goal = arguments.get("goal") or {}
        ability = str(goal.get("ability") or "")
        ability = ability if ":" in ability else f"maicraft:{ability}"
        goal_id = self._next_id
        self._next_id += 1
        run: Dict[str, Any] = {"goal_id": goal_id, "ability": ability, "state": "running"}
        if goal.get("purpose"):
            run["purpose"] = goal["purpose"]
        if ability in _ASIDE_ABILITIES:
            run["state"] = "finished"
            run["result"] = {"status": "done", "summary": "记住了「家」", "changes": [], "remaining": []}
        self.goals[goal_id] = run
        return ok(dict(run))

    async def _goal(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        operation = arguments.get("operation")
        if operation == "list":
            return ok({"goals": [dict(run) for run in self.goals.values()]})
        run = self.goals.get(arguments.get("goal_id"))
        if run is None:
            return error("unknown_id", f"没有编号为 {arguments.get('goal_id')} 的目标")
        if operation == "get" and arguments.get("path"):
            return self._read_folded(run, str(arguments["path"]))
        if operation == "answer":
            run.pop("question", None)
            run["state"] = "running"
            self._append("resumed", run["goal_id"], "收到回答")
        elif operation == "resume":
            run["state"] = "running"
        elif operation == "cancel":
            run["state"] = "finished"
            run["result"] = {"status": "cancelled", "summary": "目标被取消", "changes": [], "remaining": []}
        return ok(dict(run))

    def _read_folded(self, run: Dict[str, Any], path: str) -> Dict[str, Any]:
        """按路径读结束了的目标里折起来的一段：和真实接口一样，没结束、路径不对都按参数问题回。"""
        goal_id = run["goal_id"]
        if run.get("state") != "finished":
            return error("invalid_parameter", f"目标 {goal_id} 还没结束，没有结果可按路径读")
        attempts = self.folded_attempts.get(goal_id)
        if path != "/attempts" or attempts is None:
            return error("invalid_parameter", f"结果里没有 {path}")
        return ok(
            {"goal_id": goal_id, "path": path, "type": "list", "value": attempts, "total": len(attempts), "offset": 0}
        )

    async def _events(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        expected = arguments.get("stream_id")
        after = int(arguments.get("after_cursor") or 0)
        status = "valid"
        if expected is not None and expected != self.stream_id:
            status, after = "stream_changed", 0
        wait_s = int(arguments.get("wait_ms") or 0) / 1000
        if wait_s and not any(event["cursor"] > after for event in self.events):
            self._new_event.clear()
            # 真实服务端最多等 wait_ms，有新事件立刻返回；测试里最多等 1.5 秒，比宿主的空转下限长。
            with_timeout = min(wait_s, 1.5)
            try:
                await asyncio.wait_for(self._new_event.wait(), timeout=with_timeout)
            except asyncio.TimeoutError:
                pass
        page = [event for event in self.events if event["cursor"] > after]
        cursor = page[-1]["cursor"] if page else (self.events[-1]["cursor"] if self.events else 0)
        cursor = max(cursor, after) if status == "valid" else cursor
        data = {
            "stream_id": self.stream_id,
            "cursor": cursor,
            "has_more": False,
            "cursor_status": status,
            "events": page,
        }
        if self.reports_world:
            data["world"] = self.world
        return ok(data)

    # 测试推进目标

    def _append(
        self, kind: str, goal_id: int, message: str, status: Optional[str] = None, self_brief: Optional[str] = None
    ) -> None:
        event: Dict[str, Any] = {"cursor": len(self.events) + 1, "kind": kind, "goal_id": goal_id, "message": message}
        if status:
            event["status"] = status
        if self_brief:
            event["self_brief"] = self_brief
        self.events.append(event)
        self._new_event.set()

    def finish(
        self,
        goal_id: int,
        status: str = "done",
        summary: str = "砍了 5 块原木",
        *,
        self_brief: Optional[str] = None,
        attempts: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """目标结束：结果写进运行表、发 finished 事件。

        ``self_brief`` 是结束那一刻角色的一行自身摘要，和真实接口一样只在事件上；
        ``attempts`` 给了就像真实接口那样折起来：默认结果里只留 ``folded`` 一项，按路径 ``/attempts`` 读回。
        """
        run = self.goals[goal_id]
        run["state"] = "finished"
        run.pop("question", None)
        run["result"] = {
            "status": status,
            "summary": summary,
            "changes": [{"kind": "item_gained", "what": "minecraft:oak_log", "count": 5}],
            "remaining": [],
        }
        if attempts:
            self.folded_attempts[goal_id] = attempts
            run["result"]["folded"] = [
                {
                    "path": "/attempts",
                    "type": "list",
                    "items": len(attempts),
                    "chars": len(json.dumps(attempts, ensure_ascii=False)),
                }
            ]
        self._append("finished", goal_id, summary, status, self_brief)

    def ask(self, goal_id: int, text: str = "动哪个箱子？") -> None:
        run = self.goals[goal_id]
        run["state"] = "awaiting_answer"
        run["question"] = {
            "reason": "choose_one",
            "text": text,
            "options": [{"id": "b5", "meaning": "门口那个"}, {"id": "b6", "meaning": "屋里那个"}],
        }
        self._append("asked", goal_id, text)

    def pause(self, goal_id: int) -> None:
        self.goals[goal_id]["state"] = "paused"
        self._append("paused", goal_id, "暂停")

    def body_event(self, kind: str, message: str, fields: Optional[Dict[str, Any]] = None) -> None:
        """发一条与目标无关的身体事件；``fields`` 是事件自带的结构化字段（如 others_food_taken 的明细）。"""
        self._append(kind, -1, message)
        if fields:
            self.events[-1].update(fields)

    def die(self) -> int:
        """角色死了：和 MaiCraft v1 一样先发 character_died，再挂一条死亡恢复决策（编号是负数）并发 asked。"""
        decision_id = -1 - sum(1 for goal_id in self.goals if goal_id < 0)
        self._append("character_died", -1, "角色死了，等重生；插着的任务已结束，主任务停在原地，重生后接着做")
        question = {
            "reason": "choose_one",
            "text": "角色死了。接下来怎么办？",
            "options": [
                {"id": "respawn", "meaning": "发原版重生请求，回出生点或床，任务原地接着做"},
                {"id": "cancel_goal", "meaning": "取消当前目标，死亡屏幕留给人处理"},
            ],
        }
        self.goals[decision_id] = {
            "goal_id": decision_id,
            "ability": "death_recovery",
            "state": "awaiting_answer",
            "question": question,
        }
        self._append("asked", decision_id, question["text"])
        return decision_id

    def enter_world(self, world_id: str, name: str = "") -> None:
        """进了某个存档（Mod 认出了世界）：事件流换新编号，每页与读自己都带上这个存档。"""
        self.reports_world = True
        self.world = {"id": world_id, "name": name, "source": "world_id_file"}
        self._streams += 1
        self.stream_id = f"stream-{self._streams}"
        self._new_event.set()

    def leave_world(self) -> None:
        """退回主菜单：world 为 null，事件流不换。"""
        self.reports_world = True
        self.world = None
        self._new_event.set()

    def change_world(self) -> None:
        """换了世界：事件流换新编号，旧目标都不在了。"""
        self.stream_id = "stream-2"
        self.goals.clear()
        self.events.clear()
        # 每个世界的目标编号各自从 1 数起，可能撞上上一个世界的编号。
        self._next_id = 1
        self._new_event.set()


# ---------------------------------------------------------------------------
# 场景工厂：MinecraftAgent 测试的装配单入口
# ---------------------------------------------------------------------------

# 按剧本回复的模型剧本：每次推理把当时的消息交给剧本，剧本返回模型响应
LlmScript = Callable[[List[Dict[str, Any]]], Response]


class ScriptLlm:
    """按剧本回复的模型替身：记下推理次数与每次看到的消息、拿到的工具清单。

    ``visual`` 决定主模型能不能看图；``vision_reply`` 是视觉模型（看图转文字）的回答，
    每次转文字的问题记在 ``vision_prompts``。回答为空串时按视觉模型没转出文字处理。
    """

    def __init__(self, script: LlmScript, *, visual: bool = False, vision_reply: str = "") -> None:
        self.script = script
        self.visual = visual
        self.vision_reply = vision_reply
        self.seen: List[List[Dict[str, Any]]] = []
        self.tools_given: List[List[Dict[str, Any]]] = []
        self.vision_prompts: List[str] = []

    async def generate(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Response:
        self.seen.append([dict(message) for message in messages])
        self.tools_given.append(list(kwargs.get("tools") or []))
        # 真实的模型调用总要等网络、让出事件循环；替身也让出一次，与推理并行的后台任务（如后台折叠）才推进得了
        await asyncio.sleep(0)
        return self.script(messages)

    def is_profile_visual(self, profile: Optional[str]) -> bool:
        """能力查询替身：按构造时给的 ``visual``（缺省非视觉）。"""
        del profile
        return self.visual

    async def generate_vision(self, prompt: str, images: List[Any], **kwargs: Any) -> Response:
        """视觉模型替身：记下问题，回 ``vision_reply``；为空串时回失败。"""
        del images, kwargs
        self.vision_prompts.append(prompt)
        if not self.vision_reply:
            return Response(success=False, content="", error="视觉模型没配")
        return Response(success=True, content=self.vision_reply)

    def get_profile_context_window(self, profile: Optional[str]) -> int:
        """窗口查询替身：剧本场景按未配置（0），预算走保守窗口。"""
        del profile
        return 0


def assemble_minecraft_agent(
    *,
    config: Optional[MinecraftConfig] = None,
    llm: Optional[Any] = None,
    bus: Optional[Any] = None,
    registry: Optional[ToolRegistry] = None,
    tracker: Optional[Any] = None,
    skills: Optional[SkillLibrary] = None,
    prompt_manager: Optional[Any] = None,
    thinking_sink: Optional[Any] = None,
    live: Optional[bool] = None,
    mcp_client: Optional[Any] = None,
    mcp_provider: Optional[Any] = None,
    mcp_client_factory: Optional[Callable[..., Any]] = None,
    mcp_provider_factory: Optional[Callable[..., Any]] = None,
    clock: Optional[Callable[[], float]] = None,
    creature_repo: Optional[Any] = None,
) -> MinecraftAgent:
    """构造未启动的 MinecraftAgent（装配单入口）。

    缺省装配一律离线：不连私有 MCP、总线是捕获 emit 的桩、无 LLM。要调任务循环的
    测试自行传 ``llm``；``live`` 是直播闸初始态（None = 生产默认，启动落收工态）；
    ``clock`` 是注入假时钟（返回单调时刻），不传用系统单调时钟。
    """
    return MinecraftAgent(
        config if config is not None else offline_config(),
        llm_manager=llm,
        prompt_manager=prompt_manager,
        event_bus=bus if bus is not None else fake_bus(),
        tool_registry=registry,
        thinking_sink=thinking_sink,
        task_tracker=tracker,
        skill_library=skills,
        clock=clock,
        live_active=live,
        mcp_client=mcp_client,
        mcp_provider=mcp_provider,
        mcp_client_factory=mcp_client_factory,
        mcp_provider_factory=mcp_provider_factory,
        creature_repo=creature_repo,
    )


async def make_minecraft_agent(
    server: Optional[FakeMaicraft] = None,
    script: Optional[LlmScript] = None,
    *,
    tracker: Optional[Any] = None,
    skills: Optional[SkillLibrary] = None,
    registry: Optional[ToolRegistry] = None,
    live: bool = True,
    llm: Any = _LLM_UNSET,
    bus: Optional[Any] = None,
    thinking_sink: Optional[Any] = None,
    config: Optional[MinecraftConfig] = None,
    mcp_client: Optional[Any] = None,
    clock: Optional[Callable[[], float]] = None,
    creature_repo: Optional[Any] = None,
) -> Tuple[MinecraftAgent, Any, Any]:
    """装配并启动一个接上 MaiCraft 替身的玩家（直播中），返回 ``(agent, llm, bus)``。

    替身 ``server`` 的 spec 自带 minecraft 名单（与 MaicraftMcpProvider.list_tools
    盖名单同语义）；私有 MCP 不连真端口——客户端给已连接替身，provider 用替身 server
    本身。模型用剧本 ``script`` 回复（或直接注入 ``llm`` 替身；显式传 ``llm=None`` =
    不注入模型，任务失败 fast-fail）。``clock`` 是注入假时钟，透传给装配入口。
    """
    if registry is None:
        registry = ToolRegistry()
    if server is not None:
        for spec in server.list_tools():
            spec.visible_to = ["minecraft"]
        registry.register_provider(server)
    if llm is _LLM_UNSET:
        llm = ScriptLlm(script if script is not None else (lambda messages: Response(success=True, content="好")))
    bus = bus if bus is not None else fake_bus()
    agent = assemble_minecraft_agent(
        config=config if config is not None else offline_config(events_wait_ms=1000),
        llm=llm,
        bus=bus,
        registry=registry,
        tracker=tracker,
        skills=skills,
        thinking_sink=thinking_sink,
        live=live,
        clock=clock,
        # 有 MaiCraft 替身才给已连接的客户端替身；否则与旧装配一致（游戏未连）
        mcp_client=(mcp_client if mcp_client is not None else SimpleNamespace(connected=True))
        if server is not None or mcp_client is not None
        else None,
        mcp_provider=server,
        creature_repo=creature_repo,
    )
    await agent.start()
    return agent, llm, bus


# ---------------------------------------------------------------------------
# 场景工厂：身边有女仆、羊的 observe(scene)（名册测试与换存档端到端测试共用）
# ---------------------------------------------------------------------------

MAID_SKIN = {"skin_id": "touhou_little_maid:winefox_momo", "skin_name": "莫莫酒狐"}


def maid_entity(scene_id: str = "e3", distance: int = 2, owner: str = "你的") -> Dict[str, Any]:
    """一只女仆实体（默认是角色自己的、在身后两格）。"""
    return {
        "id": scene_id,
        "type": "touhou_little_maid:maid",
        "direction": "后方",
        "distance": distance,
        "hostile": False,
        "traits": {"驯服": owner},
    }


def scene_with(
    *entities: Dict[str, Any], maids: Sequence[Dict[str, Any]] = (), graves: Sequence[Dict[str, Any]] = ()
) -> Dict[str, Any]:
    """拼一份 observe(scene)：实体列表，加女仆模组的女仆与墓碑附加信息。"""
    return {
        "entities": list(entities),
        "mod_things": {"maid": list(maids), "maid_tombstone": list(graves)},
    }


def scene_with_maid() -> Dict[str, Any]:
    """身边一只自己的女仆（莫莫酒狐皮肤）和一只报了 UUID 的羊。"""
    # 游戏给这只羊报了 UUID（挂过命名牌或被驯服过的才有），起名认得出是它
    sheep = {"id": "e1", "type": "minecraft:sheep", "uuid": "a1", "distance": 12, "hostile": False}
    return scene_with(maid_entity(), sheep, maids=[{"id": "e3", **MAID_SKIN}])
