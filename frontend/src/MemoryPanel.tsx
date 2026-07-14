import { Alert, Button, Empty, Input, List, Popconfirm, Space, Tag, Typography, message } from "antd";
import { useCallback, useEffect, useState } from "react";
import { api, type MemoryItem } from "./api";

export function MemoryPanel() {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.memories().then((r) => setMemories(r.memories)).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  const add = async () => {
    if (!content.trim()) return;
    setBusy(true);
    try {
      await api.addMemory(content.trim());
      message.success("偏好已保存");
      setContent("");
      load();
    } catch (e) {
      message.error(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message="系统会从你的修订指令中自动学习长期创作偏好（如「字幕偏文艺」「节奏偏快」），并在之后生成方案时应用。也可以在这里手动添加或删除。"
      />
      <Space.Compact style={{ width: "100%" }}>
        <Input
          placeholder='手动添加偏好，例如："字幕不要太直白，偏散文风格"'
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onPressEnter={add}
        />
        <Button type="primary" onClick={add} loading={busy}>添加偏好</Button>
      </Space.Compact>
      {memories.length === 0 ? (
        <Empty description="还没有沉淀的偏好" />
      ) : (
        <List
          bordered
          dataSource={memories}
          renderItem={(m) => (
            <List.Item
              actions={[
                <Popconfirm
                  key="del"
                  title="删除这条偏好？"
                  onConfirm={() =>
                    api.deleteMemory(m.id)
                      .then(() => { message.success("已删除"); load(); })
                      .catch((e) => message.error(String(e)))}
                >
                  <Button danger size="small">删除</Button>
                </Popconfirm>,
              ]}
            >
              <Space>
                <Tag color={m.source === "revision" ? "purple" : "blue"}>
                  {m.source === "revision" ? "自动学习" : "手动添加"}
                </Tag>
                <Typography.Text>{m.content}</Typography.Text>
                {m.created_at && (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {new Date(m.created_at).toLocaleString()}
                  </Typography.Text>
                )}
              </Space>
            </List.Item>
          )}
        />
      )}
    </Space>
  );
}
