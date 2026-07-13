"""M1 素材分析管线测试。

- FFmpeg 工具（extract_audio / sample_frames / detect_audio_events / detect_shots）真实执行。
- VisionProvider / LLMProvider 用 mock 注入，不依赖 API Key。
- transcribe_audio 需下载 Whisper 模型，默认跳过（RUN_WHISPER_TESTS=1 启用）。
"""

import os

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.tools import load_all_tools
from app.tools.media import detect_audio_events, extract_audio, sample_frames
from app.tools.shots import detect_shots

load_all_tools()


def test_extract_audio(sample_video):
    result = extract_audio(str(sample_video))
    assert result["wav_path"].endswith("audio.wav")
    assert os.path.getsize(result["wav_path"]) > 1000


def test_sample_frames(sample_video):
    result = sample_frames(str(sample_video), [1.0, 2.5, 99.0])  # 99s 超出时长，应被跳过
    frames = result["frames"]
    assert len(frames) == 2
    assert all(os.path.exists(f["image_path"]) for f in frames)


def test_detect_audio_events(sample_video):
    wav = extract_audio(str(sample_video))["wav_path"]
    result = detect_audio_events(wav)
    # 正弦波无静音；音量字段存在
    assert result["mean_volume_db"] is not None
    assert isinstance(result["silences"], list)


def test_detect_shots_single_scene(sample_video):
    result = detect_shots(str(sample_video))
    shots = result["shots"]
    assert len(shots) >= 1
    assert shots[0]["start"] == 0.0
    assert abs(shots[-1]["end"] - 5.0) < 0.5


class MockVision:
    async def analyze_images(self, image_paths, prompt, *, json_mode=False):
        return (
            '{"category": "风景", "description": "测试画面", "quality_score": 7,'
            ' "subjects": ["图案"], "motion": "slow", "is_junk": false,'
            ' "suitable_roles": ["broll"]}'
        )


class UnavailableLLM:
    async def chat(self, messages, **kwargs):
        from app.providers.base import ProviderUnavailableError

        raise ProviderUnavailableError("测试环境无 LLM")


async def test_pipeline_end_to_end_with_cache(sample_video, monkeypatch):
    """端到端管线 + 二次运行命中缓存。vision 走 mock，whisper 跳过。"""
    from app.providers import set_providers
    from app.runtime import pipeline
    from app.store.db import db_session
    from app.store.models import AnalysisRecord, Asset

    set_providers(vision=MockVision(), llm=UnavailableLLM())
    monkeypatch.setattr(pipeline, "whisper_available", lambda: False)
    monkeypatch.setattr(pipeline.settings, "dashscope_api_key", "mock-key")

    with TestClient(app) as client:
        resp = client.post("/api/assets/import", json={"paths": [str(sample_video)]})
        assert resp.status_code == 200, resp.text
        asset = resp.json()["imported"][0]
        assert asset["duration"] and asset["has_audio"]

    result = await pipeline.analyze_asset(asset["id"])
    assert result["status"] == "analyzed", result
    summary = result["summary"]
    assert summary["category"] == "风景"
    assert summary["highlights"], "应产出精彩片段候选"
    assert summary["highlights"][0]["reason"]
    assert summary["highlight_source"] == "heuristic"  # LLM 不可用，走启发式

    # 二次运行：vision 记录应命中缓存（vision 不再被调用也能得到相同结果）
    class FailVision:
        async def analyze_images(self, *a, **k):
            raise AssertionError("缓存未命中：vision 被重复调用")

    set_providers(vision=FailVision())
    result2 = await pipeline.analyze_asset(asset["id"])
    assert result2["status"] == "analyzed"
    assert result2["summary"]["category"] == "风景"

    with db_session() as db:
        a = db.get(Asset, asset["id"])
        assert a.status == "analyzed"
        kinds = {
            r.kind
            for r in db.query(AnalysisRecord).filter_by(content_hash=a.content_hash).all()
        }
        assert {"probe", "shots", "audio_events", "vision", "summary"} <= kinds

    # 分析结果 API
    with TestClient(app) as client:
        resp = client.get(f"/api/assets/{asset['id']}/analysis")
        assert resp.status_code == 200
        analysis = resp.json()["analysis"]
        assert analysis["summary"]["category"] == "风景"


@pytest.mark.skipif(
    os.environ.get("RUN_WHISPER_TESTS") != "1",
    reason="需下载 Whisper 模型，设置 RUN_WHISPER_TESTS=1 启用",
)
def test_transcribe_audio(sample_video):
    from app.tools.audio import transcribe_audio

    wav = extract_audio(str(sample_video))["wav_path"]
    result = transcribe_audio(wav)
    assert "segments" in result and "language" in result
