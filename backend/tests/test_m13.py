"""M13 测试：IR v0.4 交付规格、竖屏渲染、输出 API、对话意图（phase2-roadmap §2）。"""

import asyncio
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.ir.schema import IRValidationError, validate_ir
from app.main import app


def _ir(video_path: str, render: dict | None) -> dict:
    return {
        "version": "0.4",
        "project": {"name": "output-test", "fps": 25, "resolution": {"width": 640, "height": 360}},
        "sources": [{"id": "src_1", "path": video_path, "duration": 5.0}],
        "tracks": [
            {"type": "video", "index": 1, "items": [
                {"type": "clip", "source_id": "src_1", "trim": {"start": 0.0, "end": 2.0},
                 "role": "opening", "reason": "r"}]},
            {"type": "subtitle", "index": 1, "items": [
                {"type": "subtitle", "content": "竖屏字幕", "timeline_start": 0.0, "timeline_end": 2.0}]},
        ],
        "render": render,
    }


def test_ir_v04_render_spec_validation(sample_video):
    ir = validate_ir(_ir(str(sample_video), {"width": 1080, "height": 1920, "fill": "blur"}))
    assert ir.version == "0.4" and ir.render.fill == "blur"
    # 旧版本无 render 仍兼容
    old = _ir(str(sample_video), None)
    old["version"] = "0.3"
    assert validate_ir(old).render is None
    # 奇数分辨率拒绝
    with pytest.raises(IRValidationError, match="偶数"):
        validate_ir(_ir(str(sample_video), {"width": 1081, "height": 1920}))


def _probe_wh(path: str) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    ).stdout.strip().split(",")
    return int(out[0]), int(out[1])


def test_render_vertical_blur(sample_video, tmp_path):
    """640x360 横素材 → 540x960 竖屏（blur 构图），字幕按交付规格绘制。"""
    from app.ir.renderer import render_video

    ir = validate_ir(_ir(str(sample_video), {"width": 540, "height": 960, "fill": "blur"}))
    result = render_video(ir, tmp_path)
    assert result["resolution"] == "540x960"
    assert _probe_wh(result["video"]) == (540, 960)
    assert result["subtitles_burned"] is True


def test_render_crop_mode(sample_video, tmp_path):
    from app.ir.renderer import render_video

    ir = validate_ir(_ir(str(sample_video), {"width": 360, "height": 360, "fill": "crop"}))
    result = render_video(ir, tmp_path / "crop", burn_subtitles=False)
    assert _probe_wh(result["video"]) == (360, 360)


def test_render_default_unchanged(sample_video, tmp_path):
    """无 render 规格：按时间线规格 pad，兼容旧行为。"""
    from app.ir.renderer import render_video

    ir = validate_ir(_ir(str(sample_video), None))
    result = render_video(ir, tmp_path / "default", burn_subtitles=False)
    assert _probe_wh(result["video"]) == (640, 360)


async def test_output_api_and_chat_intent(analyzed_asset):
    from app.providers import set_providers

    class MockLLM:
        async def chat(self, messages, **kwargs):
            if "调度员" in messages[0]["content"]:
                return {"content": json.dumps({"reply": "切换竖屏", "actions": [
                    {"intent": "set_output_spec", "params": {"aspect": "9:16"}},
                ]}, ensure_ascii=False), "tool_calls": None}
            plan = {"title": "画幅测试", "clips": [
                {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 2.0,
                 "reason": "r", "subtitle": None}]}
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    set_providers(llm=MockLLM())
    with TestClient(app) as client:
        plan_id = client.post("/api/plans", json={"goal": "画幅测试"}).json()["plan_id"]
        for _ in range(50):
            await asyncio.sleep(0.1)
            if client.get(f"/api/plans/{plan_id}").json()["status"] != "generating":
                break

        # API 直设
        resp = client.put(f"/api/plans/{plan_id}/output", json={"aspect": "1:1"})
        assert resp.json()["render"] == {"width": 1080, "height": 1080, "fill": "blur"}
        ir = client.get(f"/api/plans/{plan_id}").json()["ir"]
        assert ir["version"] not in ("0.1", "0.2", "0.3") and ir["render"]["width"] == 1080
        # 非法画幅
        assert client.put(f"/api/plans/{plan_id}/output", json={"aspect": "2:3"}).status_code == 400

        # 对话意图 → 9:16
        resp = client.post("/api/chat", json={"message": "改成竖屏"}).json()
        for _ in range(50):
            await asyncio.sleep(0.1)
            msgs = client.get(f"/api/chat/{resp['session_id']}").json()["messages"]
            acts = [m for m in msgs if m["role"] == "action"]
            if acts and all(a["status"] != "pending" for a in acts):
                break
        assert acts[-1]["status"] == "done"
        assert acts[-1]["result"]["width"] == 1080 and acts[-1]["result"]["height"] == 1920
        ir = client.get(f"/api/plans/{acts[-1]['result']['plan_id']}").json()["ir"]
        assert ir["render"]["height"] == 1920

        # 重置
        client.delete(f"/api/plans/{plan_id}/output")
        assert client.get(f"/api/plans/{plan_id}").json()["ir"]["render"] is None
