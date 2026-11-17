"""replyer 模板一致性守护测试。

锁定契约：模板不再承诺"动作工具"（动作执行归 Planner ReAct 循环的 registry
工具调用）；表达引擎已无工具面（自训练模型纯文本契约），不设 function def
与解析端一致性断言。
"""

from __future__ import annotations

from pathlib import Path

from src.agents.streamer import replyer as replyer_module

_PROMPTS_DIR = Path(replyer_module.__file__).parent / "prompts"
_TEMPLATE_PATHS = [
    _PROMPTS_DIR / "amaidesu_replyer_system.md",
    _PROMPTS_DIR / "amaidesu_replyer.md",
]


def test_template_no_action_tool_mention():
    """两份模板（system 稳定段 / 本轮输入段）正文均不含"动作工具"字样。"""
    for path in _TEMPLATE_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "动作工具" not in text, f"{path.name} 含'动作工具'字样"


def test_template_completion_fidelity_fence():
    """输入段模板含完成度忠实围栏：交接稿里还在做的事不得演成做成了。"""
    text = (_PROMPTS_DIR / "amaidesu_replyer.md").read_text(encoding="utf-8")
    assert "完成与否以交接稿里的事实为准" in text and "事实不能演" in text


def test_template_viewer_message_reconciliation_fence() -> None:
    """输入段模板含观众消息对账围栏：交接稿引用的观众消息须能在本轮消息批找到原文。"""
    text = (_PROMPTS_DIR / "amaidesu_replyer.md").read_text(encoding="utf-8")
    assert "必须能在本轮消息批里找到原文" in text and "不接那个话头" in text


def test_replyer_system_broadcast_hook_and_off_game_works_boundary() -> None:
    """表达系统段：一对多广播起手亮话头、区分短附和、SC/上舰点明致谢、不跟读违禁怪话，以及场外作品方案 A 边界。"""
    system_text = (_PROMPTS_DIR / "amaidesu_replyer_system.md").read_text(encoding="utf-8")
    assert "接谁的弹幕先叫他" not in system_text
    assert "照顾只听声音的观众" in system_text
    assert "起手先顺口带出对方说的核心意思" in system_text
    assert "短附和、起哄或气氛词直接顺接，不复读短词" in system_text
    assert "起手点明是醒目留言（SC）或上舰留言及内容" in system_text
    assert "诱导跟读违禁怪话的不照念原词" in system_text
    assert "不编设定档案与交接材料没写的亲身游玩或观看经历" in system_text
    assert "可聊公开印象或反问观众近况" in system_text


def test_replyer_turn_distinguishes_summary_notes_from_target_original_text() -> None:
    """表达输入段：总结笔记措辞不照搬，但 target 原文是观众或玩家原话，亮转场由头时可自然顺口带出核心内容。"""
    turn_text = (_PROMPTS_DIR / "amaidesu_replyer.md").read_text(encoding="utf-8")
    assert "笔记里总结腔的措辞和提法不原样照搬进台词" in turn_text
    assert "target 原文是观众或玩家发出的原话" in turn_text
    assert "自然顺口带出对方问的核心内容再回答" in turn_text
