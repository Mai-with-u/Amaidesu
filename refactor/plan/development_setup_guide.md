# 开发环境准备指南

**用途**: 为参与 Amaidesu 重构的开发人员提供环境准备指南

## 目录

1. [前置要求](#前置要求)
2. [环境配置](#环境配置)
3. [分支策略](#分支策略)
4. [测试基础设施](#测试基础设施)
5. [持续集成配置](#持续集成配置)
6. [代码审查流程](#代码审查流程)
7. [开发工具配置](#开发工具配置)
8. [常见问题](#常见问题)

---

## 前置要求

### 软件要求

| 软件 | 最低版本 | 推荐版本 | 说明 |
|------|---------|---------|------|
| Python | 3.10 | 3.11+ | 支持 async/await 和类型注解 |
| Git | 2.30 | 2.40+ | 版本控制 |
| VSCode | 1.70 | 1.85+ | 推荐的代码编辑器 |
| PyCharm | 2023.1 | 2023.3+ | 可选的 IDE |

### Python 包要求

```bash
# 核心依赖（已安装）
asyncio  # Python 内置
aiohttp  # HTTP/WebSocket 客户端
loguru   # 日志记录
toml     # 配置文件解析
pytest   # 测试框架

# 重构新增依赖（需要安装）
pytest-asyncio  # 异步测试支持
pytest-cov      # 测试覆盖率
pytest-benchmark # 性能基准测试
```

### 硬件要求

| 资源 | 最低要求 | 推荐配置 |
|------|---------|---------|
| CPU | 4 核心 | 8 核心+ |
| 内存 | 8 GB | 16 GB+ |
| 磁盘 | 10 GB 可用空间 | 20 GB+ |
| 网络 | 1 Mbps | 10 Mbps+ |

---

## 环境配置

### 1. 克隆代码仓库

```bash
# 克隆主仓库（如果你还没有）
git clone https://github.com/your-username/Amaidesu.git
cd Amaidesu

# 或者更新现有仓库
git pull origin main
```

### 2. 创建虚拟环境

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/Mac
python3 -m venv venv
source venv/bin/activate
```

### 3. 安装依赖

```bash
# 安装现有依赖
pip install -r requirements.txt

# 安装重构所需的额外依赖
pip install pytest-asyncio pytest-cov pytest-benchmark

# 验证安装
pip list | grep -E "(asyncio|pytest|loguru|aiohttp)"
```

### 4. 验证环境

```bash
# 运行现有测试
python -m pytest tests/ -v

# 运行应用程序
python main.py --help

# 检查代码质量
ruff check .
ruff format .
```

### 5. 配置开发环境变量

创建 `.env` 文件（如果不存在）：

```bash
# .env
# 开发模式
DEBUG=True

# 日志级别
LOG_LEVEL=DEBUG

# MaiCore 配置（如果需要）
MAICORE_URL=ws://localhost:8080
MAICORE_API_KEY=your_api_key_here

# 其他配置...
```

---

## 分支策略

### 主分支

| 分支 | 用途 | 保护规则 |
|------|------|---------|
| `main` | 稳定的生产代码 | 必须通过 CI，必须有代码审查 |
| `develop` | 开发分支，合并所有 feature 分支 | 必须通过 CI，推荐代码审查 |

### 功能分支

```bash
# 命名规范: feature/phase{N}-{task-name}

# 示例：
feature/phase1-provider-interfaces
feature/phase2-input-console
feature/phase3-decision-maicore
feature/phase4-output-tts
feature/phase5-extension-minecraft
```

### 分支工作流

```
main (生产分支)
  ↑
  │ merge
  │
develop (开发分支)
  ↑
  │ merge
  │
feature/* (功能分支)
```

### 开发流程

1. **开始新任务**:
```bash
# 从 develop 创建功能分支
git checkout develop
git pull origin develop
git checkout -b feature/phase1-provider-interfaces

# 开始开发...
```

2. **提交代码**:
```bash
# 查看修改
git status
git diff

# 添加文件
git add .

# 提交（使用清晰的提交信息）
git commit -m "feat: implement InputProvider interface

- Add InputProvider abstract base class
- Add connect(), disconnect(), get_raw_data() methods
- Add unit tests for InputProvider"
```

3. **推送分支**:
```bash
# 推送到远程
git push -u origin feature/phase1-provider-interfaces

# 或者设置上游分支
git branch --set-upstream-to=origin/feature/phase1-provider-interfaces
git push
```

4. **创建 Pull Request**:
```bash
# 使用 GitHub CLI 创建 PR
gh pr create \
  --title "Implement InputProvider interface" \
  --body "## Summary
- Implement InputProvider interface
- Add unit tests

## Changes
- src/providers/base/input_provider.py (new file)
- tests/providers/test_input_provider.py (new file)

## Testing
- All tests pass
- Test coverage 100%

## Related
- Closes #1" \
  --base develop
```

5. **代码审查**:
   - 等待其他开发者审查
   - 根据反馈修改代码
   - 重新提交和推送

6. **合并到 develop**:
   - 通过 CI 检查
   - 至少一个审查批准
   - 合并到 develop

7. **删除功能分支**:
```bash
# 删除本地分支
git checkout develop
git branch -d feature/phase1-provider-interfaces

# 删除远程分支
git push origin --delete feature/phase1-provider-interfaces
```

### 阶段性合并策略

```
Phase 1 完成后:
  feature/phase1-* → develop → main (Alpha release)

Phase 2 完成后:
  feature/phase2-* → develop → main (Beta release)

Phase 3 完成后:
  feature/phase3-* → develop → main (RC release)

Phase 4, 5, 6 完成后:
  feature/phase4-*,5,6-* → develop → main (Final release)
```

---

## 测试基础设施

### 1. 测试目录结构

```
tests/
├── unit/                    # 单元测试
│   ├── providers/           # Provider 测试
│   ├── extensions/          # Extension 测试
│   ├── core/               # 核心模块测试
│   └── utils/              # 工具函数测试
├── integration/             # 集成测试
│   ├── test_input_layer.py
│   ├── test_decision_layer.py
│   ├── test_output_layer.py
│   └── test_extension_system.py
├── e2e/                    # 端到端测试
│   ├── test_full_pipeline.py
│   └── test_all_plugins.py
└── performance/            # 性能测试
    ├── test_response_time.py
    └── test_concurrency.py
```

### 2. 测试配置

创建 `pytest.ini` 文件：

```ini
[pytest]
# 测试发现
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*

# 异步测试
asyncio_mode = auto

# 覆盖率
addopts =
    -v
    --strict-markers
    --cov=src
    --cov-report=html
    --cov-report=term-missing
    --cov-fail-under=80

# 标记
markers =
    unit: Unit tests
    integration: Integration tests
    e2e: End-to-end tests
    performance: Performance tests
    slow: Slow tests (run separately)
```

### 3. 运行测试

```bash
# 运行所有测试
python -m pytest

# 运行特定类型的测试
python -m pytest -m unit              # 只运行单元测试
python -m pytest -m integration        # 只运行集成测试
python -m pytest -m e2e               # 只运行端到端测试

# 运行特定测试文件
python -m pytest tests/unit/providers/test_input_provider.py

# 运行特定测试函数
python -m pytest tests/unit/providers/test_input_provider.py::test_connect

# 运行测试并显示覆盖率
python -m pytest --cov=src --cov-report=html

# 运行性能测试
python -m pytest -m performance

# 运行慢速测试（通常在 CI 中运行）
python -m pytest -m slow
```

### 4. 编写测试

#### 单元测试示例

```python
# tests/unit/providers/test_input_provider.py
import pytest
from src.providers.base.input_provider import InputProvider

class MockInputProvider(InputProvider):
    """测试用的 Mock InputProvider"""
    
    async def connect(self):
        self._connected = True
    
    async def disconnect(self):
        self._connected = False
    
    async def get_raw_data(self):
        return {"content": "test input"}

@pytest.mark.unit
class TestInputProvider:
    
    @pytest.mark.asyncio
    async def test_connect(self):
        provider = MockInputProvider(config={})
        assert not provider._connected
        
        await provider.connect()
        assert provider._connected
    
    @pytest.mark.asyncio
    async def test_disconnect(self):
        provider = MockInputProvider(config={})
        await provider.connect()
        assert provider._connected
        
        await provider.disconnect()
        assert not provider._connected
    
    @pytest.mark.asyncio
    async def test_get_raw_data(self):
        provider = MockInputProvider(config={})
        await provider.connect()
        
        data = await provider.get_raw_data()
        assert data == {"content": "test input"}
```

#### 集成测试示例

```python
# tests/integration/test_input_layer.py
import pytest
from src.core.amaidesu_core import AmaidesuCore
from src.providers.input.console_input_provider import ConsoleInputProvider

@pytest.mark.integration
@pytest.mark.asyncio
class TestInputLayer:
    
    async def test_full_input_flow(self):
        # 创建核心实例
        core = AmaidesuCore(config={})
        
        # 创建输入 Provider
        provider = ConsoleInputProvider(config={})
        
        # 启动
        await provider.connect()
        
        # 获取输入数据
        data = await provider.get_raw_data()
        
        # 验证数据
        assert data is not None
        assert 'content' in data
        
        # 清理
        await provider.disconnect()
```

### 5. 测试覆盖率要求

| 模块类型 | 覆盖率要求 | 说明 |
|---------|-----------|------|
| 核心模块 | ≥ 90% | 关键路径必须覆盖 |
| Provider | ≥ 85% | 接口实现必须覆盖 |
| Extension | ≥ 80% | 基本功能必须覆盖 |
| 工具函数 | ≥ 95% | 所有逻辑必须覆盖 |

---

## 持续集成配置

### GitHub Actions 配置

创建 `.github/workflows/ci.yml` 文件：

```yaml
name: CI

on:
  push:
    branches: [ main, develop ]
  pull_request:
    branches: [ main, develop ]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install ruff
      
      - name: Check code style
        run: |
          ruff check .
          ruff format --check .
  
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ['3.10', '3.11', '3.12']
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v4
        with:
          python-version: ${{ matrix.python-version }}
      
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-asyncio pytest-cov pytest-benchmark
      
      - name: Run tests
        run: |
          pytest --cov=src --cov-report=xml --cov-report=term-missing
      
      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          file: ./coverage.xml
  
  performance:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-asyncio pytest-benchmark
      
      - name: Run performance tests
        run: pytest -m performance
      
      - name: Upload benchmark results
        uses: benchmark-action/github-action-benchmark@v1
        with:
          tool: 'pytest'
          output-file-path: benchmark.json
```

### Git Hooks 配置

使用 `pre-commit` 配置 Git hooks：

1. 安装 pre-commit:

```bash
pip install pre-commit
```

2. 创建 `.pre-commit-config.yaml` 文件：

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.1.9
    hooks:
      - id: ruff
        args: [--fix, --exit-non-zero-on-fix]
      - id: ruff-format
  
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
```

3. 安装 hooks:

```bash
pre-commit install
```

---

## 代码审查流程

### 1. 创建 Pull Request 前的检查清单

在创建 PR 前，确保：

- [ ] 所有测试通过（`pytest`）
- [ ] 代码检查通过（`ruff check .`）
- [ ] 代码格式正确（`ruff format .`）
- [ ] 测试覆盖率达标（≥ 80%）
- [ ] 文档已更新（README、API 文档）
- [ ] 提交信息清晰规范
- [ ] 功能已经自测
- [ ] 没有调试代码或注释掉的代码

### 2. Pull Request 模板

创建 `.github/PULL_REQUEST_TEMPLATE.md` 文件：

```markdown
## 变更类型

- [ ] 新功能
- [ ] Bug 修复
- [ ] 重构
- [ ] 文档更新
- [ ] 性能优化
- [ ] 测试

## 描述

简要描述此 PR 的变更内容和目的。

## 变更内容

列出修改的文件和主要变更：

- `src/providers/base/input_provider.py` - 添加 InputProvider 接口
- `tests/providers/test_input_provider.py` - 添加单元测试

## 测试

描述如何测试此变更：

- [ ] 单元测试通过
- [ ] 集成测试通过
- [ ] 手动测试通过

## 截图（如果适用）

上传相关截图。

## 关联 Issue

Closes #1

## 检查清单

- [ ] 代码遵循项目风格指南
- [ ] 添加了必要的测试
- [ ] 测试覆盖率 ≥ 80%
- [ ] 更新了文档
- [ ] 自测通过
```

### 3. 代码审查标准

#### 审查要点

| 类别 | 检查项 | 说明 |
|------|-------|------|
| 功能 | 逻辑正确性 | 代码逻辑是否正确，是否实现了预期功能 |
| 性能 | 性能影响 | 是否引入了性能问题 |
| 安全 | 安全性 | 是否有安全漏洞或风险 |
| 可读性 | 代码清晰 | 代码是否易于理解 |
| 可维护性 | 结构清晰 | 代码结构是否清晰，是否易于维护 |
| 测试 | 测试覆盖 | 测试是否充分，覆盖率是否达标 |
| 文档 | 文档完整 | 文档是否完整，注释是否清晰 |

#### 审查反馈模板

```markdown
## 总体评价

- [ ] 批准
- [ ] 需要修改
- [ ] 拒绝

## 主要问题

1. **功能问题**
   - 问题描述
   - 建议修改方案

2. **性能问题**
   - 问题描述
   - 建议修改方案

3. **安全问题**
   - 问题描述
   - 建议修改方案

## 次要问题

1. **可读性问题**
   - 问题描述

2. **代码风格问题**
   - 问题描述

## 建议

提出改进建议。

```

### 4. 审查流程

1. **创建 PR**: 开发者创建 Pull Request
2. **自动检查**: CI 自动运行测试和代码检查
3. **人工审查**: 其他开发者进行代码审查
4. **修改代码**: 根据反馈修改代码
5. **重新审查**: 重新提交和审查
6. **合并**: 审查通过后合并到目标分支

---

## 开发工具配置

### VSCode 配置

#### 推荐插件

1. Python (Microsoft)
2. Pylance (Microsoft)
3. Ruff (Astral Software Foundation)
4. Python Test Explorer (LittleFoxTeam)
5. GitLens (GitKraken)
6. TOML Language Support (be5invis)

#### VSCode 配置文件

创建 `.vscode/settings.json` 文件：

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/venv/bin/python",
  "python.linting.enabled": true,
  "python.linting.ruffEnabled": true,
  "python.formatting.provider": "ruff",
  "python.testing.pytestEnabled": true,
  "python.testing.pytestArgs": [
    "tests"
  ],
  "editor.formatOnSave": true,
  "editor.codeActionsOnSave": {
    "source.organizeImports": true,
    "source.fixAll.ruff": true
  },
  "files.exclude": {
    "**/__pycache__": true,
    "**/.pytest_cache": true,
    "**/*.pyc": true,
    ".venv": true,
    "venv": true
  }
}
```

#### VSCode 任务配置

创建 `.vscode/tasks.json` 文件：

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Run all tests",
      "type": "shell",
      "command": "python",
      "args": ["-m", "pytest", "tests/", "-v"],
      "group": {
        "kind": "test",
        "isDefault": true
      }
    },
    {
      "label": "Run linting",
      "type": "shell",
      "command": "ruff",
      "args": ["check", "."],
      "group": "test"
    },
    {
      "label": "Format code",
      "type": "shell",
      "command": "ruff",
      "args": ["format", "."],
      "group": "build"
    }
  ]
}
```

### PyCharm 配置

#### 推荐插件

1. TOML Support
2. Rainbow Brackets
3. Key Promoter X
4. String Manipulation

#### PyCharm 设置

1. **代码风格**:
   - 打开 `Settings → Editor → Code Style → Python`
   - 设置缩进为 4 空格
   - 设置行长度为 120 字符

2. **代码检查**:
   - 打开 `Settings → Tools → External Tools`
   - 添加 Ruff:
     - Name: `Ruff`
     - Program: `$PyInterpreterDirectory$/ruff`
     - Arguments: `check .`

3. **测试配置**:
   - 打开 `Settings → Tools → Python Integrated Tools`
   - Default test runner: `pytest`

---

## 常见问题

### 1. 依赖安装失败

**问题**: `pip install` 失败

**解决方案**:
```bash
# 升级 pip
pip install --upgrade pip

# 使用国内镜像源（如果在中国）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 或者使用 conda
conda env create -f environment.yml
```

### 2. 测试失败

**问题**: `pytest` 失败

**解决方案**:
```bash
# 查看详细错误信息
pytest -v -s

# 只运行失败的测试
pytest --lf

# 运行特定测试
pytest tests/unit/providers/test_input_provider.py::TestInputProvider::test_connect
```

### 3. Git 合并冲突

**问题**: 合并时出现冲突

**解决方案**:
```bash
# 拉取最新代码
git pull origin develop

# 解决冲突
# 编辑冲突文件，选择正确的版本

# 标记冲突已解决
git add .

# 完成合并
git commit

# 推送
git push
```

### 4. Lint 错误

**问题**: `ruff check .` 报错

**解决方案**:
```bash
# 自动修复可修复的问题
ruff check --fix .

# 手动修复剩余问题
# 查看 ruff 报告，逐个修复

# 格式化代码
ruff format .
```

### 5. 虚拟环境问题

**问题**: 虚拟环境无法激活

**解决方案**:
```bash
# 删除旧的虚拟环境
rm -rf venv

# 重新创建
python -m venv venv

# 激活（Windows）
venv\Scripts\activate

# 激活（Linux/Mac）
source venv/bin/activate

# 重新安装依赖
pip install -r requirements.txt
```

### 6. WebSocket 连接失败

**问题**: 无法连接到 WebSocket

**解决方案**:
```bash
# 检查 MaiCore 是否运行
# 检查配置文件中的 URL 是否正确
# 检查防火墙设置

# 使用调试模式查看详细日志
python main.py --debug --filter MaiCoreDecisionProvider
```

---

## 文档创建时间

**创建时间**: 2026-01-18
**版本**: 1.0
**状态**: 可用
