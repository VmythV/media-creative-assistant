"""素材 API：导入、列表、触发分析、查看分析结果、SSE 进度流。"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.runtime.events import bus, sse_format
from app.runtime.pipeline import ANALYSIS_VERSION, analyze_asset
from app.store.db import get_db
from app.store.hashing import content_hash
from app.store.models import AnalysisRecord, Asset
from app.tools.media import image_to_clip, probe_media

router = APIRouter(tags=["assets"])

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mts", ".mxf"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic"}


class ImportRequest(BaseModel):
    paths: list[str] = []
    directory: str | None = None


def _asset_dict(a: Asset) -> dict:
    return {
        "id": a.id,
        "path": a.path,
        "filename": a.filename,
        "content_hash": a.content_hash,
        "size_bytes": a.size_bytes,
        "duration": a.duration,
        "width": a.width,
        "height": a.height,
        "fps": a.fps,
        "video_codec": a.video_codec,
        "has_audio": bool(a.has_audio),
        "status": a.status,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


@router.post("/assets/import")
def import_assets(req: ImportRequest, db: Session = Depends(get_db)) -> dict:
    files: list[Path] = []
    images: list[Path] = []

    def collect(path: Path) -> None:
        suffix = path.suffix.lower()
        if suffix in VIDEO_EXTS:
            files.append(path)
        elif suffix in IMAGE_EXTS:
            images.append(path)

    for p in req.paths:
        path = Path(p).expanduser()
        if path.is_file():
            collect(path)
    if req.directory:
        d = Path(req.directory).expanduser()
        if not d.is_dir():
            raise HTTPException(400, f"目录不存在: {req.directory}")
        for f in sorted(d.iterdir()):
            if f.is_file():
                collect(f)
    if not files and not images:
        raise HTTPException(400, "未找到可导入的视频或图片文件")

    imported, errors = [], []
    for img in images:  # 图片先转视频片段（设计文档 §9.1），失败不阻断其他文件
        try:
            files.append(Path(image_to_clip(str(img))["clip_path"]))
        except Exception as e:  # noqa: BLE001
            errors.append({"path": str(img), "error": f"图片转片段失败: {e}"})
    for f in files:
        try:
            existing = db.query(Asset).filter_by(path=str(f)).first()
            if existing:
                imported.append(_asset_dict(existing))
                continue
            meta = probe_media(str(f))
            video = meta.get("video") or {}
            asset = Asset(
                path=str(f),
                filename=f.name,
                content_hash=content_hash(f),
                size_bytes=meta["size_bytes"],
                duration=meta["duration"],
                width=video.get("width"),
                height=video.get("height"),
                fps=video.get("fps"),
                video_codec=video.get("codec"),
                has_audio=1 if meta.get("audio") else 0,
            )
            db.add(asset)
            db.commit()
            imported.append(_asset_dict(asset))
        except Exception as e:  # noqa: BLE001 - 单个文件失败不阻断导入
            errors.append({"path": str(f), "error": str(e)})
    return {"imported": imported, "errors": errors}


@router.get("/assets")
def list_assets(db: Session = Depends(get_db)) -> dict:
    assets = db.query(Asset).order_by(Asset.id).all()
    return {"assets": [_asset_dict(a) for a in assets]}


@router.get("/assets/{asset_id}")
def get_asset(asset_id: int, db: Session = Depends(get_db)) -> dict:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, "素材不存在")
    return _asset_dict(asset)


@router.post("/assets/{asset_id}/analyze")
async def trigger_analysis(asset_id: int, db: Session = Depends(get_db)) -> dict:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, "素材不存在")
    asyncio.create_task(analyze_asset(asset_id))
    return {"status": "started", "asset_id": asset_id}


@router.post("/assets/analyze-all")
async def trigger_analysis_all(db: Session = Depends(get_db)) -> dict:
    ids = [a.id for a in db.query(Asset).filter(Asset.status != "analyzed").all()]

    async def run_all():
        for aid in ids:
            await analyze_asset(aid)

    asyncio.create_task(run_all())
    return {"status": "started", "asset_ids": ids}


@router.get("/assets/{asset_id}/analysis")
def get_analysis(asset_id: int, db: Session = Depends(get_db)) -> dict:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, "素材不存在")
    records = (
        db.query(AnalysisRecord)
        .filter_by(content_hash=asset.content_hash, version=ANALYSIS_VERSION)
        .all()
    )
    return {"asset": _asset_dict(asset), "analysis": {r.kind: r.payload for r in records}}


@router.get("/events")
async def events_stream():
    queue = bus.subscribe()

    async def stream():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield sse_format(event)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")
