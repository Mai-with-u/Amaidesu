"""streamer_memory_correct —— 场内更正与更新记忆的工具（跟随当场主控动态开放）。

给当场主控（主播规划器或当前自主主控游戏 Agent）一个场内实时纠错与同步出口：对记忆摘要里的
某一行档案（``档#N``）或某条惦记的承诺（``约#N``）标记 ``done``（做完了）/ ``progress``（本场有进展
但尚未了结，仅适用于承诺）/ ``invalid``（不成立了）/ ``wrong``（记错了），并附本场证据：
- ``game_receipt``：本场委派终态摘要、游戏事件（含物品交付回执，如存进对方箱子或朝对方扔出）或速览原文的逐字片段；
- ``player_chat``：本场玩家在游戏公屏所说原文的逐字片段（挂外键，删玩家随之级联删除）；
- ``operator``：本场运营提醒原文的逐字片段；
- ``self_seen``：自己在游戏画面或速览里亲眼确认的事实（当场生效，跨场整理时仍须台词或记录佐证）。

观众弹幕一律不收作场内更正依据（回执提示观众的话进跨场记忆须经人工批准）。
更正只在本场生效：档案行当场附注"（本场更正：……）"，承诺 ``progress`` 保持未了结并记录本场进展，
``done / invalid / wrong`` 当场标了结或作废（记来源，人工可撤），下播整理把本场更正作为必须处理的输入。
"""

from __future__ import annotations

import re
import unicodedata
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    ClassVar,
    Dict,
    Iterable,
    List,
    Literal,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from src.agents.streamer.memory.refs import ARCHIVE as _REF_KIND_ARCHIVE
from src.agents.streamer.memory.refs import parse_strict
from src.agents.streamer.memory.textmatch import verbatim_quote
from src.modules.logging import get_logger
from src.modules.storage.repos.memory_corrections import CORRECTION_VERDICT_LABELS, format_correction_note
from src.modules.tools import ToolInvocation, ToolSpec
from src.modules.tools.models import ToolExecutionResult
from src.modules.tools.provider import BaseToolProvider

if TYPE_CHECKING:
    from src.modules.storage.repos import (
        ArchiveRepo,
        ChatRepo,
        CommitmentRepo,
        EventRepo,
        GamePlayerRepo,
        MemoryCorrectionRepo,
    )

__all__ = [
    "MemoryCorrectionToolProvider",
    "build_memory_correct_tool_spec",
    "parse_memory_target",
]

_TOOL_NAME = "memory_correct"
_PROVIDER = "streamer"
_SUMMARY_MAX_CHARS = 60
_QUOTE_MAX_CHARS = 80
_RECORD_SCAN_LIMIT = 500

_VALID_VERDICTS = ("done", "progress", "invalid", "wrong")
_VALID_EVIDENCE_KINDS = ("game_receipt", "player_chat", "operator", "self_seen")

