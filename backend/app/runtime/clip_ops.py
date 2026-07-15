"""片段级修订（M22，backlog B2）：确定性局部操作，不经 LLM 重新生成。

用户"第三个镜头换掉""开头缩短到2秒"→ 对方案 clips 列表做精确修改，
plan_to_ir 确定性重建 IR（字幕时间轴/转场钳制自动重算），产出带 diff
的新方案行（revised_from 保留回滚）。ops 按序执行，position 基于执行
到该步时的列表（1 起）。
"""

import logging

from app.ir.schema import TRANSITION_TYPES, validate_ir
from app.runtime.planning import _load_analyzed_assets, diff_plans, plan_to_ir
from app.store.db import db_session
from app.store.models import EditPlan

logger = logging.getLogger("mca.clip_ops")

CLIP_OPS = {"trim", "remove", "move", "subtitle", "transition", "replace", "speed"}


def _at(clips: list[dict], position: int) -> dict:
    if not 1 <= position <= len(clips):
        raise ValueError(f"位置 {position} 越界（当前 {len(clips)} 个片段）")
    return clips[position - 1]


def _reject_title(c: dict, op_name: str) -> None:
    if c.get("kind") == "title":
        raise ValueError(f"该位置是标题卡，不支持{op_name}（可 remove 删除或 move 移动）")


def _op_trim(clips, op, assets_by_id) -> str:
    c = _at(clips, op["position"])
    _reject_title(c, "修剪")
    asset = assets_by_id.get(c["asset_id"])
    limit = asset.duration if asset and asset.duration else None
    if op.get("start") is not None or op.get("end") is not None:
        start = float(op["start"]) if op.get("start") is not None else c["start"]
        end = float(op["end"]) if op.get("end") is not None else c["end"]
    elif op.get("duration"):
        start = c["start"]
        end = start + float(op["duration"])
    else:
        raise ValueError("trim 需要 duration 或 start/end")
    if limit:
        end = min(end, limit)
        start = max(start, 0.0)
    if end - start < 0.3:
        raise ValueError(f"片段 {op['position']} 修剪后过短（{end - start:.2f}s）")
    c["start"], c["end"] = round(start, 3), round(end, 3)
    return f"片段{op['position']} 区间调整为 {c['start']}-{c['end']}s"


def _op_remove(clips, op) -> str:
    if len(clips) <= 1:
        raise ValueError("方案只剩 1 个片段，不能再删除")
    _at(clips, op["position"])
    clips.pop(op["position"] - 1)
    return f"删除片段{op['position']}"


def _op_move(clips, op) -> str:
    to = op.get("to")
    if not to or not 1 <= to <= len(clips):
        raise ValueError(f"move 需要合法的目标位置 to（1-{len(clips)}）")
    c = _at(clips, op["position"])
    clips.pop(op["position"] - 1)
    clips.insert(to - 1, c)
    return f"片段{op['position']} 移到位置 {to}"


def _op_subtitle(clips, op) -> str:
    c = _at(clips, op["position"])
    _reject_title(c, "改字幕（标题文字请删了重加）")
    text = (op.get("text") or "").strip()
    c["subtitle"] = text or None
    return f"片段{op['position']} 字幕改为「{text or '（清除）'}」"


def _op_speed(clips, op) -> str:
    c = _at(clips, op["position"])
    _reject_title(c, "变速")
    try:
        speed = float(op.get("speed"))
    except (TypeError, ValueError) as e:
        raise ValueError("speed 需要数值（>1 快放、<1 慢动作）") from e
    speed = min(max(speed, 0.25), 4.0)
    if abs(speed - 1.0) < 0.01:  # 恢复原速
        c.pop("speed", None)
        return f"片段{op['position']} 恢复原速"
    c["speed"] = round(speed, 3)
    label = "慢动作" if speed < 1 else "快放"
    return f"片段{op['position']} {label} {speed}x"


def _op_transition(clips, op) -> str:
    if op["position"] == 1:
        raise ValueError("首个片段没有转入转场")
    c = _at(clips, op["position"])
    t_type = op.get("type")
    if t_type in (None, "", "none", "cut"):
        c["transition"] = None
        return f"片段{op['position']} 改为硬切"
    if t_type not in TRANSITION_TYPES:
        raise ValueError(f"未知转场类型: {t_type}（可选 {sorted(TRANSITION_TYPES)} 或 none）")
    c["transition"] = {"type": t_type, "duration": float(op.get("t_duration") or 0.5)}
    return f"片段{op['position']} 转场改为 {t_type}"


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return min(a_end, b_end) - max(a_start, b_start) > 0.2


def _op_replace(clips, op, analyzed) -> str:
    c = _at(clips, op["position"])
    _reject_title(c, "替换素材")
    orig_len = c["end"] - c["start"]
    hint = (op.get("hint") or "").strip()

    candidates = []
    for item in analyzed:
        asset = item["asset"]
        if op.get("asset_id") and asset.id != op["asset_id"]:
            continue
        for h in item["summary"].get("highlights") or []:
            # 排除方案中已用的重叠区间（含被替换片段自身）
            used = any(k["asset_id"] == asset.id and _overlaps(k["start"], k["end"], h["start"], h["end"])
                       for k in clips)
            if used:
                continue
            text = f"{asset.filename} {h.get('category', '')} {h.get('reason', '')}"
            if hint and hint not in text:
                continue
            candidates.append((h.get("score", 0), asset.id, h))
    if not candidates:
        raise ValueError(
            f"没有可用的替换片段{'（提示词「' + hint + '」无匹配）' if hint else ''}——"
            "可尝试给 hint 换个说法，或用 revise_plan 让模型重排"
        )
    candidates.sort(key=lambda x: (-x[0], x[1]))
    _, aid, h = candidates[0]
    start = h["start"]
    end = min(start + orig_len, h["end"])  # 尽量保持原时长
    if end - start < 0.5:
        end = h["end"]
    c["asset_id"], c["start"], c["end"] = aid, round(start, 3), round(end, 3)
    c["reason"] = h.get("reason", "替换片段")
    return f"片段{op['position']} 替换为素材#{aid} {start:.1f}-{end:.1f}s"


