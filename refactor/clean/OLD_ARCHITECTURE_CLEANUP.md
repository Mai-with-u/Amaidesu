## 旧架构与遗留内容清理清单

本清单只列出需要「真正清理或收窄」的旧架构/旧系统遗留点，不包括纯文档对比说明。

- **MessagePipeline 旧管道体系**
  - 保留：`src/pipelines/message_logger/pipeline.py`（消息日志，明确标注为旧架构，仅做记录）。
  - 待清理/收敛：
    - `src/core/pipeline_manager.py` 中与 MessagePipeline 相关的加载/注册逻辑，可在确认只保留日志用途后缩到最小（或用更直白的 LoggingPipeline 替代）。
    - `main.py` 中关于「MessagePipeline（旧架构）」的注释和加载说明，改成「仅兼容 message_logger 日志管道」，避免给人可继续扩展旧架构管道的暗示。
    - `config-template.toml` 中关于「MessagePipeline（旧架构）」的注释，统一改成「仅保留日志用旧管道」或考虑完全移除这块配置示例。

- **插件系统（BasePlugin）相关遗留**
  - 现状：`plugins_backup/` 下仍存放 VTubeStudio / VRChat / Warudo 等旧插件实现及各自 config-template。
  - 建议：
    - 明确将 `plugins_backup/` 标记为「历史备份」，在根 README 或 refactor 文档中给出一句话说明：新架构不再使用 BasePlugin。
    - 若不再需要参考这些代码，可整体移动到 `archive/` 或单独仓库，只在文档中保留链接，避免误解为现役功能。
    - 清理主代码中仍提到「插件/Plugin 系统」的残留注释（若有），统一指向 Provider + Manager 架构。

- **配置初始化中的 plugin/pipeline 模板复制逻辑**
  - 现状：`main.py` 的 `load_config()` / `exit_if_config_copied()` 仍处理 `plugin_cfg_copied`、`pipeline_cfg_copied`，并提示「src/layers 下各插件 / src/pipelines 下各管道的 config.toml 已从模板创建」。
  - 待清理：
    - 将与「插件模板」相关的提示文案改为「Provider 配置模板」，对齐新架构术语。
    - 检查 `ConfigService` 内部是否还有针对旧插件目录的特例逻辑，如仅为旧系统服务且不再触发，可考虑删除或改名为「legacy_*」并在文档中声明。

- **服务注册与 EventBus 技术债（与旧服务定位方式相关）**
  - 现状：部分 Provider 仍通过 `event_bus._llm_service` 或旧的 `get_service` 思路获取服务，`REFACTOR_REMAINING.md` 和 `llm_service.md` 已标记为技术债。
  - 待清理：
    - 为 DecisionProvider 等明确引入依赖注入方式（构造/`setup()` 传入 `llm_service`），移除对 `event_bus._llm_service` 的依赖。
    - 收敛剩余的 `get_service` 使用点，改为通过显式依赖或 EventBus 事件交互，彻底删除旧的服务注册中心概念。

- **文档与命名上的旧架构残影**
  - 检查并统一以下位置的表述，使之强调「仅保留兼容层」而非「两套并行架构」：
    - `AGENTS.md` 中关于 `MessagePipeline` 的示例；建议标注为「仅用于历史日志管道」或迁移到 TextPipeline 示例。
    - `README.md` 对 MessagePipeline 的介绍；建议弱化其作为扩展点的角色，改为「历史兼容组件」。
    - 其他提到「插件系统」的地方，统一改为「Provider + Manager」或在旁边加注「插件系统已废弃」。
