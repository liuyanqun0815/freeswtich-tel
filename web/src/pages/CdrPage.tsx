import {
  Button,
  Descriptions,
  Form,
  Input,
  Select,
  Space,
  Table,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";

export type CdrSummary = {
  fee_display: string;
  answer_rate: number;
  restricted_rate: number;
  answered: number;
  total: number;
  talk_sec: number;
};

export type CdrItem = {
  vr_call_id: string;
  billsec: number;
  duration: number;
  agent_billsec: number;
  queue_sec: number;
  robot_sec: number;
  caller: string;
  callee: string;
  direction: string;
  hangup_cause: string;
};

type CdrPageProps = {
  summary?: CdrSummary;
  items?: CdrItem[];
};

const EMPTY_SUMMARY: CdrSummary = {
  fee_display: "—",
  answer_rate: 0,
  restricted_rate: 0,
  answered: 0,
  total: 0,
  talk_sec: 0,
};

export function CdrPage({
  summary = EMPTY_SUMMARY,
  items = [],
}: CdrPageProps = {}) {
  const columns: ColumnsType<CdrItem> = [
    { title: "通话 ID", dataIndex: "vr_call_id", key: "vr_call_id" },
    { title: "主叫", dataIndex: "caller", key: "caller" },
    { title: "被叫", dataIndex: "callee", key: "callee" },
    { title: "方向", dataIndex: "direction", key: "direction" },
    {
      title: "通话时长",
      dataIndex: "billsec",
      key: "billsec",
      render: (value: number) => String(value),
    },
    {
      title: "持续时长",
      dataIndex: "duration",
      key: "duration",
      render: (value: number) => String(value),
    },
    { title: "挂断原因", dataIndex: "hangup_cause", key: "hangup_cause" },
    {
      title: "操作",
      key: "actions",
      render: () => (
        <Space size="small">
          <Button type="link" size="small">
            详情
          </Button>
          <Button type="link" size="small">
            录音
          </Button>
          <Button type="link" size="small">
            加入黑名单
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Form layout="inline" style={{ marginBottom: 16 }}>
        <Form.Item label="通话 ID">
          <Input placeholder="通话 ID" />
        </Form.Item>
        <Form.Item label="主叫">
          <Input placeholder="主叫" />
        </Form.Item>
        <Form.Item label="被叫">
          <Input placeholder="被叫" />
        </Form.Item>
        <Form.Item label="坐席">
          <Input placeholder="坐席" />
        </Form.Item>
        <Form.Item label="方向">
          <Select placeholder="方向" style={{ width: 120 }} options={[]} />
        </Form.Item>
        <Form.Item label="日期">
          <Input placeholder="日期" />
        </Form.Item>
        <Form.Item label="是否接通">
          <Select placeholder="是否接通" style={{ width: 120 }} options={[]} />
        </Form.Item>
        <Form.Item>
          <Button type="primary">查询</Button>
        </Form.Item>
      </Form>

      <Space style={{ marginBottom: 16 }} size="large">
        <Typography.Text>
          接通 {summary.answered} / {summary.total}
        </Typography.Text>
        <Typography.Text>
          接通率 {Math.round(summary.answer_rate * 100)}%
        </Typography.Text>
        <Typography.Text>
          受限率 {Math.round(summary.restricted_rate * 100)}%
        </Typography.Text>
        <Typography.Text>
          话费{" "}
          <Typography.Text>{summary.fee_display}</Typography.Text>
        </Typography.Text>
      </Space>

      <Table
        rowKey="vr_call_id"
        columns={columns}
        dataSource={items}
        pagination={false}
      />

      {items.map((record) => (
        <Descriptions
          key={`detail-${record.vr_call_id}`}
          title="详情"
          style={{ marginTop: 16 }}
          column={3}
        >
          <Descriptions.Item label="坐席时长">
            {record.agent_billsec}
          </Descriptions.Item>
          <Descriptions.Item label="排队时长">
            {record.queue_sec}
          </Descriptions.Item>
          <Descriptions.Item label="机器人时长">
            {record.robot_sec}
          </Descriptions.Item>
        </Descriptions>
      ))}
    </div>
  );
}
