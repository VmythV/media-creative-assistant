import { Empty, Table, Tabs, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { api, type AppEvent, type BackgroundTask, type LogEntry } from "./api";

function EventFeed({ events }: { events: AppEvent[] }) {
  if (events.length === 0) return <Empty description="暂无事件（分析/执行时这里会实时滚动）" />;
  return (
    <div style={{ maxHeight: 480, overflow: "auto", fontFamily: "monospace", fontSize: 13 }}>
      {[...events].reverse().map((e, i) => (
        <div key={i} style={{ padding: "2px 0" }}>
          <Typography.Text type="secondary">{String(e.ts).slice(11, 19)}</Typography.Text>{" "}
          <Tag>{String(e.type)}</Tag>
          <Typography.Text>
            {["asset_id" in e ? `素材#${e.asset_id}` : null, "plan_id" in e ? `方案#${e.plan_id}` : null, e.step, e.detail]
              .filter(Boolean)
              .join(" · ")}
          </Typography.Text>
        </div>
      ))}
    </div>
  );
}

function ToolLogs({ logs }: { logs: LogEntry[] }) {
  return (
    <Table<LogEntry>
      rowKey="id"
      size="small"
      dataSource={logs}
      pagination={{ pageSize: 20 }}
      columns={[
        { title: "时间", dataIndex: "ts", width: 100, render: (ts: string) => ts?.slice(11, 19) },
        { title: "工具", dataIndex: "tool", width: 170 },
        {
          title: "输入", dataIndex: "input", ellipsis: true,
          render: (v: string) => <Typography.Text code>{v}</Typography.Text>,
        },
        {
          title: "结果", ellipsis: true,
          render: (_, log) =>
            log.error ? (
              <Typography.Text type="danger">{log.error}</Typography.Text>
            ) : (
              <Typography.Text type="secondary">{log.output}</Typography.Text>
            ),
        },
      ]}
    />
  );
}

const TASK_STATUS: Record<string, string> = {
  running: "processing", done: "success", failed: "error",
  interrupted: "warning", recovered: "cyan",
};
const TASK_KIND: Record<string, string> = {
  analyze: "素材分析", analyze_batch: "批量分析", plan_generate: "方案生成",
  plan_revise: "方案修订", execute: "执行", render: "渲染", chat_actions: "对话动作链",
};

function TaskList() {
  const [tasks, setTasks] = useState<BackgroundTask[]>([]);
  useEffect(() => {
    const load = () => api.tasks().then((r) => setTasks(r.tasks)).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);
  return (
    <Table<BackgroundTask>
      rowKey="id" dataSource={tasks} size="small" pagination={{ pageSize: 15 }}
      locale={{ emptyText: <Empty description="还没有后台任务" /> }}
      columns={[
        { title: "#", dataIndex: "id", width: 60 },
        { title: "类型", dataIndex: "kind", width: 110,
          render: (k: string) => TASK_KIND[k] ?? k },
        { title: "状态", dataIndex: "status", width: 90,
          render: (s: string) => <Tag color={TASK_STATUS[s]}>{s}</Tag> },
        { title: "参数", dataIndex: "payload", ellipsis: true,
          render: (p: Record<string, unknown>) => JSON.stringify(p) },
        { title: "备注", dataIndex: "detail", ellipsis: true },
        { title: "更新时间", dataIndex: "updated_at", width: 170,
          render: (t: string | null) => (t ? new Date(t).toLocaleString() : "-") },
      ]}
    />
  );
}

export function LogsPanel({ events, logs }: { events: AppEvent[]; logs: LogEntry[] }) {
  return (
    <Tabs
      items={[
        { key: "events", label: "实时事件", children: <EventFeed events={events} /> },
        { key: "tasks", label: "后台任务", children: <TaskList /> },
        { key: "tools", label: "工具调用日志", children: <ToolLogs logs={logs} /> },
      ]}
    />
  );
}
