"""主体感知裁切（M28，backlog B21）：竖屏/方形输出时视觉定位主体水平位置。

M23 自检真实反馈：横素材竖屏化用 blur 填充时有效画面占比不足。改用
"填满画面 + 裁切窗口跟随主体"——每段素材抽帧问视觉模型主体横向焦点，
写入 clip.crop_focus，输出 fill 切为 crop。标题卡等无素材片段跳过（居中）。
"""

import asyncio
import json
import logging
import subprocess
import tempfile
from pathlib import Path

from app.ir.schema import validate_ir
from app.providers import get_vision_provider
from app.runtime.planning import _load_analyzed_assets, carry_ir_settings, diff_plans, plan_to_ir
from app.store.db import db_session
from app.store.models import Asset, EditPlan

logger = logging.getLogger("mca.framing")

FOCUS_PROMPT = """这是一段视频的画面。裁成竖屏时需要横向裁切。请判断画面主要主体（人物/建筑/焦点）
横向大致位于画面的哪个位置，返回 0（最左）到 1（最右）之间的一个数值；主体在中间返回 0.5。
只输出 JSON：{"focus_x": 数值}"""


def _sample_frame(path: str, ts: float, out: Path) -> bool:
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", str(round(ts, 2)), "-i", path,
         "-frames:v", "1", "-vf", "scale=480:-2", str(out)],
        capture_output=True, timeout=60, check=False,
    )
    return proc.returncode == 0 and out.is_file()


async def _focus_of(src_path: str, ts: float, frame: Path) -> float:
    if not _sample_frame(src_path, ts, frame):
        return 0.5
    try:
        resp = await get_vision_provider().analyze_images([str(frame)], FOCUS_PROMPT, json_mode=True)
        fx = float(json.loads(resp).get("focus_x"))
        return min(max(fx, 0.0), 1.0)
    except Exception as e:  # noqa: BLE001 - 单帧失败退居中
        logger.warning("主体焦点识别失败，退居中: %s", e)
        return 0.5


async def smart_crop(plan_id: int) -> dict:
    """为竖屏/方形输出计算每段主体焦点 → 写 crop_focus + fill=crop → 新方案。"""
    with db_session() as db:
        base = db.get(EditPlan, plan_id)
        if base is None or not base.plan.get("clips"):
            raise ValueError(f"方案 #{plan_id} 不存在或没有内容")
        base_plan = {k: v for k, v in dict(base.plan).items()
                     if k not in ("execution", "render", "diff", "publish",
                                  "revised_from", "revision_instruction")}
        base_goal = base.goal
        base_ir = dict(base.ir) if base.ir else None

    render = (base_ir or {}).get("render")
    if not render:
        raise ValueError("请先设置竖屏/方形输出（如「改成竖屏」）再做智能裁切")

    with db_session() as db:
        asset_paths = {a.id: a.path for a in db.query(Asset).all()}

    clips = [dict(c) for c in base_plan["clips"]]
    tmp = Path(tempfile.mkdtemp(prefix="framing-"))
    # 并发抽帧+识别（跳过标题卡）
    tasks = {}
    for i, c in enumerate(clips):
        if c.get("kind") == "title" or c.get("asset_id") not in asset_paths:
            continue
        mid = (float(c["start"]) + float(c["end"])) / 2
        tasks[i] = _focus_of(asset_paths[c["asset_id"]], mid, tmp / f"f{i}.jpg")
    focuses = dict(zip(tasks.keys(), await asyncio.gather(*tasks.values()))) if tasks else {}

    n_off = 0
    for i, c in enumerate(clips):
        fx = focuses.get(i, 0.5)
        c["crop_focus"] = round(fx, 3)
        if abs(fx - 0.5) > 0.08:
            n_off += 1

    render = {**render, "fill": "crop"}  # 智能裁切走填满式 crop
    new_plan = {**base_plan, "clips": clips}
    ir = carry_ir_settings(plan_to_ir(new_plan, analyzed=_load_analyzed_assets(None),
                                      project_name=base_plan.get("title") or "智能裁切"),
                           {**(base_ir or {}), "render": render})
    validate_ir(ir)
    diff = diff_plans(base_plan, new_plan)
    change = f"竖屏智能裁切：{len(tasks)} 段定位主体，其中 {n_off} 段偏离居中"
    with db_session() as db:
        row = EditPlan(goal=base_goal,
                       plan={**new_plan, "revised_from": plan_id,
                             "revision_instruction": f"[精确修改] {change}"},
                       ir=ir, status="draft")
        db.add(row)
        db.commit()
        new_id = row.id
    return {"plan_id": new_id, "revised_from": plan_id, "change": change,
            "focused": len(tasks), "off_center": n_off}
