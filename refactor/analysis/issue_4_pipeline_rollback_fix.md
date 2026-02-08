# 问题#4修复：管道错误处理回滚机制

## 问题描述

**文件位置**: `src/domains/input/pipelines/manager.py`

**问题**: CONTINUE 错误处理模式下，管道失败后文本可能处于部分修改状态，导致数据不一致。

例如：
1. Pipeline1 修改内部状态（添加时间戳到队列）
2. Pipeline2 修改文本（`text = "MODIFIED: " + text`）
3. Pipeline2 失败，CONTINUE 模式继续执行
4. Pipeline3 收到的是被 Pipeline2 修改后的文本，而不是原始文本
5. Pipeline1 的状态修改也没有被撤销

这会导致数据不一致和状态污染。

## 解决方案

实现 PipelineContext 类，支持回滚机制，确保管道失败时可以恢复到原始状态。

### 核心组件

#### 1. PipelineContext 类

```python
@dataclass
class PipelineContext:
    """管道执行上下文，支持回滚"""
    original_text: str
    original_metadata: Dict[str, Any]
    rollback_actions: List[Callable] = field(default_factory=list)

    def add_rollback(self, action: Callable) -> None:
        """添加回滚动作"""
        self.rollback_actions.append(action)

    async def rollback(self) -> None:
        """执行所有回滚动作（逆序执行）"""
        for action in reversed(self.rollback_actions):
            try:
                if asyncio.iscoroutinefunction(action):
                    await action()
                else:
                    action()
            except Exception as e:
                logger.warning(f"回滚动作执行失败: {e}", exc_info=True)
```

**特性**:
- 保存原始文本和元数据
- 支持注册多个回滚动作
- 回滚动作按逆序执行（LIFO）
- 回滚失败不会中断整体回滚流程

#### 2. TextPipeline 接口更新

```python
async def process(
    self,
    text: str,
    metadata: Dict[str, Any],
    context: Optional[PipelineContext] = None  # 新增参数
) -> Optional[str]:
    """处理文本"""
    ...
```

#### 3. TextPipelineBase 更新

```python
async def process(
    self,
    text: str,
    metadata: Dict[str, Any],
    context: Optional[PipelineContext] = None  # 新增参数
) -> Optional[str]:
    """处理文本（包装 _process 并记录统计）"""
    ...

@abstractmethod
async def _process(
    self,
    text: str,
    metadata: Dict[str, Any],
    context: Optional[PipelineContext] = None  # 新增参数
) -> Optional[str]:
    """实际处理文本（子类实现）"""
    ...
```

### 错误处理策略

#### CONTINUE 模式
```python
else:
    # CONTINUE模式：回滚所有副作用，使用原始文本继续
    await context.rollback()
    current_text = context.original_text
```
- **行为**: 回滚所有已注册的回滚动作，使用原始文本继续执行后续管道
- **目的**: 防止部分修改状态污染后续处理

#### STOP 模式
```python
if pipeline.error_handling == PipelineErrorHandling.STOP:
    # STOP模式：回滚所有副作用后抛出异常
    await context.rollback()
    raise error from e
```
- **行为**: 回滚所有副作用，抛出异常终止处理
- **目的**: 确保失败时系统状态一致

#### DROP 模式
```python
elif pipeline.error_handling == PipelineErrorHandling.DROP:
    # DROP模式：回滚所有副作用后丢弃消息
    stats.dropped_count += 1
    await context.rollback()
    return None
```
- **行为**: 回滚所有副作用，返回 None 丢弃消息
- **目的**: 清理状态并跳过该消息

### 管道实现示例

#### RateLimitTextPipeline 回滚实现

```python
async def _record_message(
    self,
    user_id: str,
    current_time: float,
    context: Optional[PipelineContext] = None
) -> None:
    """记录消息的发送时间到对应队列"""
    async with self._lock:
        self._global_timestamps.append(current_time)
        self._user_timestamps[user_id].append(current_time)

        # 注册回滚动作
        if context is not None:
            async def rollback():
                async with self._lock:
                    # 移除全局时间戳
                    if self._global_timestamps and self._global_timestamps[-1] == current_time:
                        self._global_timestamps.pop()

                    # 移除用户时间戳
                    user_queue = self._user_timestamps.get(user_id)
                    if user_queue and user_queue[-1] == current_time:
                        user_queue.pop()
                        if not user_queue:
                            del self._user_timestamps[user_id]

            context.add_rollback(rollback)
```

#### SimilarFilterTextPipeline 回滚实现

