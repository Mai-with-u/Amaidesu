# 屏幕感知（Vision）

一句话形状：**画面有三个入口，全部收敛到同一个进程内图像仓——快照工具（被调才看）、视觉直连（工具截图进对话观察）、直播画面流（决策窗自动看一眼）；模型看不看得了图由能力协商决定。**

## 图像仓（VisionImageStore）

`src/modules/vision/image_store.py`：进程内 LRU（默认 16 张、15 分钟过期），图片本体不落盘不入库。消息里只携带句柄（`ImageRefPart`，形如 `img_xxx` 的引用），LLM 引擎发请求前把"新鲜"的句柄换回真图（代码里叫注水 `_hydrate_image_refs`）：引用所属消息之后新增的 assistant 消息数不超过 `visible_turns`（默认 1）才算新鲜；过期或被逐出的引用降级为"截图已过期，需要时重新截图"的文字提示。另有"最新帧"槽位（`put_latest` / `latest`），供直播画面流反复覆盖写、消费方随时取当前帧。

## 三个入口

### 1. 快照工具 `vision_look_at_screen`

被动工具，被调才抓屏：mss 多显示器枚举 + 可选区域 + 可选缩宽，默认经 VLM 转成文字描述返回。**调用方的主模型自己能看图时跳过 VLM 直接回图**（能力协商，省一次转述的成本与时延）。配置在 `tools.toml` 的 `[tools.vision]`。

### 2. 视觉直连

工具回执与 Planner 可以把截图以句柄形式挂进对话观察，让主模型直接看图而不是读转述（设计见 [视觉直连：工具图像进对话观察](../decisions/037-vision-direct-tool-image-observation.md)）。硬约束：请求消息里禁止内联 base64 图片（引擎期硬错），图必须走图像仓 + 句柄。另有一道内联载荷守卫：文本承载字段合计超 20 万字符拒发（防大载荷意外泄漏，与按模型窗口换算的 token 预算是两道独立的闸）。

### 3. 直播画面流

`live_frame` 采集器常驻采样节目画面（OBS WebSocket 截指定源，OBS 不可用自动回退桌面截屏），每次采样覆盖写图像仓的"最新帧"槽位——**不发事件**，决策侧查询式取用：Planner 在每个决策窗的第一次请求尾部挂一张最新帧（同一决策窗的后续步骤不重复挂），主播每次决策都看得到直播间此刻的样子；帧龄过旧会限频告警（设计见 [直播画面做成采集流](../decisions/041-live-frame-stream.md)）。

## 能力协商

`LLMManager.is_profile_visual(profile)` 按模型注册表里的 `visual` 声明判定调用方能不能看图：非 visual 模型的决策窗不带图，引擎注水时非 visual 或越窗的引用一律降级为文字。判定宁可保守——判错的代价只是多一次 VLM 转述，不会让请求失败。

## 配置面

- `tools.toml` 的 `[tools.vision]`：`enabled` + 抓屏参数（`monitor_index` / `default_region` / `default_max_width`）。
- `collectors.toml` 的 `[live_frame]`：`source`（obs / screen）、OBS 连接、采样间隔与变化阈值等；键全集以采集器包内 ConfigSchema 为准。

## 相关决策

- [视觉直连：工具图像进对话观察](../decisions/037-vision-direct-tool-image-observation.md)
- [直播画面做成采集流](../decisions/041-live-frame-stream.md)
