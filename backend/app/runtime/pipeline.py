"""素材分析管线：probe → 音频提取/转写/事件 → 镜头检测 → 抽帧 → 视觉理解 → 聚合。

- 全异步执行，阻塞步骤跑在线程池。
- 每步结果按 (content_hash, kind, version) 落 AnalysisRecord，二次分析命中缓存。
- 能力缺失（无 API Key / 无 Whisper）时跳过对应步骤并在 summary 中标记，不报错。
"""

import asyncio
import importlib.util
import logging

from app.config import settings
from app.runtime.events import bus
from app.runtime.understanding import summarize_asset
from app.store.db import db_session
from app.store.models import AnalysisRecord, Asset
from app.tools.registry import registry

logger = logging.getLogger("mca.pipeline")

ANALYSIS_VERSION = "v1"
MAX_VISION_SHOTS = 30  # 成本控制：单素材最多做视觉理解的镜头数
_running: set[int] = set()


def _get_cached(content_hash: str, kind: str) -> dict | None:
    with db_session() as db:
        rec = (
            db.query(AnalysisRecord)
            .filter_by(content_hash=content_hash, kind=kind, version=ANALYSIS_VERSION)
            .first()
        )
        return dict(rec.payload) if rec else None


def _save_record(content_hash: str, kind: str, payload: dict) -> None:
    with db_session() as db:
        existing = (
            db.query(AnalysisRecord)
            .filter_by(content_hash=content_hash, kind=kind, version=ANALYSIS_VERSION)
            .first()
        )
        if existing:
            existing.payload = payload
        else:
            db.add(
                AnalysisRecord(
                    content_hash=content_hash, kind=kind, version=ANALYSIS_VERSION, payload=payload
                )
            )
        db.commit()


async def _step(content_hash: str, kind: str, tool: str, arguments: dict) -> tuple[dict, bool]:
    """执行一个可缓存的分析步骤。返回 (结果, 是否命中缓存)；失败抛异常。"""
    cached = _get_cached(content_hash, kind)
    if cached is not None:
        return cached, True
    result = await registry.execute(tool, arguments)
    if not result.ok:
        raise RuntimeError(f"{tool} 失败: {result.error}")
    _save_record(content_hash, kind, result.output)
    return result.output, False


def _set_status(asset_id: int, status: str) -> None:
    with db_session() as db:
        asset = db.get(Asset, asset_id)
        if asset:
            asset.status = status
            db.commit()


def _emit(asset_id: int, step: str, detail: str = "", cached: bool = False) -> None:
    bus.publish("analysis", {"asset_id": asset_id, "step": step, "detail": detail, "cached": cached})


def whisper_available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


async def analyze_asset(asset_id: int) -> dict:
    """分析单个素材。重复调用时若已在分析中则直接返回。"""
    if asset_id in _running:
        return {"status": "already_running"}
    _running.add(asset_id)
    try:
        return await _analyze(asset_id)
    finally:
        _running.discard(asset_id)


