"""M21 测试：发布文案包（受限格式/钳制/存储/意图）、渲染进度事件（backlog B11+B12）。"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _plan_mock(analyzed_asset):
    return {"title": "文案测试片", "clips": [
        {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 2.0,
         "reason": "开场山景", "subtitle": "这个周末去江南"}]}


async def test_publish_kit_clamp_and_generate(analyzed_asset):
    from app.providers import set_providers
    from app.runtime.publish import generate_publish_kit

    class MockLLM:
        async def chat(self, messages, **kwargs):
            assert "文案" in messages[0]["content"]
            return {"content": json.dumps({
                "title": "江南水乡治愈之旅" + "x" * 60,          # 超长 → 钳制到 40
                "description": "周末逃离城市🌿",
                "hashtags": ["#江南", "旅行vlog", "治愈", "", "周末去哪儿", "风景", "慢生活", "多余的第七个"],
            }, ensure_ascii=False), "tool_calls": None}

    set_providers(llm=MockLLM())
    kit = await generate_publish_kit(_plan_mock(analyzed_asset), "抖音")
    assert len(kit["title"]) == 40                      # 长度钳制
    assert kit["hashtags"][0] == "江南"                  # 去 # 号
    assert "" not in kit["hashtags"] and len(kit["hashtags"]) == 6  # 去空 + 数量钳制
    assert kit["platform"] == "抖音"

    class BadLLM:
        async def chat(self, messages, **kwargs):
            return {"content": '{"description": "没有标题"}', "tool_calls": None}

    set_providers(llm=BadLLM())
    with pytest.raises(ValueError, match="标题"):
        await generate_publish_kit(_plan_mock(analyzed_asset))


async def test_publish_api_and_chat_intent(analyzed_asset):
    from app.providers import set_providers

    class MockLLM:
        async def chat(self, messages, **kwargs):
            system = messages[0]["content"]
            if "调度员" in system:
                return {"content": json.dumps({"reply": "OK", "actions": [
                    {"intent": "publish_kit", "params": {"platform": "B站"}},
                ]}, ensure_ascii=False), "tool_calls": None}
            if "文案" in system:
                return {"content": json.dumps({
                    "title": "江南一梦", "description": "跟我走进水乡",
                    "hashtags": ["江南", "旅行"],
                }, ensure_ascii=False), "tool_calls": None}
            return {"content": json.dumps(_plan_mock(analyzed_asset), ensure_ascii=False),
                    "tool_calls": None}

    set_providers(llm=MockLLM())
    with TestClient(app) as client:
        plan_id = client.post("/api/plans", json={"goal": "文案流"}).json()["plan_id"]
        for _ in range(50):
            await asyncio.sleep(0.1)
            if client.get(f"/api/plans/{plan_id}").json()["status"] != "generating":
                break

        # API：生成 + 存储
        resp = client.post(f"/api/plans/{plan_id}/publish-kit", json={"platform": "小红书"}).json()
        assert resp["publish"]["title"] == "江南一梦" and resp["publish"]["platform"] == "小红书"
        assert client.get(f"/api/plans/{plan_id}").json()["plan"]["publish"]["title"] == "江南一梦"

        # 对话意图
        r = client.post("/api/chat", json={"message": "帮我写个B站文案"}).json()
        for _ in range(50):
            await asyncio.sleep(0.1)
            msgs = client.get(f"/api/chat/{r['session_id']}").json()["messages"]
            acts = [m for m in msgs if m["role"] == "action"]
            if acts and all(a["status"] != "pending" for a in acts):
                break
        assert acts[-1]["status"] == "done"
        assert acts[-1]["result"]["title"] == "江南一梦"
        assert acts[-1]["result"]["hashtags"] == ["江南", "旅行"]


async def test_render_progress_events(analyzed_asset, sample_video):
    """渲染工具带 progress_plan_id 时发布片段级 SSE 事件（B11）。"""
    from app.runtime.events import bus
    from app.tools import load_all_tools
    from app.tools.registry import registry

    load_all_tools()
    ir = {
        "version": "0.5",
        "project": {"name": "progress-test", "fps": 25, "resolution": {"width": 640, "height": 360}},
        "sources": [{"id": "src_1", "path": str(sample_video), "duration": 5.0}],
        "tracks": [{"type": "video", "index": 1, "items": [
            {"type": "clip", "source_id": "src_1", "trim": {"start": 0.0, "end": 1.0},
             "role": "opening", "reason": "r"}]}],
        "render": {"width": 320, "height": 180, "quality": "draft"},
    }
    q = bus.subscribe()
    try:
        import tempfile

        result = await registry.execute("render_video", {
            "ir": ir, "output_dir": tempfile.mkdtemp(), "progress_plan_id": 777})
        assert result.ok
        captured = []
        while not q.empty():
            captured.append(q.get_nowait())
        render_events = [e for e in captured if e.get("type") == "render"
                         and e.get("plan_id") == 777]
        steps = {e["step"] for e in render_events}
        assert "segment" in steps and "done" in steps  # 片段级进度已透传
    finally:
        bus.unsubscribe(q)
