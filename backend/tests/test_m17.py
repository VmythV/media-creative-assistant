"""M17 测试：素材删除/重析/缩略图、简报检索化确定性筛选（phase2-roadmap §6）。"""

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app


def _fake_analyzed(n: int, food_idx: int) -> list[dict]:
    items = []
    for i in range(n):
        is_food = i == food_idx
        items.append({
            "asset": SimpleNamespace(id=i + 1, filename=f"a{i}.mp4", duration=10.0),
            "summary": {
                "category": "美食" if is_food else "风景",
                "highlights": [{"start": 0, "end": 3, "score": 9.0 if is_food else 5.0,
                                "reason": "小笼包特写诱人" if is_food else "山景开阔"}],
            },
            "transcript": {"text": "今天来吃美食" if is_food else ""},
            "shots": [],
        })
    return items


def test_select_for_goal_filters_and_passthrough():
    from app.runtime.planning import _select_for_goal

    # 阈值内：原样返回
    small = _fake_analyzed(5, 0)
    selected, filtered = _select_for_goal(small, "美食视频", limit=20)
    assert not filtered and len(selected) == 5

    # 超阈值：目标含"美食"关键词 → 美食素材必入选
    big = _fake_analyzed(30, 25)
    selected, filtered = _select_for_goal(big, "做一个美食探店短片", limit=10)
    assert filtered and len(selected) == 10
    assert any(it["asset"].id == 26 for it in selected)  # food_idx=25 → id 26
    # 确定性：重复调用结果一致
    again, _ = _select_for_goal(big, "做一个美食探店短片", limit=10)
    assert [it["asset"].id for it in selected] == [it["asset"].id for it in again]


async def test_asset_manage_apis(analyzed_asset, sample_video):
    with TestClient(app) as client:
        # 列表附加分类与片段数
        assets = client.get("/api/assets").json()["assets"]
        target = next(a for a in assets if a["id"] == analyzed_asset)
        assert target["category"] == "风景" and target["highlight_count"] >= 1

        # 缩略图（按需生成路径）
        resp = client.get(f"/api/assets/{analyzed_asset}/thumbnail")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg" and len(resp.content) > 1000

        # 重新分析：清缓存 + 状态回退（无 API key 时管线也能跑完非视觉步骤）
        resp = client.post(f"/api/assets/{analyzed_asset}/reanalyze")
        assert resp.json()["status"] == "started"
        for _ in range(80):
            await asyncio.sleep(0.1)
            status = client.get(f"/api/assets/{analyzed_asset}").json()["status"]
            if status in ("analyzed", "failed"):
                break
        assert status == "analyzed"

        # 删除：登记消失
        new_id = client.post("/api/assets/import",
                             json={"paths": [str(sample_video)]}).json()["imported"][0]["id"]
        assert client.delete(f"/api/assets/{new_id}").json()["deleted"] == new_id
        assert client.get(f"/api/assets/{new_id}").status_code == 404
        assert client.delete(f"/api/assets/{new_id}").status_code == 404
