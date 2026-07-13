"""Capability Discovery：启动时扫描环境能力，生成 Capability Registry。

能力缺失时给出可理解的降级说明，而不是报错。
"""

import importlib.util
import shutil
import subprocess
from pathlib import Path

import httpx

from app.config import settings


def _ffmpeg_capability() -> dict:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    available = bool(ffmpeg and ffprobe)
    version = None
    if available:
        proc = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=False)
        first = proc.stdout.splitlines()[0] if proc.stdout else ""
        version = first.replace("ffmpeg version ", "").split(" ")[0] or None
    return {
        "type": "tool",
        "name": "ffmpeg",
        "available": available,
        "version": version,
        "features": ["probe", "extract_audio", "sample_frames", "audio_events"] if available else [],
        "fallback": None if available else "无法进行素材分析，请安装 FFmpeg（brew install ffmpeg）",
    }


def _resolve_capability() -> dict:
    app_installed = Path("/Applications/DaVinci Resolve/DaVinci Resolve.app").exists()
    script_module = Path(settings.resolve_script_api) / "Modules" / "DaVinciResolveScript.py"
    script_lib = Path(settings.resolve_script_lib)
    available = app_installed and script_module.exists() and script_lib.exists()
    return {
        "type": "editor",
        "name": "davinci",
        "available": available,
        "features": ["project", "timeline", "clip", "subtitle"] if available else [],
        "fallback": None if available else "降级输出 Editing IR JSON + Markdown 剪辑清单，或导出 FCPXML 手动导入",
    }


def _dashscope_capability() -> dict:
    available = bool(settings.dashscope_api_key)
    return {
        "type": "model",
        "name": "dashscope",
        "available": available,
        "features": ["vision", "llm"] if available else [],
        "models": {"vision": settings.qwen_vl_model, "llm": settings.qwen_llm_model} if available else None,
        "fallback": None if available else "请在 .env 设置 DASHSCOPE_API_KEY；缺失时无法进行视觉分析和方案生成",
    }


def _whisper_capability() -> dict:
    available = importlib.util.find_spec("faster_whisper") is not None
    return {
        "type": "model",
        "name": "faster-whisper",
        "available": available,
        "features": ["transcribe_zh", "transcribe_en"] if available else [],
        "model": settings.whisper_model if available else None,
        "fallback": None if available else "语音转写不可用，字幕功能降级；安装 faster-whisper 后恢复",
    }


def _ollama_capability() -> dict:
    available = False
    try:
        # trust_env=False：本地请求不走系统代理（用户环境可能配置了 SOCKS 代理）
        with httpx.Client(trust_env=False, timeout=0.5) as client:
            resp = client.get(f"{settings.ollama_base_url}/api/tags")
        available = resp.status_code == 200
    except httpx.HTTPError:
        pass
    return {
        "type": "model",
        "name": "ollama",
        "available": available,
        "features": ["local_llm"] if available else [],
        "fallback": None,  # 可选能力，缺失无需降级说明
    }


def discover_capabilities() -> dict:
    return {
        "capabilities": [
            _ffmpeg_capability(),
            _resolve_capability(),
            _dashscope_capability(),
            _whisper_capability(),
            _ollama_capability(),
        ]
    }
