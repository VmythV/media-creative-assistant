"""剪辑方案 API：精彩片段推荐、方案生成/查看/确认/修订/配乐。"""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ir.schema import IRValidationError, validate_ir
from app.runtime.events import bus
from app.runtime.planning import diff_plans, extract_preferences, generate_plan, revise_plan
from app.runtime.tasks import spawn
from app.store.db import db_session, get_db
from app.store.models import AnalysisRecord, Asset, EditPlan
from app.tools.media import probe_media

MUSIC_SOURCE_ID = "src_music"

logger = logging.getLogger("mca.plans")
router = APIRouter(tags=["plans"])


class PlanRequest(BaseModel):
    goal: str
    asset_ids: list[int] | None = None


def _plan_dict(p: EditPlan) -> dict:
    return {
        "id": p.id,
        "goal": p.goal,
        "plan": p.plan,
        "ir": p.ir,
        "status": p.status,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


@router.get("/highlights")
def get_highlights(db: Session = Depends(get_db)) -> dict:
    """聚合所有已分析素材的精彩片段推荐（附理由）。"""
    result = []
    for asset in db.query(Asset).filter(Asset.status == "analyzed").all():
        summary = (
            db.query(AnalysisRecord)
            .filter_by(content_hash=asset.content_hash, kind="summary")
            .first()
        )
        if summary is None:
            continue
        for h in summary.payload.get("highlights", []):
            result.append({"asset_id": asset.id, "filename": asset.filename, **h})
    result.sort(key=lambda h: h.get("score", 0), reverse=True)
    return {"highlights": result}


async def run_generation(plan_id: int, goal: str, asset_ids: list[int] | None = None) -> None:
    """方案生成核心（payload 驱动，M19 任务恢复可重放）。"""
    bus.publish("plan", {"plan_id": plan_id, "step": "generating", "detail": goal})
    try:
        result = await generate_plan(goal, asset_ids)
        with db_session() as s:
            row = s.get(EditPlan, plan_id)
            row.plan = result["plan"]
            row.ir = result["ir"]
            row.status = "draft"
            s.commit()
        bus.publish("plan", {"plan_id": plan_id, "step": "draft", "detail": "方案生成完成"})
    except Exception as e:  # noqa: BLE001 - 失败落状态并上报，再抛给任务记账
        logger.exception("方案 %s 生成失败", plan_id)
        with db_session() as s:
            row = s.get(EditPlan, plan_id)
            row.status = "failed"
            row.plan = {"error": str(e)}
            s.commit()
        bus.publish("plan", {"plan_id": plan_id, "step": "failed", "detail": str(e)[:300]})
        raise


@router.post("/plans")
async def create_plan(req: PlanRequest, db: Session = Depends(get_db)) -> dict:
    if not req.goal.strip():
        raise HTTPException(400, "创作目标不能为空")
    plan_row = EditPlan(goal=req.goal, plan={}, status="generating")
    db.add(plan_row)
    db.commit()
    plan_id = plan_row.id
    spawn("plan_generate", {"plan_id": plan_id, "goal": req.goal, "asset_ids": req.asset_ids},
          run_generation(plan_id, req.goal, req.asset_ids))
    return {"plan_id": plan_id, "status": "generating"}


@router.get("/plans")
def list_plans(db: Session = Depends(get_db)) -> dict:
    plans = db.query(EditPlan).order_by(EditPlan.id.desc()).all()
    return {"plans": [_plan_dict(p) for p in plans]}


@router.get("/plans/{plan_id}")
def get_plan(plan_id: int, db: Session = Depends(get_db)) -> dict:
    plan = db.get(EditPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "方案不存在")
    return _plan_dict(plan)


class ReviseRequest(BaseModel):
    instruction: str
    asset_ids: list[int] | None = None


@router.post("/plans/{plan_id}/revise")
async def revise(plan_id: int, req: ReviseRequest, db: Session = Depends(get_db)) -> dict:
    """自然语言修订：产出新方案行，旧方案保留可回滚（设计文档 §10）。"""
    if not req.instruction.strip():
        raise HTTPException(400, "修订指令不能为空")
    base = db.get(EditPlan, plan_id)
    if base is None:
        raise HTTPException(404, "方案不存在")
    if not base.ir or not base.plan.get("clips"):
        raise HTTPException(400, "源方案没有可修订的内容")
    base_goal = base.goal

    new_row = EditPlan(
        goal=base_goal,
        plan={"revised_from": plan_id, "revision_instruction": req.instruction},
        status="generating",
    )
    db.add(new_row)
    db.commit()
    new_id = new_row.id
    spawn("plan_revise",
          {"plan_id": new_id, "base_plan_id": plan_id, "instruction": req.instruction,
           "asset_ids": req.asset_ids},
          run_revision(new_id, plan_id, req.instruction, req.asset_ids))
    return {"plan_id": new_id, "revised_from": plan_id, "status": "generating"}


async def run_revision(new_id: int, base_plan_id: int, instruction: str,
                       asset_ids: list[int] | None = None) -> None:
    """方案修订核心（payload 驱动，M19 任务恢复可重放）：基底方案从库重取。"""
    bus.publish("plan", {"plan_id": new_id, "step": "generating", "detail": f"修订：{instruction}"})
    try:
        with db_session() as s:
            base = s.get(EditPlan, base_plan_id)
            if base is None or not base.plan.get("clips"):
                raise ValueError(f"源方案 #{base_plan_id} 不存在或没有内容")
            base_plan = {k: v for k, v in dict(base.plan).items()
                         if k not in ("execution", "render")}
        result = await revise_plan(base_plan, instruction, asset_ids)
        diff = diff_plans(base_plan, result["plan"])
        with db_session() as s:
            row = s.get(EditPlan, new_id)
            row.plan = {
                **result["plan"],
                "revised_from": base_plan_id,
                "revision_instruction": instruction,
                "diff": diff,
            }
            row.ir = result["ir"]
            row.status = "draft"
            s.commit()
        bus.publish("plan", {"plan_id": new_id, "step": "draft", "detail": "修订方案生成完成"})
        # 修订成功后沉淀长期偏好（设计文档 §14）；提取失败不影响主流程
        try:
            added = await extract_preferences(instruction)
            if added:
                bus.publish("memory", {"step": "learned", "detail": "；".join(added)})
        except Exception:  # noqa: BLE001
            logger.warning("偏好提取失败（已忽略）", exc_info=True)
    except Exception as e:  # noqa: BLE001 - 失败落状态并上报，再抛给任务记账
        logger.exception("方案 %s 修订失败", new_id)
        with db_session() as s:
            row = s.get(EditPlan, new_id)
            row.status = "failed"
            row.plan = {**row.plan, "error": str(e)}
            s.commit()
        bus.publish("plan", {"plan_id": new_id, "step": "failed", "detail": str(e)[:300]})
        raise


class MusicRequest(BaseModel):
    path: str
    gain_db: float = -16.0
    fade_in: float = 1.0
    fade_out: float = 2.0
    loop: bool = True


def apply_music(plan_id: int, path: str, *, gain_db: float = -16.0, fade_in: float = 1.0,
                fade_out: float = 2.0, loop: bool = True) -> str:
    """配乐核心（供 API 与对话执行器共用）：确定性写 IR 音频轨。返回文件名；失败抛 ValueError。"""
    file = Path(path).expanduser()
    if not file.is_file():
        raise ValueError(f"文件不存在: {path}")
    meta = probe_media(str(file))
    if not meta.get("audio"):
        raise ValueError("该文件不含音频流")

    with db_session() as db:
        plan = db.get(EditPlan, plan_id)
        if plan is None:
            raise ValueError("方案不存在")
        if not plan.ir:
            raise ValueError("方案没有 Editing IR")
        ir = dict(plan.ir)
        if ir.get("version") == "0.1":  # 音频轨需要 0.2+；更高版本（0.3 转场）保持不降级
            ir["version"] = "0.2"
        ir["sources"] = [s for s in ir["sources"] if s["id"] != MUSIC_SOURCE_ID] + [
            {"id": MUSIC_SOURCE_ID, "path": str(file), "duration": meta["duration"]}
        ]
        ir["tracks"] = [t for t in ir["tracks"] if t.get("type") != "audio"] + [
            {"type": "audio", "index": 1, "items": [{
                "type": "music", "source_id": MUSIC_SOURCE_ID, "gain_db": gain_db,
                "fade_in": fade_in, "fade_out": fade_out, "loop": loop,
            }]}
        ]
        try:
            validate_ir(ir)
        except IRValidationError as e:
            raise ValueError(f"配乐后 IR 校验失败: {e}") from e
        plan.ir = ir
        db.commit()
    return file.name


@router.put("/plans/{plan_id}/music")
def set_music(plan_id: int, req: MusicRequest, db: Session = Depends(get_db)) -> dict:
    """设置/替换方案配乐：确定性写入 IR 音频轨，不经过模型（设计文档 §11）。"""
    if db.get(EditPlan, plan_id) is None:
        raise HTTPException(404, "方案不存在")
    try:
        filename = apply_music(plan_id, req.path, gain_db=req.gain_db,
                               fade_in=req.fade_in, fade_out=req.fade_out, loop=req.loop)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"plan_id": plan_id, "music": filename, "gain_db": req.gain_db}


