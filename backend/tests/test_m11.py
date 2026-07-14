"""M11 测试：MemoryProvider、偏好提取、生成注入、修订触发、API（设计文档 §14）。"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.memory import SqliteMemoryProvider, get_memory_provider


@pytest.fixture(autouse=True)
def clean_memory():
    """每个测试前清空 user 记忆，避免相互污染。"""
    provider = get_memory_provider()
    for m in provider.list():
        provider.delete(m["id"])
    yield


def test_provider_crud_and_dedupe():
    p = SqliteMemoryProvider()
    item = p.add("user", "字幕偏文艺", source="revision")
    assert item and item["id"]
    # 归一化去重：空白/句尾标点差异视为相同
    assert p.add("user", "字幕 偏文艺。") is None
    assert p.add("user", "节奏偏快") is not None
    assert p.texts("user") == ["字幕偏文艺", "节奏偏快"]  # 按写入顺序
    with pytest.raises(ValueError, match="未知记忆类型"):
        p.add("alien", "x")
    assert p.delete(item["id"]) is True
    assert p.delete(item["id"]) is False
    assert p.texts("user") == ["节奏偏快"]


async def test_extract_preferences_restricted_format():
    from app.providers import set_providers
    from app.runtime.planning import extract_preferences

    class MockLLM:
        async def chat(self, messages, **kwargs):
            instruction = messages[-1]["content"]
            if "文艺" in instruction:
                return {"content": json.dumps({"preferences": ["字幕用词偏文艺", "字幕用词偏文艺"]},
                                              ensure_ascii=False), "tool_calls": None}
            return {"content": '{"preferences": []}', "tool_calls": None}

    set_providers(llm=MockLLM())
    # 持久偏好被提取且去重（同批重复只入一条）
    added = await extract_preferences("以后字幕都要更文艺一点")
    assert added == ["字幕用词偏文艺"]
    assert get_memory_provider().texts("user") == ["字幕用词偏文艺"]
    # 一次性指令返回空
    assert await extract_preferences("把第2段删掉") == []
    # 再次提取相同偏好不重复入库
    assert await extract_preferences("字幕再文艺些") == []
    assert len(get_memory_provider().texts("user")) == 1


async def test_preferences_injected_into_plan_prompt(analyzed_asset):
    from app.providers import set_providers
    from app.runtime.planning import generate_plan

    get_memory_provider().add("user", "节奏偏快，单片段不超过3秒")
    captured = {}

    class MockLLM:
        async def chat(self, messages, **kwargs):
            captured["system"] = messages[0]["content"]
            plan = {"title": "注入测试", "clips": [
                {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 2.0,
                 "reason": "r", "subtitle": None}]}
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    set_providers(llm=MockLLM())
    await generate_plan("测试目标")
    assert "用户长期创作偏好" in captured["system"]
    assert "节奏偏快，单片段不超过3秒" in captured["system"]


async def test_revise_triggers_extraction(analyzed_asset):
    from app.providers import set_providers

    class MockLLM:
        async def chat(self, messages, **kwargs):
            system = messages[0]["content"]
            if "长期创作偏好" in system and "修订" not in system and "剪辑师" not in system:
                pass
            if "提取" in system:  # 偏好提取调用
                return {"content": json.dumps({"preferences": ["转场要克制"]}, ensure_ascii=False),
                        "tool_calls": None}
            plan = {"title": "修订触发测试", "clips": [
                {"section": "opening", "asset_id": analyzed_asset, "start": 0.0, "end": 2.0,
                 "reason": "r", "subtitle": None}]}
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    set_providers(llm=MockLLM())
    with TestClient(app) as client:
        plan_id = client.post("/api/plans", json={"goal": "记忆流测试"}).json()["plan_id"]
        for _ in range(50):
            await asyncio.sleep(0.1)
            if client.get(f"/api/plans/{plan_id}").json()["status"] != "generating":
                break
        new_id = client.post(f"/api/plans/{plan_id}/revise",
                             json={"instruction": "转场别太花哨"}).json()["plan_id"]
        for _ in range(50):
            await asyncio.sleep(0.1)
            if client.get(f"/api/plans/{new_id}").json()["status"] != "generating":
                break
        assert client.get(f"/api/plans/{new_id}").json()["status"] == "draft"
        for _ in range(30):  # 提取在修订完成后异步进行
            await asyncio.sleep(0.1)
            if get_memory_provider().texts("user"):
                break
        assert get_memory_provider().texts("user") == ["转场要克制"]


def test_memory_api_crud():
    with TestClient(app) as client:
        item = client.post("/api/memory", json={"content": "不喜欢电子乐"}).json()
        assert item["id"] and item["source"] == "manual"
        # 重复 → 409
        assert client.post("/api/memory", json={"content": "不喜欢电子乐"}).status_code == 409
        memories = client.get("/api/memory").json()["memories"]
        assert any(m["content"] == "不喜欢电子乐" for m in memories)
        assert client.delete(f"/api/memory/{item['id']}").json()["deleted"] == item["id"]
        assert client.delete(f"/api/memory/{item['id']}").status_code == 404
