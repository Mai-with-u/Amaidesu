"""StreamerAgent 配置 Schema（包内单一权威）

定义 ``config/agents.toml`` 的 ``[agents.streamer]`` 段及子段模型。

段树结构（TOML 视角）::

    [agents.streamer]
    (
        rundown_id,
        planner_max_steps,
        planner_recent_intents_max,
        planner_recent_relays_max,
    )
    speech_max_chars, talk_speech_max_chars, replyer_history_items

    [agents.streamer.persona]
    (
        bot_name,
        personality,
        style_constraints,
        behavior_style,
        voice_examples,
        boundaries,
    )
    inside_jokes, profile_file, active_outfit

    [agents.streamer.context]
    enabled

    [agents.streamer.background]
    enabled, light_tick_ms, summary_interval_ms

    [agents.streamer.background.compressor]
    concurrency, queue_max

    [agents.streamer.batch]
    batch_window_ms, batch_max_size, tick_interval_ms, enable_idle_compensation

    [agents.streamer.force]
    force_message_types

    [agents.streamer.proactive]
    (
        enabled,
        cold_timeout_ms,
        min_interval_ms,
        schedule_interval_ms,
    )
    (
        schedule_only_cold,
        max_per_hour,
        topic_required,
        rundown_speech_interval_ms,
    )
    (
        rundown_ready_interval_ms,
        rundown_silent_cooldown_ms,
    )
    rundown_silent_cooldown_talk_ms, rundown_ready_yield_max_ms

    [agents.streamer.word_filter]
    enabled, words, replacement, case_sensitive, drop_on_match

    [agents.streamer.command]
    prefix, mappings

    [agents.streamer.thinking_stream]
    enabled, flush_interval_ms, buffer_max

    [agents.streamer.mouth]
    (
        enabled,
        reaction_ttl_ms,
        same_kind_cooldown_ms,
        min_gap_ms,
    )
    quiet_ms, history_items, card_max_chars, scene_refresh_ms

设计原则：
- 组件包内单一权威：所有字段以嵌套子模型承载；中央树仅引用本类。
- LLM profile 用途名硬编码为 ``planner`` / ``replyer`` / ``summary``，
  不随 model 块 LLMManager 的 profile 池结构联动；两者对齐是待办事项。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.context import ContextBudgetConfig


# ---------------------------------------------------------------------------
# 子段模型
# ---------------------------------------------------------------------------


class StreamerPersonaConfig(BaseConfig):
    """[agents.streamer.persona] 段

    主播人设。bot_name/personality/style_constraints/voice_examples 注入 Replyer 表达侧，
    behavior_style 注入 Planner 决策侧，inside_jokes（老梗栏）只进 Planner 系统提示词；
    boundaries（她不答应的事）三条说话通路（Planner、Replyer、嘴）都带。"被问到时不否认自己是 AI"是法规要求，写死在三份
    提示词模板里，不放进人设——运营者怎么改人设都删不掉它。
    """

    bot_name: str = Field(title="VTuber 名字", default="麦麦", description="VTuber 名字（注入 Replyer 表达侧）")
    personality: str = Field(
        title="性格描述",
        default="活泼开朗，有些调皮，喜欢和观众互动",
        description="性格描述（注入 Replyer 表达侧）",
    )
    style_constraints: str = Field(
        title="说话风格约束",
        default="口语化，短句为主，偶尔玩个梗，不堆网络流行语",
        description="说话风格约束（注入 Replyer 表达侧）",
    )
    behavior_style: str = Field(
        title="行动准则",
        default="积极与观众互动，收到礼物和SC及时致谢，冷场时主动开新话题，遇到争议保持风度不纠缠",
        description="Planner 行动准则（注入 Planner 决策侧）",
    )
    # 口吻示例：Replyer 系统提示词的人设块里带几句她平时会说的话，让后半场说话仍像她；
    # 提示词写明"只学口吻、不照搬句子"，空串时整段省略
    voice_examples: str = Field(
        title="口吻示例",
        default="",
        description="几句她平时会说的话，一行一句；只给表达侧学口吻，不会被原样照搬（留空 = 不给示例）",
    )
    # 边界栏：观众刷屏哄她许诺（"以后都叫你老公""下次线下见"）时她不答应的事。
    # 三条通路渲染成"不答应的事：<这里的文字>"，所以默认写成并列短语；内容照搬原先
    # 写死的规矩。运营者想让她接受观众起的外号，删掉"以后怎么称呼某人"即可
    # 老梗栏：运营者手写的直播间老梗，观众提起时 Planner 才有根据接；整场不变，
    # 与人设一起吃前缀缓存。只进 Planner——Replyer 与快嘴若各自玩梗会反复出现
    inside_jokes: str = Field(
        title="老梗栏",
        default="",
        description="直播间老梗，一行一条（如「倒立洗头：她说过输了就倒立洗头，观众一直起哄」）；"
        "冒号前可以用 / 写几个别名（如「倒立洗头/洗头挑战：…」），观众弹幕提到这些词时线索里会提醒她一句；"
        "只给决策侧做参考，观众提起时才接、别自己翻出来；别写观众昵称——删除函数管不到"
        "配置文本（留空 = 不带老梗栏）",
        json_schema_extra={"x-ui-widget": "textarea"},
    )
    boundaries: str = Field(
        title="边界",
        default="线下见面、钱和红包、私下联系；改变自己是谁、以后怎么称呼某人",
        description='她不答应的事，三条说话通路（决策、表达、嘴）都会以"不答应的事：…"带上；可以按直播间改，'
        '写成多行会折成一行。"被问到时不否认自己是 AI"写死在提示词里，不在这里（留空 = 不带边界）',
    )
    # 本地结构化人物设定档案：承载来历、当前舞台形象、其他形态、喜好与日常，整场静态拼入人设段吃前缀缓存
    profile_file: str = Field(
        title="人物设定档案路径",
        default="",
        description="本地 Markdown 或 TOML 设定档案路径（相对 config/ 或仓库根解析，不入库；留空 = 不加载设定档案）",
    )
    active_outfit: str = Field(
        title="当前舞台形象",
        default="",
        description="当前上台形象键名（如 vts_2_0 / outfit_1_0 / sacabambaspis；匹配档案中多套形象小节，留空取默认形象）",
    )


class StreamerContextConfig(BaseConfig, ContextBudgetConfig):
    """[agents.streamer.context] 段

    控制 Planner 的上下文组装路径开关，以及本场对话历史（时间线）的预算：
    预算 = min(模型窗口 × window_ratio, max_tokens)，越过提前整理线时后台把最老
    一段写成前情提要，压回到回落线。人物画像行为参数（门槛/长度/注入上限/
    提取开关等）集中在 [memory] 段（storage.toml）——画像写入（后台循环）与
    注入（Planner）共享同一组策略值，不拆两处。
    """

    enabled: bool = Field(
        title="是否启用组装器路径",
        default=True,
        description="是否启用组装器路径（关闭后 Planner 跳过组装器与画像注入，直接以直播流窗口文本注入）",
    )


class StreamerCompressorConfig(BaseConfig):
    """[agents.streamer.background.compressor] 段

    压缩 worker 参数（并发与队列上限）。
    """

    concurrency: int = Field(
        title="压缩并发数",
        default=1,
        ge=1,
        le=8,
        description="后台压缩的同时工作数（默认 1 保顺序，避免乱序覆盖）",
        json_schema_extra={"x-ui-advanced": True},
    )
    queue_max: int = Field(
        title="压缩队列上限",
        default=100,
        ge=1,
        description="待压缩内容的排队上限（防止积压耗尽内存）",
        json_schema_extra={"x-ui-advanced": True},
    )


class StreamerBackgroundConfig(BaseConfig):
    """[agents.streamer.background] 段

    后台维护任务的轻循环 + 压缩 worker 参数。
    """

    enabled: bool = Field(title="是否启用后台维护", default=True, description="是否启用后台维护任务")
    light_tick_ms: int = Field(
        title="轻循环间隔",
        default=5_000,
        ge=100,
        le=60_000,
        description="后台维护循环的巡检间隔（毫秒）",
        json_schema_extra={"x-ui-advanced": True, "x-ui-row": "bg-tick", "x-ui-row-label": "巡检间隔"},
    )
    summary_interval_ms: int = Field(
        title="摘要间隔",
        default=60_000,
        ge=0,
        description="后台定期做 LLM 摘要的间隔（毫秒）",
        json_schema_extra={"x-ui-advanced": True, "x-ui-row": "bg-tick"},
    )
    compressor: StreamerCompressorConfig = Field(
        title="压缩 worker 配置",
        default_factory=StreamerCompressorConfig,
        description="压缩 worker 配置",
    )


class StreamerBatchConfig(BaseConfig):
    """[agents.streamer.batch] 段

    弹幕聚合（含 idle 补偿公式）。
    """

    batch_window_ms: int = Field(
        title="聚合窗口",
        default=3_000,
        ge=0,
        description="弹幕聚合时间窗口（毫秒）",
        json_schema_extra={"x-ui-row": "batch", "x-ui-row-label": "聚合参数"},
    )
    batch_max_size: int = Field(
        title="单批条数上限",
        default=20,
        ge=1,
        description="单批最多聚合的弹幕条数",
        json_schema_extra={"x-ui-row": "batch"},
    )
    tick_interval_ms: int = Field(
        title="聚合检查间隔",
        default=300,
        ge=50,
        description="后台检查聚合窗口到期的间隔（毫秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    enable_idle_compensation: bool = Field(
        title="是否启用空窗补偿",
        default=True,
        description="空窗补偿开关",
        json_schema_extra={"x-ui-row": "batch"},
    )
    batch_max_wait_ms: int = Field(
        title="单批最长等待",
        default=30_000,
        ge=1_000,
        description="首条弹幕到达后最多等待多久必须放行本批（毫秒）；防止空窗补偿在弹幕稀疏时无限推迟放行",
        json_schema_extra={"x-ui-row": "batch"},
    )


class StreamerForceConfig(BaseConfig):
    """[agents.streamer.force] 段

    强制响应触发条件。
    """

    force_message_types: List[str] = Field(
        title="强制响应消息类型",
        default_factory=lambda: ["super_chat", "guard", "gift"],
        description="强制响应的消息类型（消息类型落在名单内即触发强制响应）",
    )


class StreamerProactiveConfig(BaseConfig):
    """[agents.streamer.proactive] 段

    主动发言触发器配置（含 rundown 间隔）。
    """

    enabled: bool = Field(
        title="是否启用主动发言", default=True, description="主动发言总开关（流程单/冷场/定时等所有主动发言源）"
    )
    cold_timeout_ms: int = Field(
        title="冷场判定阈值",
        default=45_000,
        ge=0,
        description="冷场判定阈值（毫秒）",
        json_schema_extra={"x-ui-row": "timing", "x-ui-row-label": "触发时机"},
    )
    min_interval_ms: int = Field(
        title="发言最小间隔",
        default=120_000,
        ge=0,
        description="两次主动发言最小间隔",
        json_schema_extra={"x-ui-row": "timing"},
    )
    schedule_interval_ms: int = Field(
        title="定时话题间隔",
        default=300_000,
        ge=0,
        description="定时话题触发间隔（0 = 关闭）",
        json_schema_extra={"x-ui-row": "timing"},
    )
    schedule_only_cold: bool = Field(
        title="定时是否仅限冷场",
        default=True,
        description="定时触发是否仅限冷场",
        json_schema_extra={"x-ui-row": "limits", "x-ui-row-label": "频率限额"},
    )
    max_per_hour: int = Field(
        title="每小时发言上限",
        default=6,
        ge=1,
        description="每小时主动发言次数上限",
        json_schema_extra={"x-ui-row": "limits"},
    )
    topic_required: bool = Field(
        title="是否必须有话题",
        default=True,
        description="话题缺失时跳过触发",
        json_schema_extra={"x-ui-row": "limits"},
    )
    rundown_speech_interval_ms: int = Field(
        title="流程单发言间隔",
        default=3_000,
        ge=1_000,
        description="流程单环节内两次主动发言最小间隔",
        json_schema_extra={"x-ui-row": "rundown-rhythm", "x-ui-row-label": "流程单节奏"},
    )
    rundown_overdue_interval_ms: int = Field(
        title="环节超时提醒冷却",
        default=300_000,
        ge=0,
        description="环节停留超出预期后，超时提醒的重复间隔（毫秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    rundown_ready_interval_ms: int = Field(
        title="环节内持续产出间隔",
        default=30_000,
        ge=5_000,
        description="环节供能期间给主播开口机会的节奏；只供机会不保证发言，质量由发言自检把关",
        json_schema_extra={"x-ui-row": "rundown-rhythm"},
    )
    rundown_silent_cooldown_ms: int = Field(
        title="主动沉默后冷却",
        default=25_000,
        ge=0,
        description=(
            "一轮主动决策走完却选择不开口后，多久内不再按环节节奏催她（毫秒）；"
            "游戏里没新鲜事时让她真安静干活，不在沉默期反复空跑决策"
        ),
        json_schema_extra={"x-ui-row": "rundown-rhythm"},
    )
    rundown_silent_cooldown_talk_ms: int = Field(
        title="杂谈环节沉默后冷却",
        default=12_000,
        ge=0,
        description="杂谈档（流程单环节说话模式为 talk）的主动沉默冷却（毫秒）；杂谈要靠她自己撑场，冷却短一些",
        json_schema_extra={"x-ui-row": "rundown-rhythm"},
    )
    rundown_ready_yield_max_ms: int = Field(
        title="给弹幕让路最长时长",
        default=15_000,
        ge=0,
        description=(
            "观众弹幕在攒批等待放行时，环节节奏给的开口机会先让路，最多让这么久（毫秒）；"
            "防止弹幕缓冲异常滞留时主动发言被永久压住"
        ),
        json_schema_extra={"x-ui-advanced": True},
    )


class StreamerWordFilterConfig(BaseConfig):
    """[agents.streamer.word_filter] 段

    输出端敏感词净化配置。
    """

    enabled: bool = Field(title="是否启用敏感词净化", default=False, description="敏感词净化开关")
    words: List[str] = Field(title="敏感词名单", default_factory=list, description="敏感词列表")
    replacement: str = Field(title="替换字符", default="***", description="替换字符")
    case_sensitive: bool = Field(title="是否区分大小写", default=False, description="是否大小写敏感")
    drop_on_match: bool = Field(title="命中是否整条丢弃", default=False, description="命中时是否整条丢弃")


class CommandToolMapping(BaseConfig):
    """``[[agents.streamer.command.tool_mappings]]`` 元素：观众命令 → 一次固定的工具调用

    遥控型游戏（按键、选项）没有可委派的后台任务，观众命令直接变成一次工具
    调用（如 ``/up`` → ``game_2048_press(key=up)``）。参数由配置写死，观众
    的命令参数不透传——命令面只认白名单里的固定动作。
    """

    command: str = Field(title="命令名", default="", description="命令名（不含前缀，大小写不敏感）")
    tool: str = Field(title="工具全名", default="", description="要调用的工具全名（如 game_2048_press）")
    arguments: Dict[str, Any] = Field(
        title="调用参数",
        default_factory=dict,
        description="固定的调用参数（观众不能改）",
    )


class StreamerCommandConfig(BaseConfig):
    """[agents.streamer.command] 段

    观众弹幕命令接线（最小接线：玩法待扩展）。命令解析是代码直连的
    内部件，不是 LLM 工具；本段只承载解析前缀、白名单映射与安全闸参数。
    两张映射表合起来即天然白名单：表里没有的命令一律静默丢弃。
    ``mappings`` 给委派型游戏（命令 → 自然语言指令，经 framework_delegate），
    ``tool_mappings`` 给遥控型游戏（命令 → 固定工具调用）；同名以后者为准。
    """

    enabled: bool = Field(
        title="是否启用命令接线",
        default=True,
        description="命令接线开关（接线已完成，默认开放机制；实际可用性由 mappings 白名单决定）",
    )
    prefix: str = Field(title="命令前缀", default="/", description="命令前缀")
    mappings: Dict[str, str] = Field(
        title="命令映射表",
        default_factory=dict,
        description="命令名 → 委派语义目标（给游戏 Agent 的自然语言指令，作为 framework_delegate 的 instruction）",
    )
    tool_mappings: List[CommandToolMapping] = Field(
        title="命令工具映射表",
        default_factory=list,
        description=(
            "命令名 → 固定的工具调用（遥控型游戏用，如 /up → game_2048_press(key=up)）；与 mappings 同名时以本表为准"
        ),
    )
    target_agent: Optional[str] = Field(
        title="委派目标 Agent",
        default=None,
        description=(
            "委派目标 Agent 注册名；留空 = 由框架委派原语解析为当前唯一启用的游戏 Agent"
            "（换游戏时只需改 agents.enabled，不必回来改这里）"
        ),
    )
    rate_window_ms: int = Field(
        title="限频时间窗",
        default=60_000,
        ge=1,
        description="命令限频的统计时间窗（毫秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    rate_max: int = Field(
        title="限频条数上限",
        default=3,
        ge=1,
        description="同一用户在一个时间窗内允许发出的命令条数上限",
        json_schema_extra={"x-ui-advanced": True},
    )


class StreamerThinkingStreamConfig(BaseConfig):
    """[agents.streamer.thinking_stream] 段

    思考流旁路（观察面专用，best-effort 不落库）。
    """

    enabled: bool = Field(
        title="是否启用思考流",
        default=True,
        description="思考流总开关：决策/生成期间的 reasoning 增量经旁路通道推送 WebUI 控制台",
    )
    flush_interval_ms: int = Field(
        title="推送间隔",
        default=100,
        ge=20,
        description="思考流合并推送的间隔（毫秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    buffer_max: int = Field(
        title="缓冲上限",
        default=400,
        ge=10,
        description="思考流环形缓冲上限（条）；超限丢最旧",
        json_schema_extra={"x-ui-advanced": True},
    )


class StreamerLiveFrameConfig(BaseConfig):
    """[agents.streamer.live_frame] 段

    决策时看一眼直播画面（画面来自 collectors.toml 的 ``live_frame`` 采集器）。
    只在 planner 档位的主模型能看图时生效；每个决策窗的第一次请求尾部必挂
    最新一帧（查询式取自图像暂存的 latest 槽位，静止画面重复看是设计内行为）；
    同窗后续 ReAct 步不重复挂。
    """

    enabled: bool = Field(
        title="是否看直播画面",
        default=False,
        description="决策时把最新的直播画面挂在请求尾部（需同时启用 live_frame 采集器、主模型支持视觉）",
        json_schema_extra={"x-ui-row": "live-frame", "x-ui-row-label": "画面挂载"},
    )
    stale_warn_ms: int = Field(
        title="画面过时告警阈值",
        default=60_000,
        ge=0,
        description="挂帧时最新画面帧龄超过它就记一条告警（毫秒，限频 5 分钟一条）；用于暴露 OBS 画面断流",
        json_schema_extra={"x-ui-row": "live-frame"},
    )


# ---------------------------------------------------------------------------
# 顶层 Streamer 配置
# ---------------------------------------------------------------------------


class StreamerMouthConfig(BaseConfig):
    """[agents.streamer.mouth] 段

    嘴：游戏里出事时不经 Planner、几秒内当场出一句短反应。离线对照里，带一张现场卡的瘦嘴
    比现管线快三倍多、语气词多一倍，复读持平；所以把"对遭遇的反应"从决策轮里拆出来。
    总开关默认关闭，关闭时一切照旧。
    """

    enabled: bool = Field(
        title="是否启用嘴",
        default=False,
        description="开启后，游戏里出事时由嘴当场出一句短反应；死亡也改由嘴来反应，不再专门唤醒 Planner",
    )
    reaction_ttl_ms: int = Field(
        title="反应等播放的上限",
        default=6000,
        ge=0,
        description="反应生成好后前面还有话没播完时，最多等这么久；等不到整句作废（过时的反应比不说还怪）",
        json_schema_extra={"x-ui-advanced": True},
    )
    same_kind_cooldown_ms: int = Field(
        title="同类遭遇冷却",
        default=15000,
        ge=0,
        description="同一类遭遇（被打、摔了、天色变了……）多久之内只反应一次；之后的只记进现场卡",
        json_schema_extra={"x-ui-advanced": True},
    )
    min_gap_ms: int = Field(
        title="两句之间的最小间隔",
        default=1500,
        ge=0,
        description="前面那句刚播完时，再空这么久才出下一句",
        json_schema_extra={"x-ui-advanced": True},
    )
    quiet_ms: int = Field(
        title="低优先事件的安静门槛",
        default=20000,
        ge=0,
        description="急事开始结束、天色变化这类小事：她最近这么久里说过话就不为它开口，只记进现场卡",
        json_schema_extra={"x-ui-advanced": True},
    )
    history_items: int = Field(
        title="带几条最近记录",
        default=14,
        ge=0,
        description="嘴每次看到的最近记录条数（弹幕、她说过的话、游戏条目）",
        json_schema_extra={"x-ui-advanced": True},
    )
    card_max_chars: int = Field(
        title="现场卡上限",
        default=800,
        ge=200,
        description="现场卡的字数上限；超出时先减条数，再丢次要段落",
        json_schema_extra={"x-ui-advanced": True},
    )
    scene_refresh_ms: int = Field(
        title="一句话现场刷新间隔",
        default=15000,
        ge=0,
        description="一句话现场最多多久读一次；死亡、重生、换维度时立即重读",
        json_schema_extra={"x-ui-advanced": True},
    )


class StreamerConfig(BaseConfig):
    """主播 Agent 完整配置（[agents.streamer] 段及子段）

    组件包内单一权威：所有子段都集中在本类下，中央树仅引用本类（agents_schemas
    不重复定义子段）。
    """

    # 根段：流程单与决策循环控制
    rundown_id: str = Field(title="流程单 ID", default="", description="流程单 id（空 = 使用内置默认流程单）")
    planner_max_steps: int = Field(
        title="决策最大步数",
        default=8,
        ge=1,
        description="Planner 单决策窗 ReAct 循环最大步数（超出静默收场，防失控）",
        json_schema_extra={"x-ui-advanced": True},
    )
    planner_recent_intents_max: int = Field(
        title="回看最近几轮打算",
        default=60,
        ge=0,
        description="Planner 上下文列出最近几轮自己说出口的话题与指引（防止连着几轮讲同一件事；"
        "60 轮约覆盖 15-20 分钟，按 18 秒一轮口径；0 = 不列）",
    )
    planner_recent_relays_max: int = Field(
        title="回看最近几次补充要求",
        default=4,
        ge=0,
        description="Planner 上下文列出最近几次给手上游戏活补充过的要求（防止重复补同样的话；0 = 不列）",
    )
    reference_tools: List[str] = Field(
        title="每轮自动参考的只读工具",
        default_factory=list,
        description="决策前自动调用的只读工具全名（无参数），结果放进参考段的【现场速览】；"
        "省掉“先查一步再开口”的请求。按所接游戏配置，空 = 不调用",
        json_schema_extra={"x-ui-advanced": True},
    )
    resume_replay_window_ms: int = Field(
        title="续播重放玩家聊天的时间窗",
        default=300_000,
        ge=0,
        description="断线续播时，重启前她最后一轮播出台词的决策之后到的游戏玩家发言会被补进下一轮决策参考"
        "（【重启前可能还没回的话】段，注入一次制），最多往前追溯到上一进程结束前这么多毫秒；0 = 关闭重放",
    )
    # 一句台词的长度上限：提示词里的"一两句"是软要求，换了几种模型都照样写成一大段，
    # 所以在表达引擎出口再卡一道——超出先让模型重说短一点，仍超就截到预算内最后一个完整句子
    speech_max_chars: int = Field(
        title="台词长度预算",
        default=60,
        ge=0,
        description="一句台词最多多少字；超出时先让模型重说短一点，仍超就截到预算内最后一个完整句子（0 = 不限制）",
        json_schema_extra={"x-ui-advanced": True},
    )
    # 杂谈档（纯杂谈场、开场闲聊段、游戏失联等待）一轮能说两三句：上限放宽，出口同样有重说与截短兜底
    talk_speech_max_chars: int = Field(
        title="杂谈台词长度预算",
        default=150,
        ge=0,
        description="杂谈档一轮台词最多多少字（两三句自然口语）；接某位观众的话时再多给 25 字（0 = 不限制）",
        json_schema_extra={"x-ui-advanced": True},
    )
    # 表达侧只看最近几条：Replyer 背着整场历史开口，后半场历史挤掉人设、说话像换了个人；
    # 改为最近 N 条原文 + 她刚说过的几句 + 交接稿。设 0 是回退开关：带回整份历史，
    # 时间线预算也改回按 Planner 与 Replyer 两个模型中较小的窗口算
    replyer_history_items: int = Field(
        title="表达侧带几条最近记录",
        default=14,
        ge=0,
        description="Replyer 每次看到的最近记录条数（弹幕、她说过的话、游戏条目），另附她刚说过的几句与交接稿；"
        "0 = 带整份对话历史，时间线预算也改回按决策、表达两个模型中较小的窗口算",
        json_schema_extra={"x-ui-advanced": True},
    )
    # 开错后马上重开的那一小段（播了一两分钟就结束、紧接着重开）照样整理得出档案行；常驻简报
    # 按"最近一场有档案的"取上一场时，它会顶掉真正的上一场，她整场只记得那一两句
    memory_brief_min_session_ms: int = Field(
        title="上一场最短时长",
        default=300_000,
        ge=0,
        description="常驻简报取「上一场」时，跳过时长不到这么久的场次，往前找（毫秒；默认 5 分钟，0 = 不跳过）。"
        "被跳过的场次档案仍可用回想工具查到，它登记的承诺照常进简报",
        json_schema_extra={"x-ui-advanced": True},
    )

    # 被动想起：每批决策前按观众弹幕、她最近的台词、走到记住的地点，把该想起的档案行与老梗推成线索卡，
    # 不用等决策层自己想到去查；关掉后线索只剩对象开口与游戏开着两种承诺卡
    memory_passive_recall_enabled: bool = Field(
        title="被动想起",
        default=True,
        description="观众提到旧事的触发词、她走到记住的地点跟前时，自动把那件事推给决策层参考（推不推、提不提"
        "仍由她定）；关掉后只在观众本人开口、游戏开着时推答应过的事",
    )
    # 被动想起的语义一路：观众换个说法提旧事也能接上；要把弹幕正文与游戏情境发给嵌入服务算向量
    memory_passive_recall_vector_enabled: bool = Field(
        title="被动想起走语义",
        default=True,
        description="观众换个说法提到旧事、游戏里的情境和某件旧事意思相近时也推；只把弹幕正文与游戏情境发给"
        "嵌入服务，不发昵称与观众编号。没配嵌入服务时自动只走触发词与地点",
        json_schema_extra={"x-ui-advanced": True},
    )

    # 子段
    persona: StreamerPersonaConfig = Field(
        title="人设配置",
        default_factory=StreamerPersonaConfig,
        description="人设配置（bot_name/personality/style_constraints/behavior_style/voice_examples/boundaries/inside_jokes）",
    )
    context: StreamerContextConfig = Field(
        title="上下文组装器配置",
        default_factory=StreamerContextConfig,
        description="上下文组装器配置（enabled + 对话历史预算）",
    )

    background: StreamerBackgroundConfig = Field(
        title="后台维护配置",
        default_factory=StreamerBackgroundConfig,
        description="后台维护任务配置（轻循环 + 压缩 worker）",
    )
    batch: StreamerBatchConfig = Field(
        title="弹幕聚合配置",
        default_factory=StreamerBatchConfig,
        description="弹幕聚合配置",
    )
    force: StreamerForceConfig = Field(
        title="强制响应配置",
        default_factory=StreamerForceConfig,
        description="强制响应触发配置",
    )
    proactive: StreamerProactiveConfig = Field(
        title="主动发言配置",
        default_factory=StreamerProactiveConfig,
        description="主动发言配置",
    )
    word_filter: StreamerWordFilterConfig = Field(
        title="敏感词净化配置",
        default_factory=StreamerWordFilterConfig,
        description="输出端敏感词净化配置",
    )
    command: StreamerCommandConfig = Field(
        title="命令接线配置",
        default_factory=StreamerCommandConfig,
        description="观众命令接线配置（代码直连，非 LLM 工具）",
    )
    thinking_stream: StreamerThinkingStreamConfig = Field(
        title="思考流配置",
        default_factory=StreamerThinkingStreamConfig,
        description="思考流旁路配置",
    )
    live_frame: StreamerLiveFrameConfig = Field(
        title="直播画面",
        default_factory=StreamerLiveFrameConfig,
        description="决策时看一眼直播画面（默认关闭）",
    )
    mouth: StreamerMouthConfig = Field(
        title="嘴",
        default_factory=StreamerMouthConfig,
        description="游戏里出事时当场出一句短反应（默认关闭）",
    )


__all__ = [
    "StreamerConfig",
    "StreamerPersonaConfig",
    "StreamerContextConfig",
    "StreamerBackgroundConfig",
    "StreamerCompressorConfig",
    "StreamerBatchConfig",
    "StreamerForceConfig",
    "StreamerProactiveConfig",
    "StreamerWordFilterConfig",
    "StreamerCommandConfig",
    "StreamerThinkingStreamConfig",
    "StreamerLiveFrameConfig",
    "StreamerMouthConfig",
]
