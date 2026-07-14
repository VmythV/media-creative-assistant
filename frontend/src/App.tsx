import { Badge, Layout, Space, Tabs, Tag, Tooltip, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";
import {
  api, type Asset, type Capability, type Highlight, type LogEntry, type Plan,
} from "./api";
import { AssetsPanel } from "./AssetsPanel";
import { HighlightsPanel } from "./HighlightsPanel";
import { LogsPanel } from "./LogsPanel";
import { MemoryPanel } from "./MemoryPanel";
import { PlanPanel } from "./PlanPanel";
import { useEvents } from "./useEvents";

const CAP_LABEL: Record<string, string> = {
  ffmpeg: "FFmpeg",
  davinci: "DaVinci Resolve",
  dashscope: "通义千问",
  "faster-whisper": "语音识别",
  ollama: "Ollama",
};

function CapabilityBar({ capabilities }: { capabilities: Capability[] }) {
  return (
    <Space wrap>
      {capabilities.map((c) => (
        <Tooltip key={c.name} title={c.available ? c.features.join(" / ") : c.fallback ?? "不可用"}>
          <Tag color={c.available ? "success" : "default"}>
            <Badge status={c.available ? "success" : "default"} /> {CAP_LABEL[c.name] ?? c.name}
          </Tag>
        </Tooltip>
      ))}
    </Space>
  );
}

export default function App() {
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [highlights, setHighlights] = useState<Highlight[]>([]);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [logs, setLogs] = useState<LogEntry[]>([]);

  const refresh = useCallback(() => {
    api.assets().then((r) => setAssets(r.assets)).catch(() => {});
    api.highlights().then((r) => setHighlights(r.highlights)).catch(() => {});
    api.plans().then((r) => setPlans(r.plans)).catch(() => {});
    api.logs().then((r) => setLogs(r.logs)).catch(() => {});
  }, []);

  // SSE 事件驱动刷新：分析/方案/执行状态变化时拉取最新数据
  const events = useEvents(
    useCallback((e: Record<string, unknown>) => {
      if (["done", "failed", "draft", "degraded"].includes(String(e.step))) refresh();
      if (e.type === "analysis" && e.step === "start") refresh();
    }, [refresh]),
  );

  useEffect(() => {
    api.capabilities().then((r) => setCapabilities(r.capabilities)).catch(() => {});
    refresh();
  }, [refresh]);

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Layout.Header
        style={{ background: "#fff", display: "flex", alignItems: "center",
                 justifyContent: "space-between", borderBottom: "1px solid #f0f0f0" }}
      >
        <Typography.Title level={4} style={{ margin: 0 }}>
          🎬 Media Creative Assistant
        </Typography.Title>
        <CapabilityBar capabilities={capabilities} />
      </Layout.Header>
      <Layout.Content style={{ padding: 24, maxWidth: 1100, width: "100%", margin: "0 auto" }}>
        <Tabs
          size="large"
          items={[
            {
              key: "assets",
              label: `素材（${assets.length}）`,
              children: <AssetsPanel assets={assets} refresh={refresh} />,
            },
            {
              key: "highlights",
              label: `精彩片段（${highlights.length}）`,
              children: <HighlightsPanel highlights={highlights} />,
            },
            {
              key: "plans",
              label: `剪辑方案（${plans.length}）`,
              children: <PlanPanel plans={plans} assets={assets} refresh={refresh} />,
            },
            { key: "memory", label: "偏好记忆", children: <MemoryPanel /> },
            { key: "logs", label: "日志", children: <LogsPanel events={events} logs={logs} /> },
          ]}
        />
      </Layout.Content>
    </Layout>
  );
}
