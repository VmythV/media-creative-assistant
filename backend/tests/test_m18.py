"""M18 测试：风格画像提取（快切 vs 舒缓）、入库覆盖、生成注入、对话流（phase2-roadmap §7）。"""

import asyncio
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.memory import get_memory_provider


def _make_video(path, colors, seg_dur):
    """多色段拼接视频：段间颜色跳变可被镜头检测捕捉。"""
    inputs, filters = [], []
    for i, c in enumerate(colors):
        inputs += ["-f", "lavfi", "-i", f"color={c}:duration={seg_dur}:size=320x180:rate=25"]
        filters.append(f"[{i}:v]")
    fc = "".join(filters) + f"concat=n={len(colors)}:v=1:a=0[v]"
    subprocess.run(["ffmpeg", "-y", "-v", "quiet", *inputs, "-filter_complex", fc,
                    "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", str(path)],
                   check=True)


@pytest.fixture(scope="module")
def reference_videos(tmp_path_factory):
    d = tmp_path_factory.mktemp("refs")
    fast = d / "fast_cut.mp4"
    slow = d / "slow_pace.mp4"
    _make_video(fast, ["red", "blue", "green", "yellow", "purple", "orange", "white", "gray"], 1.0)
    _make_video(slow, ["red", "blue"], 5.0)
    return fast, slow


@pytest.fixture(autouse=True)
def clean_styles():
    provider = get_memory_provider()
    for m in provider.list("business"):
        provider.delete(m["id"])
    yield


def test_learn_style_fast_vs_slow(reference_videos):
    from app.runtime.style import find_style, learn_style, list_styles

    fast, slow = reference_videos
    pf = learn_style(str(fast), "快闪风")
    ps = learn_style(str(slow), "慢节奏")
    assert pf["pace"] == "快" and pf["shots"] >= 6
    assert ps["pace"] == "舒缓" and ps["shots"] == 2
    assert pf["cuts_per_min"] > ps["cuts_per_min"] * 3

    styles = list_styles()
    assert len(styles) == 2
    assert "快闪风" in (find_style("快闪风") or "")
    # 同名重学覆盖，不累积
    learn_style(str(fast), "快闪风")
    assert len(list_styles()) == 2


async def test_style_injected_and_chat_flow(analyzed_asset, reference_videos):
    from app.providers import set_providers

    fast, _ = reference_videos
    captured = {}
    state = {"round": 0}

    class MockLLM:
        async def chat(self, messages, **kwargs):
            system = messages[0]["content"]
            if "调度员" in system:
                state["round"] += 1
                if state["round"] == 1:
                    actions = [{"intent": "learn_style",
                                "params": {"path": str(fast), "name": "测试风"}}]
                else:
                    actions = [{"intent": "create_plan", "params": {"goal": "风格注入测试"}}]
                return {"content": json.dumps({"reply": "OK", "actions": actions},
                                              ensure_ascii=False), "tool_calls": None}
            captured["system"] = system
            plan = {"title": "风格注入", "clips": [
                {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 2.0,
                 "reason": "r", "subtitle": None}]}
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    set_providers(llm=MockLLM())
    with TestClient(app) as client:
        r1 = client.post("/api/chat", json={"message": "学习这个视频的风格"}).json()
        for _ in range(100):
            await asyncio.sleep(0.2)
            msgs = client.get(f"/api/chat/{r1['session_id']}").json()["messages"]
            acts = [m for m in msgs if m["role"] == "action"]
            if acts and all(a["status"] != "pending" for a in acts):
                break
        learn = acts[-1]
        assert learn["status"] == "done" and learn["result"]["applied"] is True
        assert learn["result"]["pace"] == "快"

        # 同会话生成方案：画像注入 system prompt
        r2 = client.post("/api/chat", json={"message": "照这个感觉做个方案",
                                            "session_id": r1["session_id"]}).json()
        for _ in range(100):
            await asyncio.sleep(0.2)
            msgs = client.get(f"/api/chat/{r2['session_id']}").json()["messages"]
            acts = [m for m in msgs if m["role"] == "action"]
            if len(acts) >= 2 and all(a["status"] != "pending" for a in acts):
                break
        assert acts[-1]["status"] == "done"
        assert "参考风格画像" in captured["system"]
        assert "测试风" in captured["system"] and "节奏快" in captured["system"]


async def test_apply_unknown_style_fails():
    from app.runtime.chat import _act_apply_style

    session = {"context": {}}
    with pytest.raises(ValueError, match="没有名为"):
        await _act_apply_style(session, {"name": "不存在的风格"})
