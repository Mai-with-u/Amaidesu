# 配置文件迁移工作流

配置系统的事实源在 `src/modules/config/`：Pydantic Schema 定义结构（各 `*_schemas.py`），加载管线在 `multi_file_loader.py`，升级钩子注册与调度在 `upgrade.py`。配置布局为 7 个 TOML 文件（agents / collectors / tools / avatar / model / storage / infra），每文件头部带 `[meta]` 段记录该文件自己的结构版本号。

## 加载管线（先知道机制，再动手）

`load_config_dir(config_dir: Path)` 是管线的唯一程序化入口（`src/modules/config/multi_file_loader.py`），阶段序：

| 阶段 | 做什么 | 对迁移的意义 |
|------|--------|--------------|
| ① read_all_raw | 7 个文件全部读为 dict，缺失文件先按 Schema 生成 | 生成的新文件版本直接是基线，不吃旧钩子 |
| ②③ 版本推进 + 升级钩子 | `advance_file_versions(raw_docs)`（`upgrade.py`）逐文件跑 `旧版本 < target` 的钩子链 | 钩子在这里改数据、推进 `[meta].version` |
| ④ Pydantic 校验 | 硬错——程序不认识的段/字段/启用名直接抛 `ConfigValidationError` | 钩子欠账在这里爆，不会静默丢数据 |
| ⑤ 漂移写回 | 有漂移或版本推进的文件全量序列化写盘（备份到 `config/old/<批次>/`） | **迁移结果落盘的落点** |
| ⑥ 合并视图 | 剥 `[meta]` 后按 scope 合并 | 版本戳不进运行时配置 |

调度细节（`upgrade.py` 的 `advance_file_versions`）：文件版本缺失即硬错；链毕版本戳推进到最后一个已执行钩子的 target；没有适用钩子的文件版本保持原值；跨文件钩子双写并同步推进目标文件版本（已高于 target 的目标文件不回退）。

## 路径 A：纯新增（零成本）

适用：只给 Schema 加新字段/新段，带默认值，不需要存量文件里的旧数据变换形态。

1. 改对应域的 Schema 文件（`agents_schemas.py` / `collectors_schemas.py` / `tools_schemas.py` / `avatar_schemas.py` / `model_schemas.py` / `storage_schemas.py` / `infra_schemas.py`）。
2. 不写钩子、不升 `[meta].version`。存量文件加载时缺失字段经漂移检测补默认值，随阶段⑤自动写回落盘——这是"漂移写回自动补默认值"机制覆盖的免费路径。
3. 同步更新该组件的 schema 测试（改哪个组件就改哪个，如 `tests/config/test_agents_schema.py`）。
4. 验证：跑该域 schema 测试 + 一个加载用例，确认补默认写回（日志行 `X.toml 已自动升级: 补齐 ...`）。

动态键段（采集器 `COLLECTOR_SCHEMAS` / Agent `AGENT_SCHEMAS` / 工具提供者 `TOOL_PROVIDER_SCHEMAS` 注册表里的段）的新增同样零成本，但注意：采集器 Schema 必须可默认构造（`schema_cls()` 无参成立），凭证类字段用空串/0 占位表达"未配置"，默认值自身必须满足字段约束。

## 路径 B：数据变换（完整流程）

改默认值且需存量跟随、字段重命名、删字段/段、段跨文件搬移、类型转换，全走这条。

### B1. 升版本

把目标文件的 `[meta].version` target 定为新值（沿用发布线版本号序列，如当前最高钩子 target 为 `2.0.47` 则取下一个）。只动目标文件——各文件版本流独立。

### B2. 写钩子

在 `src/modules/config/upgrade.py` 定义函数并用 `register_file_hook` 登记：

```python
def _upgrade_<file>_<语义>(data: Dict[str, Any]) -> List[str]:
    """<file> vX.Y.Z：<一句话说明改什么、为什么>。对已迁移数据零变更（幂等）。"""
    <段存在性检查，不存在直接 return []>
    # 改默认值场景：仅值等于旧默认时改写，用户显式设置的其他值不动
    ...
    return [变更路径列表]  # 点分路径，如 "agents.streamer.x"；无变更返回 []

register_file_hook("agents.toml", "<hook 名>", "X.Y.Z", _upgrade_<file>_<语义>)
```

硬性要求（协议 `FileUpgradeHook` / `CrossFileHook` 的契约）：

- **原地修改**传入的 config dict，返回值不是数据载体，而是变更路径列表（点分）；无变更返回 `[]`。
- **幂等**：对已迁移数据重复执行零变更——用"键存在性""值等于旧默认"做守卫。
- **改默认值只改写等于旧默认的落盘值**，用户显式设置的其他值原样保留（范例：`_widen_recent_intents_window`、`_upgrade_replyer_style_drop_emoji`）。
- **跨文件搬移**用 `register_cross_file_hook(host_file, target_file, name, target_version, run)`，钩子签名 `(host_data, target_data)`，宿主与目标双写、双版本同升。target_file 必须与配置实际所在文件一致（写错文件会被校验当未知段剥离成数据丢失）。范例：`_upgrade_vts_lipsync_to_avatar`（tools.toml → avatar.toml）。
- 钩子内发现无法自动迁移的自定义值时**抛错带指引**（范例：`_upgrade_builder_scene_transport` 对非默认旧协议 raise），不要猜测映射或静默丢弃。
- 退役配置的新去向用户无从知晓时，钩子里 `logger.warning` 写明今后在哪配置（范例：`_drop_minecraft_companion_collectors`）。

### B3. 迁移测试

放 `tests/config/test_<语义>_migration.py`。标准骨架（范例 `tests/config/test_recent_intents_window_migration.py`）：

```python
def _write_old_default(tmp_path: Path, value: int, version: str = "<旧版本>") -> Path:
    """把 agents.toml 拨回旧布局：目标键落盘为旧默认，版本为旧版本。"""
    path = tmp_path / "agents.toml"
    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    doc["meta"]["version"] = version
    doc["agents"]["streamer"]["<key>"] = value
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return path


def test_old_default_rewritten_and_written_back(tmp_path: Path) -> None:
    generate_default_configs(tmp_path)   # 先生成基线文件
    path = _write_old_default(tmp_path, <旧默认>)
    load_config_dir(tmp_path)            # 走完整管线
    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    assert doc["agents"]["streamer"]["<key>"] == <新值>
    assert doc["meta"]["version"] == "<新版本>"
    first = path.read_text(encoding="utf-8-sig")
    load_config_dir(tmp_path)            # 幂等：第二次加载零变更
    assert path.read_text(encoding="utf-8-sig") == first


def test_user_custom_value_untouched() -> None:
    """用户显式设置过的值原样保留（直接调钩子函数断言）。"""
    data = {"agents": {"streamer": {"<key>": <用户自设值>}}}
    assert _upgrade_xxx(data) == []
    assert data["agents"]["streamer"]["<key>"] == <用户自设值>
```

要点：**"用户显式值不动"是独立用例**，直接调钩子函数断言零变更；**幂等用第二次加载文件逐字节不变来证明**；跨文件钩子另加目标文件断言（版本同升、旧段删除）。

### B4. 验证迁移写回落盘（不是跑了测试就算）

- **跑对应用例**：`uv run pytest tests/config/test_<语义>_migration.py -v`——用例里的"第二次加载零变更"断言 + 落盘断言即幂等与生效证据。
- **正式验收**（收尾做一次）：下次真实启动应用，确认日志出现同样的版本推进行，且旧结构在文件里确实消失——迁移的正式执行只发生在真实启动应用时。
- 人工核对落盘版本号可用 `get_config_version(config_dir, file_name)`（`multi_file_loader.py`）。

## 提交边界

一次迁移一笔提交：Schema 变更 + 钩子 + 迁移测试 + 组件 schema 测试同属一个意图。