# 语义核验前剥离的通用状态/动作/时间/元描述短语，防止“已经做完了”等通用状态句被当成具体主题词冲突
_GENERIC_CORRECTION_PHRASES: Tuple[str, ...] = tuple(
    sorted(
        (
            "已经全部完成",
            "全部整理完成",
            "已经整理完成",
            "已经做完了",
            "已经弄好了",
            "已经搞定了",
            "已经还清了",
            "已经完成了",
            "已经取消了",
            "已经放弃了",
            "全部完成",
            "整理完成",
            "本场更正",
            "本场进展",
            "游戏回执",
            "物品交付",
            "运营提醒",
            "玩家公屏",
            "不再需要",
            "不用再做",
            "不用做了",
            "不做了",
            "不成立了",
            "不成立",
            "记错了",
            "已完成",
            "做完了",
            "弄好了",
            "搞定了",
            "还清了",
            "交付了",
            "存进了",
            "扔出了",
            "推进了",
            "一部分",
            "一半",
            "全部",
            "全都",
            "已经",
            "刚才",
            "今天",
            "现在",
            "目前",
            "本场",
            "下次",
            "上次",
            "以后",
            "之后",
            "之前",
            "大家",
            "我们",
            "自己",
            "玩家",
            "观众",
            "同伴",
            "对方",
            "承诺",
            "约定",
            "答应",
            "计划",
            "目标",
            "情况",
            "现状",
            "进展",
            "进度",
            "结果",
            "问题",
            "事情",
            "东西",
            "需要",
            "可以",
            "不会",
            "没有",
            "不是",
            "不用",
            "不再",
            "取消",
            "放弃",
            "作废",
            "确认",
            "核实",
            "发现",
            "看到",
            "说到",
            "提到",
            "告诉",
            "帮我",
            "帮你",
            "一起",
            "重新",
            "继续",
            "开始",
            "结束",
            "完成",
            "成功",
            "失败",
            "整理",
            "处理",
            "解决",
            "兑现",
            "了结",
            "结案",
            "归还",
            "运送",
            "补还",
            "当面",
            "剩余",
            "还欠",
            "还有",
            "还在",
            "暂时",
            "先记",
            "明天",
            "后天",
            "昨天",
            "今晚",
            "明晚",
            "昨晚",
            "改天",
            "下回",
            "下周",
            "周末",
            "这周",
            "下场",
            "这场",
            "上场",
            "过会儿",
            "等会儿",
            "待会儿",
            "一会儿",
            "一会",
            "回头",
            "顺手",
            "顺便",
            "接着",
            "办妥",
            "妥了",
            "妥当",
            "妥善",
            "很满意",
            "满意",
            "没问题",
            "可以了",
            "差不多",
            "特别棒",
            "很开心",
            "高兴",
            "感谢",
            "谢谢",
            "顺利",
            "挺好",
            "很好",
            "不错",
            "完美",
            "彻底",
            "完全",
            "基本",
            "大致",
            "大概",
            "其实",
            "确实",
            "的确",
            "真的",
            "本来",
            "原本",
            "最后",
            "最终",
            "只好",
            "决定",
            "打算",
            "准备",
            "商量",
            "沟通",
            "同意",
            "撤回",
            "撤销",
            "作罢",
            "收尾",
            "收工",
            "完工",
            "竣工",
            "省心",
            "花了",
            "花费",
            "半小时",
            "效果",
            "台词",
            "队友",
            "朋友",
            "好友",
            "人家",
            "别人",
            "大伙",
            "各位",
            "主播",
            "运营",
        ),
        key=len,
        reverse=True,
    )
)
_GENERIC_PHRASES_RE = re.compile("|".join(re.escape(p) for p in _GENERIC_CORRECTION_PHRASES))
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
# 仅保留含至少一个字母的英文/混合标识（如玩家名、物品/模组缩写），排除纯数字防止“3 颗钻石”撞上“3 棵树苗”
_ALNUM_WORD_RE = re.compile(r"(?=[a-z0-9_]*[a-z])[a-z0-9_]{2,}", re.IGNORECASE)
# 单字实词核对时排除的虚词、时间副词、评价词、通用动词与宽泛后缀（避免“树场”与“农场”仅凭“场”或“地”误匹配）
_NON_DISTINCTIVE_CJK_CHARS: frozenset[str] = frozenset(
    "的了是在有和与把被给对从往到让叫说听看去来要做弄搞干办完好成毕清掉住过着起出进回上下前后左右"
    "大小多少一二三四五六七八九十百千万两几每各全半这那哪谁他她它你我咱已刚正还再又也都就才只不"
    "没未无非别请能会要可该想打帮找拿放摆收存扔送取带开关闭点修补建盖搭立装拆挖采砍种铺围砌移搬"
    "换改试查核确证记写用玩吗呢吧啊呀啦哦喔噢嘛嘞很真太挺超极最更较略稍颇蛮仍尚且却竟虽但而因所"
    "若如则便顺先明昨今过晚早暂临妥满意齐活停搁撤罢接续随场地区处边口头里外中内点站台院家屋房室"
    "门路道物事品具材件套批堆排组个只条块张次些位名号行线面层段颗根座扇把份"
)
# 无 target_title 时的正向实体主题字集：仅当 summary + verified_quote 出现具体游戏/直播实体词或英文标识
# 且与目标条目零重合时才判跨表错号，避免普通时间副词、口语评价词触发假阳性
_CONCRETE_ENTITY_CHARS: frozenset[str] = frozenset(
    "桥墙门床屋房棚圈仓库塔亭楼阁殿堂井灯梯窗栏栅篱碑墓坛堡垒村城岛洞坑沟渠坝堤河海山林田园圃坡"
    "谷溪湖潭池泉港湾矿窑灶木板砖瓦石岩沙土泥煤炭铁金铜银钢锌铅钻玉晶玻璃釉陶瓷纸墨笔书卷画琴鼓"
    "笛旗牌钟铃炉锅碗杯盘瓶罐箱桶柜桌椅架盆缸绳链钉锤钳剪刀剑镐斧锹弓弩箭矛盾甲盔靴袍帽鞋鞘翅船"
    "车轨帆桨锚网竿钩药水火冰雪雷电树苗槽菜农麦瓜莓豆菇菌竹藤苔花草羊牛猪鸡鸭鹅猫狗狼马驴熊狐兔"
    "鹿龟蛙鱼蜂蛛龙怪骨皮毛羽蛋肉奶糖粉饼糕酒茶歌曲诗舞戏剧影棋谜"
)
# 全局作废理由提示词：verdict=invalid 且未填 target_title 时，这类世界级变更对任意本地条目均合理
_GLOBAL_INVALIDATION_HINTS: Tuple[str, ...] = (
    "新世界",
    "换世界",
    "世界重置",
    "地形重置",
    "换档",
    "重开",
    "删档",
    "换服",
    "新存档",
    "旧存档",
    "旧世界",
    "已重置",
    "服务器重置",
)

