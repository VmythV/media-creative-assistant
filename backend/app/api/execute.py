"""执行 API：确认的方案 → Resolve 时间线；Resolve 不可用时自动降级。"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.capability.discovery import discover_capabilities
from app.config import settings
from app.ir import exporters
from app.ir.schema import validate_ir
from app.runtime.events import bus
from app.store.db import db_session, get_db
from app.store.models import EditPlan, TaskLog
from app.tools.registry import registry

logger = logging.getLogger("mca.execute")
router = APIRouter(tags=["execute"])


class ExecuteRequest(BaseModel):
    force_fallback: bool = False  # 调试/演示：强制走降级路径


def _resolve_available() -> bool:
    caps = discover_capabilities()["capabilities"]
    return any(c["name"] == "davinci" and c["available"] for c in caps)


def _write_artifacts(plan_id: int, ir_dict: dict) -> dict:
    """降级/存档产物：IR JSON + 剪辑清单 + FCPXML + SRT。"""
    out_dir = settings.data_dir / "output" / f"plan_{plan_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    parsed = validate_ir(ir_dict, check_paths=False)

    artifacts = {}
    (out_dir / "editing_ir.json").write_text(
        json.dumps(ir_dict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    artifacts["ir"] = str(out_dir / "editing_ir.json")
    (out_dir / "edit_list.md").write_text(exporters.export_edit_list(parsed), encoding="utf-8")
    artifacts["edit_list"] = str(out_dir / "edit_list.md")
    (out_dir / "timeline.fcpxml").write_text(exporters.export_fcpxml(parsed), encoding="utf-8")
    artifacts["fcpxml"] = str(out_dir / "timeline.fcpxml")
    srt = exporters.export_srt(parsed)
    if srt:
        (out_dir / "subtitles.srt").write_text(srt, encoding="utf-8")
        artifacts["srt"] = str(out_dir / "subtitles.srt")
    return artifacts


@router.post("/plans/{plan_id}/execute")
async def execute_plan(plan_id: int, req: ExecuteRequest | None = None, db: Session = Depends(get_db)) -> dict:
    req = req or ExecuteRequest()
    plan = db.get(EditPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "方案不存在")
    # executed 允许重执行：Resolve 项目名带时间戳，重复执行无副作用（支持回滚旧版）
    if plan.status not in ("draft", "confirmed", "executed"):
        raise HTTPException(400, f"当前状态不可执行: {plan.status}")
    if not plan.ir:
        raise HTTPException(400, "方案没有 Editing IR")
    ir_dict = dict(plan.ir)

    def emit(step: str, detail: str = "") -> None:
        bus.publish("execute", {"plan_id": plan_id, "step": step, "detail": detail})

    async def run():
        emit("start")
        try:
            validate_ir(ir_dict)  # 执行前校验（含素材文件存在性）
        except Exception as e:  # noqa: BLE001
            _finish(plan_id, "failed")
            emit("failed", f"IR 校验失败: {e}")
            return

        artifacts = _write_artifacts(plan_id, ir_dict)
        result: dict = {"mode": "fallback", "artifacts": artifacts}

        if not req.force_fallback and _resolve_available():
            try:
                from app.adapters.resolve_adapter import execute_ir

                parsed = validate_ir(ir_dict)
                summary = await asyncio.to_thread(
                    execute_ir, parsed, progress=lambda s, d: emit(f"resolve:{s}", d)
                )
                result = {"mode": "resolve", "resolve": summary, "artifacts": artifacts}
            except Exception as e:  # noqa: BLE001 - Resolve 失败自动降级
                logger.exception("Resolve 执行失败，降级输出产物")
                emit("degraded", f"Resolve 执行失败，已降级输出产物: {str(e)[:200]}")
        else:
            emit("degraded", "Resolve 不可用或被跳过，输出 IR / 剪辑清单 / FCPXML")

        _finish(plan_id, "executed", result)
        emit("done", f"执行完成（{result['mode']}）")

    asyncio.create_task(run())
    return {"plan_id": plan_id, "status": "executing"}


@router.post("/plans/{plan_id}/render")
async def render_plan(plan_id: int, db: Session = Depends(get_db)) -> dict:
    """确认/已执行的方案 → mp4 成片（设计文档 §9.2）。"""
    plan = db.get(EditPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "方案不存在")
    if plan.status not in ("confirmed", "executed"):
        raise HTTPException(400, f"当前状态不可渲染: {plan.status}")
    if not plan.ir:
        raise HTTPException(400, "方案没有 Editing IR")
    ir_dict = dict(plan.ir)

    def emit(step: str, detail: str = "") -> None:
        bus.publish("render", {"plan_id": plan_id, "step": step, "detail": detail})

    async def run():
        emit("start")
        try:
            out_dir = settings.data_dir / "output" / f"plan_{plan_id}"
            result = await registry.execute(
                "render_video", {"ir": ir_dict, "output_dir": str(out_dir)}
            )
            if result.error:
                raise RuntimeError(result.error)
            with db_session() as s:
                row = s.get(EditPlan, plan_id)
                row.plan = {**row.plan, "render": result.output}
                s.commit()
            emit("done", result.output["video"])
        except Exception as e:  # noqa: BLE001 - 失败上报 SSE，不改方案状态
            logger.exception("方案 %s 渲染失败", plan_id)
            with db_session() as s:
                row = s.get(EditPlan, plan_id)
                row.plan = {**row.plan, "render": {"error": str(e)[:300]}}
                s.commit()
            emit("failed", str(e)[:300])

    asyncio.create_task(run())
    return {"plan_id": plan_id, "status": "rendering"}


def _finish(plan_id: int, status: str, result: dict | None = None) -> None:
    with db_session() as s:
        row = s.get(EditPlan, plan_id)
        row.status = status
        if result is not None:
            row.plan = {**row.plan, "execution": result}
        s.commit()


@router.get("/logs")
def recent_logs(limit: int = 100, db: Session = Depends(get_db)) -> dict:
    logs = db.query(TaskLog).order_by(TaskLog.id.desc()).limit(min(limit, 500)).all()
    return {
        "logs": [
            {
                "id": lg.id,
                "task_id": lg.task_id,
                "tool": lg.tool,
                "input": lg.input_summary,
                "output": lg.output_summary,
                "error": lg.error,
                "ts": lg.created_at.isoformat() if lg.created_at else None,
            }
            for lg in logs
        ]
    }
