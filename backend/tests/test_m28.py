"""M28 测试：主体感知裁切——crop_focus 渲染偏移/smart_crop/设置保留/对话（backlog B21）。"""

import asyncio
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.ir.schema import validate_ir
from app.main import app
from app.store.db import db_session
from app.store.models import EditPlan


def _left_subject_video(tmp_path):
    src = tmp_path / "left.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "quiet", "-f", "lavfi", "-i", "color=black:duration=2:size=320x180:rate=25",
         "-vf", "drawbox=x=0:y=60:w=60:h=60:color=white:t=fill",
         "-c:v", "libx264", "-preset", "ultrafast", str(src)], check=True)
    return src


def _left_brightness(video, tmp_path, name):
    fr = tmp_path / f"{name}.png"
    subprocess.run(["ffmpeg", "-y", "-v", "quiet", "-i", video, "-frames:v", "1",
                    "-vf", "crop=90:320:0:0", str(fr)], check=True)
    from PIL import Image

    return max(Image.open(fr).convert("L").getdata())


def test_crop_focus_offsets_window(tmp_path):
    from app.ir.renderer import render_video

    src = _left_subject_video(tmp_path)

    def _ir(focus):
        return validate_ir({
            "version": "0.6", "project": {"name": "c", "fps": 25,
                                          "resolution": {"width": 320, "height": 180}},
            "sources": [{"id": "src_1", "path": str(src), "duration": 2.0}],
            "tracks": [{"type": "video", "index": 1, "items": [
                {"type": "clip", "source_id": "src_1", "trim": {"start": 0, "end": 2},
                 "role": "opening", "reason": "r", "crop_focus": focus}]}],
            "render": {"width": 180, "height": 320, "fill": "crop"}})

    left = render_video(_ir(0.0), tmp_path / "l", burn_subtitles=False)
    center = render_video(_ir(0.5), tmp_path / "c", burn_subtitles=False)
    # focus=0 保留左侧主体（亮），focus=0.5 裁掉左侧（暗）
    assert _left_brightness(left["video"], tmp_path, "lb") > 200
    assert _left_brightness(center["video"], tmp_path, "cb") < 40


def test_crop_focus_schema_bounds(sample_video):
    ir = {"version": "0.6", "project": {"name": "c", "fps": 25,
                                        "resolution": {"width": 640, "height": 360}},
          "sources": [{"id": "src_1", "path": str(sample_video), "duration": 5.0}],
          "tracks": [{"type": "video", "index": 1, "items": [
              {"type": "clip", "source_id": "src_1", "trim": {"start": 0, "end": 2},
               "role": "opening", "reason": "r", "crop_focus": 1.5}]}]}
    from app.ir.schema import IRValidationError

    with pytest.raises(IRValidationError):
        validate_ir(ir)


def _mock_llm_vision(analyzed_asset, focus=0.2):
    class MockLLM:
        async def chat(self, messages, **kwargs):
            if "调度员" in messages[0]["content"]:
                return {"content": json.dumps({"reply": "OK", "actions": [
                    {"intent": "smart_crop", "params": {}}]}, ensure_ascii=False), "tool_calls": None}
            plan = {"title": "裁切测试", "clips": [
                {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 2.0,
                 "reason": "r", "subtitle": None},
                {"section": "ending", "asset_id": analyzed_asset, "start": 2.0, "end": 4.0,
                 "reason": "r", "subtitle": None}]}
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    class MockVision:
        async def analyze_images(self, image_paths, prompt, *, json_mode=False):
            return json.dumps({"focus_x": focus})

    return MockLLM(), MockVision()


async def _make_plan(client) -> int:
    plan_id = client.post("/api/plans", json={"goal": "裁切"}).json()["plan_id"]
    for _ in range(50):
        await asyncio.sleep(0.1)
        if client.get(f"/api/plans/{plan_id}").json()["status"] != "generating":
            break
    return plan_id


async def test_smart_crop_writes_focus_and_carries_render(analyzed_asset):
    from app.providers import set_providers
    from app.runtime.framing import smart_crop

    llm, vision = _mock_llm_vision(analyzed_asset, focus=0.25)
    set_providers(llm=llm, vision=vision)
    with TestClient(app) as client:
        base = await _make_plan(client)
        # 未设竖屏 → smart_crop 报错
        with pytest.raises(ValueError, match="竖屏"):
            await smart_crop(base)
        # 设竖屏
        client.put(f"/api/plans/{base}/output", json={"aspect": "9:16"})
        r = await smart_crop(base)
        assert r["focused"] == 2 and r["off_center"] == 2  # 0.25 偏离居中
        new = client.get(f"/api/plans/{r['plan_id']}").json()
        assert all(c["crop_focus"] == 0.25 for c in new["plan"]["clips"])
        assert new["ir"]["render"]["fill"] == "crop"  # 切为填满式
        assert new["ir"]["render"]["width"] == 1080     # 竖屏规格保留


async def test_edit_preserves_vertical_output(analyzed_asset):
    """回归：设竖屏后做局部修改，交付规格不丢（carry_ir_settings）。"""
    from app.providers import set_providers
    from app.runtime.clip_ops import apply_clip_ops

    llm, vision = _mock_llm_vision(analyzed_asset)
    set_providers(llm=llm, vision=vision)
    with TestClient(app) as client:
        base = await _make_plan(client)
        client.put(f"/api/plans/{base}/output", json={"aspect": "9:16", "fill": "blur"})
        # 局部修改（删第2段）
        result = apply_clip_ops(base, [{"op": "remove", "position": 2}])
        new_ir = client.get(f"/api/plans/{result['plan_id']}").json()["ir"]
        assert new_ir["render"] is not None
        assert new_ir["render"]["width"] == 1080 and new_ir["render"]["height"] == 1920


async def test_smart_crop_chat_intent(analyzed_asset):
    from app.providers import set_providers

    llm, vision = _mock_llm_vision(analyzed_asset, focus=0.15)
    set_providers(llm=llm, vision=vision)
    with TestClient(app) as client:
        base = await _make_plan(client)
        client.put(f"/api/plans/{base}/output", json={"aspect": "9:16"})
        r = client.post("/api/chat", json={"message": "竖屏别把主体切掉，智能裁切一下"}).json()
        for _ in range(80):
            await asyncio.sleep(0.1)
            msgs = client.get(f"/api/chat/{r['session_id']}").json()["messages"]
            acts = [m for m in msgs if m["role"] == "action"]
            if acts and all(a["status"] != "pending" for a in acts):
                break
        assert acts[-1]["status"] == "done"
        new = client.get(f"/api/plans/{acts[-1]['result']['plan_id']}").json()
        assert new["plan"]["clips"][0]["crop_focus"] == 0.15
