"""对话式指挥（M12，phase2-roadmap §1）：自然语言 → 意图序列 → 串联执行。

风险控制沿用方案生成：模型只产出受限格式（reply + 白名单 actions），
参数经 Pydantic 校验，动作实现全部是确定性代码；做不了的事在 reply
中给出原因与手动指引（能力边界透明原则）。
"""

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from app.config import settings
from app.providers import get_llm_provider
from app.runtime.events import bus
from app.runtime.planning import diff_plans, generate_plan, revise_plan
from app.store.db import db_session
from app.store.models import AgentSession, Asset, EditPlan

logger = logging.getLogger("mca.chat")

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac"}


# ---------- 意图白名单与参数 schema ----------

class CreatePlanParams(BaseModel):
    goal: str


class RevisePlanParams(BaseModel):
    instruction: str
    plan_id: int | None = None


class MusicParams(BaseModel):
    path: str | None = None
    mood: str | None = None
    gain_db: float = -16.0
    plan_id: int | None = None


class PlanRefParams(BaseModel):
    plan_id: int | None = None


class RenderParams(BaseModel):
    plan_id: int | None = None
    engine: Literal["ffmpeg", "resolve"] = "ffmpeg"


class VoiceoverParams(BaseModel):
    text: str
    voice: str = "Female 1"


class LearnStyleParams(BaseModel):
    path: str
    name: str | None = None


class StyleRefParams(BaseModel):
    name: str | None = None


class ImportParams(BaseModel):
    directory: str


class SubtitleStyleParams(BaseModel):
    preset: str | None = None  # default/elegant/bold/minimal
    position: str | None = None  # bottom/top/center
    size_ratio: float | None = None
    color: str | None = None  # #RRGGBB
    outline: bool | None = None
    background: bool | None = None
    font: str | None = None  # sans/serif
    plan_id: int | None = None


class OutputParams(BaseModel):
    aspect: str | None = None  # 16:9 / 9:16 / 1:1
    width: int | None = None
    height: int | None = None
    fill: str = "blur"
    quality: Literal["draft", "final"] = "final"
    plan_id: int | None = None


INTENT_PARAMS: dict[str, type[BaseModel]] = {
    "create_plan": CreatePlanParams,
    "revise_plan": RevisePlanParams,
    "confirm_plan": PlanRefParams,
    "set_music": MusicParams,
    "remove_music": PlanRefParams,
    "render": RenderParams,
    "execute": PlanRefParams,
    "generate_voiceover": VoiceoverParams,
    "import_assets": ImportParams,
    "analyze_assets": PlanRefParams,  # 无参，占位复用
    "set_output_spec": OutputParams,
    "set_subtitle_style": SubtitleStyleParams,
    "learn_style": LearnStyleParams,
    "apply_style": StyleRefParams,
    "clear_style": StyleRefParams,
}

