"""执行 API：确认的方案 → Resolve 时间线；Resolve 不可用时自动降级。"""

import asyncio
import json
import logging
from pathlib import Path

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

    asyncio.create_task(run_execution(plan_id, ir_dict, force_fallback=req.force_fallback))
    return {"plan_id": plan_id, "status": "executing"}


async def run_execution(plan_id: int, ir_dict: dict, *, force_fallback: bool = False) -> dict:
    """执行核心（可等待）：IR → Resolve 时间线或降级产物；校验失败抛异常。

    供执行 API（后台任务）与对话执行器（串联等待，M12）共用。
    """

    def emit(step: str, detail: str = "") -> None:
        bus.publish("execute", {"plan_id": plan_id, "step": step, "detail": detail})

    emit("start")
    try:
        validate_ir(ir_dict)  # 执行前校验（含素材文件存在性）
    except Exception as e:  # noqa: BLE001
        _finish(plan_id, "failed")
        emit("failed", f"IR 校验失败: {e}")
        raise

    artifacts = _write_artifacts(plan_id, ir_dict)
    result: dict = {"mode": "fallback", "artifacts": artifacts}

    if not force_fallback and _resolve_available():
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
    return result


class RenderRequest(BaseModel):
    engine: str = "ffmpeg"  # ffmpeg / resolve（M16）


@router.post("/plans/{plan_id}/render")
async def render_plan(plan_id: int, req: RenderRequest | None = None,
                      db: Session = Depends(get_db)) -> dict:
    """确认/已执行的方案 → mp4 成片（设计文档 §9.2；engine=resolve 走 Resolve 渲染队列）。"""
    req = req or RenderRequest()
    if req.engine not in ("ffmpeg", "resolve"):
        raise HTTPException(400, f"未知渲染引擎: {req.engine}")
    plan = db.get(EditPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "方案不存在")
    if plan.status not in ("confirmed", "executed"):
        raise HTTPException(400, f"当前状态不可渲染: {plan.status}")
    if not plan.ir:
        raise HTTPException(400, "方案没有 Editing IR")
    ir_dict = dict(plan.ir)

    asyncio.create_task(_render_safely(plan_id, ir_dict, req.engine))
    return {"plan_id": plan_id, "status": "rendering", "engine": req.engine}


async def _render_safely(plan_id: int, ir_dict: dict, engine: str = "ffmpeg") -> None:
    try:
        await run_render(plan_id, ir_dict, engine=engine)
    except Exception:  # noqa: BLE001 - run_render 已落库并上报 SSE
        pass


async def run_render(plan_id: int, ir_dict: dict, *, engine: str = "ffmpeg") -> dict:
    """渲染核心（可等待）：IR → mp4 + 预览地址，结果写回方案；失败落库后抛异常。

    供渲染 API（后台任务）与对话执行器（串联等待，M12）共用。
    engine=resolve（M16）：走 Resolve 渲染队列（含时间线转场/配乐；字幕不在
    时间线上，成片不含字幕——需要字幕请用默认 ffmpeg 引擎）。
    """

    def emit(step: str, detail: str = "") -> None:
        bus.publish("render", {"plan_id": plan_id, "step": step, "detail": detail})

    emit("start", engine)
    try:
        out_dir = settings.data_dir / "output" / f"plan_{plan_id}"
        if engine == "resolve":
            from app.adapters.resolve_adapter import render_with_resolve

            parsed = validate_ir(ir_dict)
            raw = await asyncio.to_thread(
                render_with_resolve, parsed, out_dir,
                progress=lambda s, d: emit(f"resolve:{s}", d),
            )
            output = {"video": raw["video"], "duration": raw["duration"],
                      "engine": "resolve", "subtitles_burned": False,
                      "clips": len(parsed.tracks[0].items) if parsed.tracks else 0,
                      "note": "Resolve 渲染成片不含字幕（字幕请用默认引擎或在 Resolve 内上轨后手动渲染）"}
        else:
            result = await registry.execute(
                "render_video", {"ir": ir_dict, "output_dir": str(out_dir)}
            )
            if result.error:
                raise RuntimeError(result.error)
            output = {**dict(result.output), "engine": "ffmpeg"}
        # 浏览器内预览地址（main.py 挂载 /output → data/output）
        output["video_url"] = f"/output/plan_{plan_id}/{Path(output['video']).name}"
        with db_session() as s:
            row = s.get(EditPlan, plan_id)
            row.plan = {**row.plan, "render": output}
            s.commit()
        emit("done", output["video"])
        return output
    except Exception as e:  # noqa: BLE001 - 失败上报 SSE，不改方案状态
        logger.exception("方案 %s 渲染失败", plan_id)
        with db_session() as s:
            row = s.get(EditPlan, plan_id)
            row.plan = {**row.plan, "render": {"error": str(e)[:300]}}
            s.commit()
        emit("failed", str(e)[:300])
        raise


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
