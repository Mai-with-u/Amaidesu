# 第三方开源声明

本仓库直接依赖的第三方组件中，以下许可需要特别声明：

- **edge-tts**：LGPL-3.0（其中 srt_composer.py 为 MIT）。以 pip 依赖方式使用，未修改其源码。
- **libsndfile**（soundfile 所捆绑的音频库）：LGPL-2.1，随 PyPI wheel 由 soundfile 项目分发，本仓库不捆绑其二进制；将来做安装包分发时需附其声明。
- **@fontsource/jetbrains-mono**（前端字体）：OFL-1.1。

其余依赖为 MIT / Apache / BSD 等宽松许可。做安装包分发时应附完整依赖清单及许可声明（可用 `uv export` / `pnpm` 相关命令导出）。

另：MaiBot、GPT-SoVITS、VTube Studio、OBS 等为本仓库之外独立运行的程序，经网络接口与本仓库交互，不属于本仓库的分发物。
