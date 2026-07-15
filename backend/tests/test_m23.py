"""M23 测试：成片自检——确定性检查各项、视觉受限格式、API/意图（backlog B1）。"""

import asyncio
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store.db import db_session
from app.store.models import EditPlan


@pytest.fixture(scope="module")
def flawed_video(tmp_path_factory):
    """带黑场 + 静音的测试成片：前 1s 黑场，全程无声。"""
    path = tmp_path_factory.mktemp("review") / "flawed.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "quiet",
         "-f", "lavfi", "-i", "color=black:duration=1:size=320x180:rate=25",
         "-f", "lavfi", "-i", "testsrc=duration=3:size=320x180:rate=25",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=4",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
         "-map", "[v]", "-map", "2:a", "-t", "4",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path)],
        check=True,
    )
    return path


def test_deterministic_checks(flawed_video):
    from app.runtime.review import (check_audio_levels, check_black_frames,
                                    check_duration, check_repeated_clips)

    issue = check_black_frames(str(flawed_video))
    assert issue and issue["severity"] == "high" and "黑场" in issue["detail"]

    issue = check_audio_levels(str(flawed_video))
    assert issue and "静音" in issue["detail"]

    # 时长偏差：目标 20s 实际 4s
    issue = check_duration({"target_duration": 20}, 4.0)
    assert issue and "偏差" in issue["detail"]
    assert check_duration({"target_duration": 4}, 4.2) is None  # 容差内

    # 重复素材
    plan = {"clips": [
        {"asset_id": 1, "start": 0.0, "end": 3.0},
        {"asset_id": 1, "start": 1.0, "end": 4.0},   # 与片段1重叠
        {"asset_id": 2, "start": 0.0, "end": 2.0},
    ]}
    issue = check_repeated_clips(plan)
    assert issue and "片段1与片段2" in issue["detail"]
    assert check_repeated_clips({"clips": [{"asset_id": 1, "start": 0, "end": 2},
                                           {"asset_id": 1, "start": 3, "end": 5}]}) is None


async def test_review_render_and_chat(analyzed_asset, flawed_video):
    from app.providers import set_providers

    class MockProviders:
        async def chat(self, messages, **kwargs):
            if "调度员" in messages[0]["content"]:
                return {"content": json.dumps({"reply": "OK", "actions": [
                    {"intent": "review_video", "params": {}},
                ]}, ensure_ascii=False), "tool_calls": None}
            plan = {"title": "自检测试", "target_duration": 20, "clips": [
                {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 2.0,
                 "reason": "r", "subtitle": None}]}
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    class MockVision:
        async def analyze_images(self, image_paths, prompt, *, json_mode=False):
            assert len(image_paths) >= 3  # 多帧一次调用
            return json.dumps({"issues": [
                {"type": "quality", "severity": "medium",
                 "detail": "第1帧近乎全黑", "suggestion": "更换开头片段"},
            ], "summary": "开头存在黑场，整体画面正常"}, ensure_ascii=False)

    set_providers(llm=MockProviders(), vision=MockVision())
    with TestClient(app) as client:
        plan_id = client.post("/api/plans", json={"goal": "自检流"}).json()["plan_id"]
        for _ in range(50):
            await asyncio.sleep(0.1)
            if client.get(f"/api/plans/{plan_id}").json()["status"] != "generating":
                break
        # 无成片时明确报错
        assert client.post(f"/api/plans/{plan_id}/review").status_code == 400

        # 伪造渲染结果指向瑕疵成片
        with db_session() as db:
            row = db.get(EditPlan, plan_id)
            row.plan = {**row.plan, "render": {"video": str(flawed_video),
                                               "video_url": "/output/x.mp4"}}
            db.commit()

        review = client.post(f"/api/plans/{plan_id}/review").json()["review"]
        assert review["verdict"] == "has_problems"        # 黑场为 high
        types = {i["type"] for i in review["issues"]}
        assert "black_frames" in types and "audio" in types and "duration" in types
        assert "vision_quality" in types                   # 视觉自检并入
        assert any(i.get("suggestion") for i in review["issues"])
        # 报告落库
        assert client.get(f"/api/plans/{plan_id}").json()["plan"]["review"]["verdict"] == "has_problems"

        # 对话意图
        r = client.post("/api/chat", json={"message": "检查一下成片有没有问题"}).json()
        for _ in range(80):
            await asyncio.sleep(0.1)
            msgs = client.get(f"/api/chat/{r['session_id']}").json()["messages"]
            acts = [m for m in msgs if m["role"] == "action"]
            if acts and all(a["status"] != "pending" for a in acts):
                break
        assert acts[-1]["status"] == "done"
        assert acts[-1]["result"]["verdict"] == "has_problems"
        assert "黑场" in acts[-1]["result"]["issues"]
