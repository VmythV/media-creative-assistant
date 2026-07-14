"""M10 测试：FCPXML 转场导出（居中对齐数学）、剪辑清单转场标注（设计文档 §13）。"""

from xml.etree import ElementTree as ET

import pytest

from app.ir.schema import timeline_duration, validate_ir


def _ir(video_path: str, with_transitions: bool) -> dict:
    t1 = {"type": "fade", "duration": 1.0} if with_transitions else None
    t2 = {"type": "wipeleft", "duration": 0.5} if with_transitions else None
    return {
        "version": "0.3",
        "project": {"name": "fcpxml-m10", "fps": 25, "resolution": {"width": 640, "height": 360}},
        "sources": [{"id": "src_1", "path": video_path, "duration": 5.0}],
        "tracks": [{"type": "video", "index": 1, "items": [
            {"type": "clip", "source_id": "src_1", "trim": {"start": 0.0, "end": 4.0},
             "role": "opening", "reason": "r"},
            {"type": "clip", "source_id": "src_1", "trim": {"start": 1.0, "end": 5.0},
             "role": "build", "reason": "r", "transition": t1},
            {"type": "clip", "source_id": "src_1", "trim": {"start": 2.0, "end": 4.0},
             "role": "ending", "reason": "r", "transition": t2},
        ]}],
        "render": None,
    }


def _spine(xml: str):
    root = ET.fromstring(xml.split("<!DOCTYPE fcpxml>\n")[1])
    return root.find(".//spine")


def test_fcpxml_transition_centered_math(sample_video):
    from app.ir.exporters import export_fcpxml

    ir = validate_ir(_ir(str(sample_video), with_transitions=True))
    spine = _spine(export_fcpxml(ir))
    children = list(spine)
    tags = [c.tag for c in children]
    assert tags == ["asset-clip", "transition", "asset-clip", "transition", "asset-clip"]

    c1, t1, c2, t2, c3 = children
    # 片段1：4s，转出 1.0s → spine 时长 4 − 0.5 = 3.5s（87.5s*25=87.5 → round 88 帧）
    assert c1.get("offset") == "0/25s" and c1.get("start") == "0/25s"
    assert c1.get("duration") == "88/25s"
    # 转场1：居中于剪辑点 3.5s，offset = 3.5 − 0.5 = 3.0s
    assert t1.get("offset") == "75/25s" and t1.get("duration") == "25/25s"
    assert t1.get("name") == "Cross Dissolve"
    # 片段2：4s，转入 1.0 转出 0.5 → 媒体入点 1.0+0.5=1.5s，spine 时长 4−0.5−0.25=3.25s
    assert c2.get("offset") == "88/25s" and c2.get("start") == "38/25s"
    assert c2.get("duration") == "81/25s"
    # 转场2：剪辑点 3.5+3.25=6.75s，offset = 6.75 − 0.25 = 6.5s = 162.5 帧 → round 取整 162
    assert t2.get("offset") == "162/25s" and t2.get("duration") == "12/25s"
    # 片段3：2s，转入 0.5 → 媒体入点 2.25s，spine 时长 1.75s
    assert c3.get("offset") == "169/25s" and c3.get("start") == "56/25s"
    assert c3.get("duration") == "44/25s"

    # spine 总长 = IR timeline_duration（10 − 1.5 = 8.5s）
    end = int(c3.get("offset").split("/")[0]) + int(c3.get("duration").split("/")[0])
    assert end / 25 == pytest.approx(timeline_duration(ir), abs=0.1)


def test_fcpxml_without_transitions_unchanged(sample_video):
    from app.ir.exporters import export_fcpxml

    ir = validate_ir(_ir(str(sample_video), with_transitions=False))
    spine = _spine(export_fcpxml(ir))
    assert [c.tag for c in spine] == ["asset-clip"] * 3
    clips = list(spine)
    assert clips[0].get("duration") == "100/25s"  # 无转场：spine 时长即 trim 长度
    assert clips[1].get("offset") == "100/25s" and clips[1].get("start") == "25/25s"


def test_edit_list_transition_positions(sample_video):
    from app.ir.exporters import export_edit_list

    ir = validate_ir(_ir(str(sample_video), with_transitions=True))
    md = export_edit_list(ir)
    assert "fade 1.0s 转场进入" in md
    assert "时间线时长：8.5 秒" in md
    # 独占时间槽语义（与字幕一致）：片段2 起点 = 4.0s（转场重叠期间归前段）
    assert "时间线 4.0s 起" in md
    # 片段3 起点 = 4 + (4 − 1) = 7.0s
    assert "时间线 7.0s 起" in md
