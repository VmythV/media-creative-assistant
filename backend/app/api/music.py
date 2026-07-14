"""曲库 API（M14，phase2-roadmap §3）。"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.runtime.music import list_tracks, scan_library

router = APIRouter(tags=["music"])


class ScanRequest(BaseModel):
    directory: str | None = None  # 缺省扫 data/music


@router.get("/music")
def get_music() -> dict:
    return {"tracks": list_tracks()}


@router.post("/music/scan")
def scan(req: ScanRequest | None = None) -> dict:
    req = req or ScanRequest()
    try:
        return scan_library(req.directory)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from e
