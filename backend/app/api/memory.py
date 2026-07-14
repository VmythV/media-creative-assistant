"""用户偏好记忆 API（设计文档 §14）。"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.memory import get_memory_provider

router = APIRouter(tags=["memory"])


class MemoryRequest(BaseModel):
    content: str
    kind: str = "user"


@router.get("/memory")
def list_memory(kind: str | None = None) -> dict:
    return {"memories": get_memory_provider().list(kind)}


@router.post("/memory")
def add_memory(req: MemoryRequest) -> dict:
    if not req.content.strip():
        raise HTTPException(400, "记忆内容不能为空")
    try:
        item = get_memory_provider().add(req.kind, req.content, source="manual")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if item is None:
        raise HTTPException(409, "已存在相同内容的记忆")
    return item


@router.delete("/memory/{memory_id}")
def delete_memory(memory_id: int) -> dict:
    if not get_memory_provider().delete(memory_id):
        raise HTTPException(404, "记忆不存在")
    return {"deleted": memory_id}
