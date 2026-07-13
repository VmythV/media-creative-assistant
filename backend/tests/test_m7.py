"""M7 测试：方案差异计算、自然语言修订 API、executed 重执行（设计文档 §10）。"""

import asyncio
import json

from fastapi.testclient import TestClient

from app.main import app


def _clip(asset_id, start, end, section="build", subtitle=None):
    return {"asset_id": asset_id, "start": start, "end": end,
            "section": section, "reason": "r", "subtitle": subtitle}


def test_diff_plans():
    from app.runtime.planning import diff_plans

    old = {"clips": [
        _clip(1, 0.0, 4.0, "opening", "第一句"),
        _clip(2, 0.0, 4.0, "build"),
        _clip(3, 0.0, 4.0, "ending"),
    ]}
    new = {"clips": [
        _clip(1, 0.0, 2.5, "opening", "改后的字幕"),  # 缩短 + 改字幕
        _clip(3, 0.0, 4.0, "ending"),                  # 位置 3 → 2
        _clip(4, 1.0, 3.0, "climax"),                  # 新增；素材2被删
    ]}
    diff = diff_plans(old, new)
    assert len(diff["added"]) == 1 and "素材#4" in diff["added"][0]
    assert len(diff["removed"]) == 1 and "素材#2" in diff["removed"][0]
    assert any("字幕" in c and "区间" in c for c in diff["changed"])
    assert any("位置 3 → 2" in c for c in diff["changed"])
    assert diff["duration"] == "12.0s → 8.5s"


def test_diff_plans_no_change():
    from app.runtime.planning import diff_plans

    plan = {"clips": [_clip(1, 0.0, 4.0), _clip(2, 0.0, 4.0)]}
    diff = diff_plans(plan, json.loads(json.dumps(plan)))
    assert diff["added"] == [] and diff["removed"] == [] and diff["changed"] == []
    assert diff["unchanged"] == 2


async def test_revise_api_flow(sample_video, analyzed_asset):
    """生成 → 修订（mock LLM 按指令缩短片段）→ 新方案 draft 带 diff；旧方案保留。"""
    from app.providers import set_providers

    aid = analyzed_asset

    class MockLLM:
        def __init__(self):
            self.revise_called = False

        async def chat(self, messages, **kwargs):
            is_revise = "修订指令" in messages[-1]["content"]
            end = 2.0 if is_revise else 4.0
            self.revise_called = self.revise_called or is_revise
            plan = {"title": "修订测试", "clips": [
                {"section": "opening", "asset_id": aid, "start": 0.0, "end": end,
                 "reason": "开场", "subtitle": "你好"},
            ]}
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    llm = MockLLM()
    set_providers(llm=llm)
    with TestClient(app) as client:
        base_id = client.post("/api/plans", json={"goal": "修订流测试"}).json()["plan_id"]
        for _ in range(50):
            await asyncio.sleep(0.1)
            if client.get(f"/api/plans/{base_id}").json()["status"] != "generating":
                break
        assert client.get(f"/api/plans/{base_id}").json()["status"] == "draft"

        resp = client.post(f"/api/plans/{base_id}/revise", json={"instruction": "缩短到2秒"})
        assert resp.status_code == 200
        new_id = resp.json()["plan_id"]
        assert new_id != base_id and resp.json()["revised_from"] == base_id
        for _ in range(50):
            await asyncio.sleep(0.1)
            new_plan = client.get(f"/api/plans/{new_id}").json()
            if new_plan["status"] != "generating":
                break
        assert new_plan["status"] == "draft", new_plan
        assert llm.revise_called
        assert new_plan["plan"]["revised_from"] == base_id
        assert new_plan["plan"]["revision_instruction"] == "缩短到2秒"
        diff = new_plan["plan"]["diff"]
        assert any("区间" in c for c in diff["changed"])
        assert diff["duration"] == "4.0s → 2.0s"
        # 旧方案原样保留（回滚基础）
        assert client.get(f"/api/plans/{base_id}").json()["status"] == "draft"

        # 修订版 IR 可直接确认执行（降级路径）
        assert client.post(f"/api/plans/{new_id}/confirm").json()["status"] == "confirmed"
        assert client.post(f"/api/plans/{new_id}/execute",
                           json={"force_fallback": True}).json()["status"] == "executing"
        for _ in range(50):
            await asyncio.sleep(0.1)
            if client.get(f"/api/plans/{new_id}").json()["status"] == "executed":
                break
        assert client.get(f"/api/plans/{new_id}").json()["status"] == "executed"

        # executed 状态允许重执行（回滚场景）
        resp = client.post(f"/api/plans/{new_id}/execute", json={"force_fallback": True})
        assert resp.status_code == 200
        for _ in range(50):
            await asyncio.sleep(0.1)
            if client.get(f"/api/plans/{new_id}").json()["status"] == "executed":
                break


def test_revise_requires_content():
    with TestClient(app) as client:
        assert client.post("/api/plans/99999/revise", json={"instruction": "x"}).status_code == 404
