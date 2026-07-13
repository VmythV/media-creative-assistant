import { Empty, Table, Tabs, Tag, Typography } from "antd";
import type { AppEvent, LogEntry } from "./api";

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

export function LogsPanel({ events, logs }: { events: AppEvent[]; logs: LogEntry[] }) {
  return (
    <Tabs
      items={[
        { key: "events", label: "实时事件", children: <EventFeed events={events} /> },
        { key: "tools", label: "工具调用日志", children: <ToolLogs logs={logs} /> },
      ]}
    />
  );
}
