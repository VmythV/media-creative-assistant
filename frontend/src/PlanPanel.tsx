import {
  Alert, AutoComplete, Button, Card, Collapse, Descriptions, Empty, Input, List, Segmented, Space, Steps, Tag, Typography, message,
} from "antd";
import { useEffect, useState } from "react";
import { api, type Asset, type Plan, type PlanClip } from "./api";

const SECTION_LABEL: Record<string, string> = {
  opening: "开场", build: "铺垫", climax: "高潮", ending: "结尾", broll: "空镜/穿插",
};
const SECTION_COLOR: Record<string, string> = {
  opening: "green", build: "blue", climax: "red", ending: "orange", broll: "default",
};
const TRANSITION_LABEL: Record<string, string> = {
  fade: "叠化", fadeblack: "压黑", fadewhite: "闪白", dissolve: "溶解",
  wipeleft: "左划像", wiperight: "右划像", slideleft: "左滑", slideright: "右滑",
  circleopen: "圆形展开", circleclose: "圆形收拢",
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
              {c.transition && (
                <Tag color="purple">
                  ⇢ 转场：{TRANSITION_LABEL[c.transition.type] ?? c.transition.type} {c.transition.duration}s
                </Tag>
              )}
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

function DiffCard({ plan }: { plan: Plan }) {
  const diff = plan.plan.diff;
  if (!diff) return null;
  const lines = [
    ...diff.added.map((s) => ({ color: "green", text: s })),
    ...diff.removed.map((s) => ({ color: "red", text: s })),
    ...diff.changed.map((s) => ({ color: "orange", text: `修改：${s}` })),
  ];
  return (
    <Card size="small" title={`与方案 #${plan.plan.revised_from} 的差异（总时长 ${diff.duration}）`}>
      {lines.length === 0 ? (
        <Typography.Text type="secondary">没有片段级变化</Typography.Text>
      ) : (
        <Space direction="vertical" size={2}>
          {lines.map((l, i) => (
            <Typography.Text key={i}>
              <Tag color={l.color} /> {l.text}
            </Typography.Text>
          ))}
        </Space>
      )}
    </Card>
  );
}

function ReviseBox({ plan, refresh }: { plan: Plan; refresh: () => void }) {
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!instruction.trim()) return;
    setBusy(true);
    try {
      await api.revisePlan(plan.id, instruction.trim());
      message.success("修订方案生成中，将作为新方案出现");
      setInstruction("");
      refresh();
    } catch (e) {
      message.error(String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Space.Compact style={{ width: "100%" }}>
      <Input
        placeholder='用自然语言修订，例如："总长压到25秒，去掉第2段，结尾字幕改成……"'
        value={instruction}
        onChange={(e) => setInstruction(e.target.value)}
        onPressEnter={submit}
      />
      <Button onClick={submit} loading={busy}>修订方案</Button>
    </Space.Compact>
  );
}

function MusicBox({ plan, refresh }: { plan: Plan; refresh: () => void }) {
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [library, setLibrary] = useState<{ path: string; filename: string; duration: number }[]>([]);
  useEffect(() => { api.musicLibrary().then((r) => setLibrary(r.tracks)).catch(() => {}); }, []);
  const tracks = (plan.ir as { tracks?: { type: string; items?: unknown[] }[] } | null)?.tracks ?? [];
  const hasMusic = tracks.some((t) => t.type === "audio" && (t.items?.length ?? 0) > 0);
  const submit = async () => {
    if (!path.trim()) return;
    setBusy(true);
    try {
      const r = await api.setMusic(plan.id, path.trim());
      message.success(`配乐已设置：${r.music}（重新渲染生效）`);
      setPath("");
      refresh();
    } catch (e) {
      message.error(String(e));
    } finally {
      setBusy(false);
    }
  };
  const recommend = async () => {
    setBusy(true);
    try {
      const r = await api.recommendMusic(plan.id);
      message.success(`已配乐：${r.music} —— ${r.reason}`, 6);
      refresh();
    } catch (e) {
      message.error(String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Space.Compact style={{ width: "100%" }}>
      <AutoComplete
        style={{ flex: 1 }}
        placeholder="从曲库选择（data/music），或输入音乐文件绝对路径"
        value={path}
        onChange={setPath}
        options={library.map((t) => ({
          value: t.path, label: `${t.filename}（${Math.round(t.duration)}s）`,
        }))}
      >
        <Input prefix={hasMusic ? <Tag color="gold">已配乐</Tag> : undefined} onPressEnter={submit} />
      </AutoComplete>
      <Button onClick={submit} loading={busy}>设置</Button>
      <Button type="primary" ghost onClick={recommend} loading={busy}>AI 推荐</Button>
      {hasMusic && (
        <Button danger
          onClick={() => api.removeMusic(plan.id)
            .then(() => { message.success("已移除配乐"); refresh(); })
            .catch((e) => message.error(String(e)))}>
          移除
        </Button>
      )}
    </Space.Compact>
  );
}

function OutputBox({ plan, refresh }: { plan: Plan; refresh: () => void }) {
  const render = (plan.ir as { render?: { width: number; height: number } | null } | null)?.render;
  const current = render
    ? (render.width === render.height ? "1:1" : render.width > render.height ? "16:9" : "9:16")
    : "auto";
  const set = (aspect: string) => {
    const p = aspect === "auto"
      ? api.resetOutput(plan.id)
      : api.setOutput(plan.id, aspect);
    p.then(() => { message.success("输出画幅已更新（重新渲染生效）"); refresh(); })
      .catch((e) => message.error(String(e)));
  };
  return (
    <Space>
      <Typography.Text type="secondary">输出画幅：</Typography.Text>
      <Segmented
        size="small"
        value={current}
        options={[
          { label: "跟随素材", value: "auto" },
          { label: "横屏 16:9", value: "16:9" },
          { label: "竖屏 9:16", value: "9:16" },
          { label: "方形 1:1", value: "1:1" },
        ]}
        onChange={(v) => set(String(v))}
      />
    </Space>
  );
}

function SubtitleStyleBox({ plan, refresh }: { plan: Plan; refresh: () => void }) {
  const tracks = (plan.ir as {
    tracks?: { type: string; items?: unknown[]; style?: { preset?: string; position?: string } | null }[];
  } | null)?.tracks ?? [];
  const subTrack = tracks.find((t) => t.type === "subtitle" && (t.items?.length ?? 0) > 0);
  if (!subTrack) return null;
  const preset = subTrack.style?.preset ?? "default";
  const position = subTrack.style?.position ?? "bottom";
  const apply = (body: { preset?: string; position?: string }) =>
    api.setSubtitleStyle(plan.id, { preset, position, ...body })
      .then(() => { message.success("字幕样式已更新（重新渲染生效；Resolve 内请手动调样式）"); refresh(); })
      .catch((e) => message.error(String(e)));
  return (
    <Space wrap>
      <Typography.Text type="secondary">字幕样式：</Typography.Text>
      <Segmented
        size="small" value={preset}
        options={[
          { label: "默认", value: "default" }, { label: "文艺", value: "elegant" },
          { label: "醒目", value: "bold" }, { label: "简约", value: "minimal" },
        ]}
        onChange={(v) => apply({ preset: String(v) })}
      />
      <Segmented
        size="small" value={position}
        options={[
          { label: "底部", value: "bottom" }, { label: "顶部", value: "top" },
          { label: "居中", value: "center" },
        ]}
        onChange={(v) => apply({ position: String(v) })}
      />
    </Space>
  );
}

function RenderCard({ plan }: { plan: Plan }) {
  const render = plan.plan.render;
  if (!render) return null;
  if (render.error) {
    return <Alert type="error" showIcon message="成片渲染失败" description={render.error} />;
  }
  return (
    <Card size="small" title="成片">
      <Space direction="vertical" style={{ width: "100%" }}>
        {render.video_url && (
          <video controls preload="metadata" style={{ width: "100%", maxHeight: 420, background: "#000" }}
            src={encodeURI(render.video_url)} />
        )}
        <Descriptions column={1} size="small">
          <Descriptions.Item label="视频文件">
            <Typography.Text code copyable>{render.video}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="信息">
            {render.duration?.toFixed(1)} 秒 · {render.resolution ? `${render.resolution} · ` : ""}{render.clips} 个片段
            {render.transitions ? ` · ${render.transitions} 处转场` : ""}
            {render.subtitles_burned ? " · 已烧录字幕" : ""}
            {render.music ? ` · 配乐：${render.music}` : ""}
          </Descriptions.Item>
        </Descriptions>
      </Space>
    </Card>
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
            {exec.resolve.transitions?.method === "fcpxml_import" && (
              <Descriptions.Item label="转场">
                {exec.resolve.transitions.count} 处转场已随时间线导入（按类型映射为叠化/浸入颜色/划像/椭圆展开；方向与颜色可在 Resolve 中微调）
              </Descriptions.Item>
            )}
            {exec.resolve.transitions?.method === "unsupported" && (
              <Descriptions.Item label="转场">
                {exec.resolve.transitions.count} 处转场需在 Resolve 内手动添加（脚本 API 限制）；渲染成片含完整转场
              </Descriptions.Item>
            )}
            {exec.resolve.music && (
              <Descriptions.Item label="配乐">
                {exec.resolve.music.method === "timeline"
                  ? `${exec.resolve.music.file} 已放置到 A${exec.resolve.music.track} 轨（音量/淡入淡出请在 Resolve 中调整）`
                  : `${exec.resolve.music.file} 已入媒体池，拖到音频轨即可`}
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
                {p.plan.revised_from != null && (
                  <Tag color="purple">修订自 #{p.plan.revised_from}</Tag>
                )}
                <Typography.Text type="secondary">
                  {p.plan.revision_instruction ? `修订：${p.plan.revision_instruction}` : `目标：${p.goal}`}
                </Typography.Text>
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
                  {["confirmed", "executed"].includes(p.status) && (
                    <Button type="primary"
                      onClick={() => api.executePlan(p.id).then(refresh).catch((e) => message.error(String(e)))}>
                      {p.status === "executed" ? "重新执行（Resolve 时间线）" : "执行（生成 Resolve 时间线）"}
                    </Button>
                  )}
                  {["confirmed", "executed"].includes(p.status) && (
                    <>
                      <Button
                        onClick={() =>
                          api.renderPlan(p.id)
                            .then(() => { message.success("渲染已开始，完成后自动刷新"); refresh(); })
                            .catch((e) => message.error(String(e)))}>
                        渲染成片（mp4）
                      </Button>
                      <Button
                        onClick={() =>
                          api.renderPlan(p.id, "resolve")
                            .then(() => { message.success("Resolve 渲染已开始（不含字幕，含时间线转场配乐）"); refresh(); })
                            .catch((e) => message.error(String(e)))}>
                        Resolve 渲染
                      </Button>
                    </>
                  )}
                </Space>
                {["draft", "confirmed", "executed"].includes(p.status) && (
                  <>
                    <ReviseBox plan={p} refresh={refresh} />
                    <MusicBox plan={p} refresh={refresh} />
                    <OutputBox plan={p} refresh={refresh} />
                    <SubtitleStyleBox plan={p} refresh={refresh} />
                  </>
                )}
                <DiffCard plan={p} />
                <RenderCard plan={p} />
                <ExecutionCard plan={p} />
              </Space>
            ),
          }))}
        />
      )}
    </Space>
  );
}