CHAT_SYSTEM_PROMPT = """你是 AI 视频剪辑副驾驶的调度员。根据用户消息和当前系统状态，产出给用户的回应与要执行的动作序列。

只输出 JSON：{"reply": "给用户的中文回应", "actions": [{"intent": "...", "params": {...}}]}

可用动作（intent 与 params）：
- create_plan {"goal": 创作目标}：生成新剪辑方案（需已有已分析素材）
- revise_plan {"instruction": 修订指令, "plan_id"?: 方案id}：修订方案（不给 plan_id 则用当前方案）
- confirm_plan {"plan_id"?}：确认方案
- set_music {"path"?: 音乐文件绝对路径, "mood"?: 情绪描述}：设置配乐；用户没给路径就填 mood，AI 会按情绪与方案内容从曲库推荐（"换首更安静的"也用这个）
- remove_music {"plan_id"?}：移除配乐
- render {"plan_id"?, "engine"?: "ffmpeg|resolve"}：渲染 mp4 成片（draft 自动先确认）。默认 ffmpeg（含字幕烧录）；用户要"高质量/用 Resolve 渲染"时 engine=resolve（走 Resolve 渲染队列，含时间线转场配乐，但不含字幕）
- generate_voiceover {"text": 配音文本(≤350字), "voice"?: 音色}：AI 配音生成音频素材（需 Resolve Studio 已装 AI Speech Generator）
- learn_style {"path": 参考视频绝对路径, "name"?: 风格名}：学习参考视频的剪辑节奏（学完自动应用到本会话）
- apply_style {"name": 已学风格名}：应用某个风格画像（"照XX的感觉剪"）；clear_style {} 取消应用
- execute {"plan_id"?}：生成 DaVinci Resolve 时间线（含转场与配乐入轨）
- import_assets {"directory": 素材目录绝对路径}：导入素材（视频/照片）
- analyze_assets {}：分析全部待分析素材
- set_output_spec {"aspect"?: "16:9|9:16|1:1", "fill"?: "blur|crop|pad", "quality"?: "draft|final"}：输出规格——画幅切换（竖屏/方形/横屏，fill 默认 blur 模糊背景）与渲染档位（用户要"快速出个样片/预览"时 quality=draft，成片交付用 final），重新渲染生效
- set_subtitle_style {"preset"?: "default|elegant|bold|minimal", "position"?: "bottom|top|center", "color"?: "#RRGGBB", "size_ratio"?: 0.01-0.15, "outline"?: bool, "background"?: bool, "font"?: "sans|serif"}：字幕样式（elegant 文艺宋体/bold 醒目黄字底条/minimal 简约）；只填用户提到的字段；重新渲染生效，Resolve 时间线不支持样式

规则：
1. 只能使用上述 intent。多步请求按依赖顺序排 actions（如"做个方案配上音乐渲染出来"→ create_plan → set_music → render）。
2. 做不了的事：actions 留空或不含该步，在 reply 中说明原因和手动操作步骤（见能力边界）。
3. 缺关键信息或意图不明时不要猜测执行：actions=[]，reply 追问。
4. 纯咨询（问状态/问方案内容）：actions=[]，根据下方系统状态直接回答。
5. reply 简洁自然，先说要做什么/结论，不要罗列 JSON。

能力边界（系统做不了，回复时给手动指引）：
- Resolve 时间线内的转场方向/颜色微调 → 时间线上双击转场，在检查器调整（渲染成片的转场类型是精确的）
- 把字幕直接写入 Resolve 字幕轨 → 媒体池右键 SRT → Insert Selected Subtitles to Timeline
- 关键帧动画/复杂特效 → Resolve Fusion 页手动制作
- 片段音量/声像（Resolve 内）→ Fairlight 页手动；渲染成片的配乐响度可通过 set_music 的 gain 控制
- 变速、画中画 → 后续版本支持，当前请在 Resolve 中手动调整

当前系统状态：
{state}"""


def _state_brief() -> str:
    """给调度员的系统状态简报（紧凑，供指代解析与咨询回答）。"""
    lines = []
    with db_session() as db:
        assets = db.query(Asset).all()
        analyzed = [a for a in assets if a.status == "analyzed"]
        lines.append(f"素材：共 {len(assets)} 个，已分析 {len(analyzed)} 个")
        plans = db.query(EditPlan).order_by(EditPlan.id.desc()).limit(5).all()
        if plans:
            lines.append("最近方案（新→旧）：")
            for p in plans:
                title = p.plan.get("title") or p.goal[:20]
                extra = ""
                if p.plan.get("render", {}).get("video_url"):
                    extra = "，已出片"
                lines.append(f"  - #{p.id}「{title}」状态 {p.status}{extra}")
        else:
            lines.append("还没有剪辑方案")
    music_dir = settings.data_dir / "music"
    tracks = [f.name for f in music_dir.glob("*") if f.suffix.lower() in AUDIO_EXTS] \
        if music_dir.is_dir() else []
    lines.append(f"音乐目录：{len(tracks)} 个文件" + (f"（{', '.join(tracks[:5])}）" if tracks else ""))
    from app.runtime.style import list_styles

    names = [s["content"].split("」")[0].split("「")[-1] for s in list_styles()]
    if names:
        lines.append(f"已学风格画像：{'、'.join(names[:8])}")
    return "\n".join(lines)


# ---------- 会话 ----------

def _load_session(session_id: str | None) -> dict:
    with db_session() as db:
        if session_id:
            row = db.get(AgentSession, session_id)
            if row is not None:
                return {"id": row.id, "messages": list(row.messages), "context": dict(row.context)}
        new_id = session_id or uuid.uuid4().hex[:16]
        db.add(AgentSession(id=new_id, messages=[], context={}))
        db.commit()
        return {"id": new_id, "messages": [], "context": {}}


def _save_session(session: dict) -> None:
    with db_session() as db:
        row = db.get(AgentSession, session["id"])
        row.messages = session["messages"]
        row.context = session["context"]
        db.commit()


def _append(session: dict, entry: dict) -> None:
    session["messages"] = [*session["messages"], entry]
    _save_session(session)


# ---------- 路由 ----------