ASPECT_PRESETS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}


class OutputRequest(BaseModel):
    aspect: str | None = None  # 16:9 / 9:16 / 1:1
    width: int | None = None
    height: int | None = None
    fill: str = "blur"


def apply_output(plan_id: int, *, aspect: str | None = None, width: int | None = None,
                 height: int | None = None, fill: str = "blur") -> dict:
    """交付规格核心（供 API 与对话执行器共用）：确定性写 IR render 字段（v0.4）。"""
    if aspect:
        if aspect not in ASPECT_PRESETS:
            raise ValueError(f"未知画幅: {aspect}（可选 {sorted(ASPECT_PRESETS)}）")
        width, height = ASPECT_PRESETS[aspect]
    if not width or not height:
        raise ValueError("需要 aspect 预设或显式 width/height")

    with db_session() as db:
        plan = db.get(EditPlan, plan_id)
        if plan is None:
            raise ValueError("方案不存在")
        if not plan.ir:
            raise ValueError("方案没有 Editing IR")
        ir = dict(plan.ir)
        if ir.get("version") in ("0.1", "0.2", "0.3"):  # render 规格需要 0.4
            ir["version"] = "0.4"
        ir["render"] = {"width": width, "height": height, "fill": fill}
        try:
            validate_ir(ir)
        except IRValidationError as e:
            raise ValueError(f"交付规格校验失败: {e}") from e
        plan.ir = ir
        db.commit()
    return {"width": width, "height": height, "fill": fill}


