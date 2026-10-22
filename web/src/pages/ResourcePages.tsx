import {
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, getToken } from "../auth";
import { usePhoneStatus } from "../phoneStatus";
import { useSoftphone } from "../softphone";

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
        size="small"
        bordered
        rowKey={(row) => String(row.id ?? row.key ?? JSON.stringify(row))}
        columns={columns}
        dataSource={dataSource}
        locale={{ emptyText: "暂无数据" }}
        pagination={{ pageSize: 10, showTotal: (total) => `共 ${total} 条` }}
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
  const [callee, setCallee] = useState("");
  const [dialing, setDialing] = useState(false);
  const phone = usePhoneStatus();
  const softphone = useSoftphone();
  const signedIn = Boolean(getToken());
  const extLabel = softphone.extension || (signedIn ? phone.extension || (phone.ready ? "未绑定" : "…") : extension);
  const wssLabel = signedIn && phone.wssUrl ? phone.wssUrl : wssUrl;
  const regLabel =
    softphone.status === "registering"
      ? "注册中"
      : softphone.status === "registered"
        ? "已注册"
        : softphone.status === "failed"
          ? "注册失败"
          : signedIn
            ? !phone.ready
              ? "…"
              : !phone.extension
                ? "未绑定"
                : phone.registered
                  ? "已注册"
                  : "未注册"
            : extension && extension !== "—"
              ? "未注册"
              : "未绑定";

  async function onDial() {
    if (!getToken()) {
      message.error("请先登录");
      return;
    }
    if (softphone.status !== "registered") {
      message.error("分机尚未注册");
      return;
    }
    setDialing(true);
    try {
      await softphone.call(callee.trim());
      message.success("正在呼叫");
    } catch (err) {
      message.error(err instanceof Error ? err.message : "呼叫失败");
    } finally {
      setDialing(false);
    }
  }

  async function onHangup() {
    try {
      await softphone.hangup();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "挂断失败");
    }
  }

  const callLabel =
    softphone.callState === "in-call" ? "通话中" : softphone.callState === "calling" ? "呼叫中" : "—";

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        坐席工作台
      </Typography.Title>
      <Form layout="inline" style={{ marginBottom: 16 }}>
        <Form.Item label="分机">
          <Typography.Text>{extLabel}</Typography.Text>
        </Form.Item>
        <Form.Item label="注册状态">
          {regLabel === "已注册" ? <Tag color="blue">已注册</Tag> : <Tag>{regLabel}</Tag>}
        </Form.Item>
        <Form.Item label="WSS">
          <Typography.Text>{wssLabel}</Typography.Text>
        </Form.Item>
      </Form>
      <Typography.Paragraph type="secondary">
        登录后浏览器通过 WebSocket 向 FreeSWITCH 注册。退出或关闭页面会注销，来电不再振铃到这个浏览器。
      </Typography.Paragraph>
      {softphone.error ? (
        <Typography.Paragraph type="danger">{softphone.error}</Typography.Paragraph>
      ) : null}
      {softphone.incoming ? (
        <Space style={{ marginBottom: 12 }}>
          <span>来电 {softphone.incoming}</span>
          <Button type="primary" onClick={() => void softphone.answer()}>
            接听
          </Button>
          <Button onClick={() => void softphone.decline()}>拒接</Button>
        </Space>
      ) : null}
      <Space wrap style={{ marginBottom: 12 }}>
        <Input
          value={callee}
          placeholder="被叫分机"
          style={{ width: 140 }}
          onChange={(event) => setCallee(event.target.value)}
        />
        <Button
          type="primary"
          loading={dialing}
          disabled={softphone.status !== "registered" || !callee.trim()}
          onClick={() => void onDial()}
        >
          呼叫
        </Button>
        <Button danger disabled={softphone.callState === "idle"} onClick={() => void onHangup()}>
          挂断
        </Button>
      </Space>
      <div>通话：{callLabel}</div>
    </div>
  );
}

type ExtensionRow = {
  id: number;
  ext_number: string;
  display_name: string | null;
  enabled: boolean;
  registered?: boolean;
  contact?: string;
  bound_username?: string;
};

type ExtensionForm = {
  ext_number: string;
  display_name?: string;
  enabled: boolean;
};

