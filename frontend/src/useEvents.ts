import { useEffect, useRef, useState } from "react";
import type { AppEvent } from "./api";

/** 订阅后端 SSE 事件流；组件卸载时断开。 */
export function useEvents(onEvent?: (e: AppEvent) => void): AppEvent[] {
  const [events, setEvents] = useState<AppEvent[]>([]);
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    const source = new EventSource("/api/events");
    source.onmessage = (msg) => {
      try {
        const event = JSON.parse(msg.data) as AppEvent;
        setEvents((prev) => [...prev.slice(-199), event]);
        handler.current?.(event);
      } catch {
        // 忽略无法解析的心跳
      }
    };
    return () => source.close();
  }, []);

  return events;
}
