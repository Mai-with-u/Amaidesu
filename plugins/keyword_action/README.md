# Keyword Action 插件

## 概述

`KeywordActionPlugin` 是一个通用的、可扩展的**关键词触发总线**。

它的核心功能是监听所有传入的文本消息，并根据一个可配置的规则列表，当检测到特定关键词时，执行相应的"动作脚本"。这使得非程序员用户也能通过修改配置文件，轻松地创建"如果用户说A，就执行B"的自动化规则。

## 工作原理

1.  **加载规则**: 插件启动时，会从其配置文件 (`config.toml`) 中加载一个 `[[actions]]` 列表。
2.  **监听消息**: 插件会监听所有文本消息。
3.  **匹配规则**: 对于每条消息，它会遍历所有已启用的动作规则：
    a. **关键词匹配**: 根据规则的 `match_mode` (完全匹配, 包含, 开头是, 等) 检查消息文本是否包含任意一个指定的 `keywords`。
    b. **冷却检查**: 检查该规则是否处于冷却时间 (`cooldown`) 内，如果是则跳过。
4.  **执行动作**: 一旦找到第一个匹配的、且不在冷却中的规则，插件会：
    a. 记录该规则的触发时间，以供下次冷却检查。
    b. 动态地加载并执行 `actions/` 子目录中与规则的 `action_script` 对应的 Python 脚本。
5.  **脚本执行**: 动作脚本是一个简单的 Python 文件，其中必须包含一个 `async def execute(core, message)` 函数。插件会将 `AmaidesuCore` 的实例和触发该动作的原始消息 `MessageBase` 对象传递给这个函数，使动作脚本拥有完全的上下文和对核心系统所有服务（如 `vts_control`, `dg_lab_control` 等）的调用能力。

## 如何添加新动作

添加一个新动作非常简单，只需两步：

1.  **创建动作脚本**: 在 `src/plugins/keyword_action/actions/` 目录下创建一个新的 `.py` 文件。例如 `play_sound.py`。
    ```python
    # src/plugins/keyword_action/actions/play_sound.py
    async def execute(core, message):
        # 假设你有一个名为 'sound_player' 的服务
        sound_service = core.get_service("sound_player")
        if sound_service:
            await sound_service.play("hello.wav")
    ```

2.  **在配置中添加规则**: 打开 `keyword_action` 的 `config.toml` 文件，在 `[[actions]]` 列表的末尾添加一个新的规则块。
    ```toml
    # src/plugins/keyword_action/config.toml
    
    # ... 其他规则 ...

    [[actions]]
    name = "播放音效"
    enabled = true
    keywords = ["你好", "打个招呼"]
    action_script = "play_sound.py"
    cooldown = 10.0
    match_mode = "exact"
    ```
重启应用后，新的关键词触发规则即可生效。

## 配置详解

- `enabled`: 是否启用整个插件。
- `global_cooldown`: 全局冷却时间（秒），当动作规则没有单独设置 `cooldown` 时生效。
- `[[actions]]`: 一个TOML数组表，每个块代表一条规则。
    - `name`: 规则的可读名称，用于日志输出。
    - `enabled`: 是否启用本条规则。
    - `keywords`: 触发此规则的关键词列表。
    - `action_script`: 对应的动作脚本文件名，位于 `actions/` 目录下。
    - `cooldown`: (可选) 本条规则的独立冷却时间，会覆盖 `global_cooldown`。
    - `match_mode`: (可选) 关键词的匹配模式，默认为 `"anywhere"`。
        - `"anywhere"`: 关键词出现在消息文本的任何位置。
        - `"exact"`: 消息文本必须与列表中的某个关键词完全相同。
        - `"startswith"`: 消息文本必须以列表中的某个关键词开头。
        - `"endswith"`: 消息文本必须以列表中的某个关键词结尾。 