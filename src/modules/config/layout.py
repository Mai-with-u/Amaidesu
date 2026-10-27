"""七文件配置布局的单一事实源。

声明 config/ 目录下的配置文件清单：scope 名（= 文件名去 ``.toml`` 后缀）、
文件名与两者的映射。加载管线（multi_file_loader）、ConfigService 与
WebUI 配置视图都按这份清单工作，改文件布局只改这里。

清单顺序即权威顺序：既是加载管线处理各文件的次序，也是 WebUI Schema
分组的展示次序。avatar.toml 排在 tools.toml 之后：avatar 迁出跨文件
钩子（tools/infra → avatar）以 avatar.toml 为目标文件，目标 dict 在
加载阶段①已就位；宿主文件（tools/infra）先于 avatar 自身钩子被遍历，
"先搬家、后做数据变换"的次序由此保证。
"""

from __future__ import annotations

# 七文件的 scope 清单（= 文件名去后缀），按权威顺序排列
CONFIG_SCOPES: tuple[str, ...] = ("agents", "collectors", "tools", "avatar", "model", "storage", "infra")

# 七文件的 TOML 文件名，与 CONFIG_SCOPES 同序
CONFIG_FILE_NAMES: tuple[str, ...] = tuple(f"{scope}.toml" for scope in CONFIG_SCOPES)

# TOML 文件名 → 合并视图 scope
CONFIG_FILE_TO_SCOPE: dict[str, str] = dict(zip(CONFIG_FILE_NAMES, CONFIG_SCOPES, strict=True))
