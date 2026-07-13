from fastapi.testclient import TestClient

from app.capability.discovery import discover_capabilities
from app.main import app
from app.tools import load_all_tools
from app.tools.media import probe_media
from app.tools.registry import registry


def test_probe_media(sample_video):
    meta = probe_media(str(sample_video))
    assert meta["duration"] and abs(meta["duration"] - 5.0) < 0.5
    assert meta["video"]["width"] == 640
    assert meta["video"]["height"] == 360
    assert meta["video"]["fps"] == 25.0
    assert meta["audio"] is not None


def test_probe_media_missing_file():
    import pytest

    with pytest.raises(FileNotFoundError):
        probe_media("/nonexistent/file.mp4")


async def test_registry_execute(sample_video):
    load_all_tools()
    result = await registry.execute("probe_media", {"path": str(sample_video)})
    assert result.ok
    assert result.output["video"]["width"] == 640

    bad = await registry.execute("probe_media", {"path": "/nope.mp4"})
    assert not bad.ok
    assert "FileNotFoundError" in bad.error


def test_registry_lists_mcp_style_tools():
    load_all_tools()
    tools = registry.list()
    probe = next(t for t in tools if t["name"] == "probe_media")
    assert "inputSchema" in probe
    assert probe["inputSchema"]["type"] == "object"


def test_capability_discovery_shape():
    reg = discover_capabilities()
    names = {c["name"] for c in reg["capabilities"]}
    assert {"ffmpeg", "davinci", "dashscope", "faster-whisper", "ollama"} <= names
    ffmpeg = next(c for c in reg["capabilities"] if c["name"] == "ffmpeg")
    assert ffmpeg["available"] is True  # 本机已装 FFmpeg
    # 除可选的 ollama 外，能力缺失必须给出降级说明
    for cap in reg["capabilities"]:
        if not cap["available"] and cap["name"] != "ollama":
            assert cap["fallback"], f"{cap['name']} 缺失时应有 fallback 说明"


def test_capabilities_api():
    with TestClient(app) as client:
        resp = client.get("/api/capabilities")
        assert resp.status_code == 200
        assert "capabilities" in resp.json()
        assert client.get("/api/health").json() == {"status": "ok"}
