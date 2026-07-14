"""M14 测试：曲库扫描、推荐白名单/兜底、推荐 API、对话 mood 推荐（phase2-roadmap §3）。"""

import asyncio
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture()
def library(tmp_path_factory):
    """曲库两首：文件名携带语义（calm/energetic）。"""
    music_dir = settings.data_dir / "music"
    music_dir.mkdir(parents=True, exist_ok=True)
    names = ["calm_guzheng_ambient.wav", "energetic_drums_fast.wav"]
    for i, name in enumerate(names):
        f = music_dir / name
        if not f.exists():
            subprocess.run(
                ["ffmpeg", "-y", "-v", "quiet", "-f", "lavfi",
                 "-i", f"sine=frequency={220 * (i + 1)}:duration=8", str(f)],
                check=True,
            )
    from app.runtime.music import scan_library

    scan_library()
    yield names


def test_scan_registers_and_cleans(library):
    from app.runtime.music import list_tracks, scan_library

    tracks = list_tracks()
    names = {t["filename"] for t in tracks}
    assert set(library) <= names
    assert all(t["duration"] > 0 for t in tracks)
    # 重复扫描不重复登记
    before = len(tracks)
    result = scan_library()
    assert result["added"] == 0 and result["total"] == before
    # 失效清理
    ghost = settings.data_dir / "music" / "ghost.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "quiet", "-f", "lavfi",
                    "-i", "sine=duration=1", str(ghost)], check=True)
    scan_library()
    ghost.unlink()
    result = scan_library()
    assert result["removed"] == 1


async def test_recommend_whitelist_and_fallback(library):
    from app.providers import set_providers
    from app.runtime.music import list_tracks, recommend_music

    tracks = list_tracks()
    energetic = next(t for t in tracks if "energetic" in t["filename"])

    class MockLLM:
        def __init__(self, payload):
            self.payload = payload

        async def chat(self, messages, **kwargs):
            return {"content": self.payload, "tool_calls": None}

    # 正常推荐：id 在白名单内
    set_providers(llm=MockLLM(json.dumps({"music_id": energetic["id"], "reason": "节奏匹配"})))
    reco = await recommend_music("energetic", None)
    assert reco["filename"] == energetic["filename"] and reco["reason"] == "节奏匹配"

    # 白名单外 id → 确定性兜底第一首
    set_providers(llm=MockLLM(json.dumps({"music_id": 99999, "reason": "x"})))
    reco = await recommend_music("任意", None)
    assert reco["filename"] == tracks[0]["filename"]
    assert "默认" in reco["reason"]


async def test_recommend_api_and_chat_mood(analyzed_asset, library):
    from app.providers import set_providers
    from app.runtime.music import list_tracks

    calm = next(t for t in list_tracks() if "calm" in t["filename"])

    class MockLLM:
        async def chat(self, messages, **kwargs):
            system = messages[0]["content"]
            if "调度员" in system:
                return {"content": json.dumps({"reply": "OK", "actions": [
                    {"intent": "set_music", "params": {"mood": "安静舒缓"}},
                ]}, ensure_ascii=False), "tool_calls": None}
            if "配乐师" in system:
                return {"content": json.dumps({"music_id": calm["id"], "reason": "古筝氛围契合"},
                                              ensure_ascii=False), "tool_calls": None}
            plan = {"title": "配乐推荐测试", "clips": [
                {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 2.0,
                 "reason": "r", "subtitle": None}]}
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    set_providers(llm=MockLLM())
    with TestClient(app) as client:
        plan_id = client.post("/api/plans", json={"goal": "配乐推荐"}).json()["plan_id"]
        for _ in range(50):
            await asyncio.sleep(0.1)
            if client.get(f"/api/plans/{plan_id}").json()["status"] != "generating":
                break

        # 推荐 API：推荐 + 应用 + 理由
        resp = client.post(f"/api/plans/{plan_id}/music/recommend", json={"mood": "舒缓"}).json()
        assert resp["music"] == calm["filename"] and "古筝" in resp["reason"]
        ir = client.get(f"/api/plans/{plan_id}").json()["ir"]
        assert any(t["type"] == "audio" for t in ir["tracks"])

        # 对话 mood 推荐
        r = client.post("/api/chat", json={"message": "配个安静点的音乐"}).json()
        for _ in range(50):
            await asyncio.sleep(0.1)
            msgs = client.get(f"/api/chat/{r['session_id']}").json()["messages"]
            acts = [m for m in msgs if m["role"] == "action"]
            if acts and all(a["status"] != "pending" for a in acts):
                break
        assert acts[-1]["status"] == "done"
        assert acts[-1]["result"]["music"] == calm["filename"]
        assert "古筝" in acts[-1]["result"]["reason"]

        # 曲库 API
        tracks = client.get("/api/music").json()["tracks"]
        assert any("calm" in t["filename"] for t in tracks)
