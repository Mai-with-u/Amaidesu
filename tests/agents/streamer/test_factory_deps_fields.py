"""工厂构造参数与依赖清单契约：factory 从 AgentDeps 字段派生 StreamerAgent 构造参数。

清单（``_STREAMER_DEPS_FIELDS``）里的名字必须同时是 AgentDeps 的字段与
StreamerAgent 的构造形参，否则派生出的 kwargs 会在构造期静默缺参或多参。
"""

from __future__ import annotations

import inspect
from dataclasses import fields as dataclass_fields

from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.agents.deps import AgentDeps
from src.modules.agents.factory import _STREAMER_DEPS_FIELDS


def test_streamer_deps_fields_exist_on_agent_deps() -> None:
    deps_field_names = {field.name for field in dataclass_fields(AgentDeps)}
    missing = set(_STREAMER_DEPS_FIELDS) - deps_field_names
    assert not missing, f"_STREAMER_DEPS_FIELDS 引用了 AgentDeps 不存在的字段: {sorted(missing)}"


def test_streamer_deps_fields_match_constructor_params() -> None:
    param_names = set(inspect.signature(StreamerAgent.__init__).parameters)
    missing = set(_STREAMER_DEPS_FIELDS) - param_names
    assert not missing, f"_STREAMER_DEPS_FIELDS 引用了 StreamerAgent 不存在的构造形参: {sorted(missing)}"
