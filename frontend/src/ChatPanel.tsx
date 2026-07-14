import { Alert, Button, Card, Input, Space, Spin, Tag, Typography, message } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ChatMessage } from "./api";

const INTENT_LABEL: Record<string, string> = {
  create_plan: "生成方案", revise_plan: "修订方案", confirm_plan: "确认方案",
  set_music: "设置配乐", remove_music: "移除配乐", render: "渲染成片",
  execute: "生成 Resolve 时间线", import_assets: "导入素材", analyze_assets: "分析素材",
  set_output_spec: "切换输出画幅", set_subtitle_style: "字幕样式", generate_voiceover: "AI 配音",
  learn_style: "学习风格", apply_style: "应用风格", clear_style: "取消风格",
};
const STATUS_META: Record<string, { color: string; text: string }> = {
  pending: { color: "processing", text: "执行中" },
  done: { color: "success", text: "完成" },
  failed: { color: "error", text: "失败" },
  skipped: { color: "default", text: "已跳过" },
  invalid: { color: "warning", text: "已拒绝" },
};

function ActionCard({ m }: { m: ChatMessage }) {
  const meta = STATUS_META[m.status ?? "pending"] ?? STATUS_META.pending;
  const detail = m.error
    ? m.error
    : m.result
      ? Object.entries(m.result).filter(([, v]) => v != null)
          .map(([k, v]) => `${k}: ${v}`).join(" · ")
      : "";
  return (
    <Card size="small" style={{ background: "#fafafa" }}>
      <Space wrap>
        <Tag color={meta.color}>{meta.text}</Tag>
        <Typography.Text strong>{INTENT_LABEL[m.intent ?? ""] ?? m.intent}</Typography.Text>
        {m.status === "pending" && <Spin size="small" />}
        {detail && <Typography.Text type="secondary">{detail}</Typography.Text>}
        {m.result?.video_url != null && (
          <a href={encodeURI(String(m.result.video_url))} target="_blank" rel="noreferrer">播放成片</a>
        )}
      </Space>
    </Card>
  );
}

export function ChatPanel({ refresh }: { refresh: () => void }) {
  const [sessionId, setSessionId] = useState<string | null>(
    () => localStorage.getItem("mca_chat_session"),
  );
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [polling, setPolling] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const load = useCallback((id: string) => {
    api.chatSession(id)
      .then((r) => {
        setMessages(r.messages);
        setPolling(r.messages.some((m) => m.role === "action" && m.status === "pending"));
      })
      .catch(() => { localStorage.removeItem("mca_chat_session"); setSessionId(null); });
  }, []);

  useEffect(() => { if (sessionId) load(sessionId); }, [sessionId, load]);
  useEffect(() => {
    if (!polling || !sessionId) return;
    const t = setInterval(() => { load(sessionId); refresh(); }, 2000);
    return () => clearInterval(t);
  }, [polling, sessionId, load, refresh]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  const send = async () => {
    const text = input.trim();
    if (!text) return;
    setBusy(true);
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput("");
    try {
      const r = await api.chat(text, sessionId);
      localStorage.setItem("mca_chat_session", r.session_id);
      setSessionId(r.session_id);
      load(r.session_id);
      if (r.actions.length > 0) setPolling(true);
    } catch (e) {
      message.error(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Alert
        type="info" showIcon
        message='用一句话指挥整个流程，例如："把 ~/Videos/旅行 导入并分析" → "做一个30秒的旅行短片，配上音乐，渲染出来" → "节奏再快一点"。做不了的操作会告诉你如何在 Resolve 中手动完成。'
      />
      <div style={{ minHeight: 320, maxHeight: 480, overflowY: "auto", padding: 8 }}>
        <Space direction="vertical" size="small" style={{ width: "100%" }}>
          {messages.map((m, i) => {
            if (m.role === "action") return <ActionCard key={i} m={m} />;
            const isUser = m.role === "user";
            return (
              <div key={i} style={{ textAlign: isUser ? "right" : "left" }}>
                <Card
                  size="small"
                  style={{
                    display: "inline-block", maxWidth: "80%", textAlign: "left",
                    background: isUser ? "#e6f4ff" : "#fff",
                  }}
                >
                  <Typography.Text>{m.content}</Typography.Text>
                </Card>
              </div>
            );
          })}
          <div ref={bottomRef} />
        </Space>
      </div>
      <Space.Compact style={{ width: "100%" }}>
        <Input
          size="large"
          placeholder="告诉我你想要一个怎样的视频…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onPressEnter={send}
          disabled={busy}
        />
        <Button size="large" type="primary" onClick={send} loading={busy}>发送</Button>
      </Space.Compact>
      {sessionId && (
        <Button size="small" type="text" onClick={() => {
          localStorage.removeItem("mca_chat_session");
          setSessionId(null); setMessages([]);
        }}>新对话</Button>
      )}
    </Space>
  );
}