_DESCRIPTION = (
    "场内更正或更新一条本端记忆摘要里的记忆（档#N）或本端惦记的承诺（约#N；跨端约#N 来自其他分支同步，不能用此工具结案）："
    "标明做完了（done）、推进了一部分但尚未了结（progress，仅适用于 约#N）、"
    "不成立了（invalid，如换了新世界或地形重置）或记错了（wrong），"
    "并给本场证据（游戏回执——含物品交付回执如存进对方箱子或朝对方扔出、"
    "游戏玩家游戏公屏的话、运营提醒，或你自己亲眼看到的画面/速览）。"
    "建议同时填写 target_title 摘抄目标条目的核心词句以核对编号，防看串或跨表误关。"
    "当场生效：本场后续立即同步最新状态；观众弹幕不能直接当依据。"
)

_PARAMETERS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "target": {
            "type": "string",
            "description": "要更正的本端记忆摘要或本地承诺账本条目编号，写成 档#N 或 约#N（不接受 跨端约#N）",
        },
        "target_title": {
            "type": "string",
            "description": "核对用（推荐填写）：摘抄该 档#N 或 约#N 原文里的核心词句，用于校验编号与目标条目一致",
        },
        "verdict": {
            "type": "string",
            "enum": list(_VALID_VERDICTS),
            "description": (
                "done 做完了；progress 本场推进了一部分但尚未了结（仅适用于 约#N，保持 open）；"
                "invalid 不成立了（世界重置、墓碑消失等）；wrong 当初记错了"
            ),
        },
        "summary": {
            "type": "string",
            "description": "一句话说明现在是什么情况（60 字以内，不带评价词）",
        },
        "evidence_kind": {
            "type": "string",
            "enum": list(_VALID_EVIDENCE_KINDS),
            "description": (
                "证据来源：game_receipt 游戏交付/物品交付回执（如存进对方箱子或朝对方扔出）/事件/速览回执；"
                "player_chat 游戏玩家游戏公屏的话；operator 运营提醒；self_seen 你在画面或速览里亲眼确认的现状"
            ),
        },
        "evidence_quote": {
            "type": "string",
            "description": (
                "依据里的逐字片段（80 字以内；game_receipt / player_chat / operator 必填，"
                "如物品交付回执里的存物或扔物原文、事件摘要或玩家游戏公屏原话）"
            ),
        },
        "evidence_message_id": {
            "type": "string",
            "description": "若依据是游戏玩家游戏公屏的话，可填那一行末尾 [id:…] 里的号（观众弹幕号不收）",
        },
    },
    "required": ["target", "verdict", "summary", "evidence_kind"],
}


