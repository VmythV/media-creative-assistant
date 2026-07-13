import {
  Button, Descriptions, Drawer, Empty, Input, List, Space, Table, Tag, Typography, message,
} from "antd";
import { useState } from "react";
import { api, type Asset, type Highlight } from "./api";

const STATUS_TAG: Record<string, { color: string; text: string }> = {
  imported: { color: "default", text: "已导入" },
  analyzing: { color: "processing", text: "分析中" },
  analyzed: { color: "success", text: "已分析" },
  failed: { color: "error", text: "失败" },
};

function fmtDuration(seconds: number | null): string {
  if (seconds == null) return "-";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `${m}分${s}秒` : `${s}秒`;
}

export function AssetsPanel({ assets, refresh }: { assets: Asset[]; refresh: () => void }) {
  const [importPath, setImportPath] = useState("");
  const [importing, setImporting] = useState(false);
  const [detail, setDetail] = useState<{ asset: Asset; analysis: Record<string, any> } | null>(null);

  const doImport = async () => {
    const value = importPath.trim();
    if (!value) return;
    setImporting(true);
    try {
      const isDir = !/\.\w{2,4}$/.test(value);
      const result = await api.importAssets(isDir ? { directory: value } : { paths: [value] });
      message.success(`导入 ${result.imported.length} 个素材`);
      result.errors.forEach((e) => message.warning(`${e.path}: ${e.error}`));
      setImportPath("");
      refresh();
    } catch (e) {
      message.error(String(e));
    } finally {
      setImporting(false);
    }
  };

  const openDetail = async (asset: Asset) => {
    try {
      setDetail(await api.analysis(asset.id));
    } catch (e) {
      message.error(String(e));
    }
  };

  const summary = detail?.analysis?.summary;
  const transcript = detail?.analysis?.transcript;

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="large">
      <Space.Compact style={{ width: "100%" }}>
        <Input
          placeholder="输入视频/照片文件或目录的绝对路径（照片会自动转成推近片段），例如 /Users/me/Movies/素材目录"
          value={importPath}
          onChange={(e) => setImportPath(e.target.value)}
          onPressEnter={doImport}
        />
        <Button type="primary" loading={importing} onClick={doImport}>
          导入
        </Button>
        <Button
          onClick={async () => {
            await api.analyzeAll();
            message.info("已开始分析全部未分析素材");
            refresh();
          }}
        >
          分析全部
        </Button>
      </Space.Compact>

      <Table<Asset>
        rowKey="id"
        dataSource={assets}
        pagination={false}
        size="middle"
        locale={{ emptyText: <Empty description="还没有素材，先导入一个视频目录" /> }}
        columns={[
          { title: "文件", dataIndex: "filename", ellipsis: true },
          { title: "时长", dataIndex: "duration", width: 90, render: fmtDuration },
          {
            title: "规格", width: 140,
            render: (_, a) => (a.width ? `${a.width}x${a.height} @${a.fps ?? "?"}fps` : "-"),
          },
          {
            title: "状态", dataIndex: "status", width: 90,
            render: (s: string) => <Tag color={STATUS_TAG[s]?.color}>{STATUS_TAG[s]?.text ?? s}</Tag>,
          },
          {
            title: "操作", width: 170,
            render: (_, a) => (
              <Space>
                <Button size="small" onClick={() => api.analyze(a.id).then(refresh)}
                  disabled={a.status === "analyzing"}>
                  分析
                </Button>
                <Button size="small" onClick={() => openDetail(a)} disabled={a.status !== "analyzed"}>
                  查看结果
                </Button>
              </Space>
            ),
          },
        ]}
      />

      <Drawer
        title={detail?.asset.filename}
        width={640}
        open={detail !== null}
        onClose={() => setDetail(null)}
      >
        {summary ? (
          <Space direction="vertical" size="large" style={{ width: "100%" }}>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="分类">{summary.category ?? "未知"}</Descriptions.Item>
              <Descriptions.Item label="镜头数">{summary.shot_count}</Descriptions.Item>
              <Descriptions.Item label="含对白">{summary.has_speech ? "是" : "否"}</Descriptions.Item>
              <Descriptions.Item label="视觉分析">
                {summary.vision_available ? "已启用" : "未启用（缺 API Key）"}
              </Descriptions.Item>
            </Descriptions>
            <div>
              <Typography.Title level={5}>精彩片段推荐</Typography.Title>
              <List
                size="small"
                dataSource={(summary.highlights ?? []) as Highlight[]}
                renderItem={(h) => (
                  <List.Item>
                    <Space direction="vertical" size={0}>
                      <Space>
                        <Tag>{h.start.toFixed(1)}s - {h.end.toFixed(1)}s</Tag>
                        <Tag color="blue">评分 {h.score}</Tag>
                        {h.category && <Tag color="purple">{h.category}</Tag>}
                      </Space>
                      <Typography.Text type="secondary">{h.reason}</Typography.Text>
                    </Space>
                  </List.Item>
                )}
              />
            </div>
            {transcript?.text && (
              <div>
                <Typography.Title level={5}>
                  对白转写（{transcript.language}）
                </Typography.Title>
                <Typography.Paragraph ellipsis={{ rows: 6, expandable: true }}>
                  {transcript.text}
                </Typography.Paragraph>
              </div>
            )}
          </Space>
        ) : (
          <Empty description="暂无分析结果" />
        )}
      </Drawer>
    </Space>
  );
}
