"""config_adapter 服务层边界：敏感值脱敏、占位回写检测、schema 树行走、前端分组构建、对象数组占位回填"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from src.modules.config.multi_file_loader import resolve_root_schema
from src.modules.component_catalog import fill_component_schemas
from src.modules.config.registry import COMPONENT_SCHEMAS
from src.modules.dashboard.services.config_adapter import (
    _build_frontend_groups,
    _fill_array_placeholders,
    _find_placeholder_write,
    _mask_sensitive_values,
    _walk_schema,
    apply_config_updates,
)

PLACEHOLDER = "已设置"


class _SubConfig(BaseModel):
    port: int = 0
    name: str = ""


class _HostConfig(BaseModel):
    """覆盖三种字段形态的宿主：嵌套模型 / dict[str, 模型] / 自由字典。"""

    model_config = ConfigDict(extra="allow")

    nested: _SubConfig = _SubConfig()
    servers: dict[str, _SubConfig] = {}
    free: dict[str, Any] = {}


def _iter_fields(nodes: list[dict]) -> list[dict]:
    out: list[dict] = []
    for node in nodes:
        out.append(node)
        out.extend(_iter_fields(node.get("children", [])))
    return out


class _FakeConfigService:
    """main_config + 按文件视图 file_sections（动态段实例枚举的数据源）+ base_dir（版本号直读定位）。"""

    def __init__(
        self, main_config: dict[str, Any], file_sections: dict[str, dict] | None = None, base_dir: Any = ""
    ) -> None:
        self.main_config = main_config
        self._file_sections = file_sections or {}
        self.base_dir = base_dir

    def get_file_section(self, file_stem: str, default: Any = None) -> dict:
        return self._file_sections.get(file_stem, default if default is not None else {})

    async def reload_config(self, changed_scopes=None):
        return True


def _group(service: _FakeConfigService, key: str) -> dict:
    return next(g for g in _build_frontend_groups(service)["groups"] if g["key"] == key)


def _field(service: _FakeConfigService, group: str, key: str) -> dict:
    return next(f for f in _iter_fields(_group(service, group)["fields"]) if f["key"] == key)


# 脱敏与占位回写


def test_masking_hits_nested_and_list_credentials_without_touching_budgets_or_the_original() -> None:
    config = {
        "api_key": "sk-real",
        "max_tokens": 1024,  # *_tokens 是预算参数不是凭据，不得误伤
        "model": {"api_secret": "s", "keep": 1},
        "servers": [{"token": "t"}, {"plain": "v"}],
        "simulator": {
            "token_budget_per_hour": 50000,
            "enabled": True,
        },  # 数字型敏感名键不遮蔽，否则占位文本会撑爆数字输入控件
        "bot_name": "阿妈",
    }
    masked = _mask_sensitive_values(config)
    assert masked["api_key"] == PLACEHOLDER and masked["max_tokens"] == 1024
    assert masked["model"] == {"api_secret": PLACEHOLDER, "keep": 1}
    assert masked["servers"] == [{"token": PLACEHOLDER}, {"plain": "v"}]
    assert masked["simulator"]["token_budget_per_hour"] == 50000 and masked["bot_name"] == "阿妈"
    assert config["api_key"] == "sk-real"  # 返回新对象，原 dict 不变


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"llm": {"api_key": PLACEHOLDER}}, "llm.api_key"),
        ([{"token": PLACEHOLDER}], "token"),
        ({"remark": PLACEHOLDER}, None),  # 非敏感键的同文本不命中
        ({"api_key": "sk-new"}, None),  # 真实值放行
        ({"api_key": ""}, None),  # 显式清空合法
        ({}, None),
        ([], None),
    ],
)
def test_placeholder_write_detection(payload, expected) -> None:
    assert _find_placeholder_write(payload) == expected


# schema 树行走


def test_walk_schema_locates_leaves_free_dicts_and_unknowns(monkeypatch) -> None:
    kind, (host_cls, field_info) = _walk_schema(_HostConfig, ["nested", "port"])
    assert (kind, host_cls, field_info.annotation) == ("leaf", _SubConfig, int)
    kind, payload = _walk_schema(
        _HostConfig, ["servers", "my-server", "name"]
    )  # dict[str, 模型] 的动态键跳过后继续下钻
    assert (kind, payload[0]) == ("leaf", _SubConfig)
    assert _walk_schema(_HostConfig, ["free", "anything"]) == ("free_dict", None)
    for path in (["no_such_field"], []):  # extra=allow 宿主 rest 为空时不下钻注册表，仍视为未知
        assert _walk_schema(_HostConfig, path) is None
    assert _walk_schema(_SubConfig, ["no_such_field"]) is None

    class _CompSub(BaseModel):
        inner: str = ""

    root = resolve_root_schema("collectors")  # 采集器根是 extra=allow：未知段由注册表提供权威 Schema
    monkeypatch.setitem(COMPONENT_SCHEMAS, "comp", _CompSub)
    assert root.model_fields.get("comp") is None
    kind, payload = _walk_schema(root, ["comp", "inner"])
    assert (kind, payload[0]) == ("leaf", _CompSub)
    assert _walk_schema(root, ["totally_unknown", "inner"]) is None  # 注册表也无此段


@pytest.mark.parametrize(
    ("scope", "path", "kind"),
    [
        ("tools", ["tools", "studio", "obs", "config", "port"], "leaf"),  # provider config 经提供者注册表下钻
        ("tools", ["tools", "studio", "obs", "config", "no_such_key"], None),
        ("tools", ["tools", "studio", "obs", "config"], "free_dict"),  # 整对象更新不拆字段，交由加载器整体校验
        ("tools", ["tools", "studio", "obs", "enabled"], "leaf"),
        ("tools", ["tools", "studio", "ghost", "config", "x"], "free_dict"),  # 未注册 provider 回退自由字典
        ("avatar", ["platform", "vts", "vts_port"], "leaf"),  # 平台成员段直接铺键（无 .config 层），typed 引用递归下钻
    ],
)
def test_walk_schema_dynamic_provider_sections(scope, path, kind) -> None:
    fill_component_schemas()
    result = _walk_schema(resolve_root_schema(scope), path)
    assert (result[0] if result else None) == kind
    if kind == "leaf" and path[-1] in ("port", "vts_port"):
        assert result[1][1].annotation is int


# 前端分组构建


def test_groups_cover_seven_scopes_with_self_described_labels_readonly_and_widget_markers() -> None:
    result = _build_frontend_groups(_FakeConfigService({"meta": {"version": "9.9.9"}}))
    assert [g["key"] for g in result["groups"]] == [
        "agents",
        "collectors",
        "tools",
        "avatar",
        "model",
        "storage",
        "infra",
    ]
    assert result["version"]
    infra = next(g for g in result["groups"] if g["key"] == "infra")
    root_cls = resolve_root_schema("infra")
    assert (infra["label"], infra["file_name"]) == (root_cls.__section_label__, root_cls.__file_name__)

    version = next(f for f in _iter_fields(infra["fields"]) if f["key"] == "infra.meta.version")
    assert version["readonly"] is True and version["value"] == "9.9.9"  # 只读字段标注 readonly 且值可展示

    avatar = _iter_fields(next(g for g in result["groups"] if g["key"] == "avatar")["fields"])
    by_key = {f["key"]: f for f in avatar}
    assert by_key["avatar.platform.enabled"]["validation"]["options"] == [
        "vts",
        "warudo",
        "vrchat",
        "expression_sheet",
    ]  # 封闭候选池
    assert by_key["avatar.lipsync.type"]["readonly"] is True  # 常量型字段只读
    assert by_key["avatar.lipsync.min_mouth_delta"]["precision"] == 3  # 精度标记透传

    service = _FakeConfigService({})
    service.main_config = None  # None 按空配置处理
    assert len(_build_frontend_groups(service)["groups"]) == 7


def test_object_arrays_are_first_class_fields_and_secrets_are_masked_only_when_strings() -> None:
    service = _FakeConfigService(
        {"llm_providers": [{"name": "deepseek", "api_key": "sk-plain"}], "simulator": {"token_budget_per_hour": 50000}}
    )
    result = _build_frontend_groups(service)
    fields = _iter_fields(next(g for g in result["groups"] if g["key"] == "model")["fields"])
    assert not any(f["key"] == "model.llm_providers.api_key" for f in fields)  # 不再降维出伪键
    array_field = next(f for f in fields if f["key"] == "model.llm_providers")
    assert array_field["type"] == "array"
    assert {"api_key", "name"} <= {f["key"] for f in array_field["items"]["fields"]}
    assert array_field["value"][0] == {"name": "deepseek", "api_key": PLACEHOLDER}  # 元素内敏感字符串遮蔽为占位

    budget = _field(service, "infra", "infra.simulator.token_budget_per_hour")
    assert budget["value"] == 50000  # 数字预算参数照实返回


def test_no_ghost_prefixed_or_duplicate_field_keys() -> None:
    """数组字段不得重复收集出 scope 前缀翻倍的幽灵子树：读路径指向不存在的段，编辑保存必报"未知配置项"。"""
    keys = [
        f["key"] for g in _build_frontend_groups(_FakeConfigService({}))["groups"] for f in _iter_fields(g["fields"])
    ]
    for ghost in (
        "tools.tools.tools.disabled_tools",
        "agents.agents.agents.enabled",
        "model.model.llm_profiles.planner.model_list",
        "infra.infra.logging.filter",
        "avatar.avatar.platform.enabled",
    ):
        assert ghost not in keys, f"幽灵字段键复活: {ghost}"
    assert len(keys) == len(set(keys)), "同一字段被收集两次"


def test_dynamic_instances_come_from_disk_values_and_registry_members(tmp_path) -> None:
    fill_component_schemas()
    collectors = _FakeConfigService(
        {"console_input": {"user_id": "u-42"}, "custom_thing": {"x": 1}},
        file_sections={
            "collectors": {"enabled": ["console_input"], "console_input": {"user_id": "u-42"}, "custom_thing": {"x": 1}}
        },
    )
    leaf = _field(collectors, "collectors", "collectors.console_input.user_id")  # 已注册采集器段展开 typed 子字段
    assert (leaf["type"], leaf["value"]) == ("string", "u-42")
    custom = next(f for f in _group(collectors, "collectors")["fields"] if f["key"] == "collectors.custom_thing")
    assert custom["type"] == "object" and custom.get("children") is None  # 未注册段回退自由 dict 叶

    config_value = {"base_url": "https://cn.bing.com/search", "timeout_ms": 10000}
    tools = _FakeConfigService(
        {"tools": {"web": {"search": {"enabled": True, "config": config_value}}}},
        file_sections={"tools": {"tools": {"web": {"search": {"enabled": True, "config": config_value}}}}},
    )
    enabled = _field(tools, "tools", "tools.tools.web.search.enabled")
    assert (enabled["type"], enabled["value"]) == ("boolean", True)
    assert _field(tools, "tools", "tools.tools.web.search.config.base_url")["value"] == "https://cn.bing.com/search"

    absent = _FakeConfigService(
        {"tools": {"studio": {}}}, file_sections={"tools": {"tools": {"studio": {}}}}, base_dir=str(tmp_path)
    )
    obs_enabled = _field(absent, "tools", "tools.tools.studio.obs.enabled")  # 磁盘缺省但注册表在册：出实例卡走默认值
    assert obs_enabled["type"] == "boolean" and obs_enabled["default"] is False  # 未落盘 = 未装配，开关默认关
    assert "首次保存时创建" in (obs_enabled["description"] or "")

    no_avatar = _FakeConfigService({"tools": {}}, file_sections={"tools": {"tools": {}}}, base_dir=str(tmp_path))
    keys = [f["key"] for f in _iter_fields(_group(no_avatar, "tools")["fields"])]
    assert not any(k.startswith("tools.tools.avatar.") for k in keys)  # avatar 装配开关在 avatar.toml，不出无效开关

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "tools.toml").write_text('[meta]\nversion = "2.0.38"\n', encoding="utf-8")
    assert (
        _group(_FakeConfigService({}, base_dir=str(tmp_path)), "tools")["version"] == "2.0.38"
    )  # 分组透出文件 meta 版本号


# 对象数组整值提交的占位回填：身份匹配优先，位置兜底其次


def _providers(*pairs: tuple[str, str]) -> dict:
    return {"llm_providers": [{"name": name, "api_key": key} for name, key in pairs]}


def _fill(new_pairs: list[tuple[str, str]], old: dict) -> list[str]:
    value = [{"name": name, "api_key": key} for name, key in new_pairs]
    return [item["api_key"] for item in _fill_array_placeholders("model.llm_providers", value, old)]


def test_placeholder_refill_by_identity_then_position() -> None:
    old = _providers(("deepseek", "sk-deepseek"), ("openai", "sk-openai"))
    assert _fill([("deepseek2", PLACEHOLDER)], _providers(("deepseek", "sk-real"))) == ["sk-real"]  # 改非敏感字段
    assert _fill([("deepseek", "sk-new")], _providers(("deepseek", "sk-real"))) == ["sk-new"]  # 显式新值不被覆盖
    assert _fill([("openai", PLACEHOLDER), ("deepseek", PLACEHOLDER)], old) == [
        "sk-openai",
        "sk-deepseek",
    ]  # 重排不串 key
    three = _providers(("a", "sk-a"), ("b", "sk-b"), ("c", "sk-c"))
    assert _fill([("a", PLACEHOLDER), ("c", PLACEHOLDER)], three) == ["sk-a", "sk-c"]  # 删中间项后剩余各还原自己的
    assert _fill([("deepseek-renamed", PLACEHOLDER), ("openai", PLACEHOLDER)], old) == [
        "sk-deepseek",
        "sk-openai",
    ]  # 改名经位置兜底
    # 配不上的新增项占位原样保留 → 交给占位写检查拒绝（fail-closed）
    assert _fill([("deepseek", PLACEHOLDER), ("newcomer", PLACEHOLDER)], _providers(("deepseek", "sk-d"))) == [
        "sk-d",
        PLACEHOLDER,
    ]
    dup = _fill([("dup", PLACEHOLDER), ("dup", PLACEHOLDER)], _providers(("dup", "sk-one"), ("dup", "sk-two")))
    assert sorted(dup) == ["sk-one", "sk-two"]  # 同名重复按顺序确定性配对

    assert _fill_array_placeholders("infra.dashboard.port", 60215, {"port": 60214}) == 60215  # 非列表原样返回
    assert _fill_array_placeholders("infra.dashboard.port", "x", {}) == "x"


def test_whole_list_submission_keeps_the_real_key_and_never_persists_the_placeholder(tmp_path) -> None:
    (tmp_path / "model.toml").write_text(
        '[[llm_providers]]\nname = "default"\napi_key = "sk-real"\nbase_url = "u"\n', encoding="utf-8"
    )
    service = _FakeConfigService({"llm_providers": [{"name": "default", "api_key": "sk-real", "base_url": "u"}]})
    outcome = asyncio.run(
        apply_config_updates(
            service,
            tmp_path,
            [
                ("model.llm_providers", [{"name": "default", "api_key": PLACEHOLDER, "base_url": "u2"}])
            ],  # 保留 name：一致性校验要求引用存在
        )
    )
    assert outcome.success, outcome.message
    assert outcome.applied[0][1][0]["api_key"] == "sk-real" and outcome.applied[0][1][0]["base_url"] == "u2"
    written = (tmp_path / "model.toml").read_text(encoding="utf-8")
    assert "sk-real" in written and PLACEHOLDER not in written


# 保存管线热应用：agents / collectors 域变更触发组件重建回执


class _FakeHotReport:
    """component_runtime.HotApplyReport 同形替身（热应用已被替换，无需真实管理器）。"""

    def __init__(self, restarted: list[str]) -> None:
        self.restarted = restarted
        self.skipped: list[str] = []
        self.failures: list[tuple[str, str]] = []

    @property
    def attempted(self) -> bool:
        return bool(self.restarted or self.skipped or self.failures)


def _generated_config_dir(tmp_path: Any) -> Any:
    from src.modules.config.multi_file_loader import generate_default_configs

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    generate_default_configs(cfg_dir)
    return cfg_dir


def test_apply_config_updates_without_hot_context_keeps_restart_semantics(tmp_path) -> None:
    outcome = asyncio.run(
        apply_config_updates(
            _FakeConfigService({}), _generated_config_dir(tmp_path), [("tools.tools.tasks.poll_interval_ms", 3000)]
        )
    )
    assert outcome.success and outcome.requires_restart is True  # 未注入热应用上下文 = 维持需重启语义
    assert outcome.hot_apply is None


def test_apply_config_updates_with_hot_context_hot_applies_component_domain(tmp_path, monkeypatch) -> None:
    import src.modules.dashboard.services.config_adapter as adapter

    applied_keys: list[list[str]] = []

    async def fake_hot_apply(ctx, config_service, config_dir, keys):  # noqa: ANN001
        applied_keys.append(list(keys))
        return _FakeHotReport(restarted=["streamer"])

    monkeypatch.setattr(adapter, "hot_apply_components", fake_hot_apply)
    outcome = asyncio.run(
        apply_config_updates(
            _FakeConfigService({}),
            _generated_config_dir(tmp_path),
            [("agents.agents.streamer.persona.bot_name", "新名字")],
            hot_context=object(),  # 只作存在性标记：热应用入口已被替换，上下文不被消费
        )
    )

    assert outcome.success and outcome.requires_restart is False  # agents 域已热应用，不再要求重启进程
    assert outcome.hot_apply is not None and outcome.hot_apply.restarted == ["streamer"]
    assert applied_keys == [["agents.agents.streamer.persona.bot_name"]]


def test_apply_config_updates_with_hot_context_keeps_restart_for_non_component_domain(tmp_path, monkeypatch) -> None:
    import src.modules.dashboard.services.config_adapter as adapter

    async def fail_hot_apply(ctx, config_service, config_dir, keys):  # noqa: ANN001
        raise AssertionError("tools 域变更不应触发组件热应用")

    monkeypatch.setattr(adapter, "hot_apply_components", fail_hot_apply)
    outcome = asyncio.run(
        apply_config_updates(
            _FakeConfigService({}),
            _generated_config_dir(tmp_path),
            [("tools.tools.tasks.poll_interval_ms", 3000)],
            hot_context=object(),
        )
    )
    assert outcome.success and outcome.requires_restart is True and outcome.hot_apply is None
