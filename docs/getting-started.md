# 快速开始

本文档帮助你快速设置开发环境并运行 Amaidesu 项目。

## 前置要求

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) - Python 包管理器

## 安装步骤

### 1. 安装 uv

**Windows (PowerShell)**:
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS/Linux**:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. 克隆项目

```bash
git clone <repository-url>
cd Amaidesu
```

### 3. 同步依赖

```bash
# 基础依赖
uv sync

# 包含语音识别（STT）
uv sync --extra stt

# 所有可选依赖
uv sync --all-extras
```

### 4. 配置项目

首次运行会自动生成配置文件：

```bash
# 生成默认配置
cp config-template.toml config.toml

# 编辑配置
# 根据需要修改 config.toml
```

## 运行项目

### 正常运行

```bash
uv run python main.py
```

### 调试模式

```bash
# 显示详细日志
uv run python main.py --debug

# 过滤特定模块的日志（只显示指定模块）
uv run python main.py --filter EdgeTTSProvider SubtitleProvider

# 调试模式并过滤特定模块
uv run python main.py --debug --filter VTSProvider
```

## 测试

### 运行所有测试

```bash
uv run pytest tests/
```

### 运行特定测试

```bash
# 特定文件
uv run pytest tests/layers/input/test_console_provider.py

# 特定用例
uv run pytest tests/layers/input/test_console_provider.py::test_console_provider_basic

# 详细输出
uv run pytest tests/ -v

# 只运行标记的测试
uv run pytest -m "not slow"
```

## 代码质量

### 代码检查

```bash
uv run ruff check .
```

### 代码格式化

```bash
uv run ruff format .
```

### 自动修复

```bash
uv run ruff check --fix .
```

## 常用命令

### 包管理

```bash
# 添加新依赖
uv add package-name

# 添加开发依赖
uv add --dev package-name

# 移除依赖
uv remove package-name

# 升级特定依赖
uv lock --upgrade-package package-name
```

### 运行特定脚本

```bash
# 模拟服务器（没有MaiCore时使用）
uv run python mock_maicore.py
```

## 目录结构

```
Amaidesu/
├── main.py              # CLI 入口
├── config-template.toml # 配置模板
├── config.toml          # 实际配置（首次运行生成）
├── src/                 # 源代码
│   ├── amaidesu_core.py # 核心协调器
│   ├── modules/         # 核心基础设施（跨域共享）
│   ├── services/        # 共享服务（LLM、配置、上下文）
│   ├── prompts/         # 提示词管理
│   └── domains/         # 业务域（input/decision/output）
├── tests/               # 测试
├── docs/                # 文档
└── refactor/            # 重构设计文档
```

## 下一步

- 阅读 [开发规范](development-guide.md) 了解代码风格
- 阅读 [3域架构总览](architecture/overview.md) 理解项目架构
- 阅读 [Provider 开发指南](development/provider-guide.md) 学习如何开发新功能
- 查看 [插件迁移完成文档](../plugins_backup/MIGRATION_COMPLETE.md) 了解架构迁移历史

## 新增 Provider（2026-02-09）

项目已完成从插件系统到 Provider 架构的重构，新增以下 Provider：

### 输入 Provider
- **STTInputProvider**: 语音转文字（讯飞ASR + Silero VAD）
- **BiliDanmakuOfficialInputProvider**: B站官方弹幕
- **ReadPingmuInputProvider**: 读屏木输入
- **BiliDanmakuOfficialMaicraftInputProvider**: B站官方弹幕 Maicraft 版本

### 决策 Provider
- **MaiCoreDecisionProvider**: MaiBot 核心决策
- **LLMDecisionProvider**: 本地 LLM 决策
- **MaicraftDecisionProvider**: 弹幕互动游戏决策

### 输出 Provider
- **EdgeTTSProvider**: Edge TTS 语音合成
- **GPTSoVITSOutputProvider**: GPT-SoVITS 高质量语音合成
- **OmniTTSProvider**: Fish TTS 语音合成
- **VTSProvider**: VTS 虚拟形象控制
- **VRChatProvider**: VRChat OSC 协议控制
- **WarudoOutputProvider**: Warudo 虚拟主播控制
- **SubtitleOutputProvider**: 字幕输出
- **StickerOutputProvider**: 贴纸输出
- **ObsControlOutputProvider**: OBS 控制（文本显示、场景切换）
- **RemoteStreamOutputProvider**: 远程流媒体输出

### 共享服务
- **DGLabService**: DG-LAB 硬件控制服务

### 平台适配器
- **VRChatAdapter**: VRChat OSC 协议适配器

详细的配置示例和使用说明，请参考：
- [Provider 开发指南 - 新增 Provider 详细说明](development/provider-guide.md#新增-provider-详细说明)
- [config-template.toml](../config-template.toml) 中的配置示例

## 遇到问题？

1. **依赖安装失败** → 确保 uv 和 Python 版本正确
2. **配置文件问题** → 检查 `config.toml` 格式
3. **运行时错误** → 使用 `--debug` 模式查看详细日志
4. **测试失败** → 运行 `uv run pytest tests/ -v` 查看详细输出

---

*更多详细信息，参见其他文档。*
