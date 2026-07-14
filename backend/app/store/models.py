"""SQLAlchemy 模型：素材、分析记录（缓存）、任务日志、Agent 会话。"""

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Asset(Base):
    """导入的素材文件。content_hash 是分析缓存的键。"""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String, unique=True)
    filename: Mapped[str] = mapped_column(String)
    content_hash: Mapped[str] = mapped_column(String, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    video_codec: Mapped[str | None] = mapped_column(String, nullable=True)
    has_audio: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="imported")  # imported/analyzing/analyzed/failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AnalysisRecord(Base):
    """分析结果缓存：按 (content_hash, kind, version) 唯一。

    kind: probe / shots / vision / transcript / audio_events / summary
    """

    __tablename__ = "analysis_records"
    __table_args__ = (UniqueConstraint("content_hash", "kind", "version", name="uq_analysis_cache"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_hash: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String)
    version: Mapped[str] = mapped_column(String, default="v1")
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TaskLog(Base):
    """工具调用日志：输入摘要、输出摘要、错误。"""

    __tablename__ = "task_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String, index=True)
    tool: Mapped[str] = mapped_column(String)
    input_summary: Mapped[str] = mapped_column(Text, default="")
    output_summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AgentSession(Base):
    """Agent 会话：对话历史与上下文（M2 起使用）。"""

    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    messages: Mapped[list] = mapped_column(JSON, default=list)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EditPlan(Base):
    """剪辑方案与生成的 Editing IR（M2 起使用）。"""

    __tablename__ = "edit_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(String, ForeignKey("agent_sessions.id"), nullable=True)
    goal: Mapped[str] = mapped_column(Text)
    plan: Mapped[dict] = mapped_column(JSON)
    ir: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String, default="draft")  # draft/confirmed/executed/failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MemoryItem(Base):
    """记忆条目（M11 起使用）：kind 枚举见 app.memory.MEMORY_KINDS，当前实现 user。"""

    __tablename__ = "memory_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String, default="user")  # user/project/temporary/global/business
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String, default="manual")  # revision/manual
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MusicTrack(Base):
    """曲库音轨（M14 起使用）：data/music 扫描登记。"""

    __tablename__ = "music_tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String, unique=True)
    filename: Mapped[str] = mapped_column(String)
    duration: Mapped[float] = mapped_column()
    mean_volume: Mapped[float | None] = mapped_column(nullable=True)  # dB，volumedetect
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BackgroundTask(Base):
    """后台任务登记（M19 任务持久化）：重启后按 kind 恢复或标记中断。"""

    __tablename__ = "background_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String)  # analyze/analyze_batch/plan_generate/plan_revise/execute/render/chat_actions
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default="running")  # running/done/failed/interrupted/recovered
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
