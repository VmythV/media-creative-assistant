"""FastAPI 入口。启动时执行 Capability Discovery 并打印 Capability Registry。"""

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.assets import router as assets_router
from app.api.capabilities import router as capabilities_router
from app.api.chat import router as chat_router
from app.api.execute import router as execute_router
from app.api.memory import router as memory_router
from app.api.music import router as music_router
from app.api.plans import router as plans_router
from app.api.tasks import router as tasks_router
from app.capability.discovery import discover_capabilities
from app.store.db import get_engine
from app.tools import load_all_tools
from app.tools import registry as registry_module

logger = logging.getLogger("mca")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def _log_sink(task_id: str, tool: str, inp: str, out: str, err: str | None) -> None:
    """工具调用日志落库（设计文档 7.2：所有工具调用记录输入、输出和错误）。"""
    from app.store.db import db_session
    from app.store.models import TaskLog

    with db_session() as db:
        db.add(TaskLog(task_id=task_id, tool=tool, input_summary=inp, output_summary=out, error=err))
        db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_engine()
    load_all_tools()
    registry_module.registry.set_log_sink(_log_sink)
    registry = discover_capabilities()
    app.state.capabilities = registry
    logger.info("Capability Registry:\n%s", json.dumps(registry, ensure_ascii=False, indent=2))
    from app.runtime.tasks import recover_interrupted

    recovered = await recover_interrupted()  # M19：恢复上次会话被打断的后台任务
    if recovered:
        logger.info("恢复中断任务 %d 项: %s", len(recovered), "；".join(recovered))
    yield


app = FastAPI(title="Media Creative Assistant", lifespan=lifespan)
app.include_router(capabilities_router, prefix="/api")
app.include_router(assets_router, prefix="/api")
app.include_router(plans_router, prefix="/api")
app.include_router(execute_router, prefix="/api")
app.include_router(memory_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(music_router, prefix="/api")
app.include_router(tasks_router, prefix="/api")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# 成片/产物静态托管（浏览器内预览成片）；必须先于根路径挂载注册
from app.config import settings as _settings  # noqa: E402

_output_dir = _settings.data_dir / "output"
_output_dir.mkdir(parents=True, exist_ok=True)
app.mount("/output", StaticFiles(directory=_output_dir), name="output")

# 前端构建产物（frontend/dist）存在时由后端托管，浏览器直接访问 http://127.0.0.1:8000
_frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
