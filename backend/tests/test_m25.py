"""M25 测试：变速——timeline_len/atempo 链/渲染时长/edit_clips speed/diff（backlog B5）。"""

import asyncio
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.ir.schema import IRValidationError, timeline_duration, validate_ir
from app.main import app


def _ir(video_path: str, clips_speed: list[float | None]) -> dict:
    items = []
    for i, sp in enumerate(clips_speed):
        c = {"type": "clip", "source_id": "src_1",
             "trim": {"start": float(i), "end": float(i) + 2.0},
             "role": "broll", "reason": "r"}
        if sp is not None:
            c["speed"] = sp
        items.append(c)
    return {
        "version": "0.6",
        "project": {"name": "speed", "fps": 25, "resolution": {"width": 320, "height": 180}},
        "sources": [{"id": "src_1", "path": video_path, "duration": 10.0}],
        "tracks": [{"type": "video", "index": 1, "items": items}],
        "render": None,
    }


def test_timeline_len_and_validation(sample_video):
    ir = validate_ir(_ir(str(sample_video), [1.0, 2.0, 0.5]))
    clips = ir.tracks[0].items
    assert clips[1].timeline_len == pytest.approx(1.0)   # 2s @2x
    assert clips[2].timeline_len == pytest.approx(4.0)   # 2s @0.5x
    assert timeline_duration(ir) == pytest.approx(7.0)   # 2 + 1 + 4
    # 越界拒绝
    with pytest.raises(IRValidationError):
        validate_ir(_ir(str(sample_video), [5.0]))
    with pytest.raises(IRValidationError):
        validate_ir(_ir(str(sample_video), [0.1]))


def test_atempo_chain():
    from app.ir.renderer import _atempo_chain

    assert _atempo_chain(1.5) == "atempo=1.5"
    assert _atempo_chain(2.0) == "atempo=2.0"
    assert _atempo_chain(4.0) == "atempo=2.0,atempo=2.0"
    assert _atempo_chain(0.5) == "atempo=0.5"
    assert _atempo_chain(0.25) == "atempo=0.5,atempo=0.5"
    assert _atempo_chain(3.0) == "atempo=2.0,atempo=1.5"


def _probe_dur(path: str) -> float:
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True).stdout.strip())


def test_render_speed_duration(sample_video, tmp_path):
    from app.ir.renderer import render_video

    ir = validate_ir(_ir(str(sample_video), [1.0, 2.0, 0.5]))  # 时间线 7.0s
    res = render_video(ir, tmp_path, burn_subtitles=False)
    assert res["speed_clips"] == 2
    assert _probe_dur(res["video"]) == pytest.approx(7.0, abs=0.3)


def test_render_speed_with_transition(sample_video, tmp_path):
    """变速 + 转场：转场 offset 用时间线时长（变速后）。"""
    from app.ir.renderer import render_video

    ir_d = _ir(str(sample_video), [1.0, 2.0])
    ir_d["tracks"][0]["items"][1]["transition"] = {"type": "fade", "duration": 0.5}
    ir = validate_ir(ir_d)
    # 时间线 = 2 + 1 - 0.5 = 2.5s
    assert timeline_duration(ir) == pytest.approx(2.5)
    res = render_video(ir, tmp_path / "t", burn_subtitles=False)
    assert _probe_dur(res["video"]) == pytest.approx(2.5, abs=0.3)


def _mock_llm(analyzed_asset):
    class MockLLM:
        async def chat(self, messages, **kwargs):
            if "调度员" in messages[0]["content"]:
                return {"content": json.dumps({"reply": "OK", "actions": [
                    {"intent": "edit_clips", "params": {"ops": [
                        {"op": "speed", "position": 1, "speed": 0.5},
                    ]}},
                ]}, ensure_ascii=False), "tool_calls": None}
            plan = {"title": "变速测试", "clips": [
                {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 3.0,
                 "reason": "r", "subtitle": "慢镜头"},
                {"section": "ending", "asset_id": analyzed_asset, "start": 3.0, "end": 5.0,
                 "reason": "r", "subtitle": None},
            ]}
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    return MockLLM()


async def test_edit_clips_speed_op(analyzed_asset):
    from app.providers import set_providers
    from app.runtime.clip_ops import apply_clip_ops

    set_providers(llm=_mock_llm(analyzed_asset))
    with TestClient(app) as client:
        plan_id = client.post("/api/plans", json={"goal": "变速"}).json()["plan_id"]
        for _ in range(50):
            await asyncio.sleep(0.1)
            if client.get(f"/api/plans/{plan_id}").json()["status"] != "generating":
                break
        result = apply_clip_ops(plan_id, [
            {"op": "speed", "position": 1, "speed": 0.5},   # 慢动作
            {"op": "speed", "position": 2, "speed": 2.0},   # 快放
        ])
        new = client.get(f"/api/plans/{result['plan_id']}").json()
        clips = new["plan"]["clips"]
        assert clips[0]["speed"] == 0.5 and clips[1]["speed"] == 2.0
        # IR 时间线：片段1 3s@0.5x=6s + 片段2 2s@2x=1s = 7s
        assert new["ir"]["version"] == "0.6"
        # 字幕随变速时移：第一条字幕覆盖 0-6s（慢动作放大）
        subs = next(t for t in new["ir"]["tracks"] if t["type"] == "subtitle")["items"]
        assert subs[0]["timeline_end"] == pytest.approx(6.0)
        # 越界钳制 + 复原
        result2 = apply_clip_ops(result["plan_id"], [{"op": "speed", "position": 1, "speed": 1.0}])
        assert "speed" not in client.get(f"/api/plans/{result2['plan_id']}").json()["plan"]["clips"][0]


def test_diff_detects_speed():
    from app.runtime.planning import diff_plans

    old = {"clips": [{"section": "opening", "asset_id": 1, "start": 0, "end": 2, "subtitle": None}]}
    new = {"clips": [{"section": "opening", "asset_id": 1, "start": 0, "end": 2, "subtitle": None,
                      "speed": 0.5}]}
    diff = diff_plans(old, new)
    assert any("变速" in c and "0.5x" in c for c in diff["changed"])


async def test_speed_chat_intent(analyzed_asset):
    from app.providers import set_providers

    set_providers(llm=_mock_llm(analyzed_asset))
    with TestClient(app) as client:
        plan_id = client.post("/api/plans", json={"goal": "变速流"}).json()["plan_id"]
        for _ in range(50):
            await asyncio.sleep(0.1)
            if client.get(f"/api/plans/{plan_id}").json()["status"] != "generating":
                break
        r = client.post("/api/chat", json={"message": "把第一段放慢一倍"}).json()
        for _ in range(50):
            await asyncio.sleep(0.1)
            msgs = client.get(f"/api/chat/{r['session_id']}").json()["messages"]
            acts = [m for m in msgs if m["role"] == "action"]
            if acts and all(a["status"] != "pending" for a in acts):
                break
        assert acts[-1]["status"] == "done"
        new_clips = client.get(f"/api/plans/{acts[-1]['result']['plan_id']}").json()["plan"]["clips"]
        assert new_clips[0]["speed"] == 0.5