async def route_message(session: dict, message: str) -> dict:
    """用户消息 → {"reply", "actions": [已校验的动作]}；白名单外/参数非法的动作标 invalid。"""
    llm = get_llm_provider()
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in session["messages"] if m.get("role") in ("user", "assistant")
    ][-8:]
    messages = [
        # 提示词含 JSON 花括号示例，不能用 str.format
        {"role": "system", "content": CHAT_SYSTEM_PROMPT.replace("{state}", _state_brief())},
        *history,
        {"role": "user", "content": message},
    ]
    resp = await llm.chat(messages, json_mode=True, temperature=0.2)
    try:
        parsed = json.loads(resp["content"])
    except json.JSONDecodeError:
        return {"reply": "抱歉，我没理解这句话，换个说法试试？", "actions": []}

    actions = []
    for raw in (parsed.get("actions") or [])[:6]:
        intent = raw.get("intent")
        if intent in ("none", None):
            continue
        if intent not in INTENT_PARAMS:
            actions.append({"intent": str(intent), "params": {}, "status": "invalid",
                            "error": "白名单外的动作，已拒绝"})
            continue
        try:
            params = INTENT_PARAMS[intent].model_validate(raw.get("params") or {})
            actions.append({"intent": intent, "params": params.model_dump(), "status": "pending"})
        except ValidationError as e:
            actions.append({"intent": intent, "params": raw.get("params") or {},
                            "status": "invalid", "error": f"参数校验失败: {e.errors()[0].get('msg')}"})
    return {"reply": parsed.get("reply") or "", "actions": actions}


# ---------- 动作实现（全部确定性代码）----------

def _resolve_plan_id(session: dict, params: dict) -> int:
    pid = params.get("plan_id") or session["context"].get("plan_id")
    if pid is None:
        with db_session() as db:
            row = db.query(EditPlan).order_by(EditPlan.id.desc()).first()
            pid = row.id if row else None
    if pid is None:
        raise ValueError("还没有任何方案，先生成一个吧")
    return pid


async def _pick_music(session: dict, params: dict, plan_id: int) -> tuple[str, str | None]:
    """返回 (音乐路径, 推荐理由)：显式 path 直用；否则按 mood/方案从曲库推荐（M14）。"""
    if params.get("path"):
        return params["path"], None
    from app.runtime.music import recommend_music

    with db_session() as db:
        row = db.get(EditPlan, plan_id)
        plan = dict(row.plan) if row else None
    reco = await recommend_music(params.get("mood"), plan)
    return reco["path"], reco["reason"]


def _session_style(session: dict) -> str | None:
    """会话激活的风格画像文本（M18）。"""
    name = session["context"].get("style")
    if not name:
        return None
    from app.runtime.style import find_style

    return find_style(name)


async def _act_create_plan(session: dict, params: dict) -> dict:
    with db_session() as db:
        row = EditPlan(goal=params["goal"], plan={}, status="generating")
        db.add(row)
        db.commit()
        plan_id = row.id
    try:
        result = await generate_plan(params["goal"], style_text=_session_style(session))
    except Exception:
        with db_session() as db:
            row = db.get(EditPlan, plan_id)
            row.status = "failed"
            db.commit()
        raise
    with db_session() as db:
        row = db.get(EditPlan, plan_id)
        row.plan, row.ir, row.status = result["plan"], result["ir"], "draft"
        db.commit()
    session["context"]["plan_id"] = plan_id
    return {"plan_id": plan_id, "title": result["plan"].get("title"),
            "clips": len(result["plan"].get("clips") or [])}


async def _act_revise_plan(session: dict, params: dict) -> dict:
    base_id = params.get("plan_id") or _resolve_plan_id(session, params)
    with db_session() as db:
        base = db.get(EditPlan, base_id)
        if base is None or not base.ir:
            raise ValueError(f"方案 #{base_id} 不存在或没有内容")
        base_plan = {k: v for k, v in base.plan.items() if k not in ("execution", "render")}
        new_row = EditPlan(goal=base.goal, plan={"revised_from": base_id,
                           "revision_instruction": params["instruction"]}, status="generating")
        db.add(new_row)
        db.commit()
        new_id = new_row.id
    try:
        result = await revise_plan(base_plan, params["instruction"],
                                   style_text=_session_style(session))
    except Exception:
        with db_session() as db:
            row = db.get(EditPlan, new_id)
            row.status = "failed"
            db.commit()
        raise
    diff = diff_plans(base_plan, result["plan"])
    with db_session() as db:
        row = db.get(EditPlan, new_id)
        row.plan = {**result["plan"], "revised_from": base_id,
                    "revision_instruction": params["instruction"], "diff": diff}
        row.ir, row.status = result["ir"], "draft"
        db.commit()
    session["context"]["plan_id"] = new_id
    # 沉淀长期偏好（M11）；失败不影响主流程
    try:
        from app.runtime.planning import extract_preferences

        await extract_preferences(params["instruction"])
    except Exception:  # noqa: BLE001
        logger.warning("偏好提取失败（已忽略）", exc_info=True)
    return {"plan_id": new_id, "revised_from": base_id, "duration": diff.get("duration")}