def build_memory_correct_tool_spec(visible_to: Optional[Sequence[str]] = None) -> ToolSpec:
    """构造场内更正工具定义（全名 streamer_memory_correct；支持按主控权动态指定可见名单）。"""
    return ToolSpec(
        name=_TOOL_NAME,
        description=_DESCRIPTION,
        parameters_schema=_PARAMETERS,
        kind="sync",
        provider=_PROVIDER,
        visible_to=list(visible_to) if visible_to is not None else None,
    )


_FULL_NAME = build_memory_correct_tool_spec().full_name


def parse_memory_target(raw: Any) -> Optional[Tuple[Literal["archive", "commitment"], int]]:
    """解析 ``档#42`` / ``[约#15]`` 形态的目标编号；无法识别或非正整数返回 None。"""
    parsed = parse_strict(raw)
    if parsed is None:
        return None
    kind, target_id = parsed
    return ("archive" if kind == _REF_KIND_ARCHIVE else "commitment", target_id)


def _strip_msg_id(raw: Any) -> str:
    text = str(raw or "").strip()
    if text.startswith("[id:") and text.endswith("]"):
        return text[4:-1].strip()
    if text.startswith("id:"):
        return text[3:].strip()
    return text


def _extract_semantic_features(text: str) -> Tuple[Set[str], Set[str], Set[str]]:
    """提取文本的实质主题特征：(中文双字词元, 含字母英文标识, 高辨识度单字物词)。

    先做 NFKC 小写归一并剥离通用状态短语，再抽取可用于跨表错号核对的实体/话题特征。
    """
    folded = unicodedata.normalize("NFKC", str(text or "")).lower()
    cleaned = _GENERIC_PHRASES_RE.sub(" ", folded)
    bigrams: Set[str] = set()
    distinct_chars: Set[str] = set()
    for match in _CJK_RUN_RE.finditer(cleaned):
        run = match.group(0)
        if len(run) >= 2:
            for idx in range(len(run) - 1):
                pair = run[idx : idx + 2]
                if not all(ch in _NON_DISTINCTIVE_CJK_CHARS for ch in pair):
                    bigrams.add(pair)
        for ch in run:
            if ch not in _NON_DISTINCTIVE_CJK_CHARS:
                distinct_chars.add(ch)
    alnum_words = {match.group(0).lower() for match in _ALNUM_WORD_RE.finditer(cleaned)}
    return bigrams, alnum_words, distinct_chars


def _features_overlap(
    left: Tuple[Set[str], Set[str], Set[str]],
    right: Tuple[Set[str], Set[str], Set[str]],
) -> bool:
    """判断两组语义特征在双字词元、英文标识或高辨识度单字物词上是否存在交集。"""
    return bool((left[0] & right[0]) or (left[1] & right[1]) or (left[2] & right[2]))


def _has_semantic_content(features: Tuple[Set[str], Set[str], Set[str]]) -> bool:
    return bool(features[0] or features[1] or features[2])


