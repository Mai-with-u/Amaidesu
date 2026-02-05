# Pipeline 设计

## 核心目标

TextPipeline 用于文本的预处理和过滤，位于 Input Domain 内部，在标准化完成后、发送到 Decision Domain 之前。

---

## 在架构中的位置

```
InputProvider → RawData
                  ↓
              InputLayer（标准化）
                  ↓
              NormalizedMessage
                  ↓
              TextPipeline（限流、过滤）⭐
                  ↓
              EventBus: normalization.message_ready
                  ↓
              Decision Domain
```

---

## TextPipeline 接口

```python
class TextPipeline(Protocol):
    """文本预处理管道"""
    
    priority: int  # 执行优先级（数值小优先）
    
    async def process(
        self, 
        message: NormalizedMessage
    ) -> Optional[NormalizedMessage]:
        """
        处理消息
        
        Returns:
            处理后的消息，或 None 表示丢弃
        """
        ...
```

---

## 内置 Pipeline

### RateLimitTextPipeline（限流）

控制消息处理频率，防止刷屏：

```python
class RateLimitTextPipeline(TextPipeline):
    priority = 100
    
    def __init__(self, config):
        self.max_per_second = config.get("max_per_second", 5)
        self.cooldown = config.get("cooldown", 0.5)
```

### SimilarTextFilterPipeline（相似文本过滤）

过滤短时间内的重复/相似消息：

```python
class SimilarTextFilterPipeline(TextPipeline):
    priority = 200
    
    def __init__(self, config):
        self.similarity_threshold = config.get("threshold", 0.8)
        self.window_seconds = config.get("window", 10)
```

---

## Pipeline 执行流程

```python
class PipelineManager:
    async def process_text_pipelines(
        self, 
        message: NormalizedMessage
    ) -> Optional[NormalizedMessage]:
        """按优先级顺序执行所有 Pipeline"""
        
        current = message
        for pipeline in sorted(self._pipelines, key=lambda p: p.priority):
            if not pipeline.enabled:
                continue
            
            result = await pipeline.process(current)
            if result is None:
                return None  # 消息被丢弃
            current = result
        
        return current
```

---

## 配置示例

```toml
[pipelines.rate_limit]
enabled = true
priority = 100
max_per_second = 5
cooldown = 0.5

[pipelines.similar_filter]
enabled = true
priority = 200
threshold = 0.8
window = 10
```
