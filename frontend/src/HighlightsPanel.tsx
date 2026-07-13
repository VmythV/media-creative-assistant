import { Empty, List, Space, Tag, Typography } from "antd";
import type { Highlight } from "./api";

export function HighlightsPanel({ highlights }: { highlights: Highlight[] }) {
  if (highlights.length === 0) {
    return <Empty description="完成素材分析后，这里会展示跨素材的精彩片段推荐" />;
  }
  return (
    <List
      dataSource={highlights}
      renderItem={(h) => (
        <List.Item>
          <Space direction="vertical" size={0} style={{ width: "100%" }}>
            <Space wrap>
              <Typography.Text strong>{h.filename}</Typography.Text>
              <Tag>{h.start.toFixed(1)}s - {h.end.toFixed(1)}s</Tag>
              <Tag color="blue">评分 {h.score}</Tag>
              {h.category && <Tag color="purple">{h.category}</Tag>}
            </Space>
            <Typography.Text type="secondary">{h.reason}</Typography.Text>
          </Space>
        </List.Item>
      )}
    />
  );
}