def _confirm_if_draft(plan_id: int) -> None:
    with db_session() as db:
        row = db.get(EditPlan, plan_id)
        if row.status == "draft":
            row.status = "confirmed"
            db.commit()


async def _act_render(session: dict, params: dict) -> dict:
    from app.api.execute import run_render

    plan_id = _resolve_plan_id(session, params)
    _confirm_if_draft(plan_id)
    with db_session() as db:
        ir_dict = dict(db.get(EditPlan, plan_id).ir or {})
    if not ir_dict:
        raise ValueError(f"方案 #{plan_id} 没有 Editing IR")
    engine = params.get("engine", "ffmpeg")
    output = await run_render(plan_id, ir_dict, engine=engine)
    return {"plan_id": plan_id, "video_url": output.get("video_url"),
            "duration": output.get("duration"), "engine": engine,
            "note": output.get("note")}


async def _act_generate_voiceover(session: dict, params: dict) -> dict:
    from app.adapters.resolve_adapter import generate_speech
    from app.api.assets import ImportRequest, import_assets

    out_dir = settings.data_dir / "voiceover"
    result = await asyncio.to_thread(generate_speech, params["text"], out_dir,
                                     voice=params.get("voice", "Female 1"))
    # 注册为素材，便于后续用作旁白/对白素材
    imported = 0
    try:
        with db_session() as db:
            r = import_assets(ImportRequest(paths=[result["audio"]]), db)
            imported = len(r["imported"])
    except Exception:  # noqa: BLE001 - 注册失败不影响音频产出
        logger.warning("配音注册素材失败", exc_info=True)
    return {"audio": result["audio"], "registered_asset": imported > 0}


async def _act_execute(session: dict, params: dict) -> dict:
    from app.api.execute import run_execution

    plan_id = _resolve_plan_id(session, params)
    _confirm_if_draft(plan_id)
    with db_session() as db:
        ir_dict = dict(db.get(EditPlan, plan_id).ir or {})
    if not ir_dict:
        raise ValueError(f"方案 #{plan_id} 没有 Editing IR")
    result = await run_execution(plan_id, ir_dict)
    return {"plan_id": plan_id, "mode": result.get("mode")}


async def _act_set_music(session: dict, params: dict) -> dict:
    from app.api.plans import apply_music

    plan_id = _resolve_plan_id(session, params)
    path, reason = await _pick_music(session, params, plan_id)
    filename = apply_music(plan_id, path, gain_db=params.get("gain_db", -16.0))
    return {"plan_id": plan_id, "music": filename, "reason": reason}


async def _act_remove_music(session: dict, params: dict) -> dict:
    plan_id = _resolve_plan_id(session, params)
    with db_session() as db:
        plan = db.get(EditPlan, plan_id)
        if not plan or not plan.ir:
            raise ValueError(f"方案 #{plan_id} 不存在或没有 IR")
        ir = dict(plan.ir)
        ir["sources"] = [s for s in ir["sources"] if s["id"] != "src_music"]
        ir["tracks"] = [t for t in ir["tracks"] if t.get("type") != "audio"]
        plan.ir = ir
        db.commit()
    return {"plan_id": plan_id, "music": None}


async def _act_confirm(session: dict, params: dict) -> dict:
    plan_id = _resolve_plan_id(session, params)
    _confirm_if_draft(plan_id)
    return {"plan_id": plan_id, "status": "confirmed"}


async def _act_import_assets(session: dict, params: dict) -> dict:
    from fastapi import HTTPException

    from app.api.assets import ImportRequest, import_assets

    with db_session() as db:
        try:
            result = import_assets(ImportRequest(directory=params["directory"]), db)
        except HTTPException as e:
            raise ValueError(e.detail) from e
    return {"imported": len(result["imported"]), "errors": len(result["errors"])}


async def _act_analyze_assets(session: dict, params: dict) -> dict:
    from app.runtime.pipeline import analyze_asset

    with db_session() as db:
        ids = [a.id for a in db.query(Asset).filter(Asset.status != "analyzed").all()]
    for aid in ids:
        await analyze_asset(aid)
    return {"analyzed": len(ids)}


