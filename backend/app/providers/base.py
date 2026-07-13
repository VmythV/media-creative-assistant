"""Model Provider 抽象：业务逻辑不绑定单一模型提供商。"""

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """文本模型接口：方案生成、意图解析、片段评分。"""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        *,
        json_mode: bool = False,
        tools: list[dict] | None = None,
        temperature: float = 0.4,
    ) -> dict:
        """返回 {"content": str, "tool_calls": list | None}"""


class VisionProvider(ABC):
    """视觉模型接口：帧理解、分类、质量判断。"""

    @abstractmethod
    async def analyze_images(
        self,
        image_paths: list[str],
        prompt: str,
        *,
        json_mode: bool = False,
    ) -> str:
        """返回模型文本输出。"""


class ProviderUnavailableError(RuntimeError):
    """能力缺失（如未配置 API Key），调用方应走降级路径。"""
