"""发布文案包（M21，backlog B12）：方案 → 标题/简介/话题标签。

受限格式 + 确定性钳制（长度/数量），失败抛异常由调用方呈现。
"""

import json

from app.memory import get_memory_provider
from app.providers import get_llm_provider

PUBLISH_SYSTEM_PROMPT = """你是短视频运营文案专家。根据剪辑方案信息，为发布平台生成一套文案包。

只输出 JSON：
{"title": "标题（吸引点击，≤30字，不带话题标签）",
 "description": "简介（1-3句，自然口吻，可含1-2个emoji）",
 "hashtags": ["话题1", "话题2", ...]}

要求：hashtags 3-6 个、每个 ≤10 字、不带 # 号；风格贴合平台调性；不虚构视频里没有的内容。"""


def _clamp(kit: dict) -> dict:
    """确定性钳制：字段长度与数量。"""
    title = str(kit.get("title") or "").strip()[:40]
    description = str(kit.get("description") or "").strip()[:200]
    hashtags = [str(h).strip().lstrip("#")[:12]
                for h in (kit.get("hashtags") or []) if str(h).strip()][:6]
    if not title:
        raise ValueError("文案生成结果缺少标题")
    return {"title": title, "description": description, "hashtags": hashtags}


async def generate_publish_kit(plan: dict, platform: str = "抖音") -> dict:
    """按方案内容生成发布文案包。返回 {title, description, hashtags, platform}。"""
    clips = plan.get("clips") or []
    subtitles = [c.get("subtitle") for c in clips if c.get("subtitle")]
    total = sum(c["end"] - c["start"] for c in clips) if clips else 0
    brief = [
        f"视频标题（工作名）：{plan.get('title', '')}",
        f"时长约 {total:.0f} 秒，{len(clips)} 个片段",
    ]
    if subtitles:
        brief.append("字幕内容（叙事线索）：" + " / ".join(subtitles[:10]))
    reasons = [c.get("reason", "") for c in clips if c.get("reason")][:5]
    if reasons:
        brief.append("画面内容：" + "；".join(reasons))
    prefs = get_memory_provider().texts("user")
    if prefs:
        brief.append("用户长期偏好（文案风格参考）：" + "；".join(prefs))

    llm = get_llm_provider()
    resp = await llm.chat(
        [{"role": "system", "content": PUBLISH_SYSTEM_PROMPT},
         {"role": "user", "content": f"发布平台：{platform}\n\n{chr(10).join(brief)}"}],
        json_mode=True, temperature=0.7,
    )
    kit = _clamp(json.loads(resp["content"]))
    return {**kit, "platform": platform}
