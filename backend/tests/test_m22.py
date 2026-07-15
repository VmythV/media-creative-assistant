"""M22 测试：片段级修订——六种局部操作/守卫/replace 选取/API/对话意图（backlog B2）。"""

import asyncio
import json
import shutil

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store.db import db_session
from app.store.models import Asset, EditPlan


def _mock_llm(analyzed_asset):
    class MockLLM:
        async def chat(self, messages, **kwargs):
            system = messages[0]["content"]
            if "调度员" in system:
                return {"content": json.dumps({"reply": "OK", "actions": [
                    {"intent": "edit_clips", "params": {"ops": [
                        {"op": "trim", "position": 1, "duration": 1.0},
                        {"op": "remove", "position": 3},
                    ]}},
                ]}, ensure_ascii=False), "tool_calls": None}
            plan = {"title": "局部修订测试", "clips": [
                {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 2.0,
                 "reason": "r1", "subtitle": "第一段"},
                {"section": "build", "asset_id": analyzed_asset, "start": 2.0, "end": 4.0,
                 "reason": "r2", "subtitle": "第二段",
                 "transition": {"type": "fade", "duration": 0.5}},
                {"section": "ending", "asset_id": analyzed_asset, "start": 4.0, "end": 5.0,
                 "reason": "r3", "subtitle": None},
            ]}
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    return MockLLM()


async def _make_plan(client) -> int:
    plan_id = client.post("/api/plans", json={"goal": "局部修订"}).json()["plan_id"]
    for _ in range(50):
        await asyncio.sleep(0.1)
        if client.get(f"/api/plans/{plan_id}").json()["status"] != "generating":
            break
    return plan_id


async def test_clip_ops_core(analyzed_asset):
    from app.providers import set_providers
    from app.runtime.clip_ops import apply_clip_ops

    set_providers(llm=_mock_llm(analyzed_asset))
    with TestClient(app) as client:
        base_id = await _make_plan(client)

        result = apply_clip_ops(base_id, [
            {"op": "trim", "position": 1, "duration": 1.0},          # 2s → 1s
            {"op": "subtitle", "position": 2, "text": "改过的字幕"},
            {"op": "transition", "position": 2, "type": "wipeleft", "t_duration": 0.4},
            {"op": "move", "position": 3, "to": 2},                   # 尾段前移
        ])
        assert result["revised_from"] == base_id and len(result["changes"]) == 4

        new = client.get(f"/api/plans/{result['plan_id']}").json()
        clips = new["plan"]["clips"]
        assert new["status"] == "draft"
        assert clips[0]["end"] == 1.0                                 # trim 生效
        assert clips[1]["reason"] == "r3"                             # move 生效（原第3段）
        assert clips[2]["subtitle"] == "改过的字幕"
        assert clips[2]["transition"]["type"] == "wipeleft"
        assert new["plan"]["revision_instruction"].startswith("[精确修改]")
        assert new["plan"]["diff"]
        assert new["ir"]["version"]                                   # IR 已重建且校验通过
        # 原方案未被改动（回滚保留）
        assert client.get(f"/api/plans/{base_id}").json()["plan"]["clips"][0]["end"] == 2.0


async def test_clip_ops_guards(analyzed_asset):
    from app.providers import set_providers
    from app.runtime.clip_ops import apply_clip_ops

    set_providers(llm=_mock_llm(analyzed_asset))
    with TestClient(app) as client:
        base_id = await _make_plan(client)
        with pytest.raises(ValueError, match="越界"):
            apply_clip_ops(base_id, [{"op": "remove", "position": 9}])
        with pytest.raises(ValueError, match="未知操作"):
            apply_clip_ops(base_id, [{"op": "explode", "position": 1}])
        with pytest.raises(ValueError, match="首个片段"):
            apply_clip_ops(base_id, [{"op": "transition", "position": 1, "type": "fade"}])
        with pytest.raises(ValueError, match="过短"):
            apply_clip_ops(base_id, [{"op": "trim", "position": 1, "duration": 0.1}])
        # 连续删除到只剩 1 个后再删被拒
        with pytest.raises(ValueError, match="不能再删除"):
            apply_clip_ops(base_id, [{"op": "remove", "position": 1},
                                     {"op": "remove", "position": 1},
                                     {"op": "remove", "position": 1}])


async def test_replace_from_unused_highlight(analyzed_asset, sample_video, tmp_path):
    from app.providers import set_providers
    from app.runtime.clip_ops import apply_clip_ops

    # 同内容不同路径的第二素材：分析记录按 hash 共享 → 其精彩片段是"未用"候选
    copy = tmp_path / "replace-source.mp4"
    shutil.copyfile(sample_video, copy)
    with db_session() as db:
        origin = db.get(Asset, analyzed_asset)
        second = Asset(path=str(copy), filename="replace-source.mp4",
                       content_hash=origin.content_hash, size_bytes=origin.size_bytes,
                       duration=origin.duration, width=origin.width, height=origin.height,
                       fps=origin.fps, has_audio=origin.has_audio, status="analyzed")
        db.add(second)
        db.commit()
        second_id = second.id

    set_providers(llm=_mock_llm(analyzed_asset))
    with TestClient(app) as client:
        base_id = await _make_plan(client)
        result = apply_clip_ops(base_id, [{"op": "replace", "position": 2}])
        clips = client.get(f"/api/plans/{result['plan_id']}").json()["plan"]["clips"]
        # 换成了未用素材（全量跑时其他测试也会留下同 hash 副本素材，不锁定具体 id）
        assert clips[1]["asset_id"] != analyzed_asset
        assert clips[1]["end"] - clips[1]["start"] == pytest.approx(2.0, abs=0.1)  # 保持原时长

        # hint 无匹配 → 明确报错
        with pytest.raises(ValueError, match="无匹配"):
            apply_clip_ops(base_id, [{"op": "replace", "position": 1, "hint": "外星飞船"}])

    with db_session() as db:  # 清理，避免污染其他测试的素材数
        db.delete(db.get(Asset, second_id))
        db.commit()


async def test_edit_clips_api_and_chat(analyzed_asset):
    from app.providers import set_providers

    set_providers(llm=_mock_llm(analyzed_asset))
    with TestClient(app) as client:
        base_id = await _make_plan(client)

        # API：非法 op 400
        resp = client.post(f"/api/plans/{base_id}/edit-clips",
                           json={"ops": [{"op": "explode", "position": 1}]})
        assert resp.status_code == 400
        # API：正常
        resp = client.post(f"/api/plans/{base_id}/edit-clips",
                           json={"ops": [{"op": "remove", "position": 2}]}).json()
        assert len(client.get(f"/api/plans/{resp['plan_id']}").json()["plan"]["clips"]) == 2

        # 对话意图（mock 路由产出 trim+remove）；新建 3 片段基底（对话默认操作最新方案）
        await _make_plan(client)
        r = client.post("/api/chat", json={"message": "开头缩短到1秒，第三段删掉"}).json()
        for _ in range(50):
            await asyncio.sleep(0.1)
            msgs = client.get(f"/api/chat/{r['session_id']}").json()["messages"]
            acts = [m for m in msgs if m["role"] == "action"]
            if acts and all(a["status"] != "pending" for a in acts):
                break
        assert acts[-1]["status"] == "done"
        new_clips = client.get(f"/api/plans/{acts[-1]['result']['plan_id']}").json()["plan"]["clips"]
        assert len(new_clips) == 2 and new_clips[0]["end"] == 1.0
