"""通义 Qwen（DashScope）实现：走 OpenAI 兼容接口。

切换到 Claude/GPT 只需新增实现并改配置，业务代码不变。
"""

import base64
from pathlib import Path

from openai import AsyncOpenAI

from app.config import settings
from app.providers.base import LLMProvider, ProviderUnavailableError, VisionProvider


def _client() -> AsyncOpenAI:
    if not settings.dashscope_api_key:
        raise ProviderUnavailableError("未配置 DASHSCOPE_API_KEY")
    return AsyncOpenAI(api_key=settings.dashscope_api_key, base_url=settings.dashscope_base_url)


def _image_to_data_url(path: str) -> str:
    data = base64.b64encode(Path(path).read_bytes()).decode()
    return f"data:image/jpeg;base64,{data}"


class QwenLLMProvider(LLMProvider):
    async def chat(
        self,
        messages: list[dict],
        *,
        json_mode: bool = False,
        tools: list[dict] | None = None,
        temperature: float = 0.4,
    ) -> dict:
        client = _client()
        kwargs: dict = {"model": settings.qwen_llm_model, "messages": messages, "temperature": temperature}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = tools
        resp = await client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        return {
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
                for tc in (msg.tool_calls or [])
            ]
            or None,
        }


def effective_vision_model() -> str:
    """速度档位生效的视觉模型（M20）：fast 走轻量模型。"""
    if settings.vision_speed == "fast":
        return settings.qwen_vl_fast_model
    return settings.qwen_vl_model


class QwenVisionProvider(VisionProvider):
    async def analyze_images(
        self,
        image_paths: list[str],
        prompt: str,
        *,
        json_mode: bool = False,
    ) -> str:
        client = _client()
        content: list[dict] = [
            {"type": "image_url", "image_url": {"url": _image_to_data_url(p)}} for p in image_paths
        ]
        content.append({"type": "text", "text": prompt})
        kwargs: dict = {
            "model": effective_vision_model(),
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.2,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = await client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""
