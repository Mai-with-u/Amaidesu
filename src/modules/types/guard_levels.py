"""大航海等级 → 中文名 映射（B站语义，跨阶段共享）。

``bili_danmaku_official`` 采集器（生成 guard 消息文本）与 dashboard widget
（渲染 guard 展示模板）共用同一份映射，避免两处各自维护造成语义漂移。
"""

GUARD_LEVEL_NAMES: dict[int, str] = {
    1: "总督",
    2: "提督",
    3: "舰长",
}

DEFAULT_GUARD_NAME = "大航海"
