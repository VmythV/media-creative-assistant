"""Memory System：MemoryProvider 接口 + SQLite 实现（设计文档 §14，需求 5.10）。

记忆类型枚举完整保留（需求要求多类型），本阶段实现 user（用户创作偏好）；
project/temporary 的职责当前由素材分析缓存与会话机制覆盖。
"""

from typing import Protocol

MEMORY_KINDS = {"user", "project", "temporary", "global", "business"}


def _normalize(content: str) -> str:
    """去重归一化：压空白、去句尾标点。"""
    text = "".join(content.split())
    return text.rstrip("。.！!；;")


class MemoryProvider(Protocol):
    def list(self, kind: str | None = None) -> "list[dict]": ...

    def add(self, kind: str, content: str, source: str = "manual") -> dict | None: ...

    def delete(self, memory_id: int) -> bool: ...

    def texts(self, kind: str) -> "list[str]": ...


class SqliteMemoryProvider:
    """SQLite 实现：归一化去重，按 kind 过滤。"""

    def list(self, kind: str | None = None) -> "list[dict]":
        from app.store.db import db_session
        from app.store.models import MemoryItem

        with db_session() as db:
            q = db.query(MemoryItem).order_by(MemoryItem.id.desc())
            if kind:
                q = q.filter(MemoryItem.kind == kind)
            return [
                {"id": m.id, "kind": m.kind, "content": m.content, "source": m.source,
                 "created_at": m.created_at.isoformat() if m.created_at else None}
                for m in q.all()
            ]

    def add(self, kind: str, content: str, source: str = "manual") -> dict | None:
        """写入一条记忆；kind 非法抛 ValueError，内容重复返回 None。"""
        from app.store.db import db_session
        from app.store.models import MemoryItem

        if kind not in MEMORY_KINDS:
            raise ValueError(f"未知记忆类型: {kind}（可选 {sorted(MEMORY_KINDS)}）")
        content = content.strip()
        if not content:
            return None
        key = _normalize(content)
        with db_session() as db:
            for m in db.query(MemoryItem).filter(MemoryItem.kind == kind).all():
                if _normalize(m.content) == key:
                    return None
            item = MemoryItem(kind=kind, content=content, source=source)
            db.add(item)
            db.commit()
            return {"id": item.id, "kind": kind, "content": content, "source": source}

    def delete(self, memory_id: int) -> bool:
        from app.store.db import db_session
        from app.store.models import MemoryItem

        with db_session() as db:
            item = db.get(MemoryItem, memory_id)
            if item is None:
                return False
            db.delete(item)
            db.commit()
            return True

    def texts(self, kind: str) -> "list[str]":
        """按写入顺序返回内容列表（供提示词注入）。"""
        return [m["content"] for m in reversed(self.list(kind))]


_provider: MemoryProvider | None = None


def get_memory_provider() -> MemoryProvider:
    global _provider
    if _provider is None:
        _provider = SqliteMemoryProvider()
    return _provider


def set_memory_provider(provider: MemoryProvider) -> None:
    """测试/扩展用：注入自定义实现（如向量库）。"""
    global _provider
    _provider = provider
