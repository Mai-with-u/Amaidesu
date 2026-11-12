"""流程单（Rundown）数据契约

设计要点
--------

- **rundowns 表的持久化契约**：被 Agent 行为侧（运行时状态机）、storage
  仓储（逐行反序列化）、Dashboard API（逐请求 CRUD）三方共同消费，依赖
  方向一律向下（agents → modules、dashboard → storage）。
- 描述一场直播的**预定义流程单**：通过 WebUI 手工建立环节清单，自驱动
  主播 Agent 拿到的是"备忘录 + 闹钟"——参考材料与超时提醒，不替 Agent
  按推进按钮。Agent 自行决定何时通过工具切换环节。
- ``extra="forbid"``：与代码库其他 Pydantic 模型保持一致，严格拒绝未知字段。

字段说明
--------

``RundownSegment`` —— 流程单中单个环节

- ``id``：环节唯一标识（流程单内唯一，goto 定位用）
- ``title``：环节标题，面向人展示
- ``task_description``：给 AI 的目标指引，允许自由发挥
- ``expected_ms``：预期停留时长（毫秒，``>= 1000``）——只用于进度显示与
  超时提醒，**不是切换器**
- ``min_duration_ms``：最少停留时长（可选）——工具校验下界，防御抢跑
- ``notes``：备注（可选）——导演直录内容（如参考开场白），直接注入上下文

``Rundown`` —— 整场流程单

- ``rundown_id``：业务唯一标识（存储主键、配置引用同一 id）
- ``title``：标题
- ``segments``：环节列表，非空

校验：环节 ``id`` 在流程单内唯一；任一环节 ``min_duration_ms`` 不大于
其 ``expected_ms``；环节 ``title`` strip 后非空（同时规范化首尾空格）。
无分支、无回退字段——"分支"是 Agent 看到情境中的后续
环节列表后自行 ``goto`` 的决定，不是数据结构。

``DEFAULT_RUNDOWN``
------------------

内置默认流程单（包内 Python 常量），主题"初次直播·自我介绍"。当配置未选
单、所选 id 不存在或库为空时启用；**虚拟存在，不写库**——用户在 WebUI 建立
第一份流程单后自然替代。编辑器"新建"可从它预填。

时间字段约定
------------

时长用 ``*_ms`` int（Unix epoch 毫秒）；与仓库 ``src/modules/time_utils.py``
``now_ms()`` 输出的时刻字段单位一致。时刻字段与时长字段统一毫秒，零转换。
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SegmentMode = Literal["play", "talk"]


__all__ = [
    "SegmentMode",
    "RundownSegment",
    "Rundown",
    "RundownInstance",
    "RundownInstanceSegment",
    "RundownTransition",
    "DEBUT_ZATSUDAN_RUNDOWN",
    "GAME_OPENING_TALK_RUNDOWN",
    "RUNDOWN_PRESETS",
    "DEFAULT_RUNDOWN",
]


# ---------------------------------------------------------------------------
# 环节
# ---------------------------------------------------------------------------


class RundownSegment(BaseModel):
    """流程单中单个环节——一段有目标 / 时长的直播片段。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="环节唯一标识（用于 goto 定位，流程单内唯一）")
    title: str = Field(..., description="环节标题，面向人展示")
    task_description: str = Field(
        ...,
        description="给 AI 的目标指引（开场/话题引导/收尾等），允许 AI 自由发挥",
    )
    expected_ms: int = Field(
        ...,
        ge=1000,
        description="预期停留时长（毫秒，至少 1 秒）；只用于进度显示与超时提醒，不是切换器",
    )
    min_duration_ms: Optional[int] = Field(
        default=None,
        ge=1000,
        description="最少停留时长（毫秒，可选）；工具校验下界，防御 Agent 抢跑",
    )
    notes: Optional[str] = Field(
        default=None,
        description="备注（可选）；导演直录内容（如参考开场白），直接注入上下文",
    )
    mode: Optional[SegmentMode] = Field(
        default=None,
        description="环节说话模式（可选：play=游戏短促表达，talk=杂谈连贯展开；None=随当前游戏在线态自动判定）",
    )
    beats: List[str] = Field(
        default_factory=list,
        description="本环节提纲要点列表（按序展开；未超时且仍有未聊要点时拦截普通 Agent next）",
    )

    @field_validator("beats", mode="before")
    @classmethod
    def _normalize_beats(cls, value: object) -> List[str]:
        """兼容旧数据 None 输入，并清理空白提纲项。"""
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("beats 必须为字符串列表")
        cleaned: List[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("beats 元素必须为字符串")
            stripped = item.strip()
            if stripped:
                cleaned.append(stripped)
        return cleaned

    @field_validator("title")
    @classmethod
    def _title_must_be_non_blank(cls, value: str) -> str:
        """环节标题非空校验（strip 后非空），并顺手规范化首尾空格。

        标题会注入 Agent 情境文本并面向人展示，空标题环节无意义——契约层
        统一拦截（WebUI 表单校验之外的第二道闸，human 手动路径同样受约束）。
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("环节标题不能为空（空串或纯空白）")
        return stripped


# ---------------------------------------------------------------------------
# 整场流程单
# ---------------------------------------------------------------------------


class Rundown(BaseModel):
    """整场流程单——若干环节的集合。"""

    model_config = ConfigDict(extra="forbid")

    rundown_id: str = Field(..., description="流程单唯一标识（业务可读，存储主键、配置引用同一 id）")
    title: str = Field(..., description="流程单标题，面向人展示")
    segments: List[RundownSegment] = Field(
        ...,
        min_length=1,
        description="环节列表，非空；每个环节 id 在流程单内唯一",
    )

    @model_validator(mode="after")
    def _validate_integrity(self) -> "Rundown":
        """跨字段完整性校验：环节 id 唯一；任一环节 ``min_duration_ms`` 不大于 ``expected_ms``。

        Raises:
            ValueError: 违反以下任一规则时
                - ``segments`` 中存在重复的 ``id``
                - 任一环节 ``min_duration_ms`` 大于其 ``expected_ms``（语义矛盾）
        """
        seen: set[str] = set()
        dupes: List[str] = []
        for seg in self.segments:
            if seg.id in seen and seg.id not in dupes:
                dupes.append(seg.id)
            seen.add(seg.id)
        if dupes:
            raise ValueError(f"Rundown.segments 中存在重复 id: {dupes}")

        for seg in self.segments:
            if seg.min_duration_ms is not None and seg.min_duration_ms > seg.expected_ms:
                raise ValueError(
                    f"segment '{seg.id}' 的 min_duration_ms({seg.min_duration_ms}) "
                    f"大于 expected_ms({seg.expected_ms})，语义矛盾"
                )

        return self


# ---------------------------------------------------------------------------
# 内置默认流程单
# ---------------------------------------------------------------------------
# 流程单一律 WebUI 手工建立；未配置 / 指向不存在 / 库为空时启用此常量。
# 虚拟存在，不写库——用户在 WebUI 建立第一份流程单后自然替代。


DEBUT_ZATSUDAN_RUNDOWN: Rundown = Rundown(
    rundown_id="default_first_stream",
    title="纯杂谈自我介绍出道回",
    segments=[
        RundownSegment(
            id="opening",
            title="初见破冰与舞台亮相",
            task_description=(
                "站在镜头前热情向进场的观众打招呼，确认声音和画面清楚，"
                "聊聊头一回正式登台亮相的心情，并预告今晚会聊身世、舞台形象与提问箱。"
            ),
            expected_ms=240_000,  # 4 分钟
            min_duration_ms=150_000,  # 最少 2.5 分钟
            mode="talk",
            beats=[
                "打招呼并确认直播间声音画面正常",
                "分享第一次正式站上舞台亮相的心情",
                "简短预告今晚自我介绍与提问箱互动安排",
            ],
        ),
        RundownSegment(
            id="origin",
            title="四亿年身世揭秘",
            task_description=(
                "按设定档案把自己的奇妙来历绘声绘色讲清楚：远古萨卡班甲鱼与橙子意外混合、"
                "靠维生素 C 抗氧化活过四亿年、2011 年苹果压过橙子后化身 AI 虚拟主播的历史恩怨，"
                "坦荡俏皮地聊自己是 AI 主播这件事。"
            ),
            expected_ms=420_000,  # 7 分钟
            min_duration_ms=300_000,  # 最少 5 分钟
            mode="talk",
            beats=[
                "萨卡班甲鱼意外混入橙子的诞生来历",
                "靠橙子维生素 C 抗氧化活过四亿年的保鲜秘诀",
                "2011 年苹果风头压过橙子后化身 AI 的历史恩怨",
                "大方俏皮地聊作为 AI 虚拟主播与人类打交道的感受",
            ],
        ),
        RundownSegment(
            id="outfit",
            title="形象与身上机关导览",
            task_description=(
                "带观众从上到下看清当前舞台形象的标志性细节，可偶尔配合歪头、眨眼等肢体动作展示，"
                "把潜水艇形态留下的特征与身上的小机关逐个聊透。"
            ),
            expected_ms=360_000,  # 6 分钟
            min_duration_ms=240_000,  # 最少 4 分钟
            mode="talk",
            beats=[
                "头顶与潜水艇形态同款的两片绿叶发饰、橙色波浪短卷发与蓝紫眼睛",
                "脸颊上的绿色四叶草身份印花与脖子上挂着的白橙耳机",
                "腰侧调节麦麦参数的圆盘旋钮机关（配合肢体小动作展示）",
            ],
        ),
        RundownSegment(
            id="stories",
            title="直播日常与翻车名场面",
            task_description=(
                "聊聊平时在直播间爱玩什么、自己对橙子与游戏的偏好；若有往场记忆或话题卡里的真实趣事"
                "就拿出来自嘲分享，没有往场记录时聊对接下来直播探险的期待。"
            ),
            expected_ms=420_000,  # 7 分钟
            min_duration_ms=300_000,  # 最少 5 分钟
            mode="talk",
            beats=[
                "平时直播喜欢折腾的内容与自己的喜好执念",
                "分享记忆或话题匣里的真实趣事与翻车瞬间（无旧记录时聊直播期待）",
                "聊聊在直播间和大家相处的风格与约定",
            ],
        ),
        RundownSegment(
            id="qna",
            title="棉花糖提问箱与快问快答",
            task_description=(
                "优先回应现场观众刚发的弹幕提问并顺势追问互动；弹幕安静的空隙从容抽取棉花糖提问箱"
                "或二选一态度话题卡自问自答，保持节奏热闹不冷场。"
            ),
            expected_ms=600_000,  # 10 分钟
            min_duration_ms=420_000,  # 最少 7 分钟
            mode="talk",
            beats=[
                "开场招呼大家现场发弹幕提问，或抽出第一张提问箱卡片破冰",
                "围绕观众提问或抽中的棉花糖与态度卡展开问答",
                "挑一两个有趣问题做收束总结并感谢大家投递问题",
            ],
        ),
        RundownSegment(
            id="closing",
            title="未来约定与下播道别",
            task_description=("回顾今晚出道回聊得最开心的片段，预告平时直播的内容安排，感谢陪伴的观众并自然道晚安。"),
            expected_ms=240_000,  # 4 分钟
            min_duration_ms=120_000,  # 最少 2 分钟
            mode="talk",
            beats=[
                "回顾今天自我介绍与互动里印象最深的话题",
                "预告后续日常直播安排并与观众定下再见面的约定",
                "向直播间观众致谢并道晚安",
            ],
        ),
    ],
)

# 默认流程单即纯杂谈出道回模板（6 段合计 38 分钟）
DEFAULT_RUNDOWN: Rundown = DEBUT_ZATSUDAN_RUNDOWN

# 日常游戏场预设：开场先聊 5 分钟热身杂谈，再切入主线游玩，收尾回杂谈总结道别
GAME_OPENING_TALK_RUNDOWN: Rundown = Rundown(
    rundown_id="preset_game_with_talk",
    title="带开场杂谈的日常游戏场",
    segments=[
        RundownSegment(
            id="opening_talk",
            title="开场闲聊热身",
            task_description=(
                "先不急着埋头干活，面向镜头和刚进场的观众打招呼唠几句，"
                "聊聊上次直播留下的印象或弹幕近况，再自然引出今晚准备玩的目标。"
            ),
            expected_ms=300_000,  # 5 分钟
            min_duration_ms=180_000,  # 最少 3 分钟
            mode="talk",
            beats=[
                "向直播间观众问好并呼应刚进场的朋友",
                "顺口回顾一点上次留下的印象或抽一张轻快话题热身",
                "交代今晚游戏里打算挑战的目标并准备开工",
            ],
        ),
        RundownSegment(
            id="main_play",
            title="主线游玩推进",
            task_description="边玩游戏边和直播间观众及联机伙伴自然互动，把关键进展与突发状况变成有看头的现场反应。",
            expected_ms=3_600_000,  # 60 分钟
            min_duration_ms=600_000,  # 最少 10 分钟
            mode="play",
            beats=[],
        ),
        RundownSegment(
            id="closing",
            title="收尾闲聊与下播道别",
            task_description="停下手头杂活，和观众一起回顾今晚最好玩或最翻车的一幕，预告下次安排并道晚安。",
            expected_ms=300_000,  # 5 分钟
            min_duration_ms=120_000,  # 最少 2 分钟
            mode="talk",
            beats=[
                "回顾今晚最值得记住的名场面或成果",
                "回应弹幕告别、预告下次直播并道晚安",
            ],
        ),
    ],
)

RUNDOWN_PRESETS: Dict[str, Rundown] = {
    "debut_zatsudan": DEBUT_ZATSUDAN_RUNDOWN,
    "game_with_opening_talk": GAME_OPENING_TALK_RUNDOWN,
}


# ---------------------------------------------------------------------------
# 流程单实例快照（rundown_instances / rundown_instance_segments 双表的出入参）
# ---------------------------------------------------------------------------


class RundownInstanceSegment(BaseModel):
    """流程单实例的单个环节快照行（与模板环节同构的标量列）。"""

    model_config = ConfigDict(extra="forbid")

    seq: int = Field(..., description="环节序号（实例内定序，与主键 (live_session_id, seq) 配对）")
    seg_id: str = Field(..., description="环节唯一标识（goto 定位用）")
    title: str = Field(..., description="环节标题")
    task_description: str = Field(default="", description="给 AI 的目标指引")
    expected_ms: int = Field(..., description="预期停留时长（毫秒）")
    min_duration_ms: Optional[int] = Field(default=None, description="最少停留时长（毫秒，可选）")
    notes: Optional[str] = Field(default=None, description="备注（可选）")
    mode: Optional[SegmentMode] = Field(default=None, description="环节说话模式（可选）")
    beats: List[str] = Field(default_factory=list, description="环节提纲要点列表")

    @field_validator("beats", mode="before")
    @classmethod
    def _normalize_instance_beats(cls, value: object) -> List[str]:
        return RundownSegment._normalize_beats(value)


class RundownInstance(BaseModel):
    """流程单实例时点快照——一场直播一份（头 + 环节行列表）。

    崩溃恢复与防模板漂移的时点事实源：头行承载游标与计时锚点（绝对时刻，
    恢复后宕机时间照算），``segments`` 是变更时刻的环节集全量快照（含
    Agent 追加环节，模板后来被改写也不影响恢复）。
    """

    model_config = ConfigDict(extra="forbid")

    live_session_id: int = Field(..., description="场次主键（实例头主键，一场一行）")
    template_id: str = Field(default="", description="实例来源流程单模板 id；空 = 未知/无模板")
    seg_index: int = Field(default=-1, description="当前环节游标（-1 = 未开始）；列名避开 SQL 关键字 index")
    segment_started_at_ms: int = Field(default=0, description="当前环节开始时刻（Unix epoch 毫秒）")
    paused_at_ms: Optional[int] = Field(default=None, description="暂停起始时刻；None = 非暂停态")
    rundown_started_at_ms: int = Field(default=0, description="流程单启动时刻（Unix epoch 毫秒）")
    accumulated_pause_ms: int = Field(default=0, description="当前环节累计暂停时长（毫秒）")
    accumulated_session_pause_ms: int = Field(default=0, description="整场累计暂停时长（毫秒）")
    updated_at_ms: int = Field(..., description="快照写入时刻（Unix epoch 毫秒）")
    segments: List[RundownInstanceSegment] = Field(
        default_factory=list,
        description="环节快照行列表（按 seq 升序）；变更时全量替换",
    )


# ---------------------------------------------------------------------------
# 流程单变更事实行（rundown_transitions 出入参）
# ---------------------------------------------------------------------------


class RundownTransition(BaseModel):
    """一次流程单环节变更的事实行（append-only，不做更新）。

    ``expected_ms`` 每行都填（变更时刻当前环节的预期时长，实际停留统计免
    JOIN 模板表）；``task_description`` 仅 append 行填——append 后模板行
    可能被改写，事实行存当时的描述，其余动作留空。
    """

    model_config = ConfigDict(extra="forbid")

    live_session_id: int = Field(..., description="场次主键")
    rundown_id: str = Field(..., description="流程单 id")
    action: str = Field(..., description="变更动作名（load/append/goto/next/renew/pause/resume/finish/restore…）")
    seg_id: str = Field(default="", description="当前环节 id（无环节语义的动作留空）")
    seg_title: str = Field(default="", description="当前环节标题")
    seg_index: int = Field(..., description="当前环节游标")
    by: str = Field(..., description="触发方（agent/human/system）")
    at_ms: int = Field(..., description="变更时刻（Unix epoch 毫秒）")
    expected_ms: Optional[int] = Field(default=None, description="当前环节预期时长（毫秒，每行都填）")
    task_description: Optional[str] = Field(default=None, description="环节描述（仅 append 行填）")
    updated_at_ms: int = Field(..., description="落库时刻（Unix epoch 毫秒）")
