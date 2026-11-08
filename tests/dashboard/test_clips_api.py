"""切片 API：录播匹配读面、段增删改查、切割受理与组件缺席语义。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from src.modules.clipping.cutter import ClipCutError, CutPortion
from src.modules.clipping.overrides import RecordingOverrideStore
from src.modules.clipping.config_schema import ClippingConfigSchema
from src.modules.clipping.service import ClippingService


def _recording_name(started_at_ms: int, title: str = "测试") -> str:
    stamp = datetime.fromtimestamp(started_at_ms / 1000)
    return f"录制-10000-{stamp.strftime('%Y%m%d-%H%M%S')}-{stamp.microsecond // 1000:03d}-{title}.flv"


def _service(store, event_bus, recording_dir: Path, *, cutter=None, overrides: Path | None = None) -> ClippingService:
    return ClippingService(
        event_bus=event_bus,
        database=store,
        llm_manager=None,
        config=ClippingConfigSchema(enabled=True, recording_dir=str(recording_dir)),
        cutter=cutter,
        override_store=RecordingOverrideStore(overrides) if overrides else None,
    )


class _FakeCutter:
    """受控切割替身：记录调用，按 fail 开关决定出片还是抛错；含预览重封装替身。"""

    def __init__(self, *, fail: bool = False, remux_fail: bool = False) -> None:
        self.calls: list[Path] = []
        self.remux_calls: list[Path] = []
        self.fail = fail
        self.remux_fail = remux_fail

    async def cut(self, portion: CutPortion, output: Path, *, log_dir: Path) -> Path:
        if self.fail:
            raise ClipCutError("假切割失败")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake-clip")
        self.calls.append(output)
        return output

    async def remux_range_to_mp4(
        self, source: Path, output: Path, *, start_s: float, duration_s: float, log_dir: Path
    ) -> Path:
        if self.remux_fail:
            raise ClipCutError("假段级重封装失败")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(f"fake-range-{start_s:.0f}-{duration_s:.0f}".encode())
        self.remux_calls.append(output)
        return output

    async def remux_to_mp4(self, source: Path, output: Path, *, log_dir: Path) -> Path:
        if self.remux_fail:
            raise ClipCutError("假重封装失败")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake-mp4-preview")
        self.remux_calls.append(output)
        return output


async def test_clips_sessions_lists_matched(store, event_bus, tmp_path: Path, api) -> None:
    started = int(datetime(2026, 11, 4, 20, 0).timestamp() * 1000)
    session_id = await store.sessions.insert_live_session(started_at_ms=started, title="一场测试")
    (tmp_path / _recording_name(started)).write_bytes(b"x")
    (tmp_path / "手工改名.mp4").write_bytes(b"x")  # 解析不了 → unmatched

    service = _service(store, event_bus, tmp_path)
    client = await api(clipping_service=service)
    resp = await client.get("/api/v1/clips/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert [item["live_session_id"] for item in body["sessions"]] == [session_id]
    assert body["sessions"][0]["file_count"] == 1

    detail = await client.get(f"/api/v1/clips/sessions/{session_id}/recordings")
    assert detail.status_code == 200
    files = detail.json()["files"]
    unmatched = detail.json()["unmatched_files"]
    assert len(files) == 1
    assert files[0]["file_name"].startswith("录制-10000-")
    assert [u["file_name"] for u in unmatched] == ["手工改名.mp4"]


async def test_segments_crud_and_cut_without_files(store, event_bus, tmp_path: Path, api) -> None:
    started = int(datetime(2026, 11, 4, 20, 0).timestamp() * 1000)
    session_id = await store.sessions.insert_live_session(started_at_ms=started)
    service = _service(store, event_bus, tmp_path)  # 空目录：无录播文件
    client = await api(clipping_service=service)

    created = await client.post(
        f"/api/v1/clips/sessions/{session_id}/segments",
        json={"start_ms": started + 1000, "end_ms": started + 61_000, "title": "手建"},
    )
    assert created.status_code == 200
    segment_id = created.json()["segment"]["id"]

    listed = await client.get(f"/api/v1/clips/sessions/{session_id}/segments")
    assert [s["id"] for s in listed.json()["segments"]] == [segment_id]

    updated = await client.put(f"/api/v1/clips/segments/{segment_id}", json={"title": "改名", "status": "confirmed"})
    assert updated.json()["segment"]["title"] == "改名"

    deleted = await client.delete(f"/api/v1/clips/segments/{segment_id}")
    assert deleted.json()["deleted"] is True

    # 该场没有录播文件 → 切割受理 422 可读错误
    cut = await client.post(f"/api/v1/clips/sessions/{session_id}/cut", json={"segment_ids": [1]})
    assert cut.status_code == 422

    # 起止颠倒 → 422
    bad = await client.post(
        f"/api/v1/clips/sessions/{session_id}/segments",
        json={"start_ms": started + 5000, "end_ms": started + 1000},
    )
    assert bad.status_code == 422

    # 单边改动撞 end>=start CHECK 也走 422（不是 500）
    created2 = await client.post(
        f"/api/v1/clips/sessions/{session_id}/segments",
        json={"start_ms": started + 1000, "end_ms": started + 61_000},
    )
    segment2 = created2.json()["segment"]["id"]
    one_sided = await client.put(f"/api/v1/clips/segments/{segment2}", json={"start_ms": started + 120_000})
    assert one_sided.status_code == 422


async def test_cut_accepts_immediately_and_runs_in_background(store, event_bus, tmp_path: Path, api) -> None:
    """切割是受理制：HTTP 立即返回段数，ffmpeg 在后台任务里逐段执行并回填状态。"""
    started = int(datetime(2026, 11, 4, 20, 0).timestamp() * 1000)
    session_id = await store.sessions.insert_live_session(started_at_ms=started)
    (tmp_path / _recording_name(started)).write_bytes(b"x")
    cutter = _FakeCutter()
    service = _service(store, event_bus, tmp_path, cutter=cutter)
    client = await api(clipping_service=service)

    created = await client.post(
        f"/api/v1/clips/sessions/{session_id}/segments",
        json={"start_ms": started + 1000, "end_ms": started + 31_000, "title": "要切的"},
    )
    segment_id = created.json()["segment"]["id"]

    cut = await client.post(f"/api/v1/clips/sessions/{session_id}/cut", json={"segment_ids": [segment_id]})
    assert cut.status_code == 200
    assert cut.json()["accepted"] == 1

    # 后台任务完成：段状态到 done，产物路径回填
    for _ in range(200):
        segments = (await client.get(f"/api/v1/clips/sessions/{session_id}/segments")).json()["segments"]
        if segments[0]["status"] in ("done", "failed"):
            break
        await asyncio.sleep(0.01)
    assert segments[0]["status"] == "done"
    assert segments[0]["output_path"].endswith(".mp4")
    assert len(cutter.calls) == 1

    # 失败路径：切割替身抛错 → 状态 failed + 可读原因
    failing = _FakeCutter(fail=True)
    service2 = _service(store, event_bus, tmp_path, cutter=failing)
    client = await api(clipping_service=service2)
    cut2 = await client.post(f"/api/v1/clips/sessions/{session_id}/cut", json={"segment_ids": [segment_id]})
    assert cut2.json()["accepted"] == 1
    for _ in range(200):
        segments = (await client.get(f"/api/v1/clips/sessions/{session_id}/segments")).json()["segments"]
        if segments[0]["status"] in ("done", "failed"):
            break
        await asyncio.sleep(0.01)
    assert segments[0]["status"] == "failed"
    assert "假切割失败" in segments[0]["error_message"]


async def test_clips_endpoints_503_when_not_assembled(api) -> None:
    client = await api()  # 不挂 clipping_service
    resp = await client.get("/api/v1/clips/sessions")
    assert resp.status_code == 503
    assert "切片" in resp.json()["detail"]


def test_service_skips_subscription_without_recording_dir(event_bus) -> None:
    """enabled 但没配录播目录：start() 不订阅（装配降级），enabled 属性为 False。"""
    service = ClippingService(
        event_bus=event_bus,
        database=None,  # type: ignore[arg-type]
        llm_manager=None,
        config=ClippingConfigSchema(enabled=True, recording_dir=""),
    )
    service.start()  # 不应抛
    service.stop()  # 未订阅时幂等
    assert service.enabled is False


async def test_config_roundtrip_reassembles_without_restart(
    store, event_bus, initialized_config, tmp_path: Path, api
) -> None:
    """页内配置卡闭环：未装配可读配置 → 保存写盘 + 工厂重装配 → 场次接口从 503 变 200。"""

    async def factory():
        raw = initialized_config.get_section("clipping", {}) or {}
        return ClippingService(
            event_bus=event_bus,
            database=store,
            llm_manager=None,
            config=ClippingConfigSchema(**raw),
        )

    client = await api(config_service=initialized_config, clipping_factory=factory)

    before = await client.get("/api/v1/clips/config")
    assert before.status_code == 200
    assert before.json()["assembled"] is False
    # 未装配时场次接口 503
    assert (await client.get("/api/v1/clips/sessions")).status_code == 503

    recording_dir = tmp_path / "rec"
    recording_dir.mkdir()
    payload = {
        "enabled": True,
        "recording_dir": str(recording_dir),
        "ffmpeg_path": "",
        "output_dir": "",
        "llm_profile": "settle",
        "reencode": False,
        "min_segment_seconds": 30,
        "max_segment_seconds": 300,
    }
    saved = await client.post("/api/v1/clips/config", json=payload)
    assert saved.status_code == 200
    body = saved.json()
    assert body["success"] is True
    assert body["assembled"] is True
    assert body["recording_dir_exists"] is True

    # 免重启：场次接口立即可用；配置写盘落在了 infra.toml（语义读回，不比 TOML 转义形态）
    assert (await client.get("/api/v1/clips/sessions")).status_code == 200
    written = initialized_config.get_section("clipping", {})
    assert written.get("recording_dir") == str(recording_dir)
    assert written.get("enabled") is True

    # 再读：装配态与目录存在性都翻正
    after = (await client.get("/api/v1/clips/config")).json()
    assert after["assembled"] is True
    assert after["recording_dir_exists"] is True

    # 停用：服务被摘除，场次接口回到 503
    payload["enabled"] = False
    disabled = await client.post("/api/v1/clips/config", json=payload)
    assert disabled.json()["assembled"] is False
    assert (await client.get("/api/v1/clips/sessions")).status_code == 503


async def test_preview_path_validation_and_conversion_flow(store, event_bus, tmp_path: Path, api) -> None:
    """预览：mp4 直服；越界路径 422；flv 走"202 转换中 → 后台重封装 → 200 可播"。"""
    started = int(datetime(2026, 11, 4, 20, 0).timestamp() * 1000)
    source_flv = tmp_path / _recording_name(started)
    source_flv.write_bytes(b"flv-data")
    source_mp4 = tmp_path / "2026-11-04 20-00-00.mp4"
    source_mp4.write_bytes(b"mp4-data")
    cutter = _FakeCutter()
    service = _service(store, event_bus, tmp_path, cutter=cutter)
    client = await api(clipping_service=service)

    # mp4 直服（Range 由 FileResponse 层处理，这里验状态与媒体类型）
    direct = await client.get("/api/v1/clips/preview", params={"path": str(source_mp4)})
    assert direct.status_code == 200
    assert direct.headers["content-type"] == "video/mp4"
    assert direct.content == b"mp4-data"

    # 录播目录外的文件拒绝（防目录越界读任意文件）
    outside = tmp_path.parent / "外面.mp4"
    outside.write_bytes(b"x")
    escape = await client.get("/api/v1/clips/preview", params={"path": str(outside)})
    assert escape.status_code == 422

    # flv：首次 202 转换中；后台重封装完成后同一端点返回缓存 mp4
    converting = await client.get("/api/v1/clips/preview", params={"path": str(source_flv)})
    assert converting.status_code == 202
    assert converting.json()["converting"] is True
    for _ in range(200):
        check = await client.get("/api/v1/clips/preview/status", params={"path": str(source_flv)})
        if check.json()["ready"]:
            break
        await asyncio.sleep(0.01)
    ready = await client.get("/api/v1/clips/preview", params={"path": str(source_flv)})
    assert ready.status_code == 200
    assert ready.content == b"fake-mp4-preview"
    assert len(cutter.remux_calls) == 1

    # 批量预热幂等：已就绪的不再发起
    warmed = await client.post("/api/v1/clips/preview/prepare", json={"paths": [str(source_flv), str(source_mp4)]})
    assert warmed.json()["started"] == 0


async def test_preview_range_serves_segment_without_full_conversion(store, event_bus, tmp_path: Path, api) -> None:
    """段级预览：整场未转换也立即回该段的 mp4（现转现缓存）；越界范围 422。"""
    started = int(datetime(2026, 11, 4, 20, 0).timestamp() * 1000)
    source_flv = tmp_path / _recording_name(started)
    source_flv.write_bytes(b"flv-data")
    cutter = _FakeCutter()
    service = _service(store, event_bus, tmp_path, cutter=cutter)
    client = await api(clipping_service=service)

    ranged = await client.get(
        "/api/v1/clips/preview/range",
        params={"path": str(source_flv), "start_s": 92.0, "duration_s": 185.0},
    )
    assert ranged.status_code == 200
    assert ranged.content == b"fake-range-92-185"
    assert ranged.headers["content-type"] == "video/mp4"
    assert len(cutter.remux_calls) == 1

    # 同段再取：缓存命中，不再跑 ffmpeg
    again = await client.get(
        "/api/v1/clips/preview/range",
        params={"path": str(source_flv), "start_s": 92.0, "duration_s": 185.0},
    )
    assert again.status_code == 200
    assert len(cutter.remux_calls) == 1

    # 非法范围（负起点）→ 422
    bad = await client.get(
        "/api/v1/clips/preview/range",
        params={"path": str(source_flv), "start_s": -1.0, "duration_s": 10.0},
    )
    assert bad.status_code == 422


async def test_recording_assignment_roundtrip(store, event_bus, tmp_path: Path, api) -> None:
    """归属人工修正：移出→detached 组；指派未识别文件（带人工时刻）→进生效组；恢复→回自动匹配。"""
    started = int(datetime(2026, 11, 4, 20, 0).timestamp() * 1000)
    session_id = await store.sessions.insert_live_session(started_at_ms=started)
    source = tmp_path / _recording_name(started)
    source.write_bytes(b"x")
    renamed = tmp_path / "手工改名-烤海带.mp4"
    renamed.write_bytes(b"x")
    overrides_path = tmp_path / "overrides.json"
    service = _service(store, event_bus, tmp_path, overrides=overrides_path)
    client = await api(clipping_service=service)

    # 移出自动匹配的文件
    detached = await client.post(f"/api/v1/clips/sessions/{session_id}/recordings/detach", json={"path": str(source)})
    assert detached.status_code == 200
    body = (await client.get(f"/api/v1/clips/sessions/{session_id}/recordings")).json()
    assert body["files"] == []
    assert [f["file_name"] for f in body["detached_files"]] == [source.name]

    # 指派未识别文件（人工给开始时刻）
    assigned = await client.post(
        f"/api/v1/clips/sessions/{session_id}/recordings/assign",
        json={"path": str(renamed), "start_ms": started + 60_000},
    )
    assert assigned.status_code == 200
    body = (await client.get(f"/api/v1/clips/sessions/{session_id}/recordings")).json()
    assert [f["file_name"] for f in body["files"]] == [renamed.name]
    assert body["unmatched_files"] == []  # 已指派的不再出现在候选里

    # 恢复：移出与指派两类覆盖一并撤销，回自动匹配
    restored = await client.post(f"/api/v1/clips/sessions/{session_id}/recordings/restore", json={"path": str(source)})
    assert restored.status_code == 200
    body = (await client.get(f"/api/v1/clips/sessions/{session_id}/recordings")).json()
    # source 回到自动匹配；先前指派的烤海带文件仍在生效（其覆盖未被恢复）
    assert [f["file_name"] for f in body["files"]] == [source.name, renamed.name]
    assert [f["file_name"] for f in body["detached_files"]] == []

    # 无覆盖的恢复 → 422 可读错误
    noop = await client.post(f"/api/v1/clips/sessions/{session_id}/recordings/restore", json={"path": str(source)})
    assert noop.status_code == 422

    # 越界路径 422（同预览的目录校验）
    outside = tmp_path.parent / "外面.flv"
    outside.write_bytes(b"x")
    escape = await client.post(
        f"/api/v1/clips/sessions/{session_id}/recordings/assign",
        json={"path": str(outside), "start_ms": started},
    )
    assert escape.status_code == 422


async def test_rename_and_creation_time_endpoints(store, event_bus, tmp_path: Path, api) -> None:
    """WebUI 改名：同目录改名成功、覆盖记录随迁、扩展名不可改、重名拒绝；元数据端点回包。"""
    started = int(datetime(2026, 11, 4, 20, 0).timestamp() * 1000)
    session_id = await store.sessions.insert_live_session(started_at_ms=started)
    source = tmp_path / _recording_name(started)
    source.write_bytes(b"x")
    overrides_path = tmp_path / "overrides.json"
    service = _service(store, event_bus, tmp_path, overrides=overrides_path)
    client = await api(clipping_service=service)

    # 先人工移出，改名后覆盖记录应随新路径迁移
    await client.post(f"/api/v1/clips/sessions/{session_id}/recordings/detach", json={"path": str(source)})

    renamed = await client.post(
        "/api/v1/clips/recordings/rename",
        json={"path": str(source), "new_name": "录制-10000-20261104-200000-000-正式直播.flv"},
    )
    assert renamed.status_code == 200
    new_path = Path(renamed.json()["path"])
    assert new_path.is_file() and not source.exists()

    body = (await client.get(f"/api/v1/clips/sessions/{session_id}/recordings")).json()
    assert [f["file_name"] for f in body["detached_files"]] == [new_path.name]

    # 扩展名不可改 / 重名 / 越界路径 → 422
    bad_ext = await client.post(
        "/api/v1/clips/recordings/rename",
        json={"path": str(new_path), "new_name": "改成mp4.mp4"},
    )
    assert bad_ext.status_code == 422
    dup = await client.post(
        "/api/v1/clips/recordings/rename",
        json={"path": str(new_path), "new_name": new_path.name},
    )
    assert dup.status_code == 422

    # 元数据端点：假 ffmpeg 场景下 ffprobe 多半不存在 → 回 None（而非报错）
    moment = await client.get(
        "/api/v1/clips/recordings/creation-time",
        params={"path": str(new_path)},
    )
    assert moment.status_code == 200
    assert moment.json()["creation_time_ms"] is None
