"""M16 测试：渲染引擎路由（monkeypatch Resolve）、参数校验、配音意图（phase2-roadmap §5）。"""

import asyncio
import json

from fastapi.testclient import TestClient

from app.main import app


async def test_run_render_resolve_engine(analyzed_asset, monkeypatch, tmp_path):
    """engine=resolve 路由到 render_with_resolve，结果带 engine/note/video_url。"""
    from app.providers import set_providers

    class MockLLM:
        async def chat(self, messages, **kwargs):
            plan = {"title": "引擎测试", "clips": [
                {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 2.0,
                 "reason": "r", "subtitle": None}]}
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    set_providers(llm=MockLLM())

    fake_video = tmp_path / "resolve_out.mp4"
    fake_video.write_bytes(b"fake")

    import app.adapters.resolve_adapter as adapter

    def fake_render(ir, out_dir, *, filename=None, progress=None):
        return {"video": str(fake_video), "duration": 2.0, "resolve": {"project": "p"}}

    monkeypatch.setattr(adapter, "render_with_resolve", fake_render)

    with TestClient(app) as client:
        plan_id = client.post("/api/plans", json={"goal": "引擎测试"}).json()["plan_id"]
        for _ in range(50):
            await asyncio.sleep(0.1)
            if client.get(f"/api/plans/{plan_id}").json()["status"] != "generating":
                break
        client.post(f"/api/plans/{plan_id}/confirm")

        # 非法引擎拒绝
        resp = client.post(f"/api/plans/{plan_id}/render", json={"engine": "quicktime"})
        assert resp.status_code == 400

        from app.api.execute import run_render

        ir = client.get(f"/api/plans/{plan_id}").json()["ir"]
        output = await run_render(plan_id, ir, engine="resolve")
        assert output["engine"] == "resolve" and "字幕" in output["note"]
        assert output["video_url"].endswith("resolve_out.mp4")
        # 落库
        render = client.get(f"/api/plans/{plan_id}").json()["plan"]["render"]
        assert render["engine"] == "resolve"

        # 默认 ffmpeg 引擎不受影响
        output = await run_render(plan_id, ir)
        assert output["engine"] == "ffmpeg" and output.get("subtitles_burned") is not None


async def test_chat_render_engine_and_voiceover(analyzed_asset, monkeypatch, tmp_path):
    from app.providers import set_providers

    class MockLLM:
        async def chat(self, messages, **kwargs):
            if "调度员" in messages[0]["content"]:
                return {"content": json.dumps({"reply": "OK", "actions": [
                    {"intent": "render", "params": {"engine": "webm"}},        # 非法 engine
                    {"intent": "generate_voiceover", "params": {"text": "欢迎来到江南"}},
                ]}, ensure_ascii=False), "tool_calls": None}
            plan = {"title": "t", "clips": [
                {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 2.0,
                 "reason": "r", "subtitle": None}]}
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    set_providers(llm=MockLLM())

    fake_audio = tmp_path / "vo.wav"
    fake_audio.write_bytes(b"RIFF")
    import app.adapters.resolve_adapter as adapter

    monkeypatch.setattr(adapter, "generate_speech",
                        lambda text, out_dir, voice="Female 1": {"audio": str(fake_audio), "text": text})

    with TestClient(app) as client:
        resp = client.post("/api/chat", json={"message": "配音并渲染"}).json()
        # 非法 engine 参数校验拒绝
        assert resp["actions"][0]["status"] == "invalid"
        for _ in range(50):
            await asyncio.sleep(0.1)
            msgs = client.get(f"/api/chat/{resp['session_id']}").json()["messages"]
            acts = [m for m in msgs if m["role"] == "action"]
            if acts and all(a["status"] != "pending" for a in acts):
                break
        vo = next(a for a in acts if a["intent"] == "generate_voiceover")
        assert vo["status"] == "done"
        assert vo["result"]["audio"].endswith("vo.wav")
