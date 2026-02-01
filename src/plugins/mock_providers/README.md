# MockProviders Plugin - 模拟插件

## 简介

MockProviders Plugin 是一个完整的模拟插件，为系统的每一层提供模拟Provider，用于测试和演示。

## 功能

这个插件提供了三层的模拟Provider：

### 1. 输入层 - MockDanmakuProvider
- 模拟弹幕输入
- 生成随机弹幕消息
- 可配置发送间隔

### 2. 决策层 - MockDecisionProvider
- 基于关键词匹配生成回复
- 模拟AI思考延迟
- 支持随机回复变化

### 3. 输出层
- **MockTTSProvider**: 模拟TTS输出，打印到控制台
- **MockSubtitleProvider**: 模拟字幕输出，显示边框字幕

## 使用方法

### 方式1：在main.py中使用（推荐）

1. 确保已生成配置文件：
```bash
uv run python main.py
# 程序会生成config.toml，然后退出
```

2. 编辑 `config.toml`，取消注释 `mock_providers`：
```toml
[plugins]
enabled = [
    "mock_providers",    # 模拟输入、决策、输出三层（推荐用于测试）
    # 其他插件...
]
```

3. 重新运行程序：
```bash
uv run python main.py
```

程序会显示：
- 模拟弹幕输入（日志）
- 模拟AI决策（日志）
- 模拟TTS输出（控制台打印）
- 模拟字幕输出（控制台显示边框）

### 方式2：使用测试脚本

```bash
uv run python test_mock_providers.py
```

测试脚本会：
- 加载插件
- 运行30秒
- 显示所有输出
- 自动清理

## 配置选项

### 插件配置 (src/plugins/mock_providers/config-template.toml)

```toml
[general]
enabled = true              # 是否启用插件
start_immediately = true    # 是否立即开始采集
enable_input = true         # 启用模拟弹幕输入
enable_decision = true      # 启用模拟AI决策
enable_output = true        # 启用模拟输出

[general.input]
send_interval = 1.0         # 弹幕发送间隔（秒）
min_interval = 0.5          # 最小随机间隔
max_interval = 2.0          # 最大随机间隔

[general.decision]
response_delay = 0.5        # AI思考延迟（秒）
enable_keyword_match = true # 启用关键词匹配
add_random_variation = true # 添加随机变化

[general.output.tts]
speak_delay = 0.0           # TTS播放延迟
show_timestamp = true       # 显示时间戳
prefix = "🔊 TTS"          # 输出前缀

[general.output.subtitle]
display_duration = 2.0      # 字幕显示时长
show_border = true          # 显示边框
border_char = "═"          # 边框字符
width = 60                  # 字幕宽度
```

## 示例输出

运行程序后，你会看到类似这样的输出：

```
[10:30:45] 🔊 TTS 你好呀！很高兴见到你~
══════════════════════════════════════════════════════
║              你好呀！很高兴见到你~               ║
══════════════════════════════════════════════════════

[10:30:47] 🔊 TTS 哈哈！
══════════════════════════════════════════════════════
║                    哈哈！                         ║
══════════════════════════════════════════════════════
```

## 关键词回复

MockDecisionProvider 支持以下关键词：

| 关键词 | 回复示例 |
|--------|----------|
| 你好/嗨/哈喽 | "你好呀！很高兴见到你~" |
| 哈哈/呵呵 | "什么事情这么好笑呀？" |
| 谢谢/感谢 | "不客气！这是我应该做的~" |
| 厉害/牛逼 | "哪有哪有，还需要继续努力呢~" |
| 再见/拜拜 | "再见啦！下次见~" |
| 你是谁/介绍 | "我是Amaidesu，一个AI虚拟助手~" |

## 扩展

### 添加自定义关键词回复

编辑 `src/plugins/mock_providers/providers/mock_decision_provider.py`，在 `KEYWORD_RESPONSES` 字典中添加：

```python
KEYWORD_RESPONSES = {
    # ... 现有关键词 ...
    r"你的名字|名字": [
        "我叫Amaidesu~",
        "你可以叫我Amaidesu！",
    ],
}
```

### 自定义弹幕内容

编辑 `src/layers/input/providers/mock_danmaku_provider.py`，在 `DANMAKU_TEMPLATES` 列表中添加：

```python
DANMAKU_TEMPLATES = [
    # ... 现有弹幕 ...
    "新弹幕内容",
    "另一个新弹幕",
]
```

## 文件结构

```
src/plugins/mock_providers/
├── __init__.py
├── plugin.py                 # 插件主文件
├── config-template.toml      # 配置模板
├── README.md                 # 本文件
└── providers/
    ├── __init__.py
    ├── mock_decision_provider.py  # 决策Provider
    └── mock_output_provider.py    # 输出Provider
```

## 依赖

- 无额外依赖
- 使用项目现有的Provider接口

## 注意事项

1. 此插件仅用于测试和演示，不适用于生产环境
2. 模拟弹幕是随机生成的，不是真实用户输入
3. 模拟决策是基于简单规则的，不使用真实LLM
4. 模拟输出只打印到控制台，不产生实际效果

## 故障排除

### 插件没有加载

检查 `config.toml` 中是否启用了插件：
```toml
[plugins]
enabled = ["mock_providers"]
```

### 没有输出

1. 检查日志级别，使用 `--debug` 参数
2. 检查配置文件中的 `enable_*` 选项
3. 查看日志是否有错误信息

### 间隔时间太长/太短

调整配置中的 `send_interval`、`min_interval`、`max_interval`：
```toml
[general.input]
send_interval = 0.5  # 更快的弹幕
```

## 贡献

欢迎提交改进建议和bug报告！
