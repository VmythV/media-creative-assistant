"""Understanding Agent：聚合单个素材的分析结果，产出分类与精彩片段候选。

策略（设计文档 6.1）：先启发式过滤评分，若 LLM 可用则由模型精排并生成推荐理由；
LLM 不可用时降级为纯启发式，理由由规则拼接。
"""

import json
import logging

from app.config import settings
from app.providers import ProviderUnavailableError, get_llm_provider

logger = logging.getLogger("mca.understanding")

MIN_SHOT_LEN = 1.5
MAX_SHOT_LEN = 25.0


def _speech_ranges(transcript: dict | None) -> list[tuple[float, float]]:
    if not transcript:
        return []
    return [(s["start"], s["end"]) for s in transcript.get("segments", [])]


def _overlaps(start: float, end: float, ranges: list[tuple[float, float]]) -> bool:
    return any(s < end and e > start for s, e in ranges)


def heuristic_candidates(
    shots: list[dict],
    vision_by_shot: dict[int, dict],
    transcript: dict | None,
) -> list[dict]:
    """启发式评分：产出精彩片段候选（含规则拼接的理由）。"""
    speech = _speech_ranges(transcript)
    candidates = []
    for shot in shots:
        idx, start, end = shot["index"], shot["start"], shot["end"]
        duration = end - start
        vision = vision_by_shot.get(idx, {})
        if vision.get("is_junk"):
            continue
        score = float(vision.get("quality_score", 5))
        reasons = []
        if vision.get("description"):
            reasons.append(vision["description"])
        if duration < MIN_SHOT_LEN:
            score -= 3
            reasons.append("镜头过短")
        elif duration > MAX_SHOT_LEN:
            score -= 1
        else:
            score += 1
        if _overlaps(start, end, speech):
            score += 2
            reasons.append("含对白")
        if vision.get("motion") in ("slow", "medium"):
            score += 1
        if vision.get("suitable_roles"):
            reasons.append(f"适合用作{'/'.join(vision['suitable_roles'])}")
        candidates.append(
            {
                "shot_index": idx,
                "start": start,
                "end": end,
                "score": round(score, 1),
                "category": vision.get("category"),
                "suitable_roles": vision.get("suitable_roles", []),
                "reason": "；".join(reasons) or "画面质量尚可",
            }
        )
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


def aggregate_category(vision_by_shot: dict[int, dict]) -> str | None:
    votes: dict[str, int] = {}
    for v in vision_by_shot.values():
        if v.get("is_junk"):
            continue
        cat = v.get("category")
        if cat:
            votes[cat] = votes.get(cat, 0) + 1
    if not votes:
        return None
    return max(votes, key=votes.get)


async def llm_refine_highlights(candidates: list[dict], transcript: dict | None, filename: str) -> list[dict] | None:
    """LLM 精排：失败或不可用时返回 None，调用方使用启发式结果。"""
    if not settings.dashscope_api_key or not candidates:
        return None
    compact = [
        {k: c[k] for k in ("shot_index", "start", "end", "score", "category", "reason")}
        for c in candidates[:20]
    ]
    prompt = (
        f"素材文件：{filename}\n"
        f"以下是启发式评分后的镜头候选（JSON）：\n{json.dumps(compact, ensure_ascii=False)}\n"
        + (f"对白转写摘要：{transcript.get('text', '')[:500]}\n" if transcript else "")
        + "请从创作角度重排这些镜头（最多保留10个），为每个镜头写一句更专业的中文推荐理由。"
        '输出 JSON：{"highlights": [{"shot_index": int, "start": float, "end": float, '
        '"score": float, "reason": str}]}，只输出 JSON。'
    )
    try:
        resp = await get_llm_provider().chat(
            [{"role": "user", "content": prompt}], json_mode=True, temperature=0.3
        )
        data = json.loads(resp["content"])
        highlights = data.get("highlights")
        if not isinstance(highlights, list) or not highlights:
            return None
        valid_idx = {c["shot_index"]: c for c in candidates}
        result = []
        for h in highlights:
            base = valid_idx.get(h.get("shot_index"))
            if base is None:
                continue
            result.append({**base, "score": float(h.get("score", base["score"])), "reason": h.get("reason") or base["reason"]})
        return result or None
    except (ProviderUnavailableError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        logger.warning("LLM 精排失败，使用启发式结果: %s", e)
        return None
    except Exception as e:  # noqa: BLE001 - 网络/API 异常也降级
        logger.warning("LLM 精排异常，使用启发式结果: %s", e)
        return None


async def summarize_asset(
    filename: str,
    shots: list[dict],
    vision_by_shot: dict[int, dict],
    transcript: dict | None,
    audio_events: dict | None,
    vision_available: bool,
) -> dict:
    candidates = heuristic_candidates(shots, vision_by_shot, transcript)
    refined = await llm_refine_highlights(candidates, transcript, filename)
    highlights = refined if refined is not None else candidates[:10]
    junk_shots = [i for i, v in vision_by_shot.items() if v.get("is_junk")]
    return {
        "category": aggregate_category(vision_by_shot),
        "shot_count": len(shots),
        "junk_shot_indexes": junk_shots,
        "highlights": highlights,
        "highlight_source": "llm" if refined is not None else "heuristic",
        "has_speech": bool(transcript and transcript.get("segments")),
        "transcript_language": transcript.get("language") if transcript else None,
        "vision_available": vision_available,
    }
