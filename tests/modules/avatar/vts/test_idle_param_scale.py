"""VTSProvider._idle_scale_for 量纲缩放表构建单测。"""

from __future__ import annotations

from src.modules.avatar.vts.vts_provider import VTSProvider


def test_scale_uses_half_span_of_native_range() -> None:
    """角度类参数按原生半幅放大（FaceAngleX ±30 → 系数 30）。"""
    ranges = {"FaceAngleX": (-30.0, 30.0), "FaceAngleZ": (-90.0, 90.0)}
    scales = VTSProvider._idle_scale_for(ranges, ["FaceAngleX", "FaceAngleZ"])
    assert scales == {"FaceAngleX": 30.0, "FaceAngleZ": 90.0}


def test_unit_range_stays_unscaled() -> None:
    """[0,1] 类参数系数为 1，基线表情值（如 MouthSmile=0.3）行为不变。"""
    ranges = {"MouthSmile": (0.0, 1.0)}
    assert VTSProvider._idle_scale_for(ranges, ["MouthSmile"]) == {"MouthSmile": 1.0}


def test_unknown_and_blank_names_skipped() -> None:
    """范围表中没有的参数与空名跳过（保持原值写入）。"""
    scales = VTSProvider._idle_scale_for({"FaceAngleX": (-30.0, 30.0)}, ["FaceAngleX", "BodyX", ""])
    assert scales == {"FaceAngleX": 30.0}


def test_zero_span_falls_back_to_one() -> None:
    """畸形零跨度范围退化为 1.0，避免缩放为 0。"""
    assert VTSProvider._idle_scale_for({"Weird": (0.0, 0.0)}, ["Weird"]) == {"Weird": 1.0}
