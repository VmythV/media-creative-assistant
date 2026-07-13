"""进程内事件总线：分析/执行进度经 SSE 推送到前端。"""

import asyncio
import json
from datetime import datetime, timezone


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, event_type: str, data: dict) -> None:
        event = {"type": event_type, "ts": datetime.now(timezone.utc).isoformat(), **data}
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # 消费不及时则丢弃，进度事件可容忍丢失


def sse_format(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


bus = EventBus()
