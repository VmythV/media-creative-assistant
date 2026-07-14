"""M9 测试：IR v0.3 转场校验、转换钳制与字幕时移、diff 转场检测、xfade 渲染（设计文档 §12）。"""

import json
import subprocess

import pytest

from app.ir.schema import IRValidationError, timeline_duration, validate_ir


def _ir_with_transitions(video_path: str, *, second_transition: dict | None = None,
                         third_clip: bool = False, third_transition: dict | None = None) -> dict:
    items = [
        {"type": "clip", "source_id": "src_1", "trim": {"start": 0.0, "end": 2.0},
         "role": "opening", "reason": "r"},
        {"type": "clip", "source_id": "src_1", "trim": {"start": 2.0, "end": 4.0},
         "role": "build", "reason": "r", "transition": second_transition},
    ]
    if third_clip:
        items.append({"type": "clip", "source_id": "src_1", "trim": {"start": 3.0, "end": 5.0},
                      "role": "ending", "reason": "r", "transition": third_transition})
    return {
        "version": "0.3",
        "project": {"name": "transition-test", "fps": 25, "resolution": {"width": 640, "height": 360}},
        "sources": [{"id": "src_1", "path": video_path, "duration": 5.0}],
        "tracks": [{"type": "video", "index": 1, "items": items}],
        "render": None,
    }


def test_ir_v03_transition_validation(sample_video):
    # 合法转场 + 总时长扣减重叠
    ir = _ir_with_transitions(str(sample_video), second_transition={"type": "fade", "duration": 0.5})
    parsed = validate_ir(ir)
    assert parsed.version == "0.3"
    assert timeline_duration(parsed) == pytest.approx(3.5)  # 2 + 2 - 0.5

    # 首个片段不能有转场
    bad = _ir_with_transitions(str(sample_video), second_transition=None)
    bad["tracks"][0]["items"][0]["transition"] = {"type": "fade", "duration": 0.5}
    with pytest.raises(IRValidationError, match="首个片段"):
        validate_ir(bad)

    # 转入+转出超过片段自身时长
    bad = _ir_with_transitions(
        str(sample_video),
        second_transition={"type": "fade", "duration": 1.2},
        third_clip=True, third_transition={"type": "dissolve", "duration": 1.0},
    )  # 中间片段 2s < 1.2 + 1.0
    with pytest.raises(IRValidationError, match="转场重叠"):
        validate_ir(bad)

    # 白名单外类型被结构校验拒绝
    bad = _ir_with_transitions(str(sample_video), second_transition={"type": "starwipe", "duration": 0.5})
    with pytest.raises(IRValidationError, match="结构校验失败"):
        validate_ir(bad)


def test_plan_to_ir_transition_clamp_and_subtitle_shift(sample_video, analyzed_asset):
    from app.runtime.planning import _load_analyzed_assets, plan_to_ir

    analyzed = [a for a in _load_analyzed_assets(None) if a["asset"].id == analyzed_asset]
    plan = {
        "title": "钳制测试",
        "clips": [
            {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 2.0,
             "reason": "r", "subtitle": "第一段",
             "transition": {"type": "fade", "duration": 0.5}},  # 首段转场应被忽略
            {"section": "ending", "asset_id": analyzed_asset, "start": 2.0, "end": 4.0,
             "reason": "r", "subtitle": "第二段",
             "transition": {"type": "fade", "duration": 5.0}},  # 超长应 clamp 到 min(2, 2/2)=1.0
        ],
    }
    ir = plan_to_ir(plan, analyzed, "钳制测试")
    clips = ir["tracks"][0]["items"]
    assert "transition" not in clips[0]
    assert clips[1]["transition"] == {"type": "fade", "duration": 1.0}
    subs = next(t for t in ir["tracks"] if t["type"] == "subtitle")["items"]
    # 字幕独占时间槽：第二段从转场结束（2.0）起，长度 = 2 - 1 = 1
    assert subs[0]["timeline_start"] == 0.0 and subs[0]["timeline_end"] == 2.0
    assert subs[1]["timeline_start"] == 2.0 and subs[1]["timeline_end"] == 3.0
    assert timeline_duration(validate_ir(ir)) == pytest.approx(3.0)

    # 白名单外类型退化为硬切
    plan["clips"][1]["transition"] = {"type": "starwipe", "duration": 0.5}
    ir = plan_to_ir(plan, analyzed, "钳制测试")
    assert "transition" not in ir["tracks"][0]["items"][1]


def test_diff_detects_transition_change():
    from app.runtime.planning import diff_plans

    old = {"clips": [
        {"section": "opening", "asset_id": 1, "start": 0.0, "end": 2.0, "subtitle": None},
        {"section": "ending", "asset_id": 1, "start": 2.0, "end": 4.0, "subtitle": None,
         "transition": None},
    ]}
    new = json.loads(json.dumps(old))
    new["clips"][1]["transition"] = {"type": "fadeblack", "duration": 0.8}
    diff = diff_plans(old, new)
    assert any("转场" in c and "fadeblack" in c for c in diff["changed"])
    assert not diff["added"] and not diff["removed"]


def _probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def test_render_with_transition(sample_video, tmp_path):
    from app.ir.renderer import render_video

    ir = validate_ir(_ir_with_transitions(
        str(sample_video), second_transition={"type": "fade", "duration": 0.5}))
    result = render_video(ir, tmp_path)
    assert result["transitions"] == 1
    assert _probe_duration(result["video"]) == pytest.approx(3.5, abs=0.2)


def test_render_mixed_transition_and_hard_cut(sample_video, tmp_path):
    """三段：硬切 + 转场混合，一次 filter_complex 链完成。"""
    from app.ir.renderer import render_video

    ir = validate_ir(_ir_with_transitions(
        str(sample_video), second_transition=None,
        third_clip=True, third_transition={"type": "wipeleft", "duration": 0.6},
    ))
    result = render_video(ir, tmp_path)
    assert result["transitions"] == 1
    # 2 + 2 + 2 - 0.6
    assert _probe_duration(result["video"]) == pytest.approx(5.4, abs=0.2)