async def _analyze(asset_id: int) -> dict:
    with db_session() as db:
        asset = db.get(Asset, asset_id)
        if asset is None:
            raise ValueError(f"素材不存在: {asset_id}")
        path, chash, filename = asset.path, asset.content_hash, asset.filename
        has_audio = bool(asset.has_audio)

    _set_status(asset_id, "analyzing")
    _emit(asset_id, "start", filename)
    try:
        probe, cached = await _step(chash, "probe", "probe_media", {"path": path})
        _emit(asset_id, "probe", "元数据提取完成", cached)

        # --- 音频链路 ---
        transcript = None
        audio_events = None
        if has_audio:
            wav = await registry.execute("extract_audio", {"path": path})
            if wav.ok:
                wav_path = wav.output["wav_path"]
                audio_events, cached = await _step(
                    chash, "audio_events", "detect_audio_events", {"wav_path": wav_path}
                )
                _emit(asset_id, "audio_events", "音频事件检测完成", cached)
                if whisper_available():
                    transcript, cached = await _step(
                        chash, "transcript", "transcribe_audio", {"wav_path": wav_path}
                    )
                    _emit(asset_id, "transcript", f"转写完成（{transcript.get('language')}）", cached)
                else:
                    _emit(asset_id, "transcript", "跳过：faster-whisper 不可用")
            else:
                _emit(asset_id, "extract_audio", f"音频提取失败：{wav.error}")

        # --- 视觉链路 ---
        shots_payload, cached = await _step(chash, "shots", "detect_shots", {"path": path})
        shots = shots_payload["shots"]
        _emit(asset_id, "shots", f"检测到 {len(shots)} 个镜头", cached)

        vision_available = bool(settings.dashscope_api_key)
        vision_by_shot: dict[int, dict] = {}
        if vision_available:
            cached_vision = _get_cached(chash, "vision")
            if cached_vision is not None:
                vision_by_shot = {int(k): v for k, v in cached_vision.items()}
                _emit(asset_id, "vision", "视觉理解命中缓存", True)
            else:
                targets = pick_vision_shots(shots, MAX_VISION_SHOTS)
                mids = [round((s["start"] + s["end"]) / 2, 2) for s in targets]
                frames_result = await asyncio.to_thread(_sample, path, mids)
                pairs = [(s, frames_result[ts]) for s, ts in zip(targets, mids)
                         if frames_result.get(ts)]
                vision_by_shot = await _vision_batch(asset_id, pairs)
                if vision_by_shot:
                    _save_record(chash, "vision", {str(k): v for k, v in vision_by_shot.items()})
        else:
            _emit(asset_id, "vision", "跳过：未配置 DASHSCOPE_API_KEY")

        # --- 聚合 ---
        summary = await summarize_asset(
            filename, shots, vision_by_shot, transcript, audio_events, vision_available
        )
        _save_record(chash, "summary", summary)
        _set_status(asset_id, "analyzed")
        _emit(asset_id, "done", f"分类：{summary.get('category') or '未知'}")
        return {"status": "analyzed", "summary": summary}
    except Exception as e:  # noqa: BLE001 - 管线失败落状态并上报
        logger.exception("素材 %s 分析失败", asset_id)
        _set_status(asset_id, "failed")
        _emit(asset_id, "failed", str(e)[:300])
        return {"status": "failed", "error": str(e)}


def pick_vision_shots(shots: list[dict], limit: int) -> list[dict]:
    """长素材防护（M20）：超过上限时沿时间轴均匀采样（含首尾），替代头部截断。"""
    if len(shots) <= limit:
        return shots
    if limit == 1:
        return [shots[0]]
    step = (len(shots) - 1) / (limit - 1)
    indices = sorted({round(i * step) for i in range(limit)})
    return [shots[i] for i in indices]


async def _vision_batch(asset_id: int, pairs: list[tuple[dict, str]]) -> dict[int, dict]:
    """镜头视觉理解并发执行（M20）：Semaphore 限流 + 进度上报 + 耗时预估先行。"""
    if not pairs:
        return {}
    concurrency = max(settings.vision_concurrency, 1)
    per_shot = 3 if settings.vision_speed == "fast" else 13  # 实测均值（秒）
    est = -(-len(pairs) // concurrency) * per_shot
    _emit(asset_id, "vision",
          f"开始视觉理解 {len(pairs)} 个镜头（并发 {concurrency}，预计 ~{est}s）")

    sem = asyncio.Semaphore(concurrency)
    progress = {"done": 0}

    async def one(shot: dict, frame: str) -> tuple[int, dict | None]:
        async with sem:
            analysis = await registry.execute("analyze_frames", {"image_paths": [frame]})
        progress["done"] += 1
        _emit(asset_id, "vision",
              f"视觉理解 {progress['done']}/{len(pairs)}{'' if analysis.ok else '（本镜头失败）'}")
        return shot["index"], (analysis.output if analysis.ok else None)

    results = await asyncio.gather(*[one(s, f) for s, f in pairs])
    return {idx: out for idx, out in results if out is not None}


def _sample(path: str, timestamps: list[float]) -> dict[float, str]:
    """同步抽帧（跑在线程池），返回 {timestamp: image_path}。"""
    from app.tools.media import sample_frames

    try:
        result = sample_frames(path, timestamps)
    except RuntimeError:
        return {}
    return {f["timestamp"]: f["image_path"] for f in result["frames"]}
