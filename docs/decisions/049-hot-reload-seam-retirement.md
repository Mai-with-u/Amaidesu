# ADR-049：热重载回调接缝退役——infra 归入重启段

- 状态：已采纳
- 日期：2026-10-27
- 实现提交：随本 ADR 同笔交付（`git log -- docs/decisions/049-hot-reload-seam-retirement.md` 定位）

## 背景（Context）

ADR-014 的重载策略设计了 hot 段：infra 变更由 ConfigService 经"已注册回调"即时生效。该回调接缝自出生起零订阅者——`register_reload_callback` 全仓库无调用方；infra 的消费方（TTS / 字幕 / dashboard / 日志 / 拦截器）都是构造期快照，或走独立的启动期配置口（`main.py` 的 `configure_from_config`），没有任何一方具备"收通知后重读"的形态。hot 段的实际效果只剩一条：吞掉"需重启"警告，形成静默假生效——用户改了 infra.toml 看不到警告，误以为已生效。

## 决策（Decision）

**删除回调接缝（register / unregister / 回调类型 / 分发分支），infra 在策略表归入 `restart`；策略表只剩 `component` / `restart` 两态。**

- `reload_config` 的职责收缩为两件事：重读磁盘进内存合并视图 + 提示待重启。内存视图刷新对所有 scope 仍然发生。
- 组件域（agents / collectors）的保存即生效由 WebUI 保存路径的组件重建承担（`COMPONENT_REBUILD_SCOPES`），不受本决策影响。

## 替代方案（Alternatives）

- **真接线**（挑 infra 消费方改成"现读或订阅"形态）：适合热更的候选只有拦截器阈值、诊断落盘开关这类低频标量，无真实需求驱动（YAGNI）；每个消费方一个改造点，外加配置切换瞬间的读写一致性成本。将来若出现真实热更需求，按"消费方持有现读或订阅"的形态重建接缝——带着真实消费方设计，而不是复活一条无人使用的通道。

## 后果（Consequences）

- 改 infra.toml 与其他装配段一致：日志明确提示"需重启进程后完全生效"，静默假生效消失。
- 教义锁定测试（`test_no_scope_declares_hot_anymore`）防止 hot 声明复活。

## 参考（References）

- [ADR-014：配置体系六文件重构](014-config-six-file-refactor.md)（hot/restart 分流的出处，本 ADR 退役其 hot 半边）
