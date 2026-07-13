"""M2 测试：Editing IR 校验、方案→IR 确定性转换、Planning Agent（mock LLM）。"""

import json

import pytest

from app.ir.schema import IR_VERSION, IRValidationError, timeline_duration, validate_ir


def _valid_ir(source_path: str) -> dict:
    return {
        "version": IR_VERSION,
        "project": {"name": "test", "fps": 25, "resolution": {"width": 1920, "height": 1080}},
        "sources": [{"id": "src_1", "path": source_path, "duration": 5.0}],
        "tracks": [
            {
                "type": "video",
                "index": 1,
                "items": [
                    {"type": "clip", "source_id": "src_1", "trim": {"start": 0.5, "end": 3.0},
                     "role": "opening", "reason": "测试"},
                ],
            },
            {
                "type": "subtitle",
                "index": 1,
                "items": [
                    {"type": "subtitle", "content": "你好", "timeline_start": 0.0, "timeline_end": 2.0},
                ],
            },
        ],
        "render": None,
    }


def test_validate_ir_ok(sample_video):
    ir = validate_ir(_valid_ir(str(sample_video)))
    assert timeline_duration(ir) == 2.5


def test_validate_ir_catches_errors(sample_video):
    bad = _valid_ir(str(sample_video))
    bad["tracks"][0]["items"][0]["trim"] = {"start": 1.0, "end": 99.0}  # 超出素材时长
    bad["sources"].append({"id": "src_1", "path": str(sample_video), "duration": 5.0})  # 重复 id
    with pytest.raises(IRValidationError) as exc:
        validate_ir(bad)
    joined = "".join(exc.value.errors)
    assert "超出素材时长" in joined
    assert "重复" in joined


def test_validate_ir_rejects_missing_file(sample_video):
    bad = _valid_ir(str(sample_video))
    bad["sources"][0]["path"] = "/nonexistent/x.mp4"
    with pytest.raises(IRValidationError, match="素材文件不存在"):
        validate_ir(bad)


def test_validate_ir_rejects_subtitle_overlap(sample_video):
    bad = _valid_ir(str(sample_video))
    bad["tracks"][1]["items"].append(
        {"type": "subtitle", "content": "重叠", "timeline_start": 1.0, "timeline_end": 3.0}
    )
    with pytest.raises(IRValidationError, match="重叠"):
        validate_ir(bad)


def test_validate_ir_rejects_wrong_version(sample_video):
    bad = _valid_ir(str(sample_video))
    bad["version"] = "9.9"
    with pytest.raises(IRValidationError):
        validate_ir(bad)


async def test_planning_with_mock_llm(sample_video, analyzed_asset):
    """mock LLM 产出方案 → 确定性转换 → IR 校验通过。"""
    from app.providers import set_providers
    from app.runtime.planning import generate_plan

    asset_id = analyzed_asset

    class MockLLM:
        async def chat(self, messages, **kwargs):
            plan = {
                "title": "测试短片",
                "target_duration": 4,
                "clips": [
                    {"section": "opening", "asset_id": asset_id, "start": 0.0, "end": 2.0,
                     "reason": "开场画面", "subtitle": "东京之夜"},
                    {"section": "ending", "asset_id": asset_id, "start": 3.0, "end": 4.5,
                     "reason": "收尾", "subtitle": None},
                ],
            }
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    set_providers(llm=MockLLM())
    result = await generate_plan("做一个4秒测试短片")
    ir = result["ir"]
    assert ir["version"] == IR_VERSION
    assert len(ir["sources"]) == 1
    video_track = next(t for t in ir["tracks"] if t["type"] == "video")
    assert len(video_track["items"]) == 2
    assert video_track["items"][0]["role"] == "opening"
    subtitle_track = next(t for t in ir["tracks"] if t["type"] == "subtitle")
    assert subtitle_track["items"][0]["content"] == "东京之夜"
    assert subtitle_track["items"][0]["timeline_end"] == 2.0
    validate_ir(ir)  # 不应抛异常


async def test_planning_retry_on_invalid(sample_video, analyzed_asset):
    """第一次产出非法方案（引用不存在素材），重试后成功。"""
    from app.providers import set_providers
    from app.runtime.planning import generate_plan

    asset_id = analyzed_asset
    calls = {"n": 0}

    class RetryLLM:
        async def chat(self, messages, **kwargs):
            calls["n"] += 1
            plan = {
                "title": "重试",
                "clips": [
                    {"section": "opening", "asset_id": asset_id if calls["n"] > 1 else 9999,
                     "start": 0.0, "end": 4.0, "reason": "r", "subtitle": None}
                ],
            }
            return {"content": json.dumps(plan), "tool_calls": None}

    set_providers(llm=RetryLLM())
    result = await generate_plan("重试测试")
    assert calls["n"] == 2
    assert result["ir"]["tracks"][0]["items"][0]["trim"]["end"] == 4.0
