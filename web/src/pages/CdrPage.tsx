import {
  Button,
  Descriptions,
  Form,
  Input,
  Select,
  Space,
  Table,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useState } from "react";
import { api } from "../auth";

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

type CdrQuery = {
  call_id?: string;
  caller?: string;
  callee?: string;
  agent?: string;
  direction?: string;
  answered?: string;
  date?: string;
};

type CdrPageProps = {
  summary?: CdrSummary;
  items?: CdrItem[];
};

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

export function buildCdrPath(values: CdrQuery = {}): string | null {
  const params = new URLSearchParams();
  const put = (key: string, value?: string) => {
    const text = value?.trim();
    if (text) {
      params.set(key, text);
    }
  };
  put("call_id", values.call_id);
  put("caller", values.caller);
  put("callee", values.callee);
  put("agent", values.agent);
  put("direction", values.direction);
  if (values.answered === "true" || values.answered === "false") {
    params.set("answered", values.answered);
  }
  const date = values.date?.trim();
  if (date) {
    if (!DATE_PATTERN.test(date)) {
      return null;
    }
    params.set("start", `${date}T00:00:00`);
    params.set("end", `${date}T23:59:59`);
  }
  const query = params.toString();
  return query ? `/cdr?${query}` : "/cdr";
}

const EMPTY_SUMMARY: CdrSummary = {
  fee_display: "—",
  answer_rate: 0,
  restricted_rate: 0,
  answered: 0,
  total: 0,
  talk_sec: 0,
};

export function CdrPage({
  summary: summaryProp,
  items: itemsProp,
}: CdrPageProps = {}) {
  const [summary, setSummary] = useState<CdrSummary>(summaryProp ?? EMPTY_SUMMARY);
  const [items, setItems] = useState<CdrItem[]>(itemsProp ?? []);
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm<CdrQuery>();

  const load = useCallback(async (values: CdrQuery) => {
    const path = buildCdrPath(values);
    if (path === null) {
      message.error("日期请按 YYYY-MM-DD 填写");
      return;
    }
    setLoading(true);
    try {
      const resp = await api(path);
      if (resp.status === 401 || resp.status === 403) {
        message.error("请先登录后再查询话单");
        return;
      }
      if (!resp.ok) {
        message.error("话单查询失败");
        return;
      }
      const data = (await resp.json()) as { items?: CdrItem[]; summary?: CdrSummary };
      setItems(data.items ?? []);
      setSummary(data.summary ?? EMPTY_SUMMARY);
    } catch {
      message.error("话单查询失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (itemsProp !== undefined) {
      return;
    }
    void load({});
  }, [itemsProp, load]);

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
      <Form
        form={form}
        layout="inline"
        style={{ marginBottom: 16 }}
        onFinish={(values) => void load(values)}
      >
        <Form.Item label="通话 ID" name="call_id">
          <Input placeholder="通话 ID" allowClear />
        </Form.Item>
        <Form.Item label="主叫" name="caller">
          <Input placeholder="主叫" allowClear />
        </Form.Item>
        <Form.Item label="被叫" name="callee">
          <Input placeholder="被叫" allowClear />
        </Form.Item>
        <Form.Item label="坐席" name="agent">
          <Input placeholder="坐席" allowClear />
        </Form.Item>
        <Form.Item label="方向" name="direction">
          <Select
            placeholder="方向"
            style={{ width: 120 }}
            allowClear
            options={[
              { value: "inbound", label: "呼入" },
              { value: "outbound", label: "呼出" },
            ]}
          />
        </Form.Item>
        <Form.Item label="日期" name="date">
          <Input placeholder="YYYY-MM-DD" allowClear />
        </Form.Item>
        <Form.Item label="是否接通" name="answered">
          <Select
            placeholder="是否接通"
            style={{ width: 120 }}
            allowClear
            options={[
              { value: "true", label: "已接通" },
              { value: "false", label: "未接通" },
            ]}
          />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={loading}>
            查询
          </Button>
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
        loading={loading}
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