def _has_concrete_entity_signal(text: str, features: Tuple[Set[str], Set[str], Set[str]]) -> bool:
    """判断未填 target_title 时的说明与依据是否带有明确实体性主题词（英文标识或具体物品/建筑/实体词）。"""
    if features[1]:
        return True
    folded = unicodedata.normalize("NFKC", str(text or "")).lower()
    cleaned = _GENERIC_PHRASES_RE.sub(" ", folded)
    return any(ch in _CONCRETE_ENTITY_CHARS for ch in cleaned)


def _check_target_semantic_match(
    *,
    target_label: str,
    target_title_text: str,
    target_quote_text: str,
    expected_title: str,
    summary: str,
    evidence_quote: str,
    verdict: str,
) -> Optional[str]:
    """核对本次更正说明与库内目标条目的主题关联性，防止跨表错号误关无关承诺或档案行。

    触发条件：查出本地目标条目并完成本场证据校验后执行（纳入已校验的 ``verified_quote``）。
    判定顺序：
    1. 若调用方填写了 ``target_title``：核对其与目标条目（标题+原话）是否存在实质重合；重合即放行
       （允许随后使用极简口语 summary），不重合则直接打回并提示库内真实内容；
    2. 若未填 ``target_title``：对于 ``verdict == "invalid"`` 的世界级作废说明（换新世界/重置等）放行；
       仅当 ``summary + verified_quote`` 带有明确实体性主题词（具体物品/设施/地标/英文标识）却与目标条目
       零重合时才打回，普通口语时间/状态/评价短句（如“目前铺到一半，明天接着干”）一律放行不误伤。
    """
    display_title = " ".join((target_title_text or "").split()).strip()
    target_features = _extract_semantic_features(f"{target_title_text} {target_quote_text}")
    if not _has_semantic_content(target_features):
        return None

    cleaned_expected = " ".join((expected_title or "").split()).strip()
    if cleaned_expected:
        expected_features = _extract_semantic_features(cleaned_expected)
        if _has_semantic_content(expected_features):
            if _features_overlap(expected_features, target_features):
                return None
            return (
                f"本地 {target_label} 实际内容为「{display_title}」，"
                f"与所填 target_title「{cleaned_expected}」不匹配，请核对编号"
            )

    combined_note = f"{summary} {evidence_quote}".strip()
    if verdict == "invalid" and any(hint in combined_note for hint in _GLOBAL_INVALIDATION_HINTS):
        return None

    note_features = _extract_semantic_features(combined_note)
    if not _has_semantic_content(note_features) or not _has_concrete_entity_signal(combined_note, note_features):
        return None
    if _features_overlap(note_features, target_features):
        return None
    return f"本地 {target_label} 实际内容为「{display_title}」，与本次说明不匹配，请核对编号"