@router.put("/plans/{plan_id}/output")
def set_output(plan_id: int, req: OutputRequest, db: Session = Depends(get_db)) -> dict:
    """设置交付规格（画幅/分辨率/构图策略），重新渲染生效。"""
    if db.get(EditPlan, plan_id) is None:
        raise HTTPException(404, "方案不存在")
    try:
        spec = apply_output(plan_id, aspect=req.aspect, width=req.width,
                            height=req.height, fill=req.fill)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"plan_id": plan_id, "render": spec}


@router.delete("/plans/{plan_id}/output")
def reset_output(plan_id: int, db: Session = Depends(get_db)) -> dict:
    plan = db.get(EditPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "方案不存在")
    if plan.ir:
        ir = dict(plan.ir)
        ir["render"] = None
        plan.ir = ir
        db.commit()
    return {"plan_id": plan_id, "render": None}


class SubtitleStyleRequest(BaseModel):
    preset: str | None = None  # default/elegant/bold/minimal
    position: str | None = None
    size_ratio: float | None = None
    color: str | None = None
    outline: bool | None = None
    background: bool | None = None
    font: str | None = None


def apply_subtitle_style(plan_id: int, **fields) -> dict:
    """字幕样式核心（供 API 与对话执行器共用）：预设确定性展开 + 字段覆盖，写入全部字幕轨。"""
    from app.ir.schema import SUBTITLE_PRESETS, SubtitleStyle

    preset = fields.pop("preset", None) or "default"
    if preset not in SUBTITLE_PRESETS:
        raise ValueError(f"未知样式预设: {preset}（可选 {sorted(SUBTITLE_PRESETS)}）")
    merged = {"preset": preset, **SUBTITLE_PRESETS[preset],
              **{k: v for k, v in fields.items() if v is not None}}
    try:
        style = SubtitleStyle.model_validate(merged)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"样式校验失败: {e}") from e

    with db_session() as db:
        plan = db.get(EditPlan, plan_id)
        if plan is None:
            raise ValueError("方案不存在")
        if not plan.ir:
            raise ValueError("方案没有 Editing IR")
        ir = dict(plan.ir)
        subtitle_tracks = [t for t in ir["tracks"] if t.get("type") == "subtitle"]
        if not subtitle_tracks:
            raise ValueError("该方案没有字幕轨（生成方案时要求字幕即可）")
        if ir.get("version") in ("0.1", "0.2", "0.3", "0.4"):  # 样式需要 0.5
            ir["version"] = "0.5"
        style_dict = style.model_dump()
        ir["tracks"] = [
            {**t, "style": style_dict} if t.get("type") == "subtitle" else t
            for t in ir["tracks"]
        ]
        try:
            validate_ir(ir)
        except IRValidationError as e:
            raise ValueError(f"样式后 IR 校验失败: {e}") from e
        plan.ir = ir
        db.commit()
    return style_dict


