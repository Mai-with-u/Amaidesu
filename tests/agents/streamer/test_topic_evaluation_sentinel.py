"""S4 判据①的测量面哨兵：评价词在两个面上分别计数。

契约（amaidesu_planner_react.md topic_summary 段）禁定性评价词，但评价词
真正回流进决策上下文的通道是"已聊话题"渲染段——第 58–61 场实测：单看
"本轮 topic_summary 参数"评价词本来就 ≈0，框定问题藏在被渲染回上下文的
历史条目里。这个哨兵把两个测量面各自的计数口径固化成机械可核的纯函数，
下场复盘按同样的函数数，不再拿单点判据的假绿当验收。
"""

from __future__ import annotations

from src.agents.streamer.planner_render import Intent, render_recent_intents

#: 契约点名的定性评价词（amaidesu_planner_react.md topic_summary 段列举的同族）
EVALUATION_WORDS = ("白忙", "白找", "白转", "白搭", "冤", "狼狈")


def count_evaluation_words(text: str) -> dict[str, int]:
    """逐词计出现次数（只计命中词，全零不落键）。"""
    return {word: text.count(word) for word in EVALUATION_WORDS if word in text}


def test_counter_detects_each_contract_banned_word() -> None:
    """计数函数对契约点名的每个评价词都有检出能力（词表与契约同步漂移会在此暴露）。"""
    for word in EVALUATION_WORDS:
        assert count_evaluation_words(f"找了一晚，{word}了") == {word: 1}
    assert count_evaluation_words("找了一晚没找到") == {}


def test_rendered_recent_intents_surface_counts_evaluation_words() -> None:
    """测量面一：已聊话题渲染段——历史条目里的评价词会原样回流，计数必须看得见。

    无台词的旧条目按 topic_summary 渲染（_said_then 的回退路径），这是
    "白找一晚上"类框定措辞进入每轮请求上下文的通道。
    """
    current_ms = 100 * 60 * 1000
    intents = [
        Intent(
            at_ms=current_ms - 30 * 60 * 1000,
            topic_summary="在海上找了一晚没找到沉船，白转半宿",
            reply_guidance="",
            topic_tag="沉船搜索",
        ),
        Intent(
            at_ms=current_ms - 10 * 60 * 1000,
            topic_summary="矿洞入口被沙砾掩埋，挖了半天没进展",
            reply_guidance="",
            topic_tag="矿洞入口",
        ),
    ]

    rendered = render_recent_intents(intents, {}, current_ms)

    assert count_evaluation_words(rendered) == {"白转": 1}


def test_clean_topic_summary_keeps_rendered_surface_at_zero() -> None:
    """事实段不带评价词时，渲染段计数为 0——这是契约生效后的预期形态。"""
    current_ms = 100 * 60 * 1000
    intents = [
        Intent(
            at_ms=current_ms - 30 * 60 * 1000,
            topic_summary="在海上找了一晚，没找到沉船",
            reply_guidance="",
            topic_tag="沉船搜索",
        ),
    ]

    rendered = render_recent_intents(intents, {}, current_ms)

    assert count_evaluation_words(rendered) == {}


def test_topic_summary_argument_surface_counted_separately() -> None:
    """测量面二：本轮 streamer_reply 的 topic_summary 参数——单独计数，不与渲染段混算。

    复盘口径（元复盘 M2）：两个面分别数。参数面拿工具调用的 arguments 取值，
    与渲染段各自的计数互不替代。
    """
    arguments = {"topic_summary": "找了一晚没找到，白忙一场", "reply_guidance": ""}

    param_counts = count_evaluation_words(str(arguments.get("topic_summary", "")))

    assert param_counts == {"白忙": 1}
