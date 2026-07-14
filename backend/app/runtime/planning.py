"""Planning Agent：创作目标 + 素材分析 → 剪辑方案（受限中间格式）→ Editing IR。

风险控制（设计文档 6.2）：模型只产出受限的"剪辑方案"格式，
由确定性代码转换为 IR；IR 校验失败自动带错误重试一次。
"""

import json
import logging

from app.ir.schema import IR_VERSION, TRANSITION_TYPES, IRValidationError, validate_ir
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
6. transition 表示本片段与前一片段之间的转场（首个片段必须为 null）。按节奏选择：
   舒缓/情绪递进用 fade(叠化)/dissolve(溶解)，场景跳转用 wipeleft/wiperight(划像)/slideleft/slideright(滑动)，
   开场收尾用 fadeblack(压黑)/fadewhite(闪白)，circleopen/circleclose(圆形开合)点缀用；
   快节奏内容硬切更利落，设为 null。duration 常用 0.4-0.8 秒。
7. 只输出 JSON，格式：
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
      "subtitle": "字幕文本或null",
      "transition": {"type": "fade", "duration": 0.5} 或 null
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


def _clamp_transition(raw, prev_len: float, clip_len: float) -> dict | None:
    """转场钳制（设计文档 §12）：白名单过滤 + 时长 clamp 到两侧片段可承载范围。"""
    if not isinstance(raw, dict) or raw.get("type") not in TRANSITION_TYPES or prev_len <= 0:
        return None
    try:
        duration = float(raw.get("duration") or 0.5)
    except (TypeError, ValueError):
        duration = 0.5
    duration = min(duration, 2.0, prev_len / 2, clip_len / 2)
    if duration < 0.1:  # 片段太短承载不了转场，退化为硬切
        return None
    return {"type": raw["type"], "duration": round(duration, 3)}


def plan_to_ir(plan: dict, analyzed: list[dict], project_name: str) -> dict:
    """确定性转换：剪辑方案 → Editing IR。"""
    assets_by_id = {item["asset"].id: item["asset"] for item in analyzed}
    used_ids: list[int] = []
    clips_ir = []
    subtitles_ir = []
    timeline_pos = 0.0
    prev_len = 0.0

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
        transition = _clamp_transition(clip.get("transition"), prev_len, clip_len)
        clip_ir = {
            "type": "clip",
            "source_id": f"src_{aid}",
            "trim": {"start": round(start, 3), "end": round(end, 3)},
            "role": SECTION_TO_ROLE.get(clip.get("section"), "broll"),
            "reason": clip.get("reason", ""),
        }
        if transition:
            clip_ir["transition"] = transition
        clips_ir.append(clip_ir)
        # 字幕占片段的"独占时间槽"：转场重叠期间沿用上一条字幕，新字幕从转场结束起显示
        effective_len = clip_len - (transition["duration"] if transition else 0.0)
        if clip.get("subtitle"):
            subtitles_ir.append(
                {
                    "type": "subtitle",
                    "content": clip["subtitle"],
                    "timeline_start": round(timeline_pos, 3),
                    "timeline_end": round(timeline_pos + effective_len, 3),
                }
            )
        timeline_pos += effective_len
        prev_len = clip_len

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


async def _plan_llm_loop(messages: list[dict], analyzed: list[dict], fallback_name: str) -> dict:
    """LLM 产出受限方案格式 → IR 转换校验；失败带错误自动重试一次。"""
    llm = get_llm_provider()
    last_error = None
    for attempt in range(2):
        resp = await llm.chat(messages, json_mode=True, temperature=0.5)
        try:
            plan = json.loads(resp["content"])
            if not plan.get("clips"):
                raise IRValidationError(["方案不含任何片段"])
            project_name = plan.get("title") or fallback_name
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


async def generate_plan(goal: str, asset_ids: list[int] | None = None) -> dict:
    """生成剪辑方案与 IR。返回 {"plan": ..., "ir": ...}；失败抛异常。"""
    analyzed = _load_analyzed_assets(asset_ids)
    if not analyzed:
        raise ValueError("没有已完成分析的素材，请先导入并分析素材")

    brief = _build_material_brief(analyzed)
    messages = [
        {"role": "system", "content": PLAN_SYSTEM_PROMPT},
        {"role": "user", "content": f"创作目标：{goal}\n\n可用素材：\n{brief}"},
    ]
    return await _plan_llm_loop(messages, analyzed, fallback_name=goal[:40])


