# 第三方开源声明

本仓库直接依赖的第三方组件中，以下许可需要特别声明：

- **edge-tts**：LGPL-3.0（其中 srt_composer.py 为 MIT）。以 pip 依赖方式使用，未修改其源码。
- **libsndfile**（soundfile 所捆绑的音频库）：LGPL-2.1，随 PyPI wheel 由 soundfile 项目分发，本仓库不捆绑其二进制；将来做安装包分发时需附其声明。
- **@fontsource/jetbrains-mono**（前端字体）：OFL-1.1。

其余依赖为 MIT / Apache / BSD 等宽松许可。做安装包分发时应附完整依赖清单及许可声明（可用 `uv export` / `pnpm` 相关命令导出）。

另：MaiBot、GPT-SoVITS、VTube Studio、OBS 等为本仓库之外独立运行的程序，经网络接口与本仓库交互，不属于本仓库的分发物。

- **IndexTTS**（index-tts/index-tts）：用户按部署指南（docs/guides/indextts.md）自行从官方仓库获取并本地部署，本仓库仅含对接其 HTTP 服务的客户端与启动脚本（运行时 import 用户自装的官方推理包），不包含、也不分发其任何代码或权重。其许可为 **bilibili 模型使用许可**（非标准开源许可），部署使用前应阅读其仓库 LICENSE：中小规模免费使用（月活超 1 亿或年营收超 10 亿元人民币需单独授权）；不得使用其输出改进其他 AI 模型；医疗/军事等高风险场景禁用；**再分发其模型或包含它的打包产物时**须随附该许可全文并对下游施加同等条款。
