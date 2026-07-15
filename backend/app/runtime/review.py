"""成片自检（M23，backlog B1）：AI 看自己的片子——确定性检查 + 视觉回喂。

从"执行指令"到"对结果负责"：渲染产物做四项确定性检查（时长偏差/黑场/
响度异常/重复素材），再均匀抽帧回喂视觉模型审片（受限格式）；报告存
plan.review，建议措辞可被 edit_clips/revise 直接执行。
"""

import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path

from app.providers import get_vision_provider
from app.store.db import db_session
from app.store.models import EditPlan
from app.tools.media import probe_media

logger = logging.getLogger("mca.review")


def _run_ffmpeg_filter(video: str, vf_or_af: list[str]) -> str:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", video, *vf_or_af, "-f", "null", "-"],
        capture_output=True, text=True, timeout=300, check=False,
    )
    return proc.stderr


def check_duration(plan: dict, actual: float) -> dict | None:
    target = plan.get("target_duration")
    if not target:
        return None
    deviation = abs(actual - float(target)) / float(target)
    if deviation <= 0.2:
        return None
    return {"type": "duration", "severity": "medium",
            "detail": f"成片 {actual:.1f}s，与目标 {target}s 偏差 {deviation:.0%}",
            "suggestion": f"可以说「压缩到{target}秒」或「延长到{target}秒」"}


def check_black_frames(video: str) -> dict | None:
    stderr = _run_ffmpeg_filter(video, ["-vf", "blackdetect=d=0.4:pix_th=0.10", "-an"])
    spans = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", stderr)
    if not spans:
        return None
    desc = "、".join(f"{float(a):.1f}-{float(b):.1f}s" for a, b in spans[:3])
    return {"type": "black_frames", "severity": "high",
            "detail": f"检测到 {len(spans)} 处黑场（{desc}）",
            "suggestion": "黑场处的片段可能取段过暗或素材异常，可说「第N段换掉」"}


def check_audio_levels(video: str) -> dict | None:
    stderr = _run_ffmpeg_filter(video, ["-af", "volumedetect", "-vn"])
    mean = re.search(r"mean_volume: ([-\d.]+) dB", stderr)
    peak = re.search(r"max_volume: ([-\d.]+) dB", stderr)
    if mean and float(mean.group(1)) < -50:
        return {"type": "audio", "severity": "medium",
                "detail": f"整体音频近乎静音（均值 {mean.group(1)}dB）",
                "suggestion": "可以说「配上音乐」挑一首曲库配乐"}
    if peak and float(peak.group(1)) >= -0.1:
        return {"type": "audio", "severity": "low",
                "detail": f"音频峰值触顶（{peak.group(1)}dB），可能削波",
                "suggestion": "可降低配乐音量（重设配乐 gain）"}
    return None


def check_repeated_clips(plan: dict) -> dict | None:
    clips = plan.get("clips") or []
    seen: list[tuple] = []
    repeats = []
    for i, c in enumerate(clips, 1):
        for j, k in seen:
            if k["asset_id"] == c["asset_id"] and \
                    min(k["end"], c["end"]) - max(k["start"], c["start"]) > 0.5:
                repeats.append(f"片段{j}与片段{i}")
        seen.append((i, c))
    if not repeats:
        return None
    return {"type": "repeated", "severity": "medium",
            "detail": "画面重复使用：" + "、".join(repeats[:3]),
            "suggestion": "可以说「第N段换掉」用未使用的素材替换"}


VISION_REVIEW_PROMPT = """你是成片质检员。以下是同一条短视频按时间顺序均匀抽取的画面帧。请检查：
1. 画质问题（模糊/过曝/过暗/大面积黑边）
2. 字幕问题（被裁切、与画面冲突、可读性差）
3. 观感问题（相邻画面雷同、构图明显失衡）
只输出 JSON：{"issues": [{"type": "quality|subtitle|visual", "severity": "low|medium|high", "detail": "问题描述（注明大约第几帧）", "suggestion": "一句话修改建议"}], "summary": "一句话总体评价"}
没有问题时 issues 为空数组，不要吹毛求疵。"""


async def _vision_review(video: str, duration: float, n_frames: int = 5) -> tuple[list[dict], str]:
    tmp = Path(tempfile.mkdtemp(prefix="review-"))
    frames = []
    for i in range(n_frames):
        ts = duration * (i + 0.5) / n_frames
        out = tmp / f"f{i}.jpg"
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", str(round(ts, 2)), "-i", video,
             "-frames:v", "1", "-vf", "scale=640:-2", str(out)],
            capture_output=True, timeout=60, check=False,
        )
        if proc.returncode == 0 and out.is_file():
            frames.append(str(out))
    if not frames:
        return [], "抽帧失败，跳过视觉自检"
    try:
        resp = await get_vision_provider().analyze_images(
            frames, VISION_REVIEW_PROMPT, json_mode=True
        )
        parsed = json.loads(resp)
        issues = []
        for it in (parsed.get("issues") or [])[:6]:
            if it.get("detail"):
                issues.append({"type": f"vision_{it.get('type', 'quality')}",
                               "severity": it.get("severity", "low"),
                               "detail": str(it["detail"])[:150],
                               "suggestion": str(it.get("suggestion") or "")[:100]})
        return issues, str(parsed.get("summary") or "")[:150]
    except Exception as e:  # noqa: BLE001 - 视觉自检失败不阻断确定性报告
        logger.warning("视觉自检失败: %s", e)
        return [], f"视觉自检不可用（{str(e)[:80]}）"


async def review_render(plan_id: int) -> dict:
    """成片自检：确定性检查 + 视觉回喂 → 报告存 plan.review。"""
    with db_session() as db:
        row = db.get(EditPlan, plan_id)
        if row is None:
            raise ValueError("方案不存在")
        render = row.plan.get("render") or {}
        plan = dict(row.plan)
    video = render.get("video")
    if not video or not Path(video).is_file():
        raise ValueError("该方案还没有渲染成片，先渲染再检查")

    duration = probe_media(video)["duration"]
    issues = [c for c in (
        check_duration(plan, duration),
        check_black_frames(video),
        check_audio_levels(video),
        check_repeated_clips(plan),
    ) if c]
    vision_issues, vision_summary = await _vision_review(video, duration)
    issues += vision_issues

    high = sum(1 for i in issues if i["severity"] == "high")
    verdict = "pass" if not issues else ("needs_improvement" if high == 0 else "has_problems")
    review = {
        "verdict": verdict,
        "issues": issues,
        "summary": vision_summary or ("未发现明显问题" if not issues else f"发现 {len(issues)} 个可改进点"),
        "video": Path(video).name,
        "duration": round(duration, 1),
    }
    with db_session() as db:
        row = db.get(EditPlan, plan_id)
        row.plan = {**row.plan, "review": review}
        db.commit()
    return review