REVISE_RULE = """
现在的任务是**修订**一个已有方案：用户会给出当前方案 JSON 和修订指令。
只按指令修改，指令未提及的片段、顺序、字幕保持原样；输出修订后的完整方案 JSON（同上格式）。"""


async def revise_plan(base_plan: dict, instruction: str, asset_ids: list[int] | None = None) -> dict:
    """按自然语言指令修订已有方案（设计文档 §10）。返回 {"plan": ..., "ir": ...}。"""
    analyzed = _load_analyzed_assets(asset_ids)
    if not analyzed:
        raise ValueError("没有已完成分析的素材")

    brief = _build_material_brief(analyzed)
    messages = [
        {"role": "system", "content": PLAN_SYSTEM_PROMPT + REVISE_RULE},
        {
            "role": "user",
            "content": (
                f"当前方案：\n{json.dumps(base_plan, ensure_ascii=False)}\n\n"
                f"可用素材：\n{brief}\n\n修订指令：{instruction}"
            ),
        },
    ]
    fallback = base_plan.get("title") or "修订方案"
    return await _plan_llm_loop(messages, analyzed, fallback_name=fallback)


def _clip_desc(c: dict) -> str:
    sub = f"，字幕「{c.get('subtitle')}」" if c.get("subtitle") else ""
    return f"素材#{c['asset_id']} [{c['start']:.1f}s-{c['end']:.1f}s] {c.get('section', '')}{sub}"


def _transition_desc(c: dict) -> str:
    t = c.get("transition")
    if not isinstance(t, dict) or not t.get("type"):
        return "硬切"
    return f"{t['type']} {float(t.get('duration') or 0.5):.1f}s"


def diff_plans(old: dict, new: dict) -> dict:
    """确定性方案差异：按 asset_id + 时间区间重叠匹配片段，产出人类可读差异。"""
    old_clips = list(old.get("clips", []))
    new_clips = list(new.get("clips", []))
    matched_old: set[int] = set()
    added, changed = [], []

    for ni, nc in enumerate(new_clips):
        best, best_overlap = None, 0.0
        for oi, oc in enumerate(old_clips):
            if oi in matched_old or oc["asset_id"] != nc["asset_id"]:
                continue
            overlap = min(oc["end"], nc["end"]) - max(oc["start"], nc["start"])
            if overlap > best_overlap:
                best, best_overlap = oi, overlap
        if best is None:
            added.append(f"第 {ni + 1} 位新增：{_clip_desc(nc)}")
            continue
        matched_old.add(best)
        oc = old_clips[best]
        details = []
        if (round(oc["start"], 1), round(oc["end"], 1)) != (round(nc["start"], 1), round(nc["end"], 1)):
            details.append(f"区间 {oc['start']:.1f}-{oc['end']:.1f}s → {nc['start']:.1f}-{nc['end']:.1f}s")
        if oc.get("section") != nc.get("section"):
            details.append(f"角色 {oc.get('section')} → {nc.get('section')}")
        if (oc.get("subtitle") or None) != (nc.get("subtitle") or None):
            details.append(f"字幕「{oc.get('subtitle') or '无'}」→「{nc.get('subtitle') or '无'}」")
        if _transition_desc(oc) != _transition_desc(nc):
            details.append(f"转场 {_transition_desc(oc)} → {_transition_desc(nc)}")
        if best != ni:
            details.append(f"位置 {best + 1} → {ni + 1}")
        if details:
            changed.append(f"素材#{nc['asset_id']}：" + "；".join(details))

    removed = [
        f"删除：{_clip_desc(oc)}" for oi, oc in enumerate(old_clips) if oi not in matched_old
    ]
    old_dur = sum(c["end"] - c["start"] for c in old_clips)
    new_dur = sum(c["end"] - c["start"] for c in new_clips)
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "duration": f"{old_dur:.1f}s → {new_dur:.1f}s",
        "unchanged": len(matched_old) - len(changed),
    }
