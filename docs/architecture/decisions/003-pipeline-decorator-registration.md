# ADR-003: @pipeline 装饰器注册机制

## Context

当前 Pipeline 注册机制存在以下问题：

1. **目录扫描 + 反射类名机制脆弱**：当前 `src/stages/input/pipelines/manager.py:271-381` 通过扫描目录、动态 `importlib.import_module`、用 `inspect.getmembers` 反射查找类名必须匹配 `<Name>InputPipeline` 模式。如果类名不匹配，**静默不加载**（无报错），难以 debug。

2. **类名约束僵硬**：必须按 `<Name><Stage>Pipeline` 格式命名（Input 端需要 `Input` 后缀，Output 端不需要），违反命名对称性。

3. **与项目其他阶段不对称**：`@collector("xxx")`、`@decider("xxx")`、`@handler("xxx")` 都是装饰器注册（参见 `src/stages/input/registry.py`、`src/stages/decision/registry.py`、`src/stages/output/registry.py`），Pipeline 走目录扫描是唯一异类。

4. **类名错了静默失败**：开发者重命名 Pipeline 类时不会报错，运行时才发现"为什么这个 Pipeline 没生效"。

## Decision

**引入 `@pipeline("name")` 装饰器，与项目已有装饰器对称**：

```python
from src.modules.pipeline.registry import pipeline

@pipeline("rate_limit")
class RateLimitInputPipeline(InputPipeline):
    async def _process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]:
        ...
```

**Stage 自动从基类推断**：
- 继承 `InputPipeline`（即 `Pipeline[NormalizedMessage]`）→ stage = "input"
- 继承 `OutputPipeline`（即 `Pipeline[Intent]`）→ stage = "output"

**全局注册表**：`PIPELINE_REGISTRY: dict[tuple[str, str], type[Pipeline]] = {}`（key 为 `(stage, name)`）

**装饰器边界行为**：
- 重复注册（同一 `(stage, name)`）：抛出 `PipelineRegistrationError`（fail-fast）
- 抽象类（`inspect.isabstract(cls)`）：跳过，不注册
- 继承类：子类必须显式装饰，父类装饰不会自动继承

**测试隔离**：`clear_registry()` 供测试 fixture 使用，避免全局状态污染。

## Considered Alternatives

### 替代方案 A：保留目录扫描机制，仅增强错误提示

- **优点**：最小改动
- **缺点**：根本问题（隐式注册、类名约束）未解决
- **否决理由**：治标不治本

### 替代方案 B：用 setuptools entry_points 实现插件化

- **优点**：支持第三方 pip 安装插件
- **缺点**：项目当前是源码级扩展（修改项目代码加文件），不需要插件化
- **否决理由**：YAGNI（参见 ADR-004 关于核心层 vs 插件层的边界）

### 替代方案 C：装饰器 + 显式 register() 调用

- **优点**：更显式
- **缺点**：与已有装饰器模式（@collector/@decider/@handler）不一致
- **否决理由**：一致性更重要

### 替代方案 D：装饰器接收 priority / enabled 参数

- **优点**：装饰器内可设置默认值
- **缺点**：priority/enabled 是部署决策，不应由代码决定
- **否决理由**：违反"关注点分离"——代码声明身份，配置决定启用

## Consequences

### 短期收益

- 类名约束解除：Pipeline 类可任意命名（如 `RateLimit`、`TextFilter`），装饰器声明注册名
- 注册失败显式：重复注册立即抛错，不再静默失败
- 与项目其他装饰器一致：`@collector`/`@decider`/`@handler`/`@pipeline` 行为对称

### 长期影响

- 第三方开发者添加新 Pipeline 时只需：
  1. 创建文件 + 类
  2. 加 `@pipeline("name")` 装饰器
  3. 在 `config/*.toml` 启用
  4. 不需要记忆类名命名约定

### 风险

- 需要修改所有现有 Pipeline 添加装饰器
- 注册表是全局可变状态，测试需要 `clear_registry()` 隔离
- 装饰器必须在 import 时执行（与现有 `@collector` 等一致）

## Reversibility

回滚方法：`git revert <commit-hash>`，因为：
- 改动集中在 `src/modules/pipeline/registry.py`（新建）+ 现有 Pipeline 文件（添加装饰器）
- 装饰器本质上是装饰（不影响类行为）
- 旧目录扫描机制可以保留为 fallback（但用户明确要求不保留 fallback，直接删）

预计回滚成本：15 分钟。