class MemoryCorrectionToolProvider(BaseToolProvider):
    """streamer_memory_correct 的 Provider：代码核对本场证据，写入更正并刷新本场记忆摘要。"""

    category: ClassVar[str] = "framework"

    def __init__(
        self,
        *,
        correction_repo: "MemoryCorrectionRepo",
        session_provider: Callable[[], Optional[int]],
        brief_archive_ids_provider: Callable[[], Sequence[int]],
        commitments_repo: Optional["CommitmentRepo"] = None,
        event_repo: Optional["EventRepo"] = None,
        game_player_repo: Optional["GamePlayerRepo"] = None,
        chat_repo: Optional["ChatRepo"] = None,
        overview_provider: Optional[Callable[[], str]] = None,
        on_written: Optional[Callable[[], Awaitable[None]]] = None,
        lead_agent_provider: Optional[Callable[[], str]] = None,
        archive_repo: Optional["ArchiveRepo"] = None,
    ) -> None:
        self._repo = correction_repo
        self._session_provider = session_provider
        self._brief_archive_ids_provider = brief_archive_ids_provider
        self._commitments = commitments_repo
        self._events = event_repo
        self._players = game_player_repo
        self._chat = chat_repo
        self._overview_provider = overview_provider
        self._on_written = on_written
        self._lead_agent_provider = lead_agent_provider
        self._archive = archive_repo
        self._logger = get_logger("MemoryCorrectionTool")

    @property
    def name(self) -> str:
        return _PROVIDER

    def _visible_agents(self) -> List[str]:
        """构造可见名单：始终含主播，游戏主控态下并入当前主控游戏 Agent 注册名（保序去重）。"""
        visible: List[str] = ["streamer"]
        if self._lead_agent_provider is None:
            return visible
        try:
            lead_agent = (self._lead_agent_provider() or "").strip()
        except Exception as exc:  # noqa: BLE001 - 动态名单回调异常降级为仅主播可见
            self._logger.warning(
                f"streamer_memory_correct 读取当前主控游戏 Agent 失败（仅保留 streamer 可见）: {exc}",
                exc=True,
            )
            return visible
        if lead_agent and lead_agent != "*" and lead_agent not in visible:
            visible.append(lead_agent)
        return visible

    def list_tools(self) -> Iterable[ToolSpec]:
        return [build_memory_correct_tool_spec(visible_to=self._visible_agents())]

    def _bounce(self, tool_name: str, reason: str) -> ToolExecutionResult:
        self._logger.info(f"场内更正打回：{reason}")
        return ToolExecutionResult(tool_name=tool_name, success=False, error_message=reason)

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        tool_name = invocation.tool_name
        if tool_name != _FULL_NAME:
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error_message=f"MemoryCorrectionToolProvider 不处理工具 '{tool_name}'",
            )
        try:
            return await self._correct(invocation)
        except Exception as exc:  # noqa: BLE001 - 工具边界异常转失败回执，不反噬决策轮
            self._logger.exception(f"场内更正执行异常: {exc}")
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error_message=f"场内更正失败：{type(exc).__name__}: {exc}",
            )

    async def _correct(self, invocation: ToolInvocation) -> ToolExecutionResult:
        tool_name = invocation.tool_name
        args = invocation.arguments or {}

        session_id = self._session_provider()
        if session_id is None:
            return self._bounce(tool_name, "现在不在直播，不能做场内记忆更正")

        raw_target = str(args.get("target") or "").strip()
        if "跨端" in raw_target:
            return self._bounce(
                tool_name,
                "跨端约#N 来自其他分支同步记录，不在本端本地承诺表内，不能用 streamer_memory_correct 结案；请核对本端【还惦记的事】中的 约#N",
            )
        parsed = parse_memory_target(raw_target)
        if parsed is None:
            return self._bounce(tool_name, "target 必须写成记忆摘要里的 档#N 或 约#N")
        target_kind, target_id = parsed

        verdict_raw = str(args.get("verdict") or "").strip().lower()
        if verdict_raw not in _VALID_VERDICTS:
            return self._bounce(tool_name, f"verdict 必须是 {' / '.join(_VALID_VERDICTS)} 之一")
        verdict: Literal["done", "progress", "invalid", "wrong"] = verdict_raw  # type: ignore[assignment]
        if target_kind == "archive" and verdict == "progress":
            return self._bounce(
                tool_name,
                "verdict='progress' 仅适用于承诺（约#N），档案行（档#N）请使用 done / invalid / wrong",
            )

        summary = " ".join(str(args.get("summary") or "").split())
        if not summary or len(summary) > _SUMMARY_MAX_CHARS:
            return self._bounce(tool_name, f"summary 要写一句现状说明，{_SUMMARY_MAX_CHARS} 字以内")
        target_title = " ".join(str(args.get("target_title") or "").split())

        evidence_raw = str(args.get("evidence_kind") or "").strip().lower()
        if evidence_raw in ("danmaku", "viewer", "观众", "弹幕"):
            return self._bounce(
                tool_name,
                "观众弹幕的话要经人批准才进记忆，不能直接拿来更正记忆摘要或承诺",
            )
        if evidence_raw not in _VALID_EVIDENCE_KINDS:
            return self._bounce(tool_name, f"evidence_kind 必须是 {' / '.join(_VALID_EVIDENCE_KINDS)} 之一")
        evidence_kind: Literal["game_receipt", "player_chat", "operator", "self_seen"] = evidence_raw  # type: ignore[assignment]

        quote_raw = str(args.get("evidence_quote") or "").strip()
        msg_id = _strip_msg_id(args.get("evidence_message_id"))

        # 若填了消息号，先检查是不是误拿观众弹幕冒充依据
        if msg_id and await self._is_viewer_danmaku(int(session_id), msg_id):
            return self._bounce(
                tool_name,
                "这个消息号是观众弹幕；观众的话要经人批准才进跨场记忆，不能直接更正记忆摘要或承诺",
            )

        # 核对目标在本场可见且有效，记录目标原文供证据核验后做语义关联性复核
        target_title_text = ""
        target_quote_text = ""
        if target_kind == "archive":
            brief_ids = {int(item) for item in self._brief_archive_ids_provider()}
            if target_id not in brief_ids:
                return self._bounce(tool_name, f"档#{target_id} 不在本场记忆摘要里，无需场内更正")
            if self._archive is not None:
                archive_rows = await self._archive.list_lines_by_ids(line_ids=[target_id])
                if archive_rows:
                    target_title_text = archive_rows[0].text
                    target_quote_text = archive_rows[0].quote
        else:
            if self._commitments is None:
                return self._bounce(tool_name, "承诺表未接入，无法更正承诺")
            commitment = await self._commitments.get_commitment(commitment_id=target_id)
            if commitment is None or commitment.status != "open":
                return self._bounce(tool_name, f"约#{target_id} 不存在或已了结，无需重复更正")
            target_title_text = commitment.what
            target_quote_text = commitment.quote

        # 核对四类本场证据
        verified_quote = ""
        game_chat_id: Optional[int] = None
        if evidence_kind == "game_receipt":
            matched = await self._verify_game_receipt(int(session_id), quote_raw)
            if matched is None:
                return self._bounce(
                    tool_name,
                    f"evidence_quote 必须是本场游戏回执、里程碑或速览里的逐字片段（{_QUOTE_MAX_CHARS} 字以内）",
                )
            verified_quote = matched
        elif evidence_kind == "player_chat":
            matched_pair = await self._verify_player_chat(int(session_id), quote_raw, msg_id)
            if matched_pair is None:
                return self._bounce(
                    tool_name,
                    f"evidence_quote 必须是本场游戏玩家游戏公屏原话里的逐字片段（{_QUOTE_MAX_CHARS} 字以内）",
                )
            verified_quote, game_chat_id = matched_pair
        elif evidence_kind == "operator":
            matched = await self._verify_operator_note(int(session_id), quote_raw)
            if matched is None:
                return self._bounce(
                    tool_name,
                    f"evidence_quote 必须是本场运营提醒原话里的逐字片段（{_QUOTE_MAX_CHARS} 字以内）",
                )
            verified_quote = matched
        else:
            # self_seen：她自己在画面或速览看到的现状，引文可选但限长
            verified_quote = quote_raw[:_QUOTE_MAX_CHARS]

        # 纳入已核验的 verified_quote 复核语义关联性，阻断跨表错号误关同时放行合理口语更正
        if target_title_text:
            target_label = f"档#{target_id}" if target_kind == "archive" else f"约#{target_id}"
            mismatch = _check_target_semantic_match(
                target_label=target_label,
                target_title_text=target_title_text,
                target_quote_text=target_quote_text,
                expected_title=target_title,
                summary=summary,
                evidence_quote=verified_quote,
                verdict=verdict,
            )
            if mismatch is not None:
                return self._bounce(tool_name, mismatch)

        correction = await self._repo.insert_and_apply(
            live_session_id=int(session_id),
            target_kind=target_kind,
            archive_line_id=target_id if target_kind == "archive" else None,
            commitment_id=target_id if target_kind == "commitment" else None,
            verdict=verdict,
            summary=summary,
            evidence_kind=evidence_kind,
            evidence_quote=verified_quote,
            evidence_game_chat_id=game_chat_id,
            source="streamer",
        )
        if self._on_written is not None:
            try:
                await self._on_written()
            except Exception as exc:  # noqa: BLE001 - 刷新记忆摘要异常不回滚已写入的更正
                self._logger.warning(f"场内更正后刷新记忆摘要失败: {exc}", exc=exc)

        target_label = f"档#{target_id}" if target_kind == "archive" else f"约#{target_id}"
        if target_kind == "archive":
            effect = f"本场记忆摘要已为 {target_label} 附注「本场更正：{format_correction_note(verdict, summary)}」"
        elif verdict == "progress":
            effect = f"{target_label} 已记录本场进展（仍未了结）：{summary}"
        else:
            verdict_label = CORRECTION_VERDICT_LABELS.get(verdict, verdict)
            effect = f"{target_label} 已在本场标为{verdict_label}，不再留在【还惦记的事】"
        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            structured_content={
                "ok": True,
                "correction_id": correction.id,
                "target": target_label,
                "verdict": verdict,
                "effect": effect,
            },
        )

    async def _is_viewer_danmaku(self, session_id: int, message_id: str) -> bool:
        if self._chat is None or not message_id:
            return False
        try:
            rows = await self._chat.list_recent_live_chat(live_session_id=session_id, limit=200, sender_role="viewer")
        except Exception as exc:  # noqa: BLE001 - 查不到时不误拦后续玩家游戏公屏核对
            self._logger.debug(f"核对消息号是否观众弹幕失败: {exc}", exc=exc)
            return False
        return any(str(row.message_id or "") == message_id for row in rows)

    async def _verify_game_receipt(self, session_id: int, quote: str) -> Optional[str]:
        if not quote or len(quote) > _QUOTE_MAX_CHARS:
            return None
        candidates: List[str] = []
        if self._overview_provider is not None:
            try:
                overview = self._overview_provider() or ""
                if overview:
                    candidates.append(overview)
            except Exception as exc:  # noqa: BLE001 - 速览失败不挡事件回执核对
                self._logger.debug(f"读游戏速览核对更正失败: {exc}", exc=exc)
        if self._events is not None:
            events = await self._events.list_session_game_events(live_session_id=session_id, limit=_RECORD_SCAN_LIMIT)
            delegations = await self._events.list_session_delegations(live_session_id=session_id)
            candidates.extend(ev.message for ev in events if ev.message)
            candidates.extend(d.summary for d in delegations if d.summary)
            candidates.extend(d.instruction for d in delegations if d.instruction)
        for text in candidates:
            verified = verbatim_quote(quote, text, _QUOTE_MAX_CHARS)
            if verified is not None:
                return verified
        return None

    async def _verify_player_chat(self, session_id: int, quote: str, message_id: str) -> Optional[Tuple[str, int]]:
        if self._players is None or not quote or len(quote) > _QUOTE_MAX_CHARS:
            return None
        lines = await self._players.list_session_game_chat(live_session_id=session_id)
        player_lines = [line for line in reversed(lines) if line.direction == "player" and line.content]
        if message_id:
            player_lines = [line for line in player_lines if str(line.message_id or "") == message_id]
        for line in player_lines:
            verified = verbatim_quote(quote, line.content, _QUOTE_MAX_CHARS)
            if verified is not None:
                return verified, int(line.id)
        return None

    async def _verify_operator_note(self, session_id: int, quote: str) -> Optional[str]:
        if not quote or len(quote) > _QUOTE_MAX_CHARS:
            return None
        notes = await self._repo.list_active_by_session(live_session_id=session_id)
        for item in reversed(notes):
            if item.source != "operator":
                continue
            verified = verbatim_quote(quote, item.evidence_quote or item.summary, _QUOTE_MAX_CHARS)
            if verified is not None:
                return verified
        return None
