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

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


__all__ = [
    "RundownSegment",
    "Rundown",
    "RundownInstance",
    "RundownTransition",
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


DEFAULT_RUNDOWN: Rundown = Rundown(
    rundown_id="default_first_stream",
    title="初次直播",
    segments=[
        RundownSegment(
            id="opening",
            title="开场问候",
            task_description=(
                "用友善、轻松的语气向首次进入直播间的观众打招呼，"
                "说明这是首播，预告接下来会自我介绍，欢迎大家在弹幕互动。\n"
                "要点：问好、说明首播、欢迎弹幕"
            ),
            expected_ms=180_000,  # 约 3 分钟
        ),
        RundownSegment(
            id="self_intro",
            title="自我介绍",
            task_description=(
                "围绕自己的角色定位、开播初衷、未来内容规划展开，让观众快速了解主播是谁、为什么开播、接下来打算做什么。\n"
                "要点：角色身份、开播初衷、未来内容方向"
            ),
            expected_ms=600_000,  # 约 10 分钟
        ),
        RundownSegment(
            id="chat",
            title="互动闲聊",
            task_description=(
                "欢迎观众弹幕提问与闲聊，主动抛出话题制造氛围；保持对话流动，不主导话题而是呼应观众。\n"
                "要点：回应弹幕、抛出互动话题、维持氛围"
            ),
            expected_ms=1_200_000,  # 约 20 分钟
        ),
        RundownSegment(
            id="closing",
            title="收尾预告",
            task_description=(
                "感谢今晚陪伴的观众，简短预告下次直播内容与下播时间，自然结束本次首播。\n"
                "要点：感谢观众、预告下次直播、道别"
            ),
            expected_ms=300_000,  # 约 5 分钟
        ),
    ],
)


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
