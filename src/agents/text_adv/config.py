"""TextAdvGameAgent 配置（包内单一权威）

只放"该游戏特有"的配置（其它公用依赖经构造器注入，不走配置）。
默认值即可构造——不连真实文字冒险世界也可启动（空闲零消耗，无副作用）。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig


def _default_keys() -> Dict[str, str]:
    """键位单表默认值（实测有效键；危险键 s/h/f 不绑）"""
    return {"advance": "space", "skip": "ctrl", "menu": "backspace"}


class TextAdvConfig(BaseConfig):
    """TextAdvGameAgent 运行时配置

    Attributes:
        monitor_index: 游戏窗口所在显示器编号（0 起）
        region: 截图区域 [x, y, w, h]（相对所选显示器左上角，物理像素）；
            None = 整个显示器。语义为整个游戏窗口，读什么交给识别问句引导
        keys: 动作名 → 按键名的单表映射（advance/skip/menu 可按需覆盖）
        stability_sample_ms: 画面稳定判定的采样间隔（毫秒）
        stability_consecutive: 判定画面稳定所需的连续一致采样次数
        stability_timeout_ms: 稳定判定的超时兜底（毫秒）——到点按当前画面放行
        no_change_limit: 连续无变化采样次数上限——超过即判画面静止并触发兜底
        max_recent_screens: 累积剧情环缓冲保留的最近屏数
        auto_button_xy: AUTO 按钮标定坐标 [x, y]（显示器相对物理像素）；
            None = 未标定，AUTO 模式走键盘键位
        game_window_title_keyword: 游戏窗口标题关键词（空串 = 未指定，用于夺焦）
    """

    monitor_index: int = Field(default=1, title="显示器编号", ge=0, description="游戏窗口所在显示器编号（0 起）")
    region: Optional[List[int]] = Field(
        default=None,
        title="截图区域",
        description="截图区域 [x, y, w, h]（相对所选显示器左上角，物理像素）；None = 整个显示器",
        # 值恒为四元组：前端渲染为一行 4 个并排数字输入，不走通用数组编辑器
        json_schema_extra={"x-ui-widget": "fixed-tuple", "x-ui-tuple-length": 4},
    )
    keys: Dict[str, str] = Field(
        default_factory=_default_keys,
        title="键位映射",
        description="动作名 → 按键名映射（advance=推进/skip=快进（按住）/menu=菜单）",
    )
    stability_sample_ms: int = Field(
        default=150, title="稳定采样间隔", ge=10, le=2000, description="画面稳定判定的采样间隔（毫秒）"
    )
    stability_consecutive: int = Field(
        default=2, title="稳定判定次数", ge=2, le=10, description="判定画面稳定所需的连续一致采样次数"
    )
    stability_timeout_ms: int = Field(
        default=5000, title="稳定判定超时", ge=100, le=60_000, description="稳定判定超时兜底（毫秒）"
    )
    no_change_limit: int = Field(
        default=20, title="画面静止阈值", ge=1, le=1000, description="连续无变化采样次数上限（超过判画面静止）"
    )
    max_recent_screens: int = Field(
        default=10, title="剧情缓冲屏数", ge=1, le=100, description="累积剧情环缓冲保留的最近屏数"
    )
    auto_button_xy: Optional[tuple[int, int]] = Field(
        default=None,
        title="AUTO 按钮坐标",
        description="AUTO 按钮标定坐标 [x, y]（显示器相对物理像素）；None = 未标定",
    )
    game_window_title_keyword: str = Field(
        default="", title="窗口标题关键词", description="游戏窗口标题关键词（空串 = 未指定）"
    )


__all__ = ["TextAdvConfig"]
