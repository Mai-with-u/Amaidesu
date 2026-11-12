"""MinecraftAgent 局部工具（minecraft_report / minecraft_todo /
minecraft_notebook / minecraft_get_work_log / minecraft_glance /
minecraft_context_fold / minecraft_context_recall）

工具归属约定（text_adv 同构）：
- 局部工具（minecraft_todo / minecraft_notebook）= Agent 自己 LLM 用，驱动 ReAct 循环
  ——全量读写文档，无 id
- 对外工作文档读取（minecraft_get_work_log）= 主播经 ToolRegistry 调，只读——叙事素材通道
- 主播看一眼（minecraft_glance）= 主播经 ToolRegistry 调，只读——身体现状与周边的精简叙事视图，
  代替原始观察（原始观察的几何与证据是给游戏 Agent 规划用的）
- 跨 Agent 派活走框架委派原语（framework_delegate；原 minecraft_send_prompt
  已退役，职能并入 Agent 的接收委派入口）——主播是玩家 Agent 的用户：
  派发/调整任务、回答问题、补充要求；系统不代写 todo（目标分解是 LLM 用
  minecraft_todo 自己做的事）
- 局部上报（minecraft_report）= 玩家 Agent LLM 用：向主播交付总结（delivery）/
  升级决策（escalation）——玩家→主播唯一发声出口
- 上下文折叠（minecraft_context_fold / minecraft_context_recall）= 玩家 Agent LLM 用：
  折叠只在宿主的后台整理请求里由宿主解析执行，行动中调用一律不执行；取回读被折叠或收起的原文
- provider="minecraft"（来源溯源：提供者=游戏 Agent 名全称，禁缩写）

对外全名由 ``ToolSpec.full_name`` 派生（``minecraft_<工具名>``，provider 即模块名），
工具名里不手写前缀。

全量读写设计（对齐内部 todo 工具 todowrite / edit 模式）：
- read：返回当前文档全文
- write：提交新文档全文（覆盖），无 id、无增删改查逐条 API
- LLM 拿 read 全文 → 改 → write 覆盖 = 批量修改
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, ClassVar, Dict, Iterable, List, Optional

from src.modules.agents.game_tools import GameToolProvider, run_dict_tool
from src.modules.logging import get_logger
from src.modules.mcp.provider import McpToolProvider
from src.modules.skills import build_skill_spec
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolSpec,
)
from src.modules.types.game_save import GameSave
from src.modules.types.game_situation import GameSituation

from .maicraft import GOAL, MaicraftReply, goal_id_of_ledger, goal_run_of, ledger_status_of
from .state import MAX_NOTEBOOK_CHARS, MinecraftAgentState
from .work_log_snapshot import unavailable_snapshot

logger = get_logger("MinecraftTools")

# 提供者标识统一来源（ToolSpec.provider / 追溯用），避免字面量重复
PROVIDER_NAME = "minecraft"

# 工具面使用说明（给主播的玩法）：Minecraft 是委派型游戏——主播交代目标，游戏里的
# 身体自己拆解动手、做完或卡住再上报；主播只拿两件只读工具看现场与工作文档。
STREAMER_GUIDE = (
    "Minecraft 是委派型游戏：交代一个目标（framework_delegate）就是安排主播在游戏里开始做一件事，"
    "游戏里的身体自己拆解、动手，做完或卡住再以游戏记录上报；中途要改要求用 framework_prompt。"
    "想看一眼现场（身体状态、背包、身边的生物与设施）用 minecraft_glance；"
    "想看待办、笔记和近期上报用 minecraft_get_work_log。"
    "工作文档里笔记正文后面带着一段现查的当前状态快照（位置、装备、背包、血量）："
    "背包存量、装备、位置以快照为准，笔记正文里的同类数字只是当时记下的历史，别当现状念。"
    "速览里的 creatures 记着同伴（自己的生物、起过名的生物）此刻怎样：写着已死的就是死了，"
    "别再跟她说话、别再说她在身后；墓碑写着谁的就是谁的，不是陌生人。"
    "速览里的 self_account 是你在游戏里的账号名：聊天、死亡播报、玩家列表里出现这个名字说的就是你自己，不是另一个玩家。"
    # 记地点：观众或玩家说"记一下传送门坐标" -> 交给游戏里用 remember 记成命名地点 -> 当场先答应 ->
    # 看到"已把「名字」记在"的回报才说记好了。这一笔按存档写进游戏存盘，她自己的记忆留不住一串坐标。
    "观众或游戏里的玩家让你记一下某个地方在哪、记下坐标时，交给游戏里去记："
    "手上没事用 framework_delegate；手上的事没做完就用 framework_prompt 补一句顺手记下，"
    "记地点当场就记完、不打断手上的活，不要为它撤掉手上的事。"
    "交代里用「」写上对方的叫法当名字（如「下界传送门」），对方给了坐标就把坐标原样写进去，没给就记你此刻站的地方。"
    "同一轮里先交代、再用 streamer_reply 口头接一句（“好，我记一下”）；"
    "等游戏记录的交付、或手上那件事的进展里写着已把「某某」记在某处（或本来就这样记着），才说记好了，"
    "没看到这句之前不说已经记好。记下的地点存在游戏存档里，下播、重启都不丢。"
    "速览里的 remembered_places 是已经记住的地点名（只列几个）：被问到某个地方在哪，先看这里，"
    "记过就说记过，要具体位置或要过去就交代游戏里按这个名字去查、去那里；"
    "写着“等共几处”是没列全，没列出来的可能也记过，交代游戏里查；"
    "速览里没有这一项、游戏记录里也没出现过记好的回报，就是还没记过，别说记过。"
    # 调试面板：画面左上角刷字的面板是运营开着看后台的 -> 不是她按出来的、不是故障 -> 不当话题 ->
    # 观众问起才简单解释。不写明的话，面板每次刷出来她都当头一回看见，换着说法（满屏绿字、调试界面、坐标被公开）反复吐槽
    "直播画面左上角那块一直往上刷字的面板（英文日志、绿色调试信息，里面可能滚着账号名和坐标）"
    "是 MaiCraft 的调试面板，运营开着看后台用的，不是她按出来的，也不是游戏出了故障："
    "不拿它当话题、不吐槽它挡画面，观众问起再简单说一句是后台在看运行情况。"
    "方向键、选项这类逐步操作在这款游戏里不存在，所有游戏内行动都走委派。"
)

# 上报回调形态：kind/content/scene 入参 → 拒绝原因（None=受理）。
# 拒绝语义（交付门禁）由 Agent 侧判定——工具层只透传结果。
ReportCallback = Callable[[str, str, str], Awaitable[Optional[str]]]

# 局部工具可见名单（按声明名）：本地件只有 minecraft 自己可见；get_work_log
# 与 glance 是主播的读服务。list_tools 逐 spec 盖到 ``visible_to`` 字段。
_LOCAL_VISIBLE_TO: Dict[str, List[str]] = {
    "todo": ["minecraft"],
    "notebook": ["minecraft"],
    "report": ["minecraft"],
    "get_work_log": ["streamer"],
    "glance": ["streamer"],
    "skill": ["minecraft"],
    "context_fold": ["minecraft"],
    "context_recall": ["minecraft"],
}

# 行动中调用折叠工具时的回执：折叠只在宿主的后台整理请求里执行，行动路径上写摘要会让角色站着等
_FOLD_REFUSED = {
    "ok": False,
    "error": "折叠由宿主在后台安排，行动中不执行；这一步继续推进任务，需要旧细节用 minecraft_context_recall 读",
}


# ---------------------------------------------------------------------------
# ToolSpec 工厂
# ---------------------------------------------------------------------------


def build_todo_spec() -> ToolSpec:
    """``minecraft_todo`` 工具规格——待办文档（全量读写，驱动 ReAct 循环）"""
    return ToolSpec(
        name="todo",
        description=(
            "待办文档（全量读写，无 id）。管理 Minecraft 玩家自己的目标与进度："
            "read 返回当前 todo 全文；write 提交新全文（覆盖）。"
            "任务的分解与推进由你自己决定：把收到的上级指令分解为任务，"
            "逐项推进并标记 done。刚读写过且没有变化时复用已有全文，只在缺少当前内容时 read。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write"],
                    "description": "read=返回全文；write=全量覆盖",
                },
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "任务内容"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "done"],
                                "description": "任务状态",
                            },
                        },
                        "required": ["content"],
                    },
                    "description": "write 时的新 todo 全文（覆盖旧文档）",
                },
            },
            "required": ["action"],
        },
        kind="sync",
        provider=PROVIDER_NAME,
        output_schema={
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {"type": "string"},
                        },
                    },
                }
            },
        },
    )


def build_notebook_spec() -> ToolSpec:
    """``minecraft_notebook`` 工具规格——工作笔记（按存档跨重启持久化，上限 MAX_NOTEBOOK_CHARS）"""
    return ToolSpec(
        name="notebook",
        description=(
            f"工作笔记文档（全量读写，无 id，上限 {MAX_NOTEBOOK_CHARS} 字符，按存档跨重启保留）。"
            "记录这个存档里后续还用得上的关键结论（工程进度/配方结论/已知限制），"
            "对话历史会被压缩、笔记不会——重要发现写这里，写满时删掉已完成或失效的旧条目。"
            "只写跨批次之后还站得住的信息（计划、承诺、起过的名字、约定、结论这类）；"
            "背包里现在有多少、身上穿的装备、身体此刻在哪、血量多少这类当下会变的数字不写进笔记，"
            "要这些当场 observe 一次就有。"
            "要长期记住的地点（家、基地、传送门）一律用 maicraft 的 remember 记成命名地点。"
            "只写短条目，不复述历史经过、不抄回执原文。"
            "read 返回全文；write 提交新全文（覆盖）。按需使用，不每轮强制。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write"],
                    "description": "read=返回全文；write=全量覆盖",
                },
                "content": {"type": "string", "description": "write 时的新笔记全文"},
            },
            "required": ["action"],
        },
        kind="sync",
        provider=PROVIDER_NAME,
        output_schema={"type": "object", "properties": {"content": {"type": "string"}}},
    )


def build_report_spec() -> ToolSpec:
    """``minecraft_report`` 工具规格——上报通道（玩家→主播，交付/升级）"""
    return ToolSpec(
        name="report",
        description=(
            "向主播上报（玩家→主播唯一发声出口）。两种："
            "kind=delivery 交付总结——本次目标要的结果真的达成时必发一次（中途不发）；"
            "拿别的东西顶替、只做了一部分都不算交付。"
            "kind=escalation 升级决策——至少试过两种实质不同的办法仍不行，或缺关键信息/需授权时发；"
            "content 写清试过什么、各自为什么不行、下一步打算和需要主播定什么。"
            "绝大多数困难自己解决（换路线/换策略/先做前置），不打扰主播。发完 escalation 后停止行动，静默等待主播回复。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["delivery", "escalation"],
                    "description": "delivery=交付总结；escalation=升级决策（发后停止等待）",
                },
                "content": {
                    "type": "string",
                    # 角色被保护挡住时，主播会照上报原话讲给观众、再去公屏问人：上报里一旦把"记录说是别人的"
                    # 说成"被人钉死"，主播就会冤枉在场帮忙的玩家，而归属记录本身还可能把自家东西判成别人的，
                    # 所以这里要求只转述记录、记录没写是谁就不点名
                    "description": (
                        "主播要念给观众听的几句话，用第一人称：我做成了什么、没做成什么、卡在哪、需要定什么；不写“主播让我”“收到委派”“任务号”这类分工说法。"
                        "坐标、观察编号、证据清单和操作流水不写进来；要长期记住的地点用 remember 记，"
                        "其余需要留存的记进 notebook。"
                        "被保护挡住时归属照结果转述（“记录说这是别人放的”），结果没写是谁就不点名，"
                        "不说“被人钉死”“给堵上了”这类替别人编的动作"
                    ),
                },
                "scene": {"type": "string", "description": "可选场景补充（坐标/区块等上下文）"},
            },
            "required": ["kind", "content"],
        },
        kind="sync",
        provider=PROVIDER_NAME,
        output_schema={
            "type": "object",
            "properties": {
                "reported": {"type": "boolean"},
                "kind": {"type": "string"},
            },
        },
    )


def build_get_work_log_spec() -> ToolSpec:
    """``minecraft_get_work_log`` 工具规格——工作文档读服务（状态通道，主播只读调用）"""
    return ToolSpec(
        name="get_work_log",
        description=(
            "只读查询 Minecraft 玩家的工作文档："
            "todo（待办与进度）/ notebook（工作笔记）/ "
            "recent_reports（近期上报：交付与升级记录）。"
            "面向直播叙事——主播不需要自己记录游戏细节，按需查询即可。"
            "笔记正文后面附着一段现查的当前状态快照（位置/装备/背包/血量）："
            "背包存量、装备、位置以快照为准，笔记正文里的同类数字一律当历史参考。"
            "本工具不查异步任务记录表；查任务进度用 framework_task_status。"
        ),
        parameters_schema={"type": "object", "properties": {}, "required": []},
        kind="sync",
        provider=PROVIDER_NAME,
        output_schema={
            "type": "object",
            "properties": {
                "todo": {"type": "array"},
                "notebook": {"type": "string"},
                "recent_reports": {"type": "array"},
            },
        },
    )


def build_glance_spec() -> ToolSpec:
    """``minecraft_glance`` 工具规格——主播看一眼游戏（只读，精简叙事视图）"""
    return ToolSpec(
        name="glance",
        description=(
            "看一眼游戏里的自己：身体状态（维度/位置/血量/饥饿/手上拿的/护甲/效果）、背包物品与数量、"
            "时段天气、身边的生物与设施、远处的生物（按种类报只数，敌对的不列）、"
            "记住的地点名（remembered_places，只列几个），"
            "以及身体手头的工作（待办、正在跑的游戏内目标）。"
            "只读、即时，面向直播叙事；更细的勘查、判断或任何操作都交给委派，不要自己琢磨方块坐标。"
        ),
        parameters_schema={"type": "object", "properties": {}, "required": []},
        kind="sync",
        provider=PROVIDER_NAME,
        output_schema={
            "type": "object",
            "properties": {
                "body": {"type": "object"},
                "inventory": {"type": "array"},
                "entities": {"type": "array"},
                "distant_entities": {"type": "array"},
                "facilities": {"type": "array"},
                "remembered_places": {"type": "string"},
                "work": {"type": "object"},
            },
        },
    )


def build_context_fold_spec() -> ToolSpec:
    """``minecraft_context_fold`` 工具规格——只在宿主整理请求里用：把用完的历史区间折成摘要"""
    return ToolSpec(
        name="context_fold",
        provider=PROVIDER_NAME,
        kind="sync",
        description=(
            "只在收到【宿主整理请求】时使用：把已经用完的历史区间折成摘要，原文留在宿主那里，"
            "之后可用 minecraft_context_recall 取回。行动中不要调用，调了也不执行。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "folds": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "start": {
                                "type": "string",
                                "description": "起点的消息编号（如 m0012）或旧折叠块号（如 b3）",
                            },
                            "end": {"type": "string", "description": "终点的消息编号或旧折叠块号（含这一条）"},
                            "summary": {"type": "string", "minLength": 1, "description": "替代这段原文的摘要"},
                        },
                        "required": ["start", "end", "summary"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["folds"],
            "additionalProperties": False,
        },
    )


def build_context_recall_spec() -> ToolSpec:
    """``minecraft_context_recall`` 工具规格——读回被折叠或收起的历史原文"""
    return ToolSpec(
        name="context_recall",
        provider=PROVIDER_NAME,
        kind="sync",
        description=(
            "读回被折叠或收起的历史原文（历史里写着“已折叠 b3”或“原文已收起 b5”的那些）。"
            "block 填块号读整块原文；query 填关键词（坐标、编号、物品名）在所有块里查，只返回命中的片段；"
            "两个都填只在这一块里查。摘要够用就不必读。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "block": {"type": "string", "description": "块号，如 b3"},
                "query": {"type": "string", "description": "要找的关键词"},
            },
            "additionalProperties": False,
        },
    )


# ---------------------------------------------------------------------------
# Provider（注册到 ToolRegistry）
# ---------------------------------------------------------------------------


class MaicraftMcpProvider(McpToolProvider):
    """MaiCraft 私有 MCP Provider：补目标任务的查询适配器，激活任务跟踪循环的周期兜底。

    通用 MCP 查询适配器按"server 侧有任务查询工具"的形态约定（``task_query_tool``
    + ``task_status_map``）声明；MaiCraft v1 没有那种工具，任务快照经
    ``goal(get)`` 读、账本编号要先还原成目标编号——形态对不上，所以在绑定处
    用子类就地适配：``goal_lookup`` 由 Agent 注入（即宿主自己的 MaiCraft 读取通道）。

    可见名单：全部工具只给 minecraft 自己（主播看游戏走 minecraft_glance 精简
    视图），在 ``list_tools`` 逐 spec 盖 ``visible_to``——server 侧清单刷新
    （重连换血）后新工具同样经此路径携带名单。
    """

    def list_tools(self) -> Iterable[ToolSpec]:
        """缓存清单 + 逐 spec 盖 minecraft 名单（server 侧声明不携带名单）。"""
        specs = list(super().list_tools())
        for spec in specs:
            spec.visible_to = ["minecraft"]
        return specs

    def __init__(
        self, *, goal_lookup: Callable[[str, Dict[str, Any]], Awaitable[MaicraftReply]], **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._goal_lookup = goal_lookup

    async def query_task(self, task_id: str) -> Optional[dict]:
        """按账本编号查目标快照：``maicraft-goal-N`` 还原成目标编号后调 ``goal(get)``。

        连接失败、目标查不到或回复读不懂一律返回 ``None``（跟踪循环按无新事实
        处理，不据此改账）。
        """
        goal_id = goal_id_of_ledger(task_id)
        if goal_id is None:
            return None
        reply = await self._goal_lookup(GOAL, {"operation": "get", "goal_id": goal_id})
        if not reply.ok:
            return None
        run = goal_run_of(reply.data)
        if run is None:
            return None
        return {"status": ledger_status_of(run), "snapshot": run.data, "summary": run.summary}


@dataclass(slots=True)
class MinecraftToolProvider(GameToolProvider):
    """MinecraftAgent 局部工具 Provider

    持有 :class:`MinecraftAgentState`，把"文档式工具"映射为状态读写；
    ``report_callback`` 由 Agent 注入——minecraft_report 上报经它发射事件并做
    交付门禁校验（返回拒绝原因字符串；None=受理）。
    分发、计时与异常转结果走共享管道（:class:`GameToolProvider`）：处理器
    返回结构化 dict，经 ``run_dict_tool`` 包装（``ValueError`` 按业务拒绝降级
    ——参数写错由玩家纠正，不能把自己的工具熔断摘除）。
    """

    # 日志前缀（共享管道的异常日志区分用）
    log_label: ClassVar[str] = "Minecraft"

    state: MinecraftAgentState
    report_callback: Optional[ReportCallback] = None
    # 主播看一眼：由 Agent 读观察并精简成叙事视图（未注入时不提供该工具）
    glance_reader: Optional[Callable[[], Awaitable[Dict[str, Any]]]] = None
    # 按名读技能正文：由 Agent 按自己的受众名筛本地策略，远端玩法技能按地址去 MaiCraft 读（未注入技能库时不提供该工具）；
    # 远端技能要经 MCP 的 resources/read 取正文，所以读取器是异步的。
    skill_reader: Optional[Callable[[str], Awaitable[Dict[str, Any]]]] = None
    # 角色眼下在哪个存档里：由 Agent 从 MaiCraft 报的世界身份读（未注入时按不分存档）
    save_reader: Optional[Callable[[], Optional[GameSave]]] = None
    # 角色眼下离哪些记住的地点多远：由 Agent 从速览已有的缓存现算（未注入时不报情境）
    situation_reader: Optional[Callable[[], Optional[GameSituation]]] = None
    # 读工作文档时附一段现查的当前状态快照：由 Agent 现查一次身体现状（未注入时按不可用处理）
    snapshot_reader: Optional[Callable[[], Awaitable[str]]] = None
    # 取回被折叠或收起的原文：由 Agent 按块号或关键词查自己的块库（未开后台折叠时不提供折叠与取回两个工具）
    context_recall: Optional[Callable[[str, str], Dict[str, Any]]] = None
    # 待办或工作笔记写入后的持久化回调（按游戏与当前存档落库）
    on_work_docs_changed: Optional[Callable[[], Awaitable[None]]] = None

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    def current_save(self) -> Optional[GameSave]:
        """眼下的存档（主播按它分清记忆里哪些是这个存档的事）；旧版 Mod 报不出存档时为 None。"""
        return self.save_reader() if self.save_reader is not None else None

    def current_situation(self) -> Optional[GameSituation]:
        """眼下离哪些记住的地点多远（主播据此判断是不是走到了某个地点跟前）；读缓存，不发请求。"""
        return self.situation_reader() if self.situation_reader is not None else None

    def list_tools(self) -> Iterable[ToolSpec]:
        specs = [
            build_todo_spec(),
            build_notebook_spec(),
            build_get_work_log_spec(),
            build_report_spec(),
        ]
        if self.glance_reader is not None:
            specs.append(build_glance_spec())
        if self.skill_reader is not None:
            specs.append(build_skill_spec(PROVIDER_NAME))
        if self.context_recall is not None:
            specs.extend([build_context_fold_spec(), build_context_recall_spec()])
        # 逐 spec 盖可见名单（技能工具未注入读取器时本就不提供，名单映射无残留键）
        for spec in specs:
            spec.visible_to = list(_LOCAL_VISIBLE_TO[spec.name])
        return specs

    def guide_for(self, agent_name: str) -> str:
        """玩法说明只给别的 Agent（主播）：自己的工具协议写在自己的系统提示词里。"""
        return "" if agent_name == PROVIDER_NAME else STREAMER_GUIDE

    def unknown_tool_reason(self, tool_name: str) -> str:
        """未知工具的回执：点名本 provider 的名册，给调用方出路提示。"""
        return f"未知工具: '{tool_name}'（MinecraftProvider 只提供 todo/notebook/get_work_log/report）"

    def tool_handlers(self) -> Dict[str, Any]:
        """对外全名 → 处理器（随 list_tools 的条件清单走，dict 经 run_dict_tool 包装）。"""
        handlers: Dict[str, Any] = {
            build_todo_spec().full_name: self._dict_handler(build_todo_spec(), self._invoke_todo),
            build_notebook_spec().full_name: self._dict_handler(build_notebook_spec(), self._invoke_notebook),
            build_get_work_log_spec().full_name: self._dict_handler(
                build_get_work_log_spec(), self._invoke_get_work_log
            ),
            build_report_spec().full_name: self._dict_handler(build_report_spec(), self._invoke_report),
        }
        if self.glance_reader is not None:
            glance_spec = build_glance_spec()
            handlers[glance_spec.full_name] = self._dict_handler(glance_spec, self._invoke_glance)
        if self.skill_reader is not None:
            skill_spec = build_skill_spec(PROVIDER_NAME)
            handlers[skill_spec.full_name] = self._dict_handler(skill_spec, self._invoke_skill)
        if self.context_recall is not None:
            fold_spec, recall_spec = build_context_fold_spec(), build_context_recall_spec()
            handlers[fold_spec.full_name] = self._dict_handler(fold_spec, self._invoke_context_fold)
            handlers[recall_spec.full_name] = self._dict_handler(recall_spec, self._invoke_context_recall)
        return handlers

    def _dict_handler(
        self, spec: ToolSpec, run: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]
    ) -> Callable[..., Any]:
        """把返回 dict 的处理器包装成共享管道签名（异常口径见 ``run_dict_tool``）。

        结果的 ``tool_name`` 回填对外全名，与调用方使用的名字一致。
        """

        async def handle(args: Dict[str, Any], started_ms: int) -> ToolExecutionResult:
            return await run_dict_tool(
                spec.full_name,
                started_ms,
                lambda: run(args),
                log_label=self.log_label,
            )

        return handle

    async def _invoke_glance(self, args: Dict[str, Any]) -> Dict[str, Any]:
        del args
        assert self.glance_reader is not None  # 处理器表按读取器在册建表
        return await self.glance_reader()

    async def _invoke_skill(self, args: Dict[str, Any]) -> Dict[str, Any]:
        assert self.skill_reader is not None  # 处理器表按读取器在册建表
        return await self.skill_reader(str(args.get("name", "")))

    async def _invoke_context_fold(self, args: Dict[str, Any]) -> Dict[str, Any]:
        del args
        return dict(_FOLD_REFUSED)

    async def _invoke_context_recall(self, args: Dict[str, Any]) -> Dict[str, Any]:
        assert self.context_recall is not None  # 处理器表按回调在册建表
        return self.context_recall(str(args.get("block", "") or ""), str(args.get("query", "") or ""))

    async def _invoke_todo(self, args: Dict[str, Any]) -> Dict[str, Any]:
        action = str(args.get("action", ""))
        if action == "read":
            return {"tool": "todo", **self.state.todo_doc()}
        if action == "write":
            todos = args.get("todos")
            if not isinstance(todos, list):
                return {"success": False, "error": "write 需要 todos 列表"}
            self.state.set_todos(todos)
            if self.on_work_docs_changed is not None:
                await self.on_work_docs_changed()
            return {"success": True, "todos": self.state.todo_doc()["todos"]}
        return {"success": False, "error": f"未知 action: {action!r}"}

    async def _invoke_notebook(self, args: Dict[str, Any]) -> Dict[str, Any]:
        action = str(args.get("action", ""))
        if action == "read":
            return {"tool": "notebook", **self.state.notebook_doc()}
        if action == "write":
            content = str(args.get("content", ""))
            if len(content) > MAX_NOTEBOOK_CHARS:
                return {
                    "success": False,
                    "error": (
                        f"工作笔记超过 {MAX_NOTEBOOK_CHARS} 字符上限（本次提交 {len(content)} 字符）；"
                        "请删掉已完成或失效的旧条目、只保留精简结论后再写"
                    ),
                }
            self.state.set_notebook(content)
            if self.on_work_docs_changed is not None:
                await self.on_work_docs_changed()
            return {"success": True, "content": self.state.notebook}
        return {"success": False, "error": f"未知 action: {action!r}"}

    async def _invoke_get_work_log(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """工作文档 + 现查的当前状态快照。

        快照现查一次就够：笔记里的背包、装备、位置、血量是模型写下那一刻的旧数字，
        读文档的人以快照为准。
        """
        docs = self.state.to_dict()
        snapshot = await self._read_snapshot()
        notebook = str(docs.get("notebook") or "")
        docs["notebook"] = f"{notebook}\n\n{snapshot}" if notebook else snapshot
        return {"tool": "get_work_log", **docs}

    async def _read_snapshot(self) -> str:
        """现查一次身体现状写成快照段；查不到就写明当前不可用，不中断读取。"""
        if self.snapshot_reader is None:
            return unavailable_snapshot()
        try:
            return await self.snapshot_reader()
        except Exception as exc:  # noqa: BLE001 - 快照失败不该挡住工作文档的读取
            logger.warning(f"当前状态快照读取失败: {type(exc).__name__}: {exc}")
            return unavailable_snapshot(f"读取失败：{exc}")

    async def _invoke_report(self, args: Dict[str, Any]) -> Dict[str, Any]:
        kind = str(args.get("kind", ""))
        if kind not in ("delivery", "escalation"):
            return {"success": False, "error": "kind 必须是 delivery 或 escalation"}
        content = str(args.get("content", "")).strip()
        if not content:
            return {"success": False, "error": "report 需要 content 字符串"}
        scene = str(args.get("scene", ""))
        if self.report_callback is not None:
            rejection = await self.report_callback(kind, content, scene)
            if rejection:
                return {"success": False, "error": rejection, "kind": kind}
        else:
            # 降级仅记录（无 Agent 注入时上报不发射事件，供脱离 Agent 单测使用）
            logger.warning("minecraft_report 无 report_callback：上报未发射事件")
        return {"success": True, "reported": True, "kind": kind}


__all__ = [
    "MinecraftToolProvider",
    "MaicraftMcpProvider",
    "build_todo_spec",
    "build_notebook_spec",
    "build_get_work_log_spec",
    "build_glance_spec",
    "build_report_spec",
    "PROVIDER_NAME",
    "ReportCallback",
]
