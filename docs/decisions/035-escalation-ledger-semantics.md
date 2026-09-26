# ADR-035：escalation 账面语义修正（failed 终态改写 waiting_for_decision）

- 状态：已采纳（2026-09-26 定案并实现）
- 日期：2026-09-26
- 实现提交：`f99a0576f2dbf2e456d80732a8d5bef5e9b50e9f`（断连恢复续跑 + escalation 账面改判）

## 背景（Context）

MCP 断连重连后任务不续跑，诊断发现三层断裂：连接层有自愈（恢复循环连上即止，无任何任务侧动作）；任务层不知情（已挂起的批次等 MinecraftInstruction，恢复不产生消息则永不唤醒）；账面可能已死——断连期间模型 escalation 上报，`_handle_report` 把账面写成 `failed` 终态，终态粘滞（账本拒绝终态后再写）+ 追踪清单已清空，续跑后交付无处可写，账面永久失明。

"升级 = 受阻上报待定夺"，把升级记成 failed 是语义误用：failed 的词义是"干砸了"，而 escalation 的实际含义是"停在这里等人拿主意"。

## 决策（Decision）

1. **escalation 统一改写 `waiting_for_decision`，不再写 `failed`**：与 `_suspend_with_report` 两条挂起路径的账面规则就此统一——快照同形（`{"waiting_for_instruction": True, "reason": …}`），且**保留委派追踪清单**（非终态条目留在账上）。主播侧收到的变化通知相应是"等决策"而非"失败"，与"等决策"的提示文案天然对齐，零改动。
2. **恢复循环成功分支补任务侧动作**（`_on_mcp_recovered`）：注入"[系统] Minecraft 连接已恢复"通知 + 解锁挂起态（`_task_suspended` 复位）+ 唤醒 worker。批次在跑则通知被下一步推理前的 flush 吸收；已挂起则被唤醒重跑。账面复活是现成机制：批次重启时 `_mark_delegated_running` 对追踪清单旧委派重写 running。恢复循环只给信号、不代写账面（单写者规则不动）。
3. **不做超出恢复唤醒的自动续跑**：恢复通知只说"评估现场并继续原任务"，推进决策归执行 Agent 的 LLM；不自动重派、不自动跳过。

## 替代方案（Alternatives）

- **保持 failed + 终态后补一条新任务**：要求恢复循环铸新任务号并重新走受理，等于框架代执行 Agent 重写历史；且"原任务 failed + 新任务 running"在任务卡上是两个条目，运营者无法追溯同一份工作。
- **只修连接层注入通知、账面不动**：挂起能唤醒了，但已写死 failed 的账面依旧粘滞，续跑交付仍无处可写——三层断裂只修一层等于没修。
- **放宽终态粘滞，允许 failed → running 回退**：状态机单调性是账本可信的根基，为单一场景开口子会让所有终态语义变软。

## 后果（Consequences）

- 断连→恢复→续跑→交付全链账面自洽：escalation 留账可取消（硬清场）可续跑（重写 running），僵尸账两种出路都通。
- 主播与任务卡上看到的 escalation 是"待定夺"黄灯而非"失败"红灯，与真实语义一致。
- failed 终态从此只表示执行中真实失败（LLM 自己判断的交付失败仍走 delivery 门禁语义），账面词表回归本义。
