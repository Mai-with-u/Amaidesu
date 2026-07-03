"""组件配置基类定义

定义所有组件（Collector/Decider/Handler）配置的基类，
提供漂移检测和 TOML 生成功能。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union, get_args, get_origin

import tomlkit
from pydantic import BaseModel, ConfigDict


def _unwrap_to_baseconfig(annotation: Any) -> type["BaseConfig"] | None:
    """从字段注解中提取 BaseConfig 类型

    处理 Optional[SomeConfig] / Union[SomeConfig, None] 包装，
    返回内部的 BaseConfig 子类；非 BaseConfig 类型返回 None。
    """
    origin = get_origin(annotation)
    if origin is Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            annotation = args[0]

    if isinstance(annotation, type) and issubclass(annotation, BaseConfig):
        return annotation
    return None


@dataclass
class DriftReport:
    """配置漂移报告

    记录配置文件与 Schema 之间的差异。

    Attributes:
        redundant: 配置文件中存在但 Schema 中未定义的字段（死键）
        missing: Schema 中定义但配置文件中缺失的字段（新默认）
    """

    redundant: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        """是否存在漂移"""
        return bool(self.redundant or self.missing)

    def merge(self, prefix: str, other: "DriftReport") -> None:
        """合并另一个漂移报告，字段名加上前缀"""
        self.redundant.extend(f"{prefix}.{r}" for r in other.redundant)
        self.missing.extend(f"{prefix}.{m}" for m in other.missing)


class BaseConfig(BaseModel):
    """组件配置基类

    所有组件（Collector/Decider/Handler）配置的基类，
    定义通用字段、漂移检测逻辑和 TOML 生成功能。

    注意：
    - 此基类不包含 'enabled' 字段，该字段由 Manager 统一管理
    - 组件本地配置文件禁止包含 'enabled' 字段
    - 使用 extra="forbid" 拒绝未知字段（安全网）
    - 加载配置时必须通过 from_dict() / from_dict_with_drift_check() 而非直接构造
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BaseConfig":
        """从字典构造配置实例，自动剥离未知字段

        等价于 from_dict_with_drift_check 但只返回实例，丢弃漂移报告。
        适用于组件内部加载配置（漂移检测在加载器层统一处理）。

        Args:
            data: 原始配置字典

        Returns:
            配置实例（未知字段已剥离）
        """
        instance, _report = cls.from_dict_with_drift_check(data)
        return instance

    @classmethod
    def from_dict_with_drift_check(
        cls,
        data: dict[str, Any],
    ) -> tuple["BaseConfig", DriftReport]:
        """加载配置并检测漂移

        对比配置数据与 Schema 的字段定义，收集多余和缺失的字段。
        多余字段会被剥离后再交给 Pydantic 构造，避免 extra="forbid" 报错。

        对于嵌套的 BaseConfig 字段，会递归进行漂移检测。

        Args:
            data: 原始配置字典

        Returns:
            (配置实例, 漂移报告)
        """
        report = DriftReport()

        class_fields = set(cls.model_fields.keys())
        data_keys = set(data.keys())

        # 检测多余字段（配置有，Schema 没有）
        for key in data_keys - class_fields:
            report.redundant.append(key)

        # 检测缺失字段（Schema 有，配置没有）
        for key in class_fields - data_keys:
            report.missing.append(key)

        # 剥离多余字段
        clean_data = {k: v for k, v in data.items() if k in class_fields}

        # 递归处理嵌套的 BaseConfig 字段
        for field_name, field_info in cls.model_fields.items():
            if field_name not in clean_data:
                continue

            actual_type = _unwrap_to_baseconfig(field_info.annotation)
            if actual_type is not None:
                nested_data = clean_data[field_name]
                if isinstance(nested_data, dict):
                    nested_instance, nested_report = actual_type.from_dict_with_drift_check(nested_data)
                    report.merge(field_name, nested_report)
                    clean_data[field_name] = nested_instance

        instance = cls(**clean_data)
        return instance, report

    @classmethod
    def generate_toml_string(
        cls,
        name: str | None = None,
        include_comments: bool = True,
    ) -> str:
        """从 Schema 生成 TOML 配置字符串

        生成包含所有可配置字段及其默认值的 TOML 字符串。
        字段的 description 会作为注释写入。

        Args:
            name: 组件名称（用于生成 TOML 中的表名），默认为类名的小写形式
            include_comments: 是否包含字段注释（从 Field description 生成）

        Returns:
            TOML 格式的配置字符串
        """
        table_name = name if name else cls.__name__.lower()

        doc = tomlkit.document()

        # 添加类文档字符串作为文件头注释
        if include_comments and cls.__doc__:
            docstring = cls.__doc__.strip()
            for line in docstring.split("\n"):
                doc.add(tomlkit.comment(line.strip()))
            doc.add(tomlkit.nl())

        # 创建配置表
        table = tomlkit.table()

        # 获取默认值
        schema_instance = cls()
        config_dict = schema_instance.model_dump()

        for field_name, field_info in cls.model_fields.items():
            field_value = config_dict.get(field_name)

            # TOML 不支持 null，跳过 None 值
            if field_value is None:
                continue

            # 添加字段注释
            if include_comments and field_info.description:
                table.add(tomlkit.comment(field_info.description))

            # 添加字段值
            _set_toml_value(table, field_name, field_value)

        doc[table_name] = table

        return tomlkit.dumps(doc)

    @classmethod
    def generate_toml(
        cls,
        output_path: str | Path,
        name: str | None = None,
        include_comments: bool = True,
    ) -> None:
        """从 Schema 生成 TOML 配置文件

        生成包含所有可配置字段及其默认值的 TOML 文件，用作配置模板。
        包含字段的 description 作为注释。

        Args:
            output_path: 输出文件路径
            name: 组件名称（用于生成 TOML 中的表名），默认为类名的小写形式
            include_comments: 是否包含字段注释（从 Field description 生成）

        Example:
            >>> ConsoleInputConfig.generate_toml("config.toml", "console_input")
        """
        output_path = Path(output_path)
        toml_content = cls.generate_toml_string(name, include_comments)
        output_path.write_text(toml_content, encoding="utf-8")

    @classmethod
    def get_default_dict(cls) -> dict[str, Any]:
        """获取默认配置字典

        Returns:
            包含所有字段默认值的字典
        """
        schema_instance = cls()
        return schema_instance.model_dump(exclude_unset=False)


def _set_toml_value(table: tomlkit.items.Table, key: str, value: Any) -> None:
    """将 Python 值设置到 tomlkit Table 中

    处理嵌套字典（转为子表）和列表中的字典元素。

    Args:
        table: tomlkit Table 对象
        key: 字段键名
        value: Python 值
    """
    # TOML 不支持 null
    if value is None:
        return

    if isinstance(value, dict):
        # 嵌套字典转为子表
        sub_table = tomlkit.table()
        for sub_key, sub_value in value.items():
            _set_toml_value(sub_table, sub_key, sub_value)
        table[key] = sub_table
    elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
        # 字典列表转为 TOML 数组表
        aot = tomlkit.aot()
        for item in value:
            item_table = tomlkit.table()
            for item_key, item_value in item.items():
                _set_toml_value(item_table, item_key, item_value)
            aot.append(item_table)
        table[key] = aot
    else:
        # 简单类型（str, int, float, bool, list）直接赋值
        table[key] = value
