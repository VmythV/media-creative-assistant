"""vision 工具组：基于 VisionProvider 的帧理解。"""

import json

from app.providers import get_vision_provider
from app.tools.registry import registry

# prompt 模板固化版本：变更时提升版本号并同步 AnalysisRecord.version
SHOT_ANALYSIS_PROMPT_VERSION = "v1"
SHOT_ANALYSIS_PROMPT = """你是专业视频素材分析师。以下图片是同一个镜头中抽取的关键帧。
请分析该镜头并输出 JSON（只输出 JSON，不要其他文字）：

{
  "category": "风景|人物|产品|采访|空镜|美食|建筑|夜景|运动|其他",
  "description": "一句话中文描述画面内容",
  "quality_score": 0到10的整数（构图、清晰度、曝光综合评分；模糊/过曝/无内容给低分）,
  "subjects": ["画面主体列表"],
  "motion": "static|slow|medium|fast",
  "is_junk": 是否废片（黑屏/严重模糊/误拍）true或false,
  "suitable_roles": ["opening","broll","climax","ending" 中适合的角色，可为空]
}"""


@registry.register(
    name="analyze_frames",
    description="对一个镜头的关键帧做视觉理解：分类、描述、质量评分、主体、运动强度。",
    parameters={
        "type": "object",
        "properties": {
            "image_paths": {"type": "array", "items": {"type": "string"}, "description": "帧图片路径列表"},
        },
        "required": ["image_paths"],
    },
)
async def analyze_frames(image_paths: list[str]) -> dict:
    provider = get_vision_provider()
    text = await provider.analyze_images(image_paths, SHOT_ANALYSIS_PROMPT, json_mode=True)
    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"视觉模型输出不是合法 JSON: {text[:200]}") from e
    result["prompt_version"] = SHOT_ANALYSIS_PROMPT_VERSION
    return result
