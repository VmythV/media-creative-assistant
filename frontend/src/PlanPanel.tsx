import {
  Alert, Button, Card, Collapse, Descriptions, Empty, Input, List, Space, Steps, Tag, Typography, message,
} from "antd";
import { useState } from "react";
import { api, type Asset, type Plan, type PlanClip } from "./api";

const SECTION_LABEL: Record<string, string> = {
  opening: "开场", build: "铺垫", climax: "高潮", ending: "结尾", broll: "空镜/穿插",
};
const SECTION_COLOR: Record<string, string> = {
  opening: "green", build: "blue", climax: "red", ending: "orange", broll: "default",
};
const PLAN_STATUS: Record<string, { text: string; color: string }> = {
  generating: { text: "生成中", color: "processing" },
  draft: { text: "待确认", color: "warning" },
  confirmed: { text: "已确认", color: "blue" },
  executed: { text: "已执行", color: "success" },
  failed: { text: "失败", color: "error" },
};

function ClipList({ clips, assets }: { clips: PlanClip[]; assets: Asset[] }) {
  const nameOf = (id: number) => assets.find((a) => a.id === id)?.filename ?? `素材#${id}`;
  return (
    <List
      size="small"
      dataSource={clips}
      renderItem={(c, i) => (
        <List.Item>
          <Space direction="vertical" size={0} style={{ width: "100%" }}>
            <Space wrap>
              <Typography.Text type="secondary">{i + 1}.</Typography.Text>
              <Tag color={SECTION_COLOR[c.section]}>{SECTION_LABEL[c.section] ?? c.section}</Tag>
              <Typography.Text strong>{nameOf(c.asset_id)}</Typography.Text>
              <Tag>{c.start.toFixed(1)}s - {c.end.toFixed(1)}s（{(c.end - c.start).toFixed(1)}s）</Tag>
              {c.subtitle && <Tag color="cyan">字幕：{c.subtitle}</Tag>}
            </Space>
            <Typography.Text type="secondary">{c.reason}</Typography.Text>
          </Space>
        </List.Item>
      )}
    />
  );
}

function ExecutionCard({ plan }: { plan: Plan }) {
  const exec = plan.plan.execution;
  if (!exec) return null;
  return (
    <Card size="small" title={`执行结果（${exec.mode === "resolve" ? "DaVinci Resolve" : "降级输出"}）`}>
      <Space direction="vertical" style={{ width: "100%" }}>
        {exec.mode === "resolve" && exec.resolve && (
          <Descriptions column={1} size="small">
            <Descriptions.Item label="Resolve 项目">{exec.resolve.project}</Descriptions.Item>
            <Descriptions.Item label="时间线">
              {exec.resolve.timeline}（{exec.resolve.clips} 个片段）
            </Descriptions.Item>
            {exec.resolve.subtitles?.method === "media_pool" && (
              <Descriptions.Item label="字幕">
                SRT 已导入媒体池，在 Resolve 中右键 → Insert Selected Subtitles to Timeline
              </Descriptions.Item>
            )}
          </Descriptions>
        )}
        <Descriptions column={1} size="small" title="产物文件">
          {Object.entries(exec.artifacts).map(([k, v]) => (
            <Descriptions.Item key={k} label={k}>
              <Typography.Text code copyable>{v}</Typography.Text>
            </Descriptions.Item>
          ))}
        </Descriptions>
      </Space>
    </Card>
  );
}

export function PlanPanel({
  plans, assets, refresh,
}: { plans: Plan[]; assets: Asset[]; refresh: () => void }) {
  const [goal, setGoal] = useState("");
  const [creating, setCreating] = useState(false);
  const analyzedCount = assets.filter((a) => a.status === "analyzed").length;

  const createPlan = async () => {
    if (!goal.trim()) return;
    setCreating(true);
    try {
      await api.createPlan(goal.trim());
      message.success("方案生成中，请稍候");
      setGoal("");
      refresh();
    } catch (e) {
      message.error(String(e));
    } finally {
      setCreating(false);
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {analyzedCount === 0 && (
        <Alert type="info" showIcon message="请先在「素材」页导入并分析素材，再生成剪辑方案" />
      )}
      <Space.Compact style={{ width: "100%" }}>
        <Input
          placeholder='描述创作目标，例如："做一个 60 秒旅行短片，节奏舒缓，加中文字幕"'
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          onPressEnter={createPlan}
          disabled={analyzedCount === 0}
        />
        <Button type="primary" onClick={createPlan} loading={creating} disabled={analyzedCount === 0}>
          生成剪辑方案
        </Button>
      </Space.Compact>

      {plans.length === 0 ? (
        <Empty description="还没有剪辑方案" />
      ) : (
        <Collapse
          defaultActiveKey={plans[0] ? [String(plans[0].id)] : []}
          items={plans.map((p) => ({
            key: String(p.id),
            label: (
              <Space>
                <Tag color={PLAN_STATUS[p.status]?.color}>{PLAN_STATUS[p.status]?.text ?? p.status}</Tag>
                <Typography.Text strong>{p.plan.title ?? p.goal}</Typography.Text>
                <Typography.Text type="secondary">目标：{p.goal}</Typography.Text>
              </Space>
            ),
            children: (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                {p.status === "generating" && (
                  <Steps
                    size="small" current={0}
                    items={[{ title: "生成中", status: "process" }, { title: "确认" }, { title: "执行" }]}
                  />
                )}
                {p.status === "failed" && (
                  <Alert type="error" showIcon message="方案生成失败" description={p.plan.error} />
                )}
                {p.plan.clips && <ClipList clips={p.plan.clips} assets={assets} />}
                <Space>
                  {p.status === "draft" && (
                    <>
                      <Button type="primary"
                        onClick={() => api.confirmPlan(p.id).then(refresh).catch((e) => message.error(String(e)))}>
                        确认方案
                      </Button>
                      <Button
                        onClick={() => api.executePlan(p.id).then(refresh).catch((e) => message.error(String(e)))}>
                        直接执行
                      </Button>
                    </>
                  )}
                  {p.status === "confirmed" && (
                    <Button type="primary"
                      onClick={() => api.executePlan(p.id).then(refresh).catch((e) => message.error(String(e)))}>
                      执行（生成 Resolve 时间线）
                    </Button>
                  )}
                </Space>
                <ExecutionCard plan={p} />
              </Space>
            ),
          }))}
        />
      )}
    </Space>
  );
}
