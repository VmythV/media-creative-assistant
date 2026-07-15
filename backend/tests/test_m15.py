"""M15 测试：IR v0.5 字幕样式、预设展开、位置/底条渲染像素断言、对话意图（phase2-roadmap §4）。"""

import asyncio
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.ir.schema import IRValidationError, validate_ir
from app.main import app


@pytest.fixture(scope="session")
def black_video(tmp_path_factory):
    """纯黑视频：字幕像素断言不受画面干扰。"""
    path = tmp_path_factory.mktemp("media") / "black.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "quiet", "-f", "lavfi", "-i", "color=black:duration=3:size=640x360:rate=25",
         "-c:v", "libx264", "-preset", "ultrafast", str(path)],
        check=True,
    )
    return path


def _ir(video_path: str, style: dict | None) -> dict:
    return {
        "version": "0.5",
        "project": {"name": "style-test", "fps": 25, "resolution": {"width": 640, "height": 360}},
        "sources": [{"id": "src_1", "path": video_path, "duration": 3.0}],
        "tracks": [
            {"type": "video", "index": 1, "items": [
                {"type": "clip", "source_id": "src_1", "trim": {"start": 0.0, "end": 2.0},
                 "role": "opening", "reason": "r"}]},
            {"type": "subtitle", "index": 1, "style": style, "items": [
                {"type": "subtitle", "content": "样式测试字幕", "timeline_start": 0.0, "timeline_end": 2.0}]},
        ],
        "render": None,
    }


def test_ir_v05_style_validation(black_video):
    ir = validate_ir(_ir(str(black_video), {"preset": "bold", "position": "top",
                                            "color": "#FFD400", "background": True}))
    track = next(t for t in ir.tracks if t.type == "subtitle")
    assert track.style.position == "top" and track.style.color == "#FFD400"
    # 非法颜色 / 非法位置
    with pytest.raises(IRValidationError):
        validate_ir(_ir(str(black_video), {"color": "yellow"}))
    with pytest.raises(IRValidationError):
        validate_ir(_ir(str(black_video), {"position": "left"}))
    # 无样式兼容
    assert validate_ir(_ir(str(black_video), None)).version == "0.5"  # 显式 0.5 IR 仍受支持


def _row_brightness(frame_path: str, region: str) -> float:
    """帧图上/下三分之一区域的最大亮度（0-255）。"""
    from PIL import Image

    img = Image.open(frame_path).convert("L")
    w, h = img.size
    box = (0, 0, w, h // 3) if region == "top" else (0, h * 2 // 3, w, h)
    return max(img.crop(box).getdata())

def _render_and_frame(ir_dict, tmp_path, name) -> str:
    from app.ir.renderer import render_video

    result = render_video(validate_ir(ir_dict), tmp_path / name)
    frame = str(tmp_path / f"{name}.png")
    subprocess.run(["ffmpeg", "-y", "-v", "quiet", "-ss", "1", "-i", result["video"],
                    "-frames:v", "1", frame], check=True)
    return frame


def test_render_style_top_vs_bottom(black_video, tmp_path):
    """位置生效：top 样式字幕亮像素在上 1/3，底部无；默认样式则相反。"""
    frame_top = _render_and_frame(
        _ir(str(black_video), {"position": "top", "color": "#FFD400", "background": True}),
        tmp_path, "top")
    assert _row_brightness(frame_top, "top") > 150   # 顶部有黄色字
    assert _row_brightness(frame_top, "bottom") < 40  # 底部纯黑

    frame_default = _render_and_frame(_ir(str(black_video), None), tmp_path, "default")
    assert _row_brightness(frame_default, "bottom") > 150
    assert _row_brightness(frame_default, "top") < 40


async def test_style_api_and_chat_intent(analyzed_asset):
    from app.providers import set_providers

    class MockLLM:
        async def chat(self, messages, **kwargs):
            if "调度员" in messages[0]["content"]:
                return {"content": json.dumps({"reply": "OK", "actions": [
                    {"intent": "set_subtitle_style",
                     "params": {"preset": "bold", "position": "top"}},
                ]}, ensure_ascii=False), "tool_calls": None}
            plan = {"title": "样式流测试", "clips": [
                {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 2.0,
                 "reason": "r", "subtitle": "有字幕"}]}
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    set_providers(llm=MockLLM())
    with TestClient(app) as client:
        plan_id = client.post("/api/plans", json={"goal": "样式流"}).json()["plan_id"]
        for _ in range(50):
            await asyncio.sleep(0.1)
            if client.get(f"/api/plans/{plan_id}").json()["status"] != "generating":
                break

        # API：elegant 预设展开
        resp = client.put(f"/api/plans/{plan_id}/subtitle-style", json={"preset": "elegant"}).json()
        assert resp["style"]["font"] == "serif" and resp["style"]["color"] == "#FFF8E7"
        ir = client.get(f"/api/plans/{plan_id}").json()["ir"]
        assert ir["version"] == "0.6"
        # 预设 + 字段覆盖
        resp = client.put(f"/api/plans/{plan_id}/subtitle-style",
                          json={"preset": "bold", "position": "center"}).json()
        assert resp["style"]["position"] == "center" and resp["style"]["background"] is True
        # 非法预设
        assert client.put(f"/api/plans/{plan_id}/subtitle-style",
                          json={"preset": "fancy"}).status_code == 400

        # 对话意图
        r = client.post("/api/chat", json={"message": "字幕放上面醒目点"}).json()
        for _ in range(50):
            await asyncio.sleep(0.1)
            msgs = client.get(f"/api/chat/{r['session_id']}").json()["messages"]
            acts = [m for m in msgs if m["role"] == "action"]
            if acts and all(a["status"] != "pending" for a in acts):
                break
        assert acts[-1]["status"] == "done"
        assert acts[-1]["result"]["preset"] == "bold" and acts[-1]["result"]["position"] == "top"

        # 重置
        client.delete(f"/api/plans/{plan_id}/subtitle-style")
        ir = client.get(f"/api/plans/{plan_id}").json()["ir"]
        assert all("style" not in t or t.get("style") is None
                   for t in ir["tracks"] if t["type"] == "subtitle")
