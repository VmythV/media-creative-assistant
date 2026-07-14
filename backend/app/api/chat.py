"""对话式指挥 API（M12，phase2-roadmap §1）。"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.runtime.chat import handle_message
from app.store.db import db_session
from app.store.models import AgentSession

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


@router.post("/chat")
async def chat(req: ChatRequest) -> dict:
    if not req.message.strip():
        raise HTTPException(400, "消息不能为空")
    return await handle_message(req.session_id, req.message.strip())


@router.get("/chat/{session_id}")
def get_session(session_id: str) -> dict:
    with db_session() as db:
        row = db.get(AgentSession, session_id)
        if row is None:
            raise HTTPException(404, "会话不存在")
        return {"session_id": row.id, "messages": list(row.messages), "context": dict(row.context)}
