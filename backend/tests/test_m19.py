"""M19 测试：任务登记生命周期、启动恢复策略、chat 中断标记、任务 API（phase2-roadmap §9）。"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store.db import db_session
from app.store.models import AgentSession, Asset, BackgroundTask, EditPlan


@pytest.fixture(autouse=True)
def clean_tasks():
    with db_session() as db:
        db.query(BackgroundTask).delete()
        db.commit()
    yield


async def test_spawn_lifecycle():
    from app.runtime.tasks import list_tasks, spawn

    async def ok():
        await asyncio.sleep(0.01)

    async def boom():
        raise RuntimeError("炸了")

    t1 = spawn("analyze", {"asset_id": 1}, ok())
    t2 = spawn("render", {"plan_id": 1}, boom())
    await asyncio.sleep(0.3)
    by_id = {t["id"]: t for t in list_tasks()}
    assert by_id[t1]["status"] == "done"
    assert by_id[t2]["status"] == "failed" and "炸了" in by_id[t2]["detail"]


async def test_recover_interrupted(analyzed_asset, sample_video, tmp_path):
    """伪造中断态：分析中素材 + 生成中方案 + 卡住的对话动作；启动（lifespan）自动恢复。"""
    import shutil

    from app.providers import set_providers

    # 第二个已分析素材（同内容不同路径，分析记录按 hash 命中）：保证方案重生成时有可用素材
    copy = tmp_path / "copy.mp4"
    shutil.copyfile(sample_video, copy)
    with db_session() as db:
        origin = db.get(Asset, analyzed_asset)
        second = Asset(path=str(copy), filename="copy.mp4", content_hash=origin.content_hash,
                       size_bytes=origin.size_bytes, duration=origin.duration,
                       width=origin.width, height=origin.height, fps=origin.fps,
                       has_audio=origin.has_audio, status="analyzed")
        db.add(second)
        db.commit()
        second_id = second.id

    class MockLLM:
        async def chat(self, messages, **kwargs):
            if "剪辑师" in messages[0]["content"]:  # Planning 调用
                plan = {"title": "恢复测试", "clips": [
                    {"section": "opening", "asset_id": second_id, "start": 0.0, "end": 2.0,
                     "reason": "r", "subtitle": None}]}
                return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}
            return {"content": "{}", "tool_calls": None}  # 其他调用（understanding 等）走兜底

    set_providers(llm=MockLLM())

    with db_session() as db:
        # ① 分析中断：素材状态回退 + running 任务行
        db.get(Asset, analyzed_asset).status = "analyzing"
        # ② 方案生成中断
        plan = EditPlan(goal="恢复测试目标", plan={}, status="generating")
        db.add(plan)
        # ③ 对话动作链中断
        db.add(AgentSession(id="recover-test", messages=[
            {"role": "user", "content": "做个方案"},
            {"role": "action", "intent": "create_plan", "params": {}, "status": "pending"},
        ], context={}))
        db.commit()
        plan_id = plan.id
        db.add(BackgroundTask(kind="analyze", payload={"asset_id": analyzed_asset}, status="running"))
        db.add(BackgroundTask(kind="plan_generate",
                              payload={"plan_id": plan_id, "goal": "恢复测试目标", "asset_ids": None},
                              status="running"))
        db.add(BackgroundTask(kind="chat_actions", payload={"session_id": "recover-test"},
                              status="running"))
        db.add(BackgroundTask(kind="analyze", payload={"asset_id": 99999}, status="running"))  # 素材已删
        db.commit()

    with TestClient(app) as client:  # lifespan 启动即触发恢复（真实路径）
        for _ in range(150):
            await asyncio.sleep(0.2)
            with db_session() as db:
                a = db.get(Asset, analyzed_asset)
                p = db.get(EditPlan, plan_id)
                if a.status in ("analyzed", "failed") and p.status in ("draft", "failed"):
                    break
        assert client.get("/api/health").json()["status"] == "ok"

    with db_session() as db:
        assert db.get(Asset, analyzed_asset).status == "analyzed"       # 分析重跑（缓存命中）
        assert db.get(EditPlan, plan_id).status == "draft"              # 方案重新生成
        session = db.get(AgentSession, "recover-test")
        action = [m for m in session.messages if m["role"] == "action"][0]
        assert action["status"] == "interrupted"                         # 对话链只标记不重跑
        assert "重启中断" in action["error"]
        # 原 running 行标记 recovered / 无法恢复的标 interrupted
        statuses = {(t.kind, t.payload.get("asset_id") or t.payload.get("plan_id")
                     or t.payload.get("session_id")): t.status
                    for t in db.query(BackgroundTask).filter(
                        BackgroundTask.status.in_(("recovered", "interrupted"))).all()}
        assert statuses[("analyze", analyzed_asset)] == "recovered"
        assert statuses[("plan_generate", plan_id)] == "recovered"
        assert statuses[("chat_actions", "recover-test")] == "recovered"
        assert statuses[("analyze", 99999)] == "interrupted"


async def test_tasks_api_and_endpoint_registration(analyzed_asset):
    with TestClient(app) as client:
        client.post(f"/api/assets/{analyzed_asset}/analyze")
        await asyncio.sleep(0.3)
        tasks = client.get("/api/tasks").json()["tasks"]
        assert any(t["kind"] == "analyze" and t["payload"]["asset_id"] == analyzed_asset
                   for t in tasks)
