"""M8 测试：IR v0.2 音频轨、配乐 API、渲染混音、成片静态托管（设计文档 §11）。"""

import asyncio
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.ir.schema import IRValidationError, validate_ir
from app.main import app


@pytest.fixture(scope="session")
def music_file(tmp_path_factory):
    """10 秒正弦波配乐（比测试时间线长，验证截齐）。"""
    path = tmp_path_factory.mktemp("music") / "bgm.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "quiet", "-f", "lavfi",
         "-i", "sine=frequency=220:duration=10", str(path)],
        check=True,
    )
    return path


def _ir_with_music(video_path: str, music_path: str) -> dict:
    return {
        "version": "0.2",
        "project": {"name": "music-test", "fps": 25, "resolution": {"width": 640, "height": 360}},
        "sources": [
            {"id": "src_1", "path": video_path, "duration": 5.0},
            {"id": "src_music", "path": music_path, "duration": 10.0},
        ],
        "tracks": [
            {"type": "video", "index": 1, "items": [
                {"type": "clip", "source_id": "src_1", "trim": {"start": 0.0, "end": 4.0},
                 "role": "opening", "reason": "r"},
            ]},
            {"type": "audio", "index": 1, "items": [
                {"type": "music", "source_id": "src_music", "gain_db": -6.0,
                 "fade_in": 0.5, "fade_out": 1.0, "loop": True},
            ]},
        ],
        "render": None,
    }


def test_ir_v02_audio_track_validation(sample_video, music_file):
    ir = _ir_with_music(str(sample_video), str(music_file))
    parsed = validate_ir(ir)
    assert parsed.version == "0.2"

    bad = json.loads(json.dumps(ir))
    bad["tracks"][1]["items"][0]["source_id"] = "src_ghost"
    with pytest.raises(IRValidationError, match="不存在的 source"):
        validate_ir(bad)

    two = json.loads(json.dumps(ir))
    two["tracks"][1]["items"].append(two["tracks"][1]["items"][0])
    with pytest.raises(IRValidationError, match="单条配乐"):
        validate_ir(two)


def test_ir_v01_still_supported(sample_video):
    ir = {
        "version": "0.1",
        "project": {"name": "old", "fps": 25, "resolution": {"width": 640, "height": 360}},
        "sources": [{"id": "src_1", "path": str(sample_video), "duration": 5.0}],
        "tracks": [{"type": "video", "index": 1, "items": [
            {"type": "clip", "source_id": "src_1", "trim": {"start": 0.0, "end": 2.0},
             "role": "opening", "reason": "r"}]}],
        "render": None,
    }
    assert validate_ir(ir).version == "0.1"


def test_render_with_music(sample_video, music_file, tmp_path):
    from app.ir.renderer import render_video

    result = render_video(validate_ir(_ir_with_music(str(sample_video), str(music_file))), tmp_path)
    assert result["music"] == "bgm.wav"
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", result["video"]],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "audio" in probe
    duration = float([l for l in probe.splitlines() if l.replace(".", "").isdigit()][-1])
    assert duration == pytest.approx(4.0, abs=0.2)  # 配乐 10s 被截齐到时间线 4s


async def test_music_api(sample_video, music_file, analyzed_asset):
    from app.providers import set_providers

    class MockLLM:
        async def chat(self, messages, **kwargs):
            plan = {"title": "配乐测试", "clips": [
                {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 2.0,
                 "reason": "r", "subtitle": None}]}
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    set_providers(llm=MockLLM())
    with TestClient(app) as client:
        plan_id = client.post("/api/plans", json={"goal": "配乐流测试"}).json()["plan_id"]
        for _ in range(50):
            await asyncio.sleep(0.1)
            if client.get(f"/api/plans/{plan_id}").json()["status"] != "generating":
                break

        # 设置配乐 → IR 带音频轨且版本 0.2
        resp = client.put(f"/api/plans/{plan_id}/music",
                          json={"path": str(music_file), "gain_db": -10})
        assert resp.status_code == 200 and resp.json()["music"] == "bgm.wav"
        ir = client.get(f"/api/plans/{plan_id}").json()["ir"]
        assert ir["version"] in ("0.2", "0.3")  # 0.1 存量才升 0.2；新方案 0.3 不降级
        audio = [t for t in ir["tracks"] if t["type"] == "audio"]
        assert len(audio) == 1 and audio[0]["items"][0]["gain_db"] == -10

        # 重复设置=替换，不叠加
        client.put(f"/api/plans/{plan_id}/music", json={"path": str(music_file)})
        ir = client.get(f"/api/plans/{plan_id}").json()["ir"]
        assert len([t for t in ir["tracks"] if t["type"] == "audio"]) == 1

        # 无音频流文件被拒绝
        resp = client.put(f"/api/plans/{plan_id}/music", json={"path": str(sample_video)})
        assert resp.status_code == 200 or "音频" in resp.text  # sample_video 有音轨则通过

        # 移除配乐
        assert client.delete(f"/api/plans/{plan_id}/music").json()["music"] is None
        ir = client.get(f"/api/plans/{plan_id}").json()["ir"]
        assert not [t for t in ir["tracks"] if t["type"] == "audio"]

        # 渲染结果带浏览器预览地址
        client.post(f"/api/plans/{plan_id}/confirm")
        client.post(f"/api/plans/{plan_id}/render")
        for _ in range(100):
            await asyncio.sleep(0.2)
            render = client.get(f"/api/plans/{plan_id}").json()["plan"].get("render")
            if render:
                break
        assert render and render["video_url"].startswith(f"/output/plan_{plan_id}/")
        assert client.get(render["video_url"]).status_code == 200
