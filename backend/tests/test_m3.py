"""M3 测试：导出器、方案 API 全流程（生成→确认→降级执行）、执行日志。"""

import asyncio
import json
from xml.etree import ElementTree as ET

from fastapi.testclient import TestClient

from app.config import settings
from app.ir.schema import validate_ir
from app.main import app


def _valid_ir(source_path: str) -> dict:
    return {
        "version": "0.1",
        "project": {"name": "export-test", "fps": 25, "resolution": {"width": 1920, "height": 1080}},
        "sources": [{"id": "src_1", "path": source_path, "duration": 5.0}],
        "tracks": [
            {"type": "video", "index": 1, "items": [
                {"type": "clip", "source_id": "src_1", "trim": {"start": 0.0, "end": 2.0},
                 "role": "opening", "reason": "测试开场"},
                {"type": "clip", "source_id": "src_1", "trim": {"start": 3.0, "end": 5.0},
                 "role": "ending", "reason": "测试结尾"},
            ]},
            {"type": "subtitle", "index": 1, "items": [
                {"type": "subtitle", "content": "东京之夜", "timeline_start": 0.0, "timeline_end": 2.0},
            ]},
        ],
        "render": None,
    }


def test_export_srt(sample_video):
    from app.ir.exporters import export_srt

    srt = export_srt(validate_ir(_valid_ir(str(sample_video))))
    assert "00:00:00,000 --> 00:00:02,000" in srt
    assert "东京之夜" in srt


def test_export_edit_list(sample_video):
    from app.ir.exporters import export_edit_list

    md = export_edit_list(validate_ir(_valid_ir(str(sample_video))))
    assert "开场" in md and "结尾" in md
    assert "时间线时长：4.0 秒" in md
    assert "测试开场" in md


def test_export_fcpxml(sample_video):
    from app.ir.exporters import export_fcpxml

    xml = export_fcpxml(validate_ir(_valid_ir(str(sample_video))))
    root = ET.fromstring(xml.split("<!DOCTYPE fcpxml>\n")[1])
    clips = root.findall(".//asset-clip")
    assert len(clips) == 2
    assert clips[0].get("offset") == "0/25s"
    assert clips[1].get("offset") == "50/25s"  # 第二个片段从 2s（50帧）开始
    assert clips[1].get("start") == "75/25s"


async def test_plan_api_full_flow_with_fallback_execute(sample_video, analyzed_asset):
    """POST /plans（mock LLM）→ draft → confirm → execute（强制降级）→ 产物落盘。"""
    from app.providers import set_providers

    asset_id = analyzed_asset

    class MockLLM:
        async def chat(self, messages, **kwargs):
            plan = {
                "title": "api-flow-测试",
                "clips": [
                    {"section": "opening", "asset_id": asset_id, "start": 0.0, "end": 2.0,
                     "reason": "开场", "subtitle": "你好东京"},
                ],
            }
            return {"content": json.dumps(plan, ensure_ascii=False), "tool_calls": None}

    set_providers(llm=MockLLM())
    with TestClient(app) as client:
        # 精彩片段推荐
        resp = client.get("/api/highlights")
        assert resp.status_code == 200
        highlights = resp.json()["highlights"]
        assert highlights and highlights[0]["reason"]

        # 生成方案（后台任务），轮询到 draft
        resp = client.post("/api/plans", json={"goal": "10秒测试短片"})
        plan_id = resp.json()["plan_id"]
        for _ in range(50):
            await asyncio.sleep(0.1)
            status = client.get(f"/api/plans/{plan_id}").json()["status"]
            if status != "generating":
                break
        assert status == "draft", client.get(f"/api/plans/{plan_id}").json()
        plan = client.get(f"/api/plans/{plan_id}").json()
        assert plan["ir"]["version"] == "0.1"

        # 确认 + 执行（强制降级路径）
        assert client.post(f"/api/plans/{plan_id}/confirm").json()["status"] == "confirmed"
        resp = client.post(f"/api/plans/{plan_id}/execute", json={"force_fallback": True})
        assert resp.json()["status"] == "executing"
        for _ in range(50):
            await asyncio.sleep(0.1)
            status = client.get(f"/api/plans/{plan_id}").json()["status"]
            if status == "executed":
                break
        final = client.get(f"/api/plans/{plan_id}").json()
        assert final["status"] == "executed"
        execution = final["plan"]["execution"]
        assert execution["mode"] == "fallback"

        out_dir = settings.data_dir / "output" / f"plan_{plan_id}"
        assert (out_dir / "editing_ir.json").exists()
        assert (out_dir / "edit_list.md").exists()
        assert (out_dir / "timeline.fcpxml").exists()
        assert "你好东京" in (out_dir / "subtitles.srt").read_text(encoding="utf-8")

        # 工具调用日志 API
        resp = client.get("/api/logs")
        assert resp.status_code == 200
