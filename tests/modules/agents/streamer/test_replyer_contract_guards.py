"""replyer 模板-运行时一致性守护测试。

锁定两条契约：
① 模板不再承诺"动作工具"（动作执行归 Planner ReAct 循环的 registry 工具调用）；
② reply function def 的 schema 字段集与 ``_parse_tool_calls`` 实际解析的字段集一致
  （speech/emotion/intensity），防止定义与解析两端漂移。
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from src.agents.streamer import replyer as replyer_module
from src.agents.streamer.replyer import Replyer

_TEMPLATE_PATH = Path(replyer_module.__file__).parent / "prompts" / "amaidesu_replyer.md"


def test_template_no_action_tool_mention():
    """模板正文不含"动作工具"字样。"""
    text = _TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "动作工具" not in text


def test_function_def_props_match_parser():
    """reply function def 的 properties == _parse_tool_calls 解析的字段集。

    parser 支持字段从源码中的 ``args.get("...")`` 提取，避免硬编码副本漂移。
    """
    fn_def = Replyer._build_reply_function_def()
    props = set(fn_def["parameters"]["properties"])

    parser_source = inspect.getsource(Replyer._parse_tool_calls)
    # 兼容 args.get("k") 与 args.get("k", default) 两种形态
    parsed_keys = set(re.findall(r'args\.get\("(\w+)"', parser_source))

    assert parsed_keys, "未能从 _parse_tool_calls 源码提取字段集"
    assert props == parsed_keys
