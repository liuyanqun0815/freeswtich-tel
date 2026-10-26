import {
  Button,
  Descriptions,
  Form,
  Input,
  Modal,
  Popconfirm,
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
  started_at?: string;
  answered_at?: string;
  ended_at?: string;
  hangup_cause: string;
};

function formatCdrTime(value?: string): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

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
  const [detail, setDetail] = useState<CdrItem | null>(null);
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

  async function onPlayRecording(row: CdrItem) {
    const resp = await api(`/cdr/${encodeURIComponent(row.vr_call_id)}/recording`);
    if (resp.status === 404) {
      message.warning("该通话没有录音");
      return;
    }
    if (!resp.ok) {
      message.error("获取录音失败");
      return;
    }
    const body = (await resp.json()) as { path?: string };
    message.info(body.path ? `录音地址：${body.path}` : "暂无可用录音地址");
  }

  async function onAddBlacklist(row: CdrItem) {
    const phone = (row.caller || row.callee || "").trim();
    if (!phone) {
      message.error("该话单没有可用号码");
      return;
    }
    const resp = await api("/blacklist", {
      method: "POST",
      body: JSON.stringify({
        phone,
        reason: `话单 ${row.vr_call_id}`,
      }),
    });
    if (resp.status === 403) {
      message.error("需要管理员权限才能加入黑名单");
      return;
    }
    if (!resp.ok) {
      message.error("加入黑名单失败");
      return;
    }
    message.success(`已将 ${phone} 加入黑名单`);
  }

  const columns: ColumnsType<CdrItem> = [
    { title: "通话 ID", dataIndex: "vr_call_id", key: "vr_call_id", ellipsis: true },
    { title: "主叫", dataIndex: "caller", key: "caller" },
    { title: "被叫", dataIndex: "callee", key: "callee" },
    { title: "方向", dataIndex: "direction", key: "direction" },
    {
      title: "开始时间",
      dataIndex: "started_at",
      key: "started_at",
      width: 170,
      render: (value: string | undefined) => formatCdrTime(value),
    },
    {
      title: "接通时间",
      dataIndex: "answered_at",
      key: "answered_at",
      width: 170,
      render: (value: string | undefined) => formatCdrTime(value),
    },
    {
      title: "结束时间",
      dataIndex: "ended_at",
      key: "ended_at",
      width: 170,
      render: (value: string | undefined) => formatCdrTime(value),
    },
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
      fixed: "right",
      width: 220,
      render: (_: unknown, row: CdrItem) => (
        <Space size="small">
          <Button type="link" size="small" onClick={() => setDetail(row)}>
            详情
          </Button>
          <Button type="link" size="small" onClick={() => void onPlayRecording(row)}>
            录音
          </Button>
          <Popconfirm
            title={`将 ${row.caller || row.callee || "该号码"} 加入黑名单？`}
            onConfirm={() => void onAddBlacklist(row)}
          >
            <Button type="link" size="small">
              加入黑名单
            </Button>
          </Popconfirm>
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
        pagination={{ pageSize: 20, showTotal: (total) => `共 ${total} 条` }}
        scroll={{ x: 1400 }}
      />

      <Modal
        title="话单详情"
        open={detail !== null}
        onCancel={() => setDetail(null)}
        footer={
          <Button type="primary" onClick={() => setDetail(null)}>
            关闭
          </Button>
        }
        width={720}
        destroyOnHidden
      >
        {detail ? (
          <Descriptions column={2} size="small">
            <Descriptions.Item label="通话 ID" span={2}>
              {detail.vr_call_id}
            </Descriptions.Item>
            <Descriptions.Item label="主叫">{detail.caller || "—"}</Descriptions.Item>
            <Descriptions.Item label="被叫">{detail.callee || "—"}</Descriptions.Item>
            <Descriptions.Item label="方向">{detail.direction || "—"}</Descriptions.Item>
            <Descriptions.Item label="挂断原因">{detail.hangup_cause || "—"}</Descriptions.Item>
            <Descriptions.Item label="开始时间">
              {formatCdrTime(detail.started_at)}
            </Descriptions.Item>
            <Descriptions.Item label="接通时间">
              {formatCdrTime(detail.answered_at)}
            </Descriptions.Item>
            <Descriptions.Item label="结束时间">
              {formatCdrTime(detail.ended_at)}
            </Descriptions.Item>
            <Descriptions.Item label="通话时长">{detail.billsec}</Descriptions.Item>
            <Descriptions.Item label="持续时长">{detail.duration}</Descriptions.Item>
            <Descriptions.Item label="坐席时长">{detail.agent_billsec}</Descriptions.Item>
            <Descriptions.Item label="排队时长">{detail.queue_sec}</Descriptions.Item>
            <Descriptions.Item label="机器人时长">{detail.robot_sec}</Descriptions.Item>
          </Descriptions>
        ) : null}
      </Modal>
    </div>
  );
}