async def _act_set_output(session: dict, params: dict) -> dict:
    from app.api.plans import apply_output

    plan_id = _resolve_plan_id(session, params)
    spec = apply_output(plan_id, aspect=params.get("aspect"), width=params.get("width"),
                        height=params.get("height"), fill=params.get("fill", "blur"),
                        quality=params.get("quality", "final"))
    return {"plan_id": plan_id, **spec}


async def _act_set_subtitle_style(session: dict, params: dict) -> dict:
    from app.api.plans import apply_subtitle_style

    plan_id = _resolve_plan_id(session, params)
    fields = {k: v for k, v in params.items() if k != "plan_id"}
    style = apply_subtitle_style(plan_id, **fields)
    return {"plan_id": plan_id, "preset": style["preset"], "position": style["position"],
            "color": style["color"]}


async def _act_learn_style(session: dict, params: dict) -> dict:
    from app.runtime.style import learn_style

    profile = await asyncio.to_thread(learn_style, params["path"], params.get("name"))
    session["context"]["style"] = profile["name"]  # 学完自动应用到本会话
    return {"name": profile["name"], "pace": profile["pace"],
            "avg_shot": profile["avg_shot"], "cuts_per_min": profile["cuts_per_min"],
            "applied": True}


async def _act_apply_style(session: dict, params: dict) -> dict:
    from app.runtime.style import find_style, list_styles

    name = params.get("name")
    if not name:
        raise ValueError("需要风格名；已学风格：" +
                         ("、".join(s["content"].split("」")[0].split("「")[-1]
                                    for s in list_styles()) or "（无）"))
    if find_style(name) is None:
        raise ValueError(f"没有名为「{name}」的风格画像，先用 learn_style 学习参考视频")
    session["context"]["style"] = name
    return {"applied": name}


async def _act_clear_style(session: dict, params: dict) -> dict:
    session["context"].pop("style", None)
    return {"applied": None}


ACTION_IMPL = {
    "create_plan": _act_create_plan,
    "revise_plan": _act_revise_plan,
    "confirm_plan": _act_confirm,
    "set_music": _act_set_music,
    "remove_music": _act_remove_music,
    "render": _act_render,
    "execute": _act_execute,
    "import_assets": _act_import_assets,
    "analyze_assets": _act_analyze_assets,
    "set_output_spec": _act_set_output,
    "set_subtitle_style": _act_set_subtitle_style,
    "generate_voiceover": _act_generate_voiceover,
    "learn_style": _act_learn_style,
    "apply_style": _act_apply_style,
    "clear_style": _act_clear_style,
}


async def run_actions(session_id: str, actions: list[dict]) -> None:
    """串行执行动作；失败中断后续（标 skipped）。结果追加进会话消息流。"""
    session = _load_session(session_id)
    failed = False
    for action in actions:
        entry = {"role": "action", **action}
        if action["status"] == "invalid":
            _append(session, entry)
            continue
        if failed:
            entry["status"] = "skipped"
            _append(session, entry)
            continue
        bus.publish("chat", {"session_id": session_id, "intent": action["intent"], "step": "start"})
        try:
            result = await ACTION_IMPL[action["intent"]](session, action["params"])
            entry.update(status="done", result=result)
            bus.publish("chat", {"session_id": session_id, "intent": action["intent"],
                                 "step": "done", "detail": json.dumps(result, ensure_ascii=False)[:200]})
        except Exception as e:  # noqa: BLE001 - 单动作失败中断链条并回报
            logger.exception("对话动作 %s 失败", action["intent"])
            entry.update(status="failed", error=str(e)[:300])
            bus.publish("chat", {"session_id": session_id, "intent": action["intent"],
                                 "step": "failed", "detail": str(e)[:200]})
            failed = True
        _append(session, entry)
    _save_session(session)
    bus.publish("chat", {"session_id": session_id, "step": "all_done"})


async def handle_message(session_id: str | None, message: str) -> dict:
    """对话入口：路由 + 启动后台执行。立即返回 reply 与计划动作。"""
    session = _load_session(session_id)
    _append(session, {"role": "user", "content": message})
    routed = await route_message(session, message)
    _append(session, {"role": "assistant", "content": routed["reply"]})
    if routed["actions"]:
        from app.runtime.tasks import spawn

        spawn("chat_actions", {"session_id": session["id"]},
              run_actions(session["id"], routed["actions"]))
    return {"session_id": session["id"], "reply": routed["reply"], "actions": routed["actions"]}
