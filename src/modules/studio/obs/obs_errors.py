"""OBS 失败原因翻译：把 obsws-python / websocket-client 抛出的异常翻成能照着改的原因。

直播画面采集器、OBS 控制工具与诊断脚本共用。两边的连接参数配在不同文件里（采集器在
``collectors.toml`` 的 ``[live_frame]``，控制工具在 ``tools.toml`` 的
``[tools.studio.obs.config]``），找的资源也不同（要截的画面 / 要切的场景 / 要写的文本源），
所以"密码填在哪"和"找不到时该查什么"由调用方给出。

obsws-python 与 websocket-client 的异常按类名识别，不在这里导入它们：没装依赖时同样要能说清原因。
"""

from __future__ import annotations

import json

__all__ = ["describe_obs_error"]


def describe_obs_error(
    error: BaseException,
    *,
    host: str,
    port: int,
    password_setting: str,
    not_found: str = "",
    connected: bool = False,
) -> str:
    """把一次 OBS 连接或请求失败翻译成原因与改法。

    ``password_setting`` 是密码配置项的位置（如 ``collectors.toml 的 [live_frame].obs_password``）；
    ``not_found`` 是 OBS 回"找不到资源"（代码 600）时的整句原因，空串按通用请求错误描述。
    ``connected`` 表示连接早已建立、失败发生在请求阶段：此时连接被关掉说明 OBS 关闭或重启了；
    握手阶段同样的现象则是 OBS 拒绝了身份验证。
    """
    name = type(error).__name__
    text = str(error)
    where = f"{host}:{port}"
    if isinstance(error, ImportError):
        return "没装 obsws-python：在项目目录执行 uv sync 安装依赖"
    if isinstance(error, ConnectionRefusedError):
        return (
            f"连不上 OBS（{where}）：OBS 没开，或没在「工具 → WebSocket 服务器设置」里勾选启用 WebSocket 服务器"
            "（需要 OBS 28 及以上，端口默认 4455）"
        )
    if isinstance(error, TimeoutError) or name in ("WebSocketTimeoutException", "OBSSDKTimeoutError"):
        return f"连接 OBS（{where}）超时：检查地址、端口与防火墙；OBS 卡死时也会超时"
    if "no password provided" in text:
        return f"OBS 开启了身份验证：在 {password_setting} 填 WebSocket 服务器密码"
    if name == "OBSSDKRequestError":
        code = getattr(error, "code", None)
        if code == 600 and not_found:
            return not_found
        return f"OBS 拒绝了 {getattr(error, 'req_name', '请求')}（代码 {code}）：{text}"
    # 请求阶段 OBS 正常退出会先发关闭帧，obsws-python 读到空串后 JSON 解析失败；直接断网则是连接被关闭
    closed = name == "WebSocketConnectionClosedException" or isinstance(error, json.JSONDecodeError)
    if isinstance(error, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)) or (connected and closed):
        return f"与 OBS（{where}）的连接断开了：OBS 关闭或重启过"
    if name == "WebSocketConnectionClosedException" or "failed to identify" in text:
        return f"OBS 拒绝了身份验证：{password_setting} 与 OBS「WebSocket 服务器设置」里的密码不一致"
    return f"{name}: {text}"
