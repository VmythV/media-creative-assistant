"""后台任务持久化（M19，phase2-roadmap §9）：登记 + 重启恢复。

所有 asyncio.create_task 统一经 spawn() 包装落库；启动时把遗留 running
任务标为 interrupted，按 kind 策略恢复（幂等/成本可控的自动重跑，
对话动作链标记中断由用户重发）。
"""

import asyncio
import logging
from collections.abc import Coroutine

from app.store.db import db_session
from app.store.models import AgentSession, Asset, BackgroundTask, EditPlan

logger = logging.getLogger("mca.tasks")


def _update(task_id: int, **fields) -> None:
    with db_session() as db:
        row = db.get(BackgroundTask, task_id)
        if row is not None:
            for k, v in fields.items():
                setattr(row, k, v)
            db.commit()


def spawn(kind: str, payload: dict, coro: Coroutine) -> int:
    """登记并启动后台任务。返回任务 id。"""
    with db_session() as db:
        row = BackgroundTask(kind=kind, payload=payload, status="running")
        db.add(row)
        db.commit()
        task_id = row.id

    async def _wrap():
        try:
            await coro
            _update(task_id, status="done")
        except Exception as e:  # noqa: BLE001 - 任务内部已各自处理副作用，这里只记账
            logger.exception("后台任务 %s#%s 失败", kind, task_id)
            _update(task_id, status="failed", detail=str(e)[:300])

    asyncio.create_task(_wrap())
    return task_id


def list_tasks(limit: int = 50) -> list[dict]:
    with db_session() as db:
        rows = db.query(BackgroundTask).order_by(BackgroundTask.id.desc()).limit(limit).all()
        return [
            {"id": r.id, "kind": r.kind, "status": r.status, "payload": dict(r.payload),
             "detail": r.detail,
             "created_at": r.created_at.isoformat() if r.created_at else None,
             "updated_at": r.updated_at.isoformat() if r.updated_at else None}
            for r in rows
        ]


def _mark_chat_interrupted(session_id: str) -> None:
    with db_session() as db:
        row = db.get(AgentSession, session_id)
        if row is None:
            return
        row.messages = [
            {**m, "status": "interrupted", "error": "服务重启中断，请重新发起"}
            if m.get("role") == "action" and m.get("status") == "pending" else m
            for m in row.messages
        ]
        db.commit()


def _plan_ir(plan_id: int) -> dict | None:
    with db_session() as db:
        row = db.get(EditPlan, plan_id)
        return dict(row.ir) if row and row.ir else None


def _redispatch(kind: str, payload: dict) -> str | None:
    """按 kind 恢复一个中断任务。返回描述；不可恢复返回 None。"""
    if kind == "analyze":
        from app.runtime.pipeline import analyze_asset

        aid = payload["asset_id"]
        with db_session() as db:
            asset = db.get(Asset, aid)
            if asset is None or asset.status == "analyzed":
                return None
        spawn("analyze", payload, analyze_asset(aid))
        return f"重跑素材分析 #{aid}"

    if kind == "analyze_batch":
        from app.api.assets import analyze_batch

        with db_session() as db:
            remaining = [a.id for a in db.query(Asset)
                         .filter(Asset.id.in_(payload.get("asset_ids", [])),
                                 Asset.status != "analyzed").all()]
        if not remaining:
            return None
        spawn("analyze_batch", {"asset_ids": remaining}, analyze_batch(remaining))
        return f"重跑批量分析（剩余 {len(remaining)} 个）"

    if kind == "plan_generate":
        from app.api.plans import run_generation

        pid = payload["plan_id"]
        with db_session() as db:
            row = db.get(EditPlan, pid)
            if row is None or row.status != "generating":
                return None
        spawn("plan_generate", payload, run_generation(pid, payload["goal"], payload.get("asset_ids")))
        return f"重跑方案生成 #{pid}"

    if kind == "plan_revise":
        from app.api.plans import run_revision

        pid = payload["plan_id"]
        with db_session() as db:
            row = db.get(EditPlan, pid)
            if row is None or row.status != "generating":
                return None
        spawn("plan_revise", payload,
              run_revision(pid, payload["base_plan_id"], payload["instruction"],
                           payload.get("asset_ids")))
        return f"重跑方案修订 #{pid}"

    if kind == "execute":
        from app.api.execute import run_execution

        ir = _plan_ir(payload["plan_id"])
        if ir is None:
            return None
        spawn("execute", payload,
              run_execution(payload["plan_id"], ir, force_fallback=payload.get("force_fallback", False)))
        return f"重跑执行 #{payload['plan_id']}"

    if kind == "render":
        from app.api.execute import _render_safely

        ir = _plan_ir(payload["plan_id"])
        if ir is None:
            return None
        spawn("render", payload,
              _render_safely(payload["plan_id"], ir, payload.get("engine", "ffmpeg")))
        return f"重跑渲染 #{payload['plan_id']}"

    if kind == "chat_actions":
        _mark_chat_interrupted(payload["session_id"])
        return f"对话动作链标记中断（会话 {payload['session_id']}）"

    return None


async def recover_interrupted() -> list[str]:
    """启动恢复：running → interrupted，按 kind 重派或标记。返回恢复描述列表。"""
    with db_session() as db:
        stale = db.query(BackgroundTask).filter_by(status="running").all()
        pending = [(r.id, r.kind, dict(r.payload)) for r in stale]
        for r in stale:
            r.status = "interrupted"
        db.commit()

    recovered = []
    for task_id, kind, payload in pending:
        try:
            desc = _redispatch(kind, payload)
        except Exception:  # noqa: BLE001 - 单任务恢复失败不阻断启动
            logger.exception("任务 %s#%s 恢复失败", kind, task_id)
            continue
        if desc:
            _update(task_id, status="recovered", detail=desc)
            recovered.append(desc)
    return recovered
