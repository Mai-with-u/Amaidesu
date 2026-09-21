"""config_adapter 服务层边界单测。

覆盖敏感值脱敏、占位回写检测、schema 树行走与前端分组构建的纯函数行为。
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict

from src.modules.dashboard.services.config_adapter import (
    _build_frontend_groups,
    _fill_array_placeholders,
    _find_placeholder_write,
    _mask_sensitive_values,
    _walk_schema,
    apply_config_updates,
)
from src.modules.config.registry import COMPONENT_SCHEMAS
from src.modules.config.multi_file_loader import resolve_root_schema


# ---------------------------------------------------------------------------
# 测试用本地 Schema（_walk_schema 接受任意 BaseModel 子类）
# ---------------------------------------------------------------------------


class _SubConfig(BaseModel):
    """叶子子模型。"""

    port: int = 0
    name: str = ""


class _HostConfig(BaseModel):
    """覆盖三种字段形态的宿主模型：嵌套模型 / dict[str, 模型] / 自由字典。"""

    model_config = ConfigDict(extra="allow")

    nested: _SubConfig = _SubConfig()
    servers: dict[str, _SubConfig] = {}
    free: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# _mask_sensitive_values
# ---------------------------------------------------------------------------


class TestMaskSensitiveValues:
    """敏感字段脱敏：递归命中、原对象不变、非敏感键原样。"""

    def test_顶层敏感键被替换为占位(self) -> None:
        config = {"api_key": "sk-real", "max_tokens": 1024}
        masked = _mask_sensitive_values(config)
        assert masked["api_key"] == "已设置"
        # *_tokens 是预算参数，不是凭据，不得误伤
        assert masked["max_tokens"] == 1024

    def test_嵌套_dict_与_list内dict_均命中(self) -> None:
        config = {
            "model": {"api_secret": "s", "keep": 1},
            "servers": [{"token": "t"}, {"plain": "v"}],
        }
        masked = _mask_sensitive_values(config)
        assert masked["model"]["api_secret"] == "已设置"
        assert masked["model"]["keep"] == 1
        assert masked["servers"][0]["token"] == "已设置"
        assert masked["servers"][1]["plain"] == "v"

    def test_返回新对象_不修改原dict(self) -> None:
        config = {"password": "real"}
        _mask_sensitive_values(config)
        assert config["password"] == "real"

    def test_非敏感标量原样保留(self) -> None:
        config = {"bot_name": "阿妈", "enabled": True, "port": 8000}
        assert _mask_sensitive_values(config) == config

    def test_数字型敏感名键不遮蔽(self) -> None:
        """凭据均为字符串：数字预算参数（token_budget_per_hour 等）键名含敏感词也不遮蔽，
        否则占位文本会撑爆前端的数字输入控件且字段不可读。"""
        config = {"simulator": {"token_budget_per_hour": 50000, "api_key": "sk-real", "enabled": True}}
        masked = _mask_sensitive_values(config)
        assert masked["simulator"]["token_budget_per_hour"] == 50000
        assert masked["simulator"]["api_key"] == "已设置"
        assert masked["simulator"]["enabled"] is True


# ---------------------------------------------------------------------------
# _find_placeholder_write
# ---------------------------------------------------------------------------


class TestFindPlaceholderWrite:
    """占位回写检测：占位文本出现即拒绝，真实值与空值放行。"""

    def test_敏感键占位文本命中并返回路径(self) -> None:
        assert _find_placeholder_write({"llm": {"api_key": "已设置"}}) == "llm.api_key"

    def test_列表内dict占位命中(self) -> None:
        assert _find_placeholder_write([{"token": "已设置"}]) == "token"

    def test_非敏感键同文本不命中(self) -> None:
        assert _find_placeholder_write({"remark": "已设置"}) is None

    def test_敏感键真实值放行(self) -> None:
        assert _find_placeholder_write({"api_key": "sk-new"}) is None

    def test_敏感键空字符串放行_显式清空合法(self) -> None:
        assert _find_placeholder_write({"api_key": ""}) is None

    def test_空结构无占位(self) -> None:
        assert _find_placeholder_write({}) is None
        assert _find_placeholder_write([]) is None


# ---------------------------------------------------------------------------
# _walk_schema
# ---------------------------------------------------------------------------


class TestWalkSchema:
    """schema 树行走三态：叶子定位 / 自由字典 / 路径不存在。"""

    def test_已知段下钻到叶子字段(self) -> None:
        result = _walk_schema(_HostConfig, ["nested", "port"])
        assert result is not None
        kind, payload = result
        assert kind == "leaf"
        host_cls, field_info = payload
        assert host_cls is _SubConfig
        assert field_info.annotation is int

    def test_dict_str_模型_动态键跳过后继续下钻(self) -> None:
        result = _walk_schema(_HostConfig, ["servers", "my-server", "name"])
        assert result is not None
        kind, payload = result
        assert kind == "leaf"
        assert payload[0] is _SubConfig

    def test_自由字典段返回free_dict(self) -> None:
        assert _walk_schema(_HostConfig, ["free", "anything"]) == ("free_dict", None)

    def test_未知段无子路径返回None(self) -> None:
        # extra="allow" 宿主：rest 为空时不下钻注册表，仍视为未知
        assert _walk_schema(_HostConfig, ["no_such_field"]) is None

    def test_非extra宿主未知段返回None(self) -> None:
        assert _walk_schema(_SubConfig, ["no_such_field"]) is None

    def test_extra_allow宿主未知段走注册表下钻(self, monkeypatch) -> None:
        class _CompRoot(BaseModel):
            meta_version: str = "x"

        class _CompSub(BaseModel):
            inner: str = ""

        # 采集器根是 extra="allow"：未知段 <comp> 由注册表提供权威 Schema
        root = resolve_root_schema("collectors")
        assert root is not None
        monkeypatch.setitem(COMPONENT_SCHEMAS, "comp", _CompSub)
        assert root.model_fields.get("comp") is None  # 前置：确属未知段

        result = _walk_schema(root, ["comp", "inner"])
        assert result is not None
        kind, payload = result
        assert kind == "leaf"
        assert payload[0] is _CompSub

    def test_extra_allow宿主_注册表也无此段返回None(self) -> None:
        root = resolve_root_schema("collectors")
        assert root is not None
        assert _walk_schema(root, ["totally_unknown", "inner"]) is None

    def test_空路径返回None(self) -> None:
        assert _walk_schema(_HostConfig, []) is None


class TestWalkSchemaToolProviders:
    """tools 动态分类段：config 子键经工具提供者注册表下钻校验。"""

    def setup_method(self) -> None:
        from src.modules.config.registry import fill_component_schemas

        fill_component_schemas()

    def test_provider_config_叶子字段定位成功(self) -> None:
        root = resolve_root_schema("tools")
        assert root is not None
        result = _walk_schema(root, ["tools", "studio", "obs", "config", "port"])
        assert result is not None
        kind, payload = result
        assert kind == "leaf"
        assert payload[1].annotation is int

    def test_provider_config_未知键返回None(self) -> None:
        root = resolve_root_schema("tools")
        assert root is not None
        assert _walk_schema(root, ["tools", "studio", "obs", "config", "no_such_key"]) is None

    def test_provider_config_整对象更新返回free_dict(self) -> None:
        """config 整对象更新不拆字段，交由加载器整体校验。"""
        root = resolve_root_schema("tools")
        assert root is not None
        assert _walk_schema(root, ["tools", "studio", "obs", "config"]) == ("free_dict", None)

    def test_provider段内enabled仍走通用叶子路径(self) -> None:
        root = resolve_root_schema("tools")
        assert root is not None
        result = _walk_schema(root, ["tools", "studio", "obs", "enabled"])
        assert result is not None
        assert result[0] == "leaf"

    def test_未注册provider的config回退free_dict(self) -> None:
        root = resolve_root_schema("tools")
        assert root is not None
        assert _walk_schema(root, ["tools", "studio", "ghost", "config", "x"]) == ("free_dict", None)

    def test_avatar平台成员段typed字段下钻(self) -> None:
        """avatar.toml 平台成员段直接铺键（无 .config 层），typed 引用递归下钻。"""
        root = resolve_root_schema("avatar")
        assert root is not None
        result = _walk_schema(root, ["platform", "vts", "vts_port"])
        assert result is not None
        kind, payload = result
        assert kind == "leaf"
        assert payload[1].annotation is int


# ---------------------------------------------------------------------------
# _build_frontend_groups
# ---------------------------------------------------------------------------


class _FakeConfigService:
    """只读 main_config 属性；reload_config 为写链路尾部调用提供空实现。"""

    def __init__(self, main_config: dict[str, Any]) -> None:
        self.main_config = main_config

    async def reload_config(self, changed_scopes=None):
        return True


class TestBuildFrontendGroups:
    """前端分组构建：七 scope 分组、敏感值占位、只读标注。"""

    def test_七个scope各生成一个分组(self) -> None:
        result = _build_frontend_groups(_FakeConfigService({}))
        group_keys = [g["key"] for g in result["groups"]]
        assert group_keys == ["agents", "collectors", "tools", "avatar", "model", "storage", "infra"]
        assert result["version"]

    def test_分组label与文件名来自根类自描述(self) -> None:
        result = _build_frontend_groups(_FakeConfigService({}))
        by_key = {g["key"]: g for g in result["groups"]}
        infra = by_key["infra"]
        root_cls = resolve_root_schema("infra")
        assert root_cls is not None
        assert infra["label"] == root_cls.__section_label__
        assert infra["file_name"] == root_cls.__file_name__

    def test_只读字段标注readonly且值可展示(self) -> None:
        result = _build_frontend_groups(_FakeConfigService({"meta": {"version": "9.9.9"}}))

        def _iter_fields(nodes: list[dict]) -> list[dict]:
            out = []
            for node in nodes:
                out.append(node)
                out.extend(_iter_fields(node.get("children", [])))
            return out

        infra_group = next(g for g in result["groups"] if g["key"] == "infra")
        version = next(f for f in _iter_fields(infra_group["fields"]) if f["key"] == "infra.meta.version")
        assert version["readonly"] is True
        assert version["value"] == "9.9.9"

    def test_敏感字段值替换为占位且带标记(self) -> None:
        result = _build_frontend_groups(
            _FakeConfigService({"llm_providers": [{"name": "deepseek", "api_key": "sk-plain"}]})
        )

        def _iter_fields(nodes: list[dict]) -> list[dict]:
            out = []
            for node in nodes:
                out.append(node)
                out.extend(_iter_fields(node.get("children", [])))
            return out

        model_group = next(g for g in result["groups"] if g["key"] == "model")
        fields = _iter_fields(model_group["fields"])
        # 对象数组是一等字段：元素子字段树在 items.fields，不再降维出 llm_providers.api_key 伪键
        assert not any(f["key"] == "model.llm_providers.api_key" for f in fields)
        array_field = next(f for f in fields if f["key"] == "model.llm_providers")
        assert array_field["type"] == "array"
        item_names = [f["key"] for f in array_field["items"]["fields"]]
        assert "api_key" in item_names and "name" in item_names
        # 数组值照常返回，元素内敏感字符串遮蔽为占位
        assert array_field["value"][0]["name"] == "deepseek"
        assert array_field["value"][0]["api_key"] == "已设置"

    def test_数字敏感名字段值照实返回(self) -> None:
        """schema 的 value 占位只针对字符串凭据；数字预算参数照实返回（占位文本撑爆数字输入控件）。"""
        result = _build_frontend_groups(_FakeConfigService({"simulator": {"token_budget_per_hour": 50000}}))

        def _iter_fields(nodes: list[dict]) -> list[dict]:
            out = []
            for node in nodes:
                out.append(node)
                out.extend(_iter_fields(node.get("children", [])))
            return out

        infra_group = next(g for g in result["groups"] if g["key"] == "infra")
        budget = next(
            f for f in _iter_fields(infra_group["fields"]) if f["key"] == "infra.simulator.token_budget_per_hour"
        )
        assert budget["value"] == 50000

    def test_main_config为None时按空配置处理(self) -> None:
        service = _FakeConfigService({})
        service.main_config = None
        result = _build_frontend_groups(service)
        assert len(result["groups"]) == 7


class TestFillArrayPlaceholders:
    """对象数组整值提交的占位回填：未编辑的敏感字段从磁盘现值按索引补回。"""

    def test_改非敏感字段时占位回填真实值(self) -> None:
        old = {"llm_providers": [{"name": "deepseek", "api_key": "sk-real"}]}
        new_value = [{"name": "deepseek2", "api_key": "已设置"}]
        filled = _fill_array_placeholders("model.llm_providers", new_value, old)
        assert filled == [{"name": "deepseek2", "api_key": "sk-real"}]

    def test_显式填入的新值不被回填覆盖(self) -> None:
        old = {"llm_providers": [{"name": "deepseek", "api_key": "sk-real"}]}
        new_value = [{"name": "deepseek", "api_key": "sk-new"}]
        filled = _fill_array_placeholders("model.llm_providers", new_value, old)
        assert filled[0]["api_key"] == "sk-new"

    def test_新增元素无磁盘对应时占位原样保留(self) -> None:
        """新元素超出磁盘列表长度（zip 截断），占位原样保留 → 交给占位写检查拒绝。"""
        old = {"llm_providers": [{"name": "deepseek", "api_key": "sk-real"}]}
        new_value = [
            {"name": "deepseek", "api_key": "已设置"},
            {"name": "newcomer", "api_key": "已设置"},
        ]
        filled = _fill_array_placeholders("model.llm_providers", new_value, old)
        assert filled[0]["api_key"] == "sk-real"
        assert filled[1]["api_key"] == "已设置"

    def test_非列表值原样返回(self) -> None:
        old = {"port": 60214}
        assert _fill_array_placeholders("infra.dashboard.port", 60215, old) == 60215
        assert _fill_array_placeholders("infra.dashboard.port", "x", {}) == "x"


class TestApplyArrayUpdates:
    """端到端：整列表提交 → 占位回填 → 校验通过（占位不落盘）。"""

    def test_整列表提交改字段保留真实key(self, tmp_path) -> None:
        toml_text = '[[llm_providers]]\nname = "default"\napi_key = "sk-real"\nbase_url = "u"\n'
        (tmp_path / "model.toml").write_text(toml_text, encoding="utf-8")
        service = _FakeConfigService({"llm_providers": [{"name": "default", "api_key": "sk-real", "base_url": "u"}]})
        outcome = asyncio.run(
            apply_config_updates(
                service,
                tmp_path,
                # 保留 name：根 Schema 一致性校验要求 llm_models 引用的 provider 存在
                [("model.llm_providers", [{"name": "default", "api_key": "已设置", "base_url": "u2"}])],
            )
        )
        assert outcome.success, outcome.message
        applied_value = outcome.applied[0][1]
        assert applied_value[0]["api_key"] == "sk-real"
        assert applied_value[0]["base_url"] == "u2"
        # 落盘复核：占位不落盘，真实 key 保留
        written = (tmp_path / "model.toml").read_text(encoding="utf-8")
        assert "sk-real" in written and "已设置" not in written
