"""后台任务 API（M19 任务持久化）。"""

from fastapi import APIRouter

from app.runtime.tasks import list_tasks

router = APIRouter(tags=["tasks"])


@router.get("/tasks")
def get_tasks(limit: int = 50) -> dict:
    return {"tasks": list_tasks(min(limit, 200))}
