# GLOSSARY

项目域词汇表：给关键概念与良好接缝命名，命名与语义以本文件为准；新增深化模块时随实现登记词条，改语义时同步改这里。

## 场次（live session）

一次开播到下播的完整直播过程，是主播 Agent 业务边界的默认单位：主动发言、对话记忆、游戏账本、流程单起算都以场次为界。库内事实源为 live_sessions 表，进程内对应物见"场次作用域"。

## 场次作用域（session scope）

以场次为界的进程内状态全集：游戏账本、待入私聊行、未落库对话轮、时间线 notes、本窗游戏批次、上场回顾快照。开播建立、下播清空。承载模块为 `src/agents/streamer/session_scope.py` 的 `SessionScope`；入账动词经 `scope.ledger` 直打账本接口，出账、清场、读历史、补记走 `SessionScope` 自己的动词。

## 决策窗（decision window）

一轮决策消费一批材料的逻辑窗口：窗边界处账本整批出账、私聊清空，出账行进入本轮历史投影。出账只发生在窗边界恰好一处，读历史是纯读。

## 决策窗材料（window materials）

一个决策窗需要的一次性材料包：对话历史投影、本窗游戏事件批次、上场回顾段。由 `SessionScope.open_window()` 按"出账先于读取"的顺序一次取齐，返回 `WindowMaterials`；字段语义 None 与空不同义（None = 读库失败宁可无历史、空列表 = 真无历史、空串 = 不注入），见其类型注释。

## 易逝图（transient images）

工具回执带图时的图片生命周期约定：图不进对话历史——历史里只留文字回执；真图入库换句柄后只挂在下一次请求的消息末尾，用完即弃。承载模块为 `src/modules/context/transient_images.py` 的 `TransientImages`（暂存与取走）与 `absorb_result_images`（从工具结果收图）。ADR-040 所称"尾部临时图片/易逝内容"即此物。
