"""M26 测试：标题卡——生成/plan_to_ir/add_title/守卫/review 防护/对话（backlog B7）。"""

import asyncio
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store.db import db_session
from app.store.models import EditPlan


def test_generate_title_clip_cached(tmp_path):
    from app.tools.media import generate_title_clip

    r1 = generate_title_clip("秋日江南", subtitle="旅行手记", duration=2.0, width=640, height=360)
    assert r1["cached"] is False
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", r1["clip_path"]],
        capture_output=True, text=True, check=True).stdout.strip())
    assert dur == pytest.approx(2.0, abs=0.2)
    # 同参数幂等缓存
    r2 = generate_title_clip("秋日江南", subtitle="旅行手记", duration=2.0, width=640, height=360)
    assert r2["cached"] is True and r2["clip_path"] == r1["clip_path"]


def test_plan_to_ir_title(analyzed_asset):
    from app.runtime.planning import _load_analyzed_assets, plan_to_ir
    from app.ir.schema import timeline_duration, validate_ir

    analyzed = [a for a in _load_analyzed_assets(None) if a["asset"].id == analyzed_asset]
    plan = {"title": "标题测试", "clips": [
        {"kind": "title", "text": "开场", "position": "intro", "duration": 2.5},
        {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 3.0,
         "reason": "r", "subtitle": "正片字幕"},
        {"kind": "title", "text": "完", "position": "outro", "duration": 2.0},
    ]}
    ir = validate_ir(plan_to_ir(plan, analyzed, "标题测试"))
    items = ir.tracks[0].items
    assert len(items) == 3
    # 标题卡作为普通 clip 进入 IR，引用生成的 title 源
    assert items[0].source_id.startswith("title_") and items[2].source_id.startswith("title_")
    assert items[1].source_id == f"src_{analyzed_asset}"
    # 时间线 = 2.5 + 3.0 + 2.0 = 7.5
    assert timeline_duration(ir) == pytest.approx(7.5)
    # 字幕时移：正片字幕从 2.5s（片头之后）开始
    subs = next(t for t in ir.tracks if t.type == "subtitle").items
    assert subs[0].timeline_start == pytest.approx(2.5)


def _mock_llm(analyzed_asset):
    class MockLLM:
        async def chat(self, messages, **kwargs):
            if "调度员" in messages[0]["content"]:
                return {"content": json.dumps({"reply": "OK", "actions": [
                    {"intent": "add_title", "params": {"text": "秋日江南", "subtitle": "旅行手记",
                                                       "position": "intro", "duration": 2.5}},
                ]}, ensure_ascii=False), "tool_calls": None}
            plan = {"title": "标题流", "clips": [
                {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 3.0,
                 "reason": "r", "subtitle": None},
                {"section": "ending", "asset_id": analyzed_asset, "start": 0.0, "end": 2.0,
                 "reason": "r", "subtitle": None,
                 "transition": {"type": "fade", "duration": 0.5}}]}
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    return MockLLM()


async def _make_plan(client) -> int:
    plan_id = client.post("/api/plans", json={"goal": "标题流"}).json()["plan_id"]
    for _ in range(50):
        await asyncio.sleep(0.1)
        if client.get(f"/api/plans/{plan_id}").json()["status"] != "generating":
            break
    return plan_id


async def test_add_title_and_guards(analyzed_asset):
    from app.providers import set_providers
    from app.runtime.clip_ops import add_title_card, apply_clip_ops

    set_providers(llm=_mock_llm(analyzed_asset))
    with TestClient(app) as client:
        base = await _make_plan(client)
        # 加片头
        r = add_title_card(base, text="秋日江南", subtitle="旅行手记", position="intro", duration=2.5)
        clips = client.get(f"/api/plans/{r['plan_id']}").json()["plan"]["clips"]
        assert clips[0]["kind"] == "title" and clips[0]["text"] == "秋日江南"
        assert len(clips) == 3
        # 原首段的转入转场应已挪走（首段不能有转场）——此处首段(素材)本无转场
        # 加片尾
        r2 = add_title_card(r["plan_id"], text="完", position="outro", duration=2.0)
        clips2 = client.get(f"/api/plans/{r2['plan_id']}").json()["plan"]["clips"]
        assert clips2[-1]["kind"] == "title" and clips2[-1]["position"] == "outro"

        # 守卫：对标题卡（位置1）trim/replace/speed 被拒
        for op in ("trim", "replace", "speed"):
            payload = {"op": op, "position": 1}
            if op == "trim":
                payload["duration"] = 1.0
            if op == "speed":
                payload["speed"] = 2.0
            with pytest.raises(ValueError, match="标题卡"):
                apply_clip_ops(r2["plan_id"], [payload])
        # 但 move/remove 可以（标题卡是普通位置）
        moved = apply_clip_ops(r2["plan_id"], [{"op": "move", "position": 1, "to": 2}])
        assert client.get(f"/api/plans/{moved['plan_id']}").json()["plan"]["clips"][1]["kind"] == "title"

        # 空文字拒绝
        with pytest.raises(ValueError, match="不能为空"):
            add_title_card(base, text="   ")


def test_diff_and_review_title_safe(analyzed_asset):
    """diff/review 遇标题卡不崩：diff 报新增标题、check_repeated 跳过标题。"""
    from app.runtime.planning import diff_plans
    from app.runtime.review import check_repeated_clips

    old = {"clips": [{"section": "opening", "asset_id": 1, "start": 0, "end": 2, "subtitle": None}]}
    new = {"clips": [
        {"kind": "title", "text": "片头", "position": "intro", "duration": 2.5},
        {"section": "opening", "asset_id": 1, "start": 0, "end": 2, "subtitle": None}]}
    diff = diff_plans(old, new)
    assert any("标题卡" in a for a in diff["added"])

    # 标题卡无 asset_id，重复检测不应 KeyError
    plan = {"clips": [
        {"kind": "title", "text": "T", "duration": 2.5},
        {"asset_id": 1, "start": 0, "end": 3},
        {"asset_id": 1, "start": 1, "end": 4}]}  # 片段2、3重复
    issue = check_repeated_clips(plan)
    assert issue and "pairs" in issue


async def test_add_title_chat_intent(analyzed_asset):
    from app.providers import set_providers

    set_providers(llm=_mock_llm(analyzed_asset))
    with TestClient(app) as client:
        await _make_plan(client)
        r = client.post("/api/chat", json={"message": "加个片头，标题叫秋日江南，副标题旅行手记"}).json()
        for _ in range(50):
            await asyncio.sleep(0.1)
            msgs = client.get(f"/api/chat/{r['session_id']}").json()["messages"]
            acts = [m for m in msgs if m["role"] == "action"]
            if acts and all(a["status"] != "pending" for a in acts):
                break
        assert acts[-1]["status"] == "done"
        new_plan = client.get(f"/api/plans/{acts[-1]['result']['plan_id']}").json()["plan"]
        assert new_plan["clips"][0]["kind"] == "title"
        assert new_plan["clips"][0]["text"] == "秋日江南"
