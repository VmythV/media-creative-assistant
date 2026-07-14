"""M12 测试：对话式指挥——路由受限格式/白名单/参数校验/串联执行/上下文指代（phase2-roadmap §1）。"""

import asyncio
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture()
def music_in_library(tmp_path_factory):
    """往 data/music 放一个测试音频（set_music 无 path 时从库选）。"""
    music_dir = settings.data_dir / "music"
    music_dir.mkdir(parents=True, exist_ok=True)
    path = music_dir / "chat_bgm.wav"
    if not path.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-v", "quiet", "-f", "lavfi",
             "-i", "sine=frequency=330:duration=6", str(path)],
            check=True,
        )
    yield path


def _router_llm(analyzed_asset):
    """Mock：调度员返回三连动作；Planning 返回固定方案；偏好提取返回空。"""

    class MockLLM:
        async def chat(self, messages, **kwargs):
            system = messages[0]["content"]
            if "调度员" in system:
                return {"content": json.dumps({
                    "reply": "好的，我来生成方案、配乐并渲染。",
                    "actions": [
                        {"intent": "create_plan", "params": {"goal": "6秒测试片"}},
                        {"intent": "set_music", "params": {"mood": "舒缓"}},
                        {"intent": "render", "params": {}},
                        {"intent": "hack_system", "params": {}},          # 白名单外
                        {"intent": "revise_plan", "params": {}},          # 缺 instruction
                    ],
                }, ensure_ascii=False), "tool_calls": None}
            if "提取" in system:
                return {"content": '{"preferences": []}', "tool_calls": None}
            plan = {"title": "对话测试片", "clips": [
                {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 2.0,
                 "reason": "r", "subtitle": "你好"},
                {"section": "ending", "asset_id": analyzed_asset, "start": 3.0, "end": 5.0,
                 "reason": "r", "subtitle": None,
                 "transition": {"type": "fade", "duration": 0.5}},
            ]}
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    return MockLLM()


async def _wait_actions_settled(client, session_id, expect_total, timeout=60.0):
    for _ in range(int(timeout / 0.2)):
        await asyncio.sleep(0.2)
        msgs = client.get(f"/api/chat/{session_id}").json()["messages"]
        acts = [m for m in msgs if m["role"] == "action"]
        if len(acts) >= expect_total and all(a["status"] != "pending" for a in acts):
            return acts
    raise AssertionError(f"动作未在 {timeout}s 内完成: {acts}")


async def test_chat_full_chain(analyzed_asset, music_in_library):
    """一句话 → 生成方案 + 库中选乐 + 渲染；白名单外拒绝、参数缺失拒绝。"""
    from app.providers import set_providers

    set_providers(llm=_router_llm(analyzed_asset))
    with TestClient(app) as client:
        resp = client.post("/api/chat", json={"message": "做个6秒测试片配上音乐渲染出来"}).json()
        assert resp["reply"].startswith("好的")
        assert [a["status"] for a in resp["actions"]] == \
            ["pending", "pending", "pending", "invalid", "invalid"]

        acts = await _wait_actions_settled(client, resp["session_id"], 5)
        by_intent = {a["intent"]: a for a in acts}
        assert by_intent["create_plan"]["status"] == "done"
        plan_id = by_intent["create_plan"]["result"]["plan_id"]
        assert by_intent["set_music"]["status"] == "done"
        assert by_intent["set_music"]["result"]["music"] == "chat_bgm.wav"  # 库中自动选取
        assert by_intent["render"]["status"] == "done"
        assert by_intent["render"]["result"]["video_url"].startswith(f"/output/plan_{plan_id}/")
        assert by_intent["hack_system"]["status"] == "invalid"
        assert by_intent["revise_plan"]["status"] == "invalid"

        # 渲染产物真实可访问，且方案状态已推进
        assert client.get(by_intent["render"]["result"]["video_url"]).status_code == 200
        plan = client.get(f"/api/plans/{plan_id}").json()
        assert plan["status"] == "confirmed"  # render 自动确认 draft
        assert plan["plan"]["render"]["music"] == "chat_bgm.wav"


async def test_chat_context_plan_reference(analyzed_asset):
    """上下文指代：revise 不带 plan_id 时用会话当前方案。"""
    from app.providers import set_providers

    state = {"round": 0}

    class MockLLM:
        async def chat(self, messages, **kwargs):
            system = messages[0]["content"]
            if "调度员" in system:
                state["round"] += 1
                if state["round"] == 1:
                    actions = [{"intent": "create_plan", "params": {"goal": "指代测试"}}]
                else:
                    actions = [{"intent": "revise_plan", "params": {"instruction": "去掉字幕"}}]
                return {"content": json.dumps({"reply": "OK", "actions": actions},
                                              ensure_ascii=False), "tool_calls": None}
            if "提取" in system:
                return {"content": '{"preferences": []}', "tool_calls": None}
            plan = {"title": "指代测试", "clips": [
                {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 2.0,
                 "reason": "r", "subtitle": None}]}
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    set_providers(llm=MockLLM())
    with TestClient(app) as client:
        r1 = client.post("/api/chat", json={"message": "做个方案"}).json()
        acts = await _wait_actions_settled(client, r1["session_id"], 1)
        base_id = acts[-1]["result"]["plan_id"]

        r2 = client.post("/api/chat", json={"message": "去掉字幕",
                                            "session_id": r1["session_id"]}).json()
        acts = await _wait_actions_settled(client, r2["session_id"], 2)
        revise = [a for a in acts if a["intent"] == "revise_plan"][-1]
        assert revise["status"] == "done"
        assert revise["result"]["revised_from"] == base_id  # 指代命中会话当前方案


async def test_chat_unsupported_reply_no_actions():
    """做不了的事：actions 为空，reply 透传手动指引。"""
    from app.providers import set_providers

    class MockLLM:
        async def chat(self, messages, **kwargs):
            return {"content": json.dumps({
                "reply": "画中画目前做不了，请在 Resolve 中手动叠加视频轨：Edit 页把素材拖到 V2。",
                "actions": [],
            }, ensure_ascii=False), "tool_calls": None}

    set_providers(llm=MockLLM())
    with TestClient(app) as client:
        resp = client.post("/api/chat", json={"message": "给我加个画中画"}).json()
        assert resp["actions"] == []
        assert "手动" in resp["reply"] and "V2" in resp["reply"]
        # 会话已保存双方消息
        msgs = client.get(f"/api/chat/{resp['session_id']}").json()["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant"]


async def test_chat_failure_skips_rest(analyzed_asset):
    """链条中某动作失败：后续动作标 skipped。"""
    from app.providers import set_providers

    class MockLLM:
        async def chat(self, messages, **kwargs):
            if "调度员" in messages[0]["content"]:
                return {"content": json.dumps({"reply": "OK", "actions": [
                    {"intent": "set_music", "params": {"path": "/no/such/file.mp3"}},
                    {"intent": "render", "params": {}},
                ]}, ensure_ascii=False), "tool_calls": None}
            return {"content": "{}", "tool_calls": None}

    set_providers(llm=MockLLM())
    with TestClient(app) as client:
        resp = client.post("/api/chat", json={"message": "配乐并渲染"}).json()
        acts = await _wait_actions_settled(client, resp["session_id"], 2)
        assert acts[0]["status"] == "failed" and "不存在" in acts[0]["error"]
        assert acts[1]["status"] == "skipped"
