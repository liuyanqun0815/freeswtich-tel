import { Button, Form, Input, Select, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { ReactNode } from "react";

type PageShellProps = {
  title: string;
  filterItems?: ReactNode;
  columns: ColumnsType<Record<string, unknown>>;
  dataSource?: Record<string, unknown>[];
  extra?: ReactNode;
};

function PageShell({
  title,
  filterItems,
  columns,
  dataSource = [],
  extra,
}: PageShellProps) {
  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        {title}
      </Typography.Title>
      <Form layout="inline" style={{ marginBottom: 16 }}>
        {filterItems}
        <Form.Item>
          <Button type="primary">查询</Button>
        </Form.Item>
      </Form>
      {extra}
      <Table
        rowKey={(row) => String(row.id ?? row.key ?? JSON.stringify(row))}
        columns={columns}
        dataSource={dataSource}
        pagination={false}
      />
    </div>
  );
}

export function OverviewPage() {
  return (
    <PageShell
      title="概览"
      filterItems={
        <Form.Item label="日期">
          <Input placeholder="日期" />
        </Form.Item>
      }
      columns={[
        { title: "指标", dataIndex: "metric", key: "metric" },
        { title: "值", dataIndex: "value", key: "value" },
      ]}
    />
  );
}

type AgentWorkbenchPageProps = {
  extension: string;
  wssUrl: string;
};

export function AgentWorkbenchPage({
  extension,
  wssUrl,
}: AgentWorkbenchPageProps) {
  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        坐席工作台
      </Typography.Title>
      <Form layout="inline" style={{ marginBottom: 16 }}>
        <Form.Item label="分机">
          <Typography.Text>{extension}</Typography.Text>
        </Form.Item>
        <Form.Item label="WSS">
          <Typography.Text>{wssUrl}</Typography.Text>
        </Form.Item>
      </Form>
    </div>
  );
}

export function ExtensionsPage() {
  return (
    <PageShell
      title="分机"
      filterItems={
        <Form.Item label="分机号">
          <Input placeholder="分机号" />
        </Form.Item>
      }
      columns={[
        { title: "分机号", dataIndex: "ext_number", key: "ext_number" },
        { title: "绑定坐席", dataIndex: "agent", key: "agent" },
        { title: "启用", dataIndex: "enabled", key: "enabled" },
      ]}
    />
  );
}

export function GatewaysNumbersPage() {
  return (
    <PageShell
      title="线路与号码"
      filterItems={
        <>
          <Form.Item label="号码">
            <Input placeholder="号码" />
          </Form.Item>
          <Form.Item label="网关">
            <Input placeholder="网关" />
          </Form.Item>
        </>
      }
      columns={[
        { title: "号码", dataIndex: "number", key: "number" },
        { title: "入口", dataIndex: "entry", key: "entry" },
        { title: "网关状态", dataIndex: "status", key: "status" },
      ]}
    />
  );
}

export function IvrPage() {
  return (
    <PageShell
      title="IVR"
      filterItems={
        <Form.Item label="流程">
          <Input placeholder="流程" />
        </Form.Item>
      }
      columns={[
        { title: "名称", dataIndex: "name", key: "name" },
        { title: "版本", dataIndex: "version", key: "version" },
      ]}
    />
  );
}

export function SkillGroupsPage() {
  return (
    <PageShell
      title="技能组"
      filterItems={
        <Form.Item label="名称">
          <Input placeholder="名称" />
        </Form.Item>
      }
      columns={[
        { title: "名称", dataIndex: "name", key: "name" },
        { title: "溢出目标", dataIndex: "overflow_target", key: "overflow_target" },
        {
          title: "振铃超时",
          dataIndex: "ring_timeout_seconds",
          key: "ring_timeout_seconds",
        },
      ]}
    />
  );
}

export function DialTasksPage() {
  return (
    <PageShell
      title="外呼任务"
      filterItems={
        <Form.Item label="任务">
          <Input placeholder="任务" />
        </Form.Item>
      }
      columns={[
        { title: "名称", dataIndex: "name", key: "name" },
        { title: "并发", dataIndex: "concurrency", key: "concurrency" },
        { title: "启用", dataIndex: "enabled", key: "enabled" },
      ]}
    />
  );
}

export function BlacklistPage() {
  return (
    <PageShell
      title="黑名单"
      filterItems={
        <Form.Item label="号码">
          <Input placeholder="号码" />
        </Form.Item>
      }
      columns={[
        { title: "号码", dataIndex: "phone", key: "phone" },
        { title: "原因", dataIndex: "reason", key: "reason" },
        { title: "生效时间", dataIndex: "effective_at", key: "effective_at" },
      ]}
    />
  );
}

export function AiScenesPage() {
  return (
    <PageShell
      title="AI 场景"
      filterItems={
        <Form.Item label="场景">
          <Input placeholder="场景" />
        </Form.Item>
      }
      columns={[
        { title: "名称", dataIndex: "name", key: "name" },
        { title: "WebSocket", dataIndex: "ws_url", key: "ws_url" },
        { title: "降级", dataIndex: "fallback", key: "fallback" },
        { title: "开场白", dataIndex: "opening", key: "opening" },
      ]}
      extra={
        <Form layout="inline" style={{ marginBottom: 16 }}>
          <Form.Item label="降级方式">
            <Select
              style={{ width: 140 }}
              options={[
                { value: "transfer", label: "transfer" },
                { value: "hangup", label: "hangup" },
              ]}
            />
          </Form.Item>
        </Form>
      }
    />
  );
}

export function PublishPage() {
  return (
    <PageShell
      title="配置发布"
      filterItems={
        <Form.Item label="类型">
          <Select
            style={{ width: 140 }}
            options={[
              { value: "gateway", label: "网关" },
              { value: "acl", label: "ACL" },
            ]}
          />
        </Form.Item>
      }
      columns={[
        { title: "状态", dataIndex: "status", key: "status" },
        { title: "时间", dataIndex: "applied_at", key: "applied_at" },
      ]}
    />
  );
}

export function SystemPage() {
  return (
    <PageShell
      title="系统"
      filterItems={
        <Form.Item label="用户">
          <Input placeholder="用户" />
        </Form.Item>
      }
      columns={[
        { title: "用户名", dataIndex: "username", key: "username" },
        { title: "角色", dataIndex: "role", key: "role" },
        { title: "FS 节点", dataIndex: "fs_node", key: "fs_node" },
      ]}
    />
  );
}
