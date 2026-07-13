"""Planning Agent：创作目标 + 素材分析 → 剪辑方案（受限中间格式）→ Editing IR。

风险控制（设计文档 6.2）：模型只产出受限的"剪辑方案"格式，
由确定性代码转换为 IR；IR 校验失败自动带错误重试一次。
"""

import json
import logging

from app.ir.schema import IR_VERSION, IRValidationError, validate_ir
from app.providers import get_llm_provider
from app.store.db import db_session
from app.store.models import AnalysisRecord, Asset

logger = logging.getLogger("mca.planning")

PLAN_SYSTEM_PROMPT = """你是专业视频剪辑师的 AI 副驾驶。根据用户的创作目标和素材分析结果，生成结构化剪辑方案。

规则：
1. 只能使用提供的素材和镜头时间范围，绝不虚构素材或超出镜头边界。
2. 方案必须有叙事结构：opening（开场）→ build（铺垫）→ climax（高潮）→ ending（结尾），broll 可穿插。
3. 每个片段给出选择理由（中文，从创作角度）。
4. 片段时长要服务于目标总时长，单个片段一般 2-8 秒。
5. 如果用户要求字幕，为关键片段配简短中文字幕（subtitle 字段），否则设为 null。
6. 只输出 JSON，格式：
{
  "title": "方案标题",
  "target_duration": 目标总时长秒数,
  "clips": [
    {
      "section": "opening|build|climax|ending|broll",
      "asset_id": 素材id(整数),
      "start": 素材内起始秒(浮点),
      "end": 素材内结束秒(浮点),
      "reason": "选择理由",
      "subtitle": "字幕文本或null"
    }
  ]
}"""

SECTION_TO_ROLE = {"opening": "opening", "build": "build", "climax": "climax", "ending": "ending", "broll": "broll"}


def _load_analyzed_assets(asset_ids: list[int] | None) -> list[dict]:
    """加载已分析素材及其 summary/transcript，供 Planning 提示词使用。"""
    with db_session() as db:
        q = db.query(Asset).filter(Asset.status == "analyzed")
        if asset_ids:
            q = q.filter(Asset.id.in_(asset_ids))
        assets = q.all()
        result = []
        for a in assets:
            records = {
                r.kind: r.payload
                for r in db.query(AnalysisRecord).filter_by(content_hash=a.content_hash).all()
            }
            result.append(
                {
                    "asset": a,
                    "summary": records.get("summary", {}),
                    "transcript": records.get("transcript"),
                    "shots": (records.get("shots") or {}).get("shots", []),
                }
            )
        return result


def _build_material_brief(analyzed: list[dict]) -> str:
    """把素材分析压缩成给模型的简报。"""
    lines = []
    for item in analyzed:
        a = item["asset"]
        s = item["summary"]
        lines.append(
            f"素材 asset_id={a.id}：{a.filename}，时长 {a.duration:.1f}s，"
            f"分类：{s.get('category') or '未知'}，{s.get('shot_count', '?')} 个镜头"
        )
        for h in (s.get("highlights") or [])[:8]:
            lines.append(
                f"  - 推荐片段 [{h['start']:.1f}s - {h['end']:.1f}s]"
                f"（评分 {h.get('score')}）：{h.get('reason', '')}"
            )
        transcript = item.get("transcript")
        if transcript and transcript.get("text"):
            lines.append(f"  对白摘要：{transcript['text'][:200]}")
    return "\n".join(lines)


def plan_to_ir(plan: dict, analyzed: list[dict], project_name: str) -> dict:
    """确定性转换：剪辑方案 → Editing IR v0.1。"""
    assets_by_id = {item["asset"].id: item["asset"] for item in analyzed}
    used_ids: list[int] = []
    clips_ir = []
    subtitles_ir = []
    timeline_pos = 0.0

    for clip in plan.get("clips", []):
        aid = clip["asset_id"]
        asset = assets_by_id.get(aid)
        if asset is None:
            raise IRValidationError([f"方案引用了不存在或未分析的素材 asset_id={aid}"])
        if aid not in used_ids:
            used_ids.append(aid)
        start = max(0.0, float(clip["start"]))
        end = min(float(clip["end"]), asset.duration or float(clip["end"]))
        clip_len = end - start
        clips_ir.append(
            {
                "type": "clip",
                "source_id": f"src_{aid}",
                "trim": {"start": round(start, 3), "end": round(end, 3)},
                "role": SECTION_TO_ROLE.get(clip.get("section"), "broll"),
                "reason": clip.get("reason", ""),
            }
        )
        if clip.get("subtitle"):
            subtitles_ir.append(
                {
                    "type": "subtitle",
                    "content": clip["subtitle"],
                    "timeline_start": round(timeline_pos, 3),
                    "timeline_end": round(timeline_pos + clip_len, 3),
                }
            )
        timeline_pos += clip_len

    first = assets_by_id[used_ids[0]] if used_ids else None
    tracks: list[dict] = [{"type": "video", "index": 1, "items": clips_ir}]
    if subtitles_ir:
        tracks.append({"type": "subtitle", "index": 1, "items": subtitles_ir})
    return {
        "version": IR_VERSION,
        "project": {
            "name": project_name,
            "fps": (first.fps if first and first.fps else 25.0),
            "resolution": {
                "width": first.width if first and first.width else 1920,
                "height": first.height if first and first.height else 1080,
            },
        },
        "sources": [
            {"id": f"src_{aid}", "path": assets_by_id[aid].path, "duration": assets_by_id[aid].duration}
            for aid in used_ids
        ],
        "tracks": tracks,
        "render": None,
    }


async def generate_plan(goal: str, asset_ids: list[int] | None = None) -> dict:
    """生成剪辑方案与 IR。返回 {"plan": ..., "ir": ...}；失败抛异常。"""
    analyzed = _load_analyzed_assets(asset_ids)
    if not analyzed:
        raise ValueError("没有已完成分析的素材，请先导入并分析素材")

    brief = _build_material_brief(analyzed)
    llm = get_llm_provider()
    messages = [
        {"role": "system", "content": PLAN_SYSTEM_PROMPT},
        {"role": "user", "content": f"创作目标：{goal}\n\n可用素材：\n{brief}"},
    ]

    last_error = None
    for attempt in range(2):  # 校验失败自动带错误重试一次
        resp = await llm.chat(messages, json_mode=True, temperature=0.5)
        try:
            plan = json.loads(resp["content"])
            if not plan.get("clips"):
                raise IRValidationError(["方案不含任何片段"])
            project_name = plan.get("title") or goal[:40]
            ir = plan_to_ir(plan, analyzed, project_name)
            validate_ir(ir, check_paths=True)
            return {"plan": plan, "ir": ir}
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            errors = e.errors if isinstance(e, IRValidationError) else [str(e)]
            last_error = errors
            logger.warning("方案第 %d 次生成校验失败: %s", attempt + 1, errors)
            messages.append({"role": "assistant", "content": resp["content"]})
            messages.append(
                {
                    "role": "user",
                    "content": "上述方案校验失败，错误如下，请修正后重新输出完整 JSON：\n"
                    + "\n".join(f"- {err}" for err in errors),
                }
            )
    raise IRValidationError(last_error or ["方案生成失败"])
