# 插件备份目录

## 概述

此目录包含 Amaidesu 项目的旧插件代码备份。这些代码已被新的 Provider 系统替代，仅供历史参考和代码迁移分析使用。

## 📌 重要说明

**这些插件代码已废弃**，不应用于新开发。所有新功能都应使用新的 Provider 架构实现。

## 迁移状态

所有插件已成功迁移到新架构：

- ✅ **Input Provider**: 7 个全部完成迁移
- ✅ **Decision Provider**: 2 个完成迁移，1 个不迁移
- ✅ **Output Provider**: 11 个完成迁移
- ✅ **Service**: 1 个完成迁移

详细信息请查看：[`MIGRATION_COMPLETE.md`](./MIGRATION_COMPLETE.md)

## 目录结构

```
plugins_backup/
├── README.md                          # 本文件
├── MIGRATION_COMPLETE.md              # 迁移完成清单
├── Input/                            # 输入插件备份
│   ├── bili_danmaku/                  # B站弹幕（旧版）
│   ├── bili_danmaku_official/         # B站官方弹幕（旧版）
│   ├── console_input/                 # 控制台输入（旧版）
│   ├── stt/                          # 语音识别（旧版）
│   ├── mainosaba/                    # 麦木巴巴（旧版）
│   ├── read_pingmu/                   # 读屏木（旧版）
│   └── mock_danmaku/                  # 模拟弹幕（旧版）
├── Decision/                         # 决策插件备份
│   ├── keyword_action/                # 关键词动作（旧版）
│   ├── maicraft/                      # MaiCraft（旧版）
│   └── emotion_judge/                 # 情感判断（不迁移）
├── Output/                           # 输出插件备份
│   ├── tts/                          # Edge TTS（旧版）
│   ├── subtitle/                     # 字幕（旧版）
│   ├── vtube_studio/                 # VTS（旧版）
│   ├── gptsovits_tts/                # GPT-SoVITS（旧版）
│   ├── omni_tts/                     # Omni TTS（旧版）
│   ├── vrchat/                       # VRChat（旧版，已合并到 avatar）
│   ├── sticker/                      # 表情贴纸（旧版）
│   ├── obs_control/                  # OBS控制（旧版）
│   ├── warudo/                       # Warudo（旧版）
│   └── remote_stream/                # 远程流（旧版）
├── Service/                          # 服务插件备份
│   └── dg_lab_service/               # DG-Lab服务（旧版）
└── 其他辅助文件...
```

## 新架构参考

### Provider 系统

新的 Provider 系统位于以下目录：

- **Input Provider**: `src/domains/input/providers/`
- **Decision Provider**: `src/domains/decision/providers/`
- **Output Provider**: `src/domains/output/providers/`

### 配置管理

新的配置统一在 `config-template.toml` 中管理：

```toml
# 输入Provider
[providers.input]
enabled_inputs = ["console_input", "bili_danmaku"]

# 决策Provider
[providers.decision]
active_provider = "maicore"

# 输出Provider
[providers.output]
enabled_outputs = ["tts", "subtitle", "vts"]
```

### 3域架构

项目采用严格的 3 域架构：

```
外部输入 → 【Input Domain】NormalizedMessage → 【Decision Domain】Intent → 【Output Domain】渲染输出
```

## 重要提示

1. **不要直接使用这些插件代码**
2. **参考迁移文档理解架构变化**
3. **新功能请使用 Provider 系统开发**
4. **配置文件已更新为新的 TOML 格式**

## 相关文档

- [迁移完成清单](./MIGRATION_COMPLETE.md)
- [AGENTS.md](../AGENTS.md) - 项目核心规则
- [Provider 开发指南](docs/development/provider-guide.md)
- [3域架构](docs/architecture/overview.md)

---

*备份创建时间：2026-02-09*
*最后更新：2026-02-09*