@router.put("/plans/{plan_id}/subtitle-style")
def set_subtitle_style(plan_id: int, req: SubtitleStyleRequest, db: Session = Depends(get_db)) -> dict:
    """设置字幕样式（重新渲染生效；Resolve 时间线不支持，仅体现在成片）。"""
    if db.get(EditPlan, plan_id) is None:
        raise HTTPException(404, "方案不存在")
    try:
        style = apply_subtitle_style(plan_id, **req.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"plan_id": plan_id, "style": style}


@router.delete("/plans/{plan_id}/subtitle-style")
def reset_subtitle_style(plan_id: int, db: Session = Depends(get_db)) -> dict:
    plan = db.get(EditPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "方案不存在")
    if plan.ir:
        ir = dict(plan.ir)
        ir["tracks"] = [
            {k: v for k, v in t.items() if k != "style"} if t.get("type") == "subtitle" else t
            for t in ir["tracks"]
        ]
        plan.ir = ir
        db.commit()
    return {"plan_id": plan_id, "style": None}


class RecommendRequest(BaseModel):
    mood: str | None = None
    gain_db: float = -16.0


@router.post("/plans/{plan_id}/music/recommend")
async def recommend_and_set_music(plan_id: int, req: RecommendRequest | None = None,
                                  db: Session = Depends(get_db)) -> dict:
    """AI 从曲库推荐配乐并应用（M14）：id 白名单校验 + 确定性写 IR。"""
    from app.runtime.music import recommend_music

    req = req or RecommendRequest()
    plan = db.get(EditPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "方案不存在")
    try:
        reco = await recommend_music(req.mood, plan.plan)
        filename = apply_music(plan_id, reco["path"], gain_db=req.gain_db)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"plan_id": plan_id, "music": filename, "reason": reco["reason"]}


@router.delete("/plans/{plan_id}/music")
def remove_music(plan_id: int, db: Session = Depends(get_db)) -> dict:
    plan = db.get(EditPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "方案不存在")
    if not plan.ir:
        raise HTTPException(400, "方案没有 Editing IR")
    ir = dict(plan.ir)
    ir["sources"] = [s for s in ir["sources"] if s["id"] != MUSIC_SOURCE_ID]
    ir["tracks"] = [t for t in ir["tracks"] if t.get("type") != "audio"]
    plan.ir = ir
    db.commit()
    return {"plan_id": plan_id, "music": None}


@router.post("/plans/{plan_id}/confirm")
def confirm_plan(plan_id: int, db: Session = Depends(get_db)) -> dict:
    plan = db.get(EditPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "方案不存在")
    if plan.status != "draft":
        raise HTTPException(400, f"当前状态不可确认: {plan.status}")
    plan.status = "confirmed"
    db.commit()
    return _plan_dict(plan)
