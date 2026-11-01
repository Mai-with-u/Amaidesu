"""面捕合成器测试替身：记录每一帧（与身体动作提示）的平台驱动、只记渲染器挂载的口型分析器"""

from __future__ import annotations

from typing import Any, Iterable, List

from src.modules.avatar.tracking.body import BodyCue
from src.modules.avatar.tracking.channels import Frame


class FakeLipsyncAnalyzer:
    """口型分析器替身：只记录挂上 / 摘下的渲染器（测试直接调渲染器喂信号）。"""

    def __init__(self) -> None:
        self.renderers: List[Any] = []

    def add_renderer(self, renderer: Any) -> None:
        self.renderers.append(renderer)

    def remove_renderer(self, renderer: Any) -> None:
        self.renderers.remove(renderer)


class RecordingDriver:
    """满足 ``TrackingDriver`` 契约的替身：记下收到的每一帧，可设为未就绪或写入失败。"""

    def __init__(self, name: str = "fake", *, ready: bool = True, fail: bool = False) -> None:
        self.name = name
        self.is_ready = ready
        self.fail = fail
        self.frames: List[Frame] = []

    def ready(self) -> bool:
        return self.is_ready

    async def apply(self, frame: Frame) -> None:
        if self.fail:
            raise RuntimeError("驱动写入失败（测试注入）")
        self.frames.append(dict(frame))


class RecordingBodyDriver(RecordingDriver):
    """还满足 ``BodyDriver`` 契约的替身：只做 ``supported`` 里的动作（类别:名字），记下收到的动作提示。"""

    def __init__(self, name: str = "fake-body", *, supported: Iterable[str] = ()) -> None:
        super().__init__(name)
        self.supported = set(supported)
        self.cues: List[BodyCue] = []

    def body_supports(self, cue: BodyCue) -> bool:
        return f"{cue.kind}:{cue.name}" in self.supported

    async def perform_body(self, cue: BodyCue) -> None:
        self.cues.append(cue)