def apply_clip_ops(base_plan_id: int, ops: list[dict]) -> dict:
    """按序执行局部操作 → 新方案行（draft）。返回 {plan_id, revised_from, changes, diff}。"""
    if not ops:
        raise ValueError("没有任何操作")
    with db_session() as db:
        base = db.get(EditPlan, base_plan_id)
        if base is None or not base.plan.get("clips"):
            raise ValueError(f"方案 #{base_plan_id} 不存在或没有内容")
        # 派生字段（执行/渲染/文案/差异）不随局部修订继承
        base_plan = {k: v for k, v in dict(base.plan).items()
                     if k not in ("execution", "render", "diff", "publish",
                                  "revised_from", "revision_instruction")}
        base_goal = base.goal

    analyzed = _load_analyzed_assets(None)
    assets_by_id = {item["asset"].id: item["asset"] for item in analyzed}
    clips = [dict(c) for c in base_plan["clips"]]

    changes = []
    for op in ops:
        kind = op.get("op")
        if kind == "trim":
            changes.append(_op_trim(clips, op, assets_by_id))
        elif kind == "remove":
            changes.append(_op_remove(clips, op))
        elif kind == "move":
            changes.append(_op_move(clips, op))
        elif kind == "subtitle":
            changes.append(_op_subtitle(clips, op))
        elif kind == "transition":
            changes.append(_op_transition(clips, op))
        elif kind == "speed":
            changes.append(_op_speed(clips, op))
        elif kind == "replace":
            changes.append(_op_replace(clips, op, analyzed))
        else:
            raise ValueError(f"未知操作: {kind}（可选 {sorted(CLIP_OPS)}）")

    new_plan = {**base_plan, "clips": clips}
    ir = plan_to_ir(new_plan, analyzed, base_plan.get("title") or "局部修订")
    validate_ir(ir)  # 全量校验：局部操作不得破坏 IR 合法性
    diff = diff_plans(base_plan, new_plan)
    instruction = "；".join(changes)

    with db_session() as db:
        row = EditPlan(
            goal=base_goal,
            plan={**new_plan, "revised_from": base_plan_id,
                  "revision_instruction": f"[精确修改] {instruction}", "diff": diff},
            ir=ir,
            status="draft",
        )
        db.add(row)
        db.commit()
        new_id = row.id
    return {"plan_id": new_id, "revised_from": base_plan_id,
            "changes": changes, "duration": diff.get("duration")}


def add_title_card(base_plan_id: int, *, text: str, subtitle: str = "",
                   position: str = "intro", duration: float = 2.5,
                   background: str = "#000000", color: str = "#FFFFFF") -> dict:
    """加片头/片尾标题卡（M26）：确定性插入 title 条目 → 重建 IR → 新方案行。"""
    if not str(text).strip():
        raise ValueError("标题文字不能为空")
    if position not in ("intro", "outro"):
        raise ValueError(f"位置只能是 intro（片头）或 outro（片尾），收到 {position}")
    with db_session() as db:
        base = db.get(EditPlan, base_plan_id)
        if base is None or not base.plan.get("clips"):
            raise ValueError(f"方案 #{base_plan_id} 不存在或没有内容")
        base_plan = {k: v for k, v in dict(base.plan).items()
                     if k not in ("execution", "render", "diff", "publish",
                                  "revised_from", "revision_instruction")}
        base_goal = base.goal

    title_entry = {
        "kind": "title", "text": str(text).strip(), "subtitle": str(subtitle or "").strip(),
        "position": position, "duration": round(min(max(float(duration), 1.0), 10.0), 2),
        "background": background, "color": color, "subtitle_none": None,
    }
    title_entry.pop("subtitle_none", None)
    clips = [dict(c) for c in base_plan["clips"]]
    if position == "intro":
        clips.insert(0, title_entry)
        # 原第一段若带转入转场，移到标题卡上更自然（首段不能有转场）
        if len(clips) > 1 and clips[1].get("transition"):
            clips[1].pop("transition", None)
    else:
        clips.append(title_entry)

    analyzed = _load_analyzed_assets(None)
    new_plan = {**base_plan, "clips": clips}
    ir = plan_to_ir(new_plan, analyzed, base_plan.get("title") or "加标题卡")
    validate_ir(ir)
    label = "片头" if position == "intro" else "片尾"
    change = f"新增{label}标题卡「{text}」{('/' + subtitle) if subtitle else ''}（{duration}s）"
    with db_session() as db:
        row = EditPlan(
            goal=base_goal,
            plan={**new_plan, "revised_from": base_plan_id,
                  "revision_instruction": f"[精确修改] {change}"},
            ir=ir, status="draft",
        )
        db.add(row)
        db.commit()
        new_id = row.id
    return {"plan_id": new_id, "revised_from": base_plan_id, "change": change}
