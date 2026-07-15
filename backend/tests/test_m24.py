"""M24 测试：自检自动修复——fix_ops 编译/时间线映射/去重/退回/API/意图（backlog B22）。"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store.db import db_session
from app.store.models import Asset, EditPlan


def test_clip_slots_and_mapping():
    from app.runtime.review import _clip_at, _clip_slots

    clips = [
        {"start": 0, "end": 4},                                          # 槽 0-4
        {"start": 0, "end": 4, "transition": {"type": "fade", "duration": 1.0}},  # 转入1s，槽 4-7
        {"start": 0, "end": 3},                                          # 槽 7-10
    ]
    slots = _clip_slots(clips)
    assert slots == [(1, 0.0, 4.0), (2, 4.0, 7.0), (3, 7.0, 10.0)]
    assert _clip_at(slots, 2.0) == 1
    assert _clip_at(slots, 5.5) == 2
    assert _clip_at(slots, 8.0) == 3
    assert _clip_at(slots, 99.0) == 3  # 超范围落最后一段


def test_attach_fix_ops():
    from app.runtime.review import _attach_fix_ops

    plan = {"clips": [
        {"asset_id": 1, "start": 0, "end": 4},
        {"asset_id": 1, "start": 0, "end": 4},   # 与片段1重复
        {"asset_id": 2, "start": 0, "end": 4},
    ]}
    issues = [
        {"type": "black_frames", "spans": [[0.5, 1.0]]},     # 落在片段1
        {"type": "repeated", "pairs": [[1, 2]]},             # 替换较晚者片段2
        {"type": "duration", "target": 6.0, "actual": 12.0},  # 12→6，ratio 0.5
    ]
    _attach_fix_ops(issues, plan, 12.0)
    assert issues[0]["fix_ops"] == [{"op": "replace", "position": 1}]
    # 片段2 已被黑场？不，黑场是片段1；重复替换片段2
    assert issues[1]["fix_ops"] == [{"op": "replace", "position": 2}]
    # 时长：每段 4s * 0.5 = 2s
    trims = issues[2]["fix_ops"]
    assert all(o["op"] == "trim" and o["duration"] == 2.0 for o in trims)
    assert len(trims) == 3

    # 去重：黑场和重复指向同一片段时只替换一次
    issues2 = [
        {"type": "black_frames", "spans": [[4.5, 5.0]]},     # 落在片段2
        {"type": "repeated", "pairs": [[1, 2]]},             # 也想替换片段2
    ]
    _attach_fix_ops(issues2, plan, 12.0)
    assert issues2[0]["fix_ops"] == [{"op": "replace", "position": 2}]
    assert "fix_ops" not in issues2[1]  # 片段2 已被占用，重复项无操作

    # 时长偏短不自动修复
    issues3 = [{"type": "duration", "target": 20.0, "actual": 12.0}]
    _attach_fix_ops(issues3, plan, 12.0)
    assert "fix_ops" not in issues3[0]


def _mock_llm(analyzed_asset):
    class MockLLM:
        async def chat(self, messages, **kwargs):
            system = messages[0]["content"]
            if "调度员" in system:
                return {"content": json.dumps({"reply": "OK", "actions": [
                    {"intent": "fix_issues", "params": {}},
                ]}, ensure_ascii=False), "tool_calls": None}
            plan = {"title": "修复测试", "target_duration": 4, "clips": [
                {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 3.0,
                 "reason": "r1", "subtitle": None},
                {"section": "build", "asset_id": analyzed_asset, "start": 0.5, "end": 3.5,
                 "reason": "r2", "subtitle": None},   # 与片段1重叠 → 重复
            ]}
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    return MockLLM()


async def _make_plan(client) -> int:
    plan_id = client.post("/api/plans", json={"goal": "修复流"}).json()["plan_id"]
    for _ in range(50):
        await asyncio.sleep(0.1)
        if client.get(f"/api/plans/{plan_id}").json()["status"] != "generating":
            break
    return plan_id


async def test_apply_fixes_no_review_returns_message(analyzed_asset, sample_video, tmp_path):
    """无可用替换素材时，apply_review_fixes 退回/如实上报，不崩。"""
    import shutil

    from app.providers import set_providers
    from app.runtime.review import apply_review_fixes

    set_providers(llm=_mock_llm(analyzed_asset))
    with TestClient(app) as client:
        plan_id = await _make_plan(client)
        # 伪造渲染结果（用真实视频，触发确定性检查中的重复 + 时长）
        with db_session() as db:
            row = db.get(EditPlan, plan_id)
            row.plan = {**row.plan, "render": {"video": str(sample_video),
                                               "video_url": "/output/x.mp4"}}
            db.commit()
        # 无未使用素材 → replace 失败，但时长偏长有 trim → 部分修复成功
        result = await apply_review_fixes(plan_id)
        # 时长 5s（sample_video）vs target 4：偏差 25% → 有 trim 修复
        assert result["fixed"] is True
        assert any("修剪" in a or "区间" in a for a in result["applied"])
        assert result["new_plan_id"] != plan_id


async def test_fix_replace_with_unused_asset(analyzed_asset, sample_video, tmp_path):
    import shutil

    from app.providers import set_providers
    from app.runtime.review import apply_review_fixes

    # 第二素材（同 hash）提供未用精彩片段
    copy = tmp_path / "fix-source.mp4"
    shutil.copyfile(sample_video, copy)
    with db_session() as db:
        origin = db.get(Asset, analyzed_asset)
        second = Asset(path=str(copy), filename="fix-source.mp4",
                       content_hash=origin.content_hash, size_bytes=origin.size_bytes,
                       duration=origin.duration, width=origin.width, height=origin.height,
                       fps=origin.fps, has_audio=origin.has_audio, status="analyzed")
        db.add(second)
        db.commit()
        second_id = second.id

    set_providers(llm=_mock_llm(analyzed_asset))
    with TestClient(app) as client:
        plan_id = await _make_plan(client)
        with db_session() as db:
            row = db.get(EditPlan, plan_id)
            row.plan = {**row.plan, "render": {"video": str(sample_video),
                                               "video_url": "/output/x.mp4"}}
            db.commit()
        result = await apply_review_fixes(plan_id)
        assert result["fixed"] is True
        new_clips = client.get(f"/api/plans/{result['new_plan_id']}").json()["plan"]["clips"]
        # 重复的片段2 被替换为某个未用素材（全量跑时其他测试也留同 hash 副本，不锁定具体 id）
        assert new_clips[1]["asset_id"] != analyzed_asset

    with db_session() as db:
        db.delete(db.get(Asset, second_id))
        db.commit()


async def test_apply_fixes_api_and_chat(analyzed_asset, sample_video):
    from app.providers import set_providers

    set_providers(llm=_mock_llm(analyzed_asset))
    with TestClient(app) as client:
        plan_id = await _make_plan(client)
        # 未渲染 → apply-fixes 400（review 先跑但无成片）
        assert client.post(f"/api/plans/{plan_id}/apply-fixes").status_code == 400

        with db_session() as db:
            row = db.get(EditPlan, plan_id)
            row.plan = {**row.plan, "render": {"video": str(sample_video),
                                               "video_url": "/output/x.mp4"}}
            db.commit()

        # API：一键修复（自动先自检）
        resp = client.post(f"/api/plans/{plan_id}/apply-fixes").json()
        assert resp["fixed"] is True and resp["new_plan_id"] != plan_id

        # 对话意图
        with db_session() as db:  # 重置为可修复态
            row = db.get(EditPlan, plan_id)
            row.plan = {k: v for k, v in row.plan.items() if k != "review"}
            db.commit()
        r = client.post("/api/chat", json={"message": "帮我按建议修一下"}).json()
        for _ in range(80):
            await asyncio.sleep(0.1)
            msgs = client.get(f"/api/chat/{r['session_id']}").json()["messages"]
            acts = [m for m in msgs if m["role"] == "action"]
            if acts and all(a["status"] != "pending" for a in acts):
                break
        assert acts[-1]["status"] == "done"
        assert acts[-1]["result"].get("applied")
