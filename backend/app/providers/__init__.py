"""Provider 工厂：默认 Qwen（DashScope），按配置可扩展其他提供商。"""

from app.providers.base import LLMProvider, ProviderUnavailableError, VisionProvider  # noqa: F401

_llm: LLMProvider | None = None
_vision: VisionProvider | None = None


def get_llm_provider() -> LLMProvider:
    global _llm
    if _llm is None:
        from app.providers.qwen import QwenLLMProvider

        _llm = QwenLLMProvider()
    return _llm


def get_vision_provider() -> VisionProvider:
    global _vision
    if _vision is None:
        from app.providers.qwen import QwenVisionProvider

        _vision = QwenVisionProvider()
    return _vision


def set_providers(llm: LLMProvider | None = None, vision: VisionProvider | None = None) -> None:
    """测试/扩展用：注入自定义 Provider。"""
    global _llm, _vision
    if llm is not None:
        _llm = llm
    if vision is not None:
        _vision = vision