```python
async def _process(
    self,
    text: str,
    metadata: Dict[str, Any],
    context: Optional[PipelineContext] = None
) -> Optional[str]:
    """处理文本，检查是否需要过滤"""
    # ... 检查逻辑 ...

    # 添加到缓存
    self._text_cache[group_id].append((now, text, user_id))

    # 注册回滚动作
    if context is not None:
        def rollback():
            # 移除最后添加的文本
            if (self._text_cache[group_id]
                and self._text_cache[group_id][-1] == (now, text, user_id)):
                self._text_cache[group_id].pop()
                if not self._text_cache[group_id]:
                    del self._text_cache[group_id]

        context.add_rollback(rollback)

    return text
```

## 测试验证

### 测试文件: `tests/core/test_pipeline_rollback.py`

包含 8 个测试用例，全面验证回滚机制：

1. **test_pipeline_rollback_on_continue_error**: 验证 CONTINUE 模式下失败时回滚所有副作用
2. **test_pipeline_rollback_on_drop_error**: 验证 DROP 模式下回滚所有副作用
3. **test_pipeline_rollback_on_stop_error**: 验证 STOP 模式下回滚并抛出异常
4. **test_pipeline_rollback_on_normal_drop**: 验证正常返回 None 时回滚所有副作用
5. **test_pipeline_context_multiple_rollback_actions**: 验证多个回滚动作的执行
6. **test_pipeline_rollback_preserves_original_text**: 验证失败后使用原始文本继续
7. **test_pipeline_no_rollback_on_success**: 验证成功时不执行回滚
8. **test_pipeline_rollback_execution_order**: 验证回滚动作按逆序执行

### 测试结果

```
========================= 32 passed in 0.96s =========================
```

所有 32 个测试用例通过，包括：
- 24 个原有的 PipelineManager 测试
- 8 个新增的回滚功能测试

## 影响范围

### 修改的文件

1. **src/domains/input/pipelines/manager.py**
   - 新增 `PipelineContext` 类
   - 更新 `TextPipeline` 协议，添加可选的 `context` 参数
   - 更新 `TextPipelineBase` 基类，添加可选的 `context` 参数
   - 修改 `process_text()` 方法，实现回滚逻辑

2. **src/domains/input/pipelines/rate_limit/pipeline.py**
   - 更新 `_record_message()` 方法，注册回滚动作
   - 更新 `_process()` 方法签名，添加 `context` 参数

3. **src/domains/input/pipelines/similar_filter/pipeline.py**
   - 更新 `_process()` 方法，注册回滚动作
   - 添加 `context` 参数支持

4. **tests/core/test_pipeline_manager.py**
   - 更新 Mock 类，添加 `context` 参数支持

### 新增的文件

1. **tests/core/test_pipeline_rollback.py**
   - 完整的回滚功能测试套件

## 向后兼容性

- **完全兼容**: `context` 参数是可选的（`Optional[PipelineContext] = None`）
- **现有管道**: 不使用 `context` 参数的管道仍能正常工作
- **新管道**: 可以选择性地实现回滚功能

## 使用指南

### 管道开发者指南

如果您的管道修改了内部状态（如缓存、队列等），应该实现回滚：

```python
async def _process(
    self,
    text: str,
    metadata: Dict[str, Any],
    context: Optional[PipelineContext] = None
) -> Optional[str]:
    # 1. 修改状态
    self._internal_state.append(new_item)

    # 2. 注册回滚动作（如果 context 可用）
    if context is not None:
        def rollback():
            # 撤销状态修改
            if self._internal_state and self._internal_state[-1] == new_item:
                self._internal_state.pop()

        context.add_rollback(rollback)

    # 3. 继续处理
    return processed_text
```

### 最佳实践

1. **幂等性**: 回滚动作应该是幂等的，多次执行不应该产生错误
2. **原子性**: 回滚动作应该完全撤销对应的操作
3. **异常处理**: 回滚动作内部应该处理异常，避免影响其他回滚动作
4. **性能**: 回滚动作应该快速执行，避免阻塞

## 总结

问题#4修复通过引入 PipelineContext 回滚机制，彻底解决了管道错误处理可能导致的数据损坏问题：

- ✅ **状态一致性**: 管道失败时，所有副作用都可以被正确回滚
- ✅ **文本完整性**: CONTINUE 模式下，失败后使用原始文本继续，避免部分修改
- ✅ **向后兼容**: 现有管道无需修改即可继续工作
- ✅ **可扩展性**: 新管道可以灵活选择是否实现回滚功能
- ✅ **测试覆盖**: 完整的测试套件确保功能正确性
