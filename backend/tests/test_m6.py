"""M6 测试：照片转片段、图片导入 API、IR 成片渲染（设计文档 §9）。"""

import subprocess

import pytest
from fastapi.testclient import TestClient

from app.ir.schema import validate_ir
from app.main import app


def _probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def _probe_size(path: str) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    w, h = out.split(",")
    return int(w), int(h)


@pytest.fixture(scope="session")
def sample_images(tmp_path_factory):
    """横构图 + 竖构图（带 EXIF 旋转）各一张。"""
    from PIL import Image

    d = tmp_path_factory.mktemp("images")
    landscape = d / "land.jpg"
    Image.new("RGB", (800, 600), (200, 120, 40)).save(landscape)
    portrait = d / "port.jpg"
    img = Image.new("RGB", (800, 600), (40, 120, 200))
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation=6：需顺时针旋转 90 度 → 实际竖构图
    img.save(portrait, exif=exif)
    return {"landscape": landscape, "portrait": portrait, "dir": d}


def test_image_to_clip_landscape(sample_images):
    from app.tools.media import image_to_clip

    result = image_to_clip(str(sample_images["landscape"]))
    assert not result["cached"]
    assert _probe_size(result["clip_path"]) == (1920, 1080)
    assert _probe_duration(result["clip_path"]) == pytest.approx(4.0, abs=0.1)
    # 幂等：二次调用直接复用
    assert image_to_clip(str(sample_images["landscape"]))["cached"]


def test_image_to_clip_portrait_exif(sample_images):
    """EXIF 旋转后按竖构图走模糊背景路径，输出仍是 1080p 横幅。"""
    from app.tools.media import image_to_clip

    result = image_to_clip(str(sample_images["portrait"]))
    assert _probe_size(result["clip_path"]) == (1920, 1080)


def test_import_images_api(sample_images):
    with TestClient(app) as client:
        resp = client.post("/api/assets/import", json={"directory": str(sample_images["dir"])})
        assert resp.status_code == 200
        data = resp.json()
        assert data["errors"] == []
        clips = [a for a in data["imported"] if a["filename"].startswith(("land_", "port_"))]
        assert len(clips) == 2
        for a in clips:
            assert a["filename"].endswith(".mp4")
            assert (a["width"], a["height"]) == (1920, 1080)
            assert not a["has_audio"]


def _render_ir(source_path: str) -> dict:
    return {
        "version": "0.1",
        "project": {"name": "render-test", "fps": 25, "resolution": {"width": 640, "height": 360}},
        "sources": [{"id": "src_1", "path": source_path, "duration": 5.0}],
        "tracks": [
            {"type": "video", "index": 1, "items": [
                {"type": "clip", "source_id": "src_1", "trim": {"start": 0.0, "end": 2.0},
                 "role": "opening", "reason": "测试开场"},
                {"type": "clip", "source_id": "src_1", "trim": {"start": 3.0, "end": 5.0},
                 "role": "ending", "reason": "测试结尾"},
            ]},
            {"type": "subtitle", "index": 1, "items": [
                {"type": "subtitle", "content": "第一段字幕", "timeline_start": 0.5, "timeline_end": 1.5},
                {"type": "subtitle", "content": "第二段字幕", "timeline_start": 2.5, "timeline_end": 3.5},
            ]},
        ],
        "render": None,
    }


def test_render_video(sample_video, tmp_path):
    from app.ir.renderer import render_video

    steps = []
    result = render_video(
        validate_ir(_render_ir(str(sample_video))), tmp_path,
        progress=lambda step, detail: steps.append(step),
    )
    assert result["clips"] == 2
    assert result["duration"] == pytest.approx(4.0)
    assert _probe_duration(result["video"]) == pytest.approx(4.0, abs=0.2)
    assert _probe_size(result["video"]) == (640, 360)
    assert "concat" in steps and "done" in steps
    # macOS/Linux 常见中文字体存在时必须烧录成功；无字体环境降级为不烧录
    from app.ir.renderer import _load_font

    assert result["subtitles_burned"] == (_load_font(20) is not None)


async def test_render_api(sample_video, analyzed_asset):
    """确认后的方案可通过 /render 产出 mp4，路径写回 plan.render。"""
    import asyncio
    import json

    from app.providers import set_providers

    class MockLLM:
        async def chat(self, messages, **kwargs):
            plan = {
                "title": "render-api-测试",
                "clips": [
                    {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 2.0,
                     "reason": "开场", "subtitle": "渲染测试"},
                ],
            }
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    set_providers(llm=MockLLM())
    with TestClient(app) as client:
        plan_id = client.post("/api/plans", json={"goal": "渲染测试"}).json()["plan_id"]
        for _ in range(50):
            await asyncio.sleep(0.1)
            if client.get(f"/api/plans/{plan_id}").json()["status"] != "generating":
                break
        assert client.post(f"/api/plans/{plan_id}/confirm").json()["status"] == "confirmed"

        assert client.post(f"/api/plans/{plan_id}/render").json()["status"] == "rendering"
        for _ in range(100):
            await asyncio.sleep(0.2)
            render = client.get(f"/api/plans/{plan_id}").json()["plan"].get("render")
            if render:
                break
        assert render and "error" not in render, render
        assert _probe_duration(render["video"]) == pytest.approx(2.0, abs=0.2)
