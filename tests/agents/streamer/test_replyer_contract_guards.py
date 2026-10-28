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
