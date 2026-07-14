"""音乐库与 BGM 推荐（M14，phase2-roadmap §3）。

推荐延续风险控制：模型只在曲库 id 白名单内选择（受限格式 + 校验），
任何失败确定性兜底到第一首；写 IR 仍走 apply_music，不经模型。
"""

import json
import logging
import subprocess
from pathlib import Path

from app.config import settings
from app.memory import get_memory_provider
from app.providers import get_llm_provider
from app.store.db import db_session
from app.store.models import MusicTrack
from app.tools.media import probe_media

logger = logging.getLogger("mca.music")

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac"}


def _mean_volume(path: str) -> float | None:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120, check=False,
    )
    for line in proc.stderr.splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].split("dB")[0].strip())
            except ValueError:
                return None
    return None


def scan_library(directory: str | None = None) -> dict:
    """扫描曲库目录登记新曲、清理失效行。返回 {added, removed, total}。"""
    music_dir = Path(directory).expanduser() if directory else settings.data_dir / "music"
    music_dir.mkdir(parents=True, exist_ok=True)
    files = {str(f) for f in sorted(music_dir.iterdir())
             if f.is_file() and f.suffix.lower() in AUDIO_EXTS}

    added, removed = 0, 0
    with db_session() as db:
        for row in db.query(MusicTrack).all():
            if not Path(row.path).is_file():
                db.delete(row)
                removed += 1
        db.commit()
        known = {r.path for r in db.query(MusicTrack).all()}
        for path in sorted(files - known):
            try:
                meta = probe_media(path)
                if not meta.get("audio"):
                    continue
                db.add(MusicTrack(path=path, filename=Path(path).name,
                                  duration=meta["duration"], mean_volume=_mean_volume(path)))
                added += 1
            except Exception as e:  # noqa: BLE001 - 单文件失败不阻断扫描
                logger.warning("曲库登记失败 %s: %s", path, e)
        db.commit()
        total = db.query(MusicTrack).count()
    return {"added": added, "removed": removed, "total": total}


def list_tracks() -> list[dict]:
    with db_session() as db:
        rows = db.query(MusicTrack).order_by(MusicTrack.filename).all()
    if not rows:  # 懒扫描默认目录
        scan_library()
        with db_session() as db:
            rows = db.query(MusicTrack).order_by(MusicTrack.filename).all()
    return [_track_dict(r) for r in rows]


def _track_dict(r: MusicTrack) -> dict:
    return {"id": r.id, "path": r.path, "filename": r.filename,
            "duration": r.duration, "mean_volume": r.mean_volume}


RECO_SYSTEM_PROMPT = """你是视频配乐师。根据剪辑方案的内容与用户期望的情绪，从曲库中选择最合适的一首。
文件名是主要的语义线索（通常包含风格/情绪/乐器），时长和响度是次要参考。
只输出 JSON：{"music_id": 曲目id(整数), "reason": "一句话推荐理由（中文）"}"""


async def recommend_music(mood: str | None = None, plan: dict | None = None) -> dict:
    """按情绪/方案从曲库推荐一首。返回 {path, filename, reason}；曲库为空抛 ValueError。"""
    tracks = list_tracks()
    if not tracks:
        raise ValueError(f"曲库为空（{settings.data_dir / 'music'}），请放入音乐文件后重试")
    if len(tracks) == 1:
        return {**tracks[0], "reason": "曲库当前仅此一首"}

    lines = [
        f"id={t['id']}：{t['filename']}，{t['duration']:.0f}s"
        + (f"，响度 {t['mean_volume']:.0f}dB" if t["mean_volume"] is not None else "")
        for t in tracks
    ]
    brief = []
    if plan:
        clips = plan.get("clips") or []
        total = sum(c["end"] - c["start"] for c in clips)
        brief.append(f"方案：《{plan.get('title', '')}》，{len(clips)} 个片段，约 {total:.0f} 秒")
    if mood:
        brief.append(f"期望情绪：{mood}")
    prefs = get_memory_provider().texts("user")
    if prefs:
        brief.append("用户长期偏好：" + "；".join(prefs))

    try:
        llm = get_llm_provider()
        resp = await llm.chat(
            [{"role": "system", "content": RECO_SYSTEM_PROMPT},
             {"role": "user", "content": "曲库：\n" + "\n".join(lines) + "\n\n" + "\n".join(brief)}],
            json_mode=True, temperature=0.2,
        )
        parsed = json.loads(resp["content"])
        chosen = next(t for t in tracks if t["id"] == parsed.get("music_id"))  # id 白名单校验
        return {**chosen, "reason": str(parsed.get("reason") or "")[:100]}
    except Exception as e:  # noqa: BLE001 - 推荐失败确定性兜底，不阻断配乐
        logger.warning("BGM 推荐失败，兜底第一首: %s", e)
        return {**tracks[0], "reason": "推荐服务不可用，默认选择曲库第一首"}