export function ExtensionsPage() {
  const [items, setItems] = useState<ExtensionRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ExtensionRow | null>(null);
  const [form] = Form.useForm<ExtensionForm>();

  const load = useCallback(async () => {
    if (!getToken()) {
      setItems([]);
      return;
    }
    setLoading(true);
    try {
      const resp = await api("/extensions");
      if (resp.status === 401 || resp.status === 403) {
        message.error("请使用管理员账号登录");
        setItems([]);
        return;
      }
      if (!resp.ok) {
        message.error("分机列表加载失败");
        return;
      }
      const data = (await resp.json()) as { items: ExtensionRow[] };
      setItems(data.items ?? []);
    } catch {
      message.error("分机列表加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const onAuth = () => void load();
    window.addEventListener("vr-auth", onAuth);
    const timer = window.setInterval(() => void load(), 5000);
    return () => {
      window.removeEventListener("vr-auth", onAuth);
      window.clearInterval(timer);
    };
  }, [load]);

  const shown = useMemo(() => {
    const q = keyword.trim();
    if (!q) {
      return items;
    }
    return items.filter(
      (row) => row.ext_number.includes(q) || (row.display_name ?? "").includes(q),
    );
  }, [items, keyword]);

  function openCreate() {
    setEditing(null);
    form.setFieldsValue({ ext_number: "", display_name: "", enabled: true });
    setOpen(true);
  }

  function openEdit(row: ExtensionRow) {
    setEditing(row);
    form.setFieldsValue({
      ext_number: row.ext_number,
      display_name: row.display_name ?? "",
      enabled: row.enabled,
    });
    setOpen(true);
  }

  async function onSubmit(values: ExtensionForm) {
    const body = {
      ext_number: values.ext_number.trim(),
      display_name: values.display_name?.trim() || null,
      enabled: values.enabled,
    };
    const resp = editing
      ? await api(`/extensions/${editing.id}`, {
          method: "PUT",
          body: JSON.stringify(body),
        })
      : await api("/extensions", { method: "POST", body: JSON.stringify(body) });
    if (resp.status === 409) {
      message.error("分机号已存在");
      return;
    }
    if (!resp.ok) {
      message.error(editing ? "保存失败" : "新增失败");
      return;
    }
    message.success(editing ? "已保存" : "已新增");
    setOpen(false);
    await load();
  }

  async function onDelete(row: ExtensionRow) {
    const resp = await api(`/extensions/${row.id}`, { method: "DELETE" });
    if (resp.status === 409) {
      message.error("分机已绑定用户，不能删除");
      return;
    }
    if (!resp.ok) {
      message.error("删除失败");
      return;
    }
    message.success("已删除");
    await load();
  }

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        分机
      </Typography.Title>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <Form
          layout="inline"
          onFinish={(values: { ext?: string }) => setKeyword(values.ext ?? "")}
        >
          <Form.Item name="ext" label="分机号">
            <Input allowClear placeholder="分机号 / 显示名" style={{ width: 180 }} />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                查询
              </Button>
              <Button onClick={() => setKeyword("")}>重置</Button>
            </Space>
          </Form.Item>
        </Form>
        <Button type="primary" onClick={openCreate}>
          新增
        </Button>
      </div>
      <Table<ExtensionRow>
        style={{ marginTop: 12 }}
        size="small"
        bordered
        rowKey="id"
        loading={loading}
        dataSource={shown}
        locale={{ emptyText: getToken() ? "暂无数据" : "请先在右上角登录" }}
        pagination={{ pageSize: 10, showTotal: (total) => `共 ${total} 条` }}
        columns={[
          { title: "分机号", dataIndex: "ext_number", width: 140 },
          {
            title: "显示名",
            dataIndex: "display_name",
            render: (value: string | null) => value || "—",
          },
          {
            title: "启用",
            dataIndex: "enabled",
            width: 90,
            render: (value: boolean) =>
              value ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>,
          },
          {
            title: "注册",
            dataIndex: "registered",
            width: 100,
            render: (value: boolean) =>
              value ? <Tag color="blue">已注册</Tag> : <Tag>未注册</Tag>,
          },
          {
            title: "绑定用户",
            dataIndex: "bound_username",
            width: 140,
            render: (value: string) => value || "—",
          },
          {
            title: "操作",
            width: 140,
            render: (_: unknown, row: ExtensionRow) => (
              <Space>
                <Button type="link" size="small" onClick={() => openEdit(row)}>
                  编辑
                </Button>
                <Popconfirm title="确认删除该分机？" onConfirm={() => onDelete(row)}>
                  <Button type="link" size="small" danger>
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
      <Modal
        title={editing ? "编辑分机" : "新增分机"}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={onSubmit}>
          <Form.Item
            name="ext_number"
            label="分机号"
            rules={[{ required: true, message: "请输入分机号" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="display_name" label="显示名">
            <Input />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
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

type UserRow = {
  id: number;
  username: string;
  role: string;
  extension_id: number | null;
  ext_number: string | null;
};

type UserForm = {
  username: string;
  password: string;
  role: "admin" | "agent";
  extension_id?: number | null;
};

export function SystemPage() {
  const [items, setItems] = useState<UserRow[]>([]);
  const [extensions, setExtensions] = useState<ExtensionRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [open, setOpen] = useState(false);
  const [binding, setBinding] = useState<UserRow | null>(null);
  const [form] = Form.useForm<UserForm>();
  const [bindForm] = Form.useForm<{ extension_id?: number | null }>();

  const load = useCallback(async () => {
    if (!getToken()) {
      setItems([]);
      setExtensions([]);
      return;
    }
    setLoading(true);
    try {
      const [usersResp, extResp] = await Promise.all([api("/users"), api("/extensions")]);
      if (usersResp.status === 401 || usersResp.status === 403) {
        message.error("请使用管理员账号登录");
        setItems([]);
        return;
      }
      if (!usersResp.ok) {
        message.error("用户列表加载失败");
        return;
      }
      const users = (await usersResp.json()) as { items: UserRow[] };
      setItems(users.items ?? []);
      if (extResp.ok) {
        const exts = (await extResp.json()) as { items: ExtensionRow[] };
        setExtensions(exts.items ?? []);
      }
    } catch {
      message.error("用户列表加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const onAuth = () => void load();
    window.addEventListener("vr-auth", onAuth);
    return () => window.removeEventListener("vr-auth", onAuth);
  }, [load]);

  const shown = useMemo(() => {
    const q = keyword.trim();
    if (!q) {
      return items;
    }
    return items.filter(
      (row) => row.username.includes(q) || (row.ext_number ?? "").includes(q),
    );
  }, [items, keyword]);

  const extensionOptions = extensions.map((row) => ({
    value: row.id,
    label: row.bound_username
      ? `${row.ext_number}（已绑定 ${row.bound_username}）`
      : row.ext_number,
  }));

  function openCreate() {
    form.setFieldsValue({
      username: "",
      password: "",
      role: "agent",
      extension_id: undefined,
    });
    setOpen(true);
  }

  function openBind(row: UserRow) {
    setBinding(row);
    bindForm.setFieldsValue({ extension_id: row.extension_id ?? undefined });
  }

  async function onCreate(values: UserForm) {
    const resp = await api("/users", {
      method: "POST",
      body: JSON.stringify({
        username: values.username.trim(),
        password: values.password,
        role: values.role,
        must_change_password: false,
        extension_id: values.extension_id ?? null,
      }),
    });
    if (resp.status === 409) {
      message.error("分机已绑定其他用户");
      return;
    }
    if (resp.status === 404) {
      message.error("分机不存在");
      return;
    }
    if (!resp.ok) {
      message.error("新增用户失败");
      return;
    }
    message.success("已新增");
    setOpen(false);
    await load();
  }

  async function onBind(values: { extension_id?: number | null }) {
    if (!binding) {
      return;
    }
    const resp = await api(`/users/${binding.id}`, {
      method: "PUT",
      body: JSON.stringify({ extension_id: values.extension_id ?? null }),
    });
    if (resp.status === 409) {
      message.error("分机已绑定其他用户");
      return;
    }
    if (!resp.ok) {
      message.error("绑定失败");
      return;
    }
    message.success(values.extension_id ? "已绑定" : "已解除绑定");
    setBinding(null);
    await load();
  }

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        系统
      </Typography.Title>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <Form
          layout="inline"
          onFinish={(values: { q?: string }) => setKeyword(values.q ?? "")}
        >
          <Form.Item name="q" label="用户">
            <Input allowClear placeholder="用户名 / 分机号" style={{ width: 180 }} />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                查询
              </Button>
              <Button onClick={() => setKeyword("")}>重置</Button>
            </Space>
          </Form.Item>
        </Form>
        <Button type="primary" onClick={openCreate}>
          新增
        </Button>
      </div>
      <Table<UserRow>
        style={{ marginTop: 12 }}
        size="small"
        bordered
        rowKey="id"
        loading={loading}
        dataSource={shown}
        locale={{ emptyText: getToken() ? "暂无数据" : "请先在右上角登录" }}
        pagination={{ pageSize: 10, showTotal: (total) => `共 ${total} 条` }}
        columns={[
          { title: "用户名", dataIndex: "username", width: 160 },
          {
            title: "角色",
            dataIndex: "role",
            width: 100,
            render: (value: string) => (value === "admin" ? "管理员" : "坐席"),
          },
          {
            title: "绑定分机",
            dataIndex: "ext_number",
            width: 140,
            render: (value: string | null) => value || "—",
          },
          {
            title: "操作",
            width: 120,
            render: (_: unknown, row: UserRow) => (
              <Button type="link" size="small" onClick={() => openBind(row)}>
                绑定
              </Button>
            ),
          },
        ]}
      />
      <Modal
        title="新增用户"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={onCreate}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: "请输入用户名" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: "请输入密码" }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "agent", label: "坐席" },
                { value: "admin", label: "管理员" },
              ]}
            />
          </Form.Item>
          <Form.Item name="extension_id" label="绑定分机">
            <Select allowClear placeholder="可不绑定" options={extensionOptions} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={binding ? `绑定分机：${binding.username}` : "绑定分机"}
        open={binding !== null}
        onCancel={() => setBinding(null)}
        onOk={() => bindForm.submit()}
        destroyOnHidden
      >
        <Form form={bindForm} layout="vertical" onFinish={onBind}>
          <Form.Item name="extension_id" label="分机">
            <Select allowClear placeholder="留空则解除绑定" options={extensionOptions} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
