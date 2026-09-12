"""配置域错误类型

校验失败的统一硬错载体：加载管线（multi_file_loader / upgrade）在类型违约、
引用缺失、版本缺失等场景抛出；错误信息必须携带文件名与字段 dotted path，
便于启动期定位与 WebUI 呈现。
"""

from __future__ import annotations


class ConfigValidationError(Exception):
    """配置校验失败（硬错）

    Attributes:
        file_name: 所属配置文件名（如 "agents.toml"）
        dotted_key: 字段路径（如 "llm_profiles.planner.model_list"）；
            与具体字段无关的文件级错误用 "" 表示
        message: 失败原因
    """

    def __init__(self, file_name: str, dotted_key: str, message: str) -> None:
        self.file_name = file_name
        self.dotted_key = dotted_key
        self.message = message
        location = f"{file_name}:{dotted_key}" if dotted_key else file_name
        super().__init__(f"配置校验失败 [{location}] {message}")


__all__ = ["ConfigValidationError"]
