"""M20(a) 测试：视觉并发提速、均匀采样、快速模型档位、渲染质量档位。"""

import asyncio
import json
import time

import pytest

from app.ir.schema import validate_ir


def test_pick_vision_shots_even_sampling():
    from app.runtime.pipeline import pick_vision_shots

    shots = [{"index": i, "start": float(i), "end": float(i + 1)} for i in range(100)]
    picked = pick_vision_shots(shots, 30)
    assert len(picked) == 30
    idx = [s["index"] for s in picked]
    assert idx[0] == 0 and idx[-1] == 99          # 含首尾
    gaps = [b - a for a, b in zip(idx, idx[1:])]
    assert max(gaps) <= 5                          # 均匀分布（≈3.4 步长），非头部截断
    # 阈值内原样返回
    assert pick_vision_shots(shots[:10], 30) == shots[:10]


async def test_vision_batch_concurrency(tmp_path, monkeypatch):
    """8 个镜头 × 0.2s 延迟，并发 4 → 用时接近 2 批而非 8 次串行。"""
    from PIL import Image

    from app.providers import set_providers
    from app.runtime import pipeline

    class SlowVision:
        async def analyze_images(self, image_paths, prompt, *, json_mode=False):
            await asyncio.sleep(0.2)
            return json.dumps({"category": "风景", "description": "d", "quality_score": 7,
                               "subjects": [], "motion": "slow", "is_junk": False,
                               "suitable_roles": ["broll"]})

    from app.tools import load_all_tools

    load_all_tools()
    set_providers(vision=SlowVision())
    monkeypatch.setattr(pipeline.settings, "vision_concurrency", 4)

    pairs = []
    for i in range(8):
        f = tmp_path / f"f{i}.jpg"
        Image.new("RGB", (32, 32), (i * 30, 0, 0)).save(f)
        pairs.append(({"index": i}, str(f)))

    t0 = time.monotonic()
    result = await pipeline._vision_batch(asset_id=0, pairs=pairs)
    elapsed = time.monotonic() - t0
    assert len(result) == 8
    assert elapsed < 1.0, f"并发未生效：{elapsed:.2f}s（串行应 ≈1.6s）"


def test_effective_vision_model(monkeypatch):
    from app.config import settings
    from app.providers.qwen import effective_vision_model

    monkeypatch.setattr(settings, "vision_speed", "quality")
    assert effective_vision_model() == settings.qwen_vl_model
    monkeypatch.setattr(settings, "vision_speed", "fast")
    assert effective_vision_model() == settings.qwen_vl_fast_model


def test_render_quality_tier(sample_video):
    from app.ir.renderer import _encode_args

    def _ir(quality):
        return validate_ir({
            "version": "0.5",
            "project": {"name": "q", "fps": 25, "resolution": {"width": 640, "height": 360}},
            "sources": [{"id": "src_1", "path": str(sample_video), "duration": 5.0}],
            "tracks": [{"type": "video", "index": 1, "items": [
                {"type": "clip", "source_id": "src_1", "trim": {"start": 0.0, "end": 1.0},
                 "role": "opening", "reason": "r"}]}],
            "render": {"width": 640, "height": 360, "quality": quality},
        })

    assert "-crf" in _encode_args(_ir("draft")) and "veryfast" in _encode_args(_ir("draft"))
    assert "medium" in _encode_args(_ir("final"))
    # 非法档位被 schema 拒绝
    from app.ir.schema import IRValidationError

    with pytest.raises(IRValidationError):
        _ir("ultra")


async def test_apply_output_quality_only(analyzed_asset):
    """只改质量档位：沿用现有分辨率，不要求 aspect。"""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.providers import set_providers

    class MockLLM:
        async def chat(self, messages, **kwargs):
            plan = {"title": "档位测试", "clips": [
                {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 2.0,
                 "reason": "r", "subtitle": None}]}
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    set_providers(llm=MockLLM())
    with TestClient(app) as client:
        plan_id = client.post("/api/plans", json={"goal": "档位"}).json()["plan_id"]
        for _ in range(50):
            await asyncio.sleep(0.1)
            if client.get(f"/api/plans/{plan_id}").json()["status"] != "generating":
                break
        resp = client.put(f"/api/plans/{plan_id}/output", json={"quality": "draft"}).json()
        assert resp["render"]["quality"] == "draft"
        assert resp["render"]["width"] == 640  # 沿用时间线规格（sample_video 640x360）
        # 再切画幅：质量沿参数
        resp = client.put(f"/api/plans/{plan_id}/output",
                          json={"aspect": "9:16", "quality": "draft"}).json()
        assert resp["render"] == {"width": 1080, "height": 1920, "fill": "blur",
                                  "quality": "draft"}
