"""
参数验证工具模块

提供基于 TypedDict 的自动参数验证功能。
"""

from typing import Any, Mapping, get_type_hints, get_origin, get_args, Type, TYPE_CHECKING, Optional
import sys

if TYPE_CHECKING:
    from typing import TypedDict


class ParamValidator:
    """
    参数验证器基类。

    自动根据 TypedDict 的类型注解生成验证逻辑。
    """

    @staticmethod
    def validate_typed_dict(params: Mapping[str, Any], typed_dict_class: Type) -> tuple[bool, str]:
        """
        验证参数是否符合 TypedDict 定义。

        Args:
            params: 要验证的参数字典
            typed_dict_class: TypedDict 类型类

        Returns:
            (是否有效, 错误信息)
        """
        try:
            # 获取 TypedDict 的类型提示
            hints = get_type_hints(typed_dict_class)

            # 检查必需字段（Python 3.11+ 支持 Required/NotRequired）
            required_keys = (
                typed_dict_class.__required_keys__
                if hasattr(typed_dict_class, "__required_keys__")
                else set(hints.keys())
            )
            optional_keys = (
                typed_dict_class.__optional_keys__ if hasattr(typed_dict_class, "__optional_keys__") else set()
            )

            # 检查是否缺少必需字段
            for key in required_keys:
                if key not in params:
                    return False, f"缺少必需参数: {key}"

            # 检查字段类型
            for key, value in params.items():
                if key not in hints:
                    # 忽略多余的键（TypedDict 允许）
                    continue

                expected_type = hints[key]

                # 基本类型检查
                if not ParamValidator._check_type(value, expected_type):
                    return False, f"参数 '{key}' 类型错误: 期望 {expected_type}, 实际 {type(value)}"

            return True, ""

        except Exception as e:
            return False, f"验证失败: {e}"

    @staticmethod
    def _check_type(value: Any, expected_type: type) -> bool:
        """
        检查值是否符合期望类型。

        Args:
            value: 要检查的值
            expected_type: 期望的类型

        Returns:
            是否匹配
        """
        # 处理 None
        if value is None:
            return expected_type is type(None) or get_origin(expected_type) is type(None)

        # 处理泛型类型（如 Union, Optional 等）
        origin = get_origin(expected_type)

        if origin is None:
            # 简单类型
            return isinstance(value, expected_type)

        # Union 类型（包括 Optional）
        if origin is type(None) or (sys.version_info >= (3, 10) and origin is type(None) | type):
            args = get_args(expected_type)
            return any(ParamValidator._check_type(value, arg) for arg in args)

        # 其他泛型类型暂时简单处理
        return isinstance(value, origin)


def create_validator(typed_dict_class: Type):
    """
    创建一个验证函数的装饰器工厂。

    Args:
        typed_dict_class: TypedDict 类型类

    Returns:
        验证函数
    """

    def validate_params(params: Mapping[str, Any]) -> bool:
        """验证参数"""
        valid, error = ParamValidator.validate_typed_dict(params, typed_dict_class)
        if not valid:
            # 这里可以记录日志
            print(f"参数验证失败: {error}")
        return valid

    return validate_params


# ============ 便捷的基类 ============


class ValidatedAction:
    """
    带自动验证的动作基类。

    子类只需要指定 PARAMS_TYPE 即可自动获得参数验证。
    """

    PARAMS_TYPE: Optional[Type] = None  # 子类需要设置这个

    def validate_params(self, params: Mapping[str, Any]) -> bool:
        """
        自动验证参数。

        Args:
            params: 参数字典

        Returns:
            参数是否有效
        """
        if self.PARAMS_TYPE is None:
            raise NotImplementedError("子类必须设置 PARAMS_TYPE")

        valid, error = ParamValidator.validate_typed_dict(params, self.PARAMS_TYPE)

        # 如果有 logger，记录错误
        if not valid:
            logger = getattr(self, "logger", None)
            if logger:
                logger.error(f"参数验证失败: {error}, 参数: {params}")

        return valid
