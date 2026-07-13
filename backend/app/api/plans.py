"""剪辑方案 API：精彩片段推荐、方案生成/查看/确认。"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.runtime.events import bus
from app.runtime.planning import generate_plan
from app.store.db import db_session, get_db
from app.store.models import AnalysisRecord, Asset, EditPlan

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


@router.post("/plans")
async def create_plan(req: PlanRequest, db: Session = Depends(get_db)) -> dict:
    if not req.goal.strip():
        raise HTTPException(400, "创作目标不能为空")
    plan_row = EditPlan(goal=req.goal, plan={}, status="generating")
    db.add(plan_row)
    db.commit()
    plan_id = plan_row.id

    async def run():
        bus.publish("plan", {"plan_id": plan_id, "step": "generating", "detail": req.goal})
        try:
            result = await generate_plan(req.goal, req.asset_ids)
            with db_session() as s:
                row = s.get(EditPlan, plan_id)
                row.plan = result["plan"]
                row.ir = result["ir"]
                row.status = "draft"
                s.commit()
            bus.publish("plan", {"plan_id": plan_id, "step": "draft", "detail": "方案生成完成"})
        except Exception as e:  # noqa: BLE001 - 失败落状态并上报
            logger.exception("方案 %s 生成失败", plan_id)
            with db_session() as s:
                row = s.get(EditPlan, plan_id)
                row.status = "failed"
                row.plan = {"error": str(e)}
                s.commit()
            bus.publish("plan", {"plan_id": plan_id, "step": "failed", "detail": str(e)[:300]})

    asyncio.create_task(run())
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
