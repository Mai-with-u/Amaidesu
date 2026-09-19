# ADR-020：dashboard 服务层分层（api 协议转换 / services 业务逻辑 / schemas 契约）

- 状态：已采纳
- 日期：2026-09-19
- 实现提交：`21fbb62302db8ab8d8546e6348af1e19e2e2d430`（refactor(dashboard): 配置管理拆出 services 服务层；扩展见 `5cf9cf61c6169f687f34c0eface2f0fc84999ab3`）

## 背景（Context）

dashboard 后端在重构前呈现"单 api 文件越摊越厚"的形态：

1. `api/config.py` 748 行中约 500 行是"配置 → 表单"的呈现适配与写路径业务逻辑，路由声明本身只占零头。
2. tools / sessions / llm 等端点各自内联装配逻辑（查询、聚合、组装响应），同一装配套路在多个端点间复制。
3. 更严重的结构问题是 API 互调：streamer / rundowns 端点为复用逻辑，跨文件 import 另一个 api 模块的端点函数直接调用。端点函数携带 HTTP 语义（Request/Response），这种复用使"调用一个端点"和"发一个 HTTP 请求"不可区分，测试必须起 HTTP 层，复用面完全被路由声明绑架。

## 决策（Decision）

dashboard 模块固定为三层，每层职责封闭：

- **api/**：只做协议转换——路由声明、参数解析、响应包装；不写业务逻辑。
- **services/**：承载业务逻辑与呈现适配（含配置 → 表单的转换），**禁止 import fastapi**——服务层对 HTTP 无感知，这是"服务层可脱离 HTTP 单测"的结构保证。
- **schemas/**：承载全部请求/响应契约，端点必须声明 `response_model`。

跨 api 模块的逻辑复用一律下沉 services，或放入 `api/common.py`（仅限纯协议辅助）；**禁止 API 互调**（一个端点函数调用另一个端点函数）。

## 替代方案（Alternatives）

### 维持单 api 文件

**拒绝**。改动成本最小，但 748 行单文件已经证明呈现适配、业务逻辑、路由声明纠缠后复用与测试被堵死：任何装配逻辑的复用都只能靠端点互调或复制粘贴，单测必须穿透 HTTP 层。

### 抽独立 FastAPI 子应用（sub-application 挂载）

**拒绝**。子应用解决的是路由命名空间与中间件隔离问题，本项目的诉求是同一应用内的逻辑分层，引入子应用是过度设计，徒增挂载路径与依赖注入的复杂度。

## 后果（Consequences）

- 服务层可以无 HTTP 依赖地单测，测试不再需要起应用实例。
- 公开面需要显式划分：api 层只能调用服务层公开符号。实际出现过 api 层 import 服务层 `_` 前缀私有符号的反例，说明划面不是自动的——下沉逻辑时须同时审视哪些符号对外公开。
- 新增端点必须声明 `response_model`，契约落在 schemas 层，前后端接口变更可检索。
- services 层禁止 fastapi 依赖是可执行的红线，review 与测试均可机械检查。
