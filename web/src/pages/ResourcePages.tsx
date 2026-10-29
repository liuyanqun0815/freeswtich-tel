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
  Upload,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, getToken, uploadMedia } from "../auth";
import { usePhoneStatus } from "../phoneStatus";
import { useSoftphone } from "../softphone";

function SoundPathField({
  value,
  onChange,
  placeholder = "/usr/local/freeswitch/sounds/vr/xxx.wav",
}: {
  value?: string;
  onChange?: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <Space.Compact style={{ width: "100%" }}>
      <Input
        value={value}
        onChange={(event) => onChange?.(event.target.value)}
        placeholder={placeholder}
      />
      <Upload
        accept=".wav,.mp3,.gsm,audio/*"
        showUploadList={false}
        customRequest={async (options) => {
          const file = options.file as File;
          try {
            const result = await uploadMedia(file);
            onChange?.(result.path);
            message.success("上传成功");
            options.onSuccess?.(result);
          } catch (err) {
            const text = err instanceof Error ? err.message : "上传失败";
            message.error(text);
            options.onError?.(err as Error);
          }
        }}
      >
        <Button>上传</Button>
      </Upload>
    </Space.Compact>
  );
}

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
  const [transferMode, setTransferMode] = useState<"extension" | "skill_group">("extension");
  const [transferTarget, setTransferTarget] = useState("");
  const [transferSkillGroupId, setTransferSkillGroupId] = useState<number | undefined>();
  const [skillGroups, setSkillGroups] = useState<{ id: number; name: string }[]>([]);
  const [dialing, setDialing] = useState(false);
  const [transferring, setTransferring] = useState(false);
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

  useEffect(() => {
    if (!getToken()) {
      setSkillGroups([]);
      return;
    }
    void (async () => {
      const resp = await api("/skill-groups");
      if (!resp.ok) {
        return;
      }
      const body = (await resp.json()) as { items: { id: number; name: string }[] };
      setSkillGroups(body.items ?? []);
    })();
  }, [signedIn]);

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

  async function onTransfer() {
    if (!getToken()) {
      message.error("请先登录");
      return;
    }
    const body =
      transferMode === "skill_group"
        ? { skill_group_id: transferSkillGroupId }
        : { target: transferTarget.trim() };
    if (transferMode === "skill_group" && transferSkillGroupId == null) {
      message.error("请选择转接技能组");
      return;
    }
    if (transferMode === "extension" && !transferTarget.trim()) {
      message.error("请填写转接分机");
      return;
    }
    setTransferring(true);
    try {
      const resp = await api("/agent/transfer", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const detail = await resp.json().catch(() => null);
        const msg =
          typeof detail?.detail === "string"
            ? detail.detail
            : Array.isArray(detail?.detail)
              ? detail.detail.map((item: { msg?: string }) => item?.msg).filter(Boolean).join("; ") ||
                `转接失败 (${resp.status})`
              : `转接失败 (${resp.status})`;
        message.error(msg);
        return;
      }
      message.success("已发起转接：主叫等待对端接听，本机将自动退出通话");
      setTransferTarget("");
      setTransferSkillGroupId(undefined);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "转接失败");
    } finally {
      setTransferring(false);
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
      {softphone.trace ? (
        <Typography.Paragraph type="secondary" style={{ whiteSpace: "pre-wrap" }}>
          {softphone.trace}
        </Typography.Paragraph>
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
        <Select
          value={transferMode}
          style={{ width: 110 }}
          disabled={softphone.callState !== "in-call"}
          options={[
            { value: "extension", label: "转分机" },
            { value: "skill_group", label: "转技能组" },
          ]}
          onChange={(value: "extension" | "skill_group") => {
            setTransferMode(value);
            setTransferTarget("");
            setTransferSkillGroupId(undefined);
          }}
        />
        {transferMode === "skill_group" ? (
          <Select
            value={transferSkillGroupId}
            placeholder="选择技能组"
            style={{ width: 160 }}
            disabled={softphone.callState !== "in-call"}
            options={skillGroups.map((sg) => ({ value: sg.id, label: sg.name }))}
            onChange={(value: number) => setTransferSkillGroupId(value)}
            allowClear
          />
        ) : (
          <Input
            value={transferTarget}
            placeholder="转接分机"
            style={{ width: 120 }}
            disabled={softphone.callState !== "in-call"}
            onChange={(event) => setTransferTarget(event.target.value)}
          />
        )}
        <Button
          loading={transferring}
          disabled={
            softphone.callState !== "in-call" ||
            (transferMode === "skill_group" ? transferSkillGroupId == null : !transferTarget.trim())
          }
          onClick={() => void onTransfer()}
        >
          转接
        </Button>
      </Space>
      <div style={{ marginBottom: 12 }}>通话：{callLabel}</div>
      <Typography.Text type="secondary">IVR 按键（通话中可用）</Typography.Text>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 48px)",
          gap: 8,
          marginTop: 8,
        }}
      >
        {["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"].map((digit) => (
          <Button
            key={digit}
            disabled={softphone.callState !== "in-call"}
            onClick={() => {
              void softphone.sendDtmf(digit).catch((err) => {
                message.error(err instanceof Error ? err.message : "按键失败");
              });
            }}
          >
            {digit}
          </Button>
        ))}
      </div>
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

type NumberRow = {
  id: number;
  number: string;
  label: string | null;
  enabled: boolean;
  entry_kind: string | null;
  entry_target: string | null;
};

type NumberForm = {
  number: string;
  label?: string;
  enabled: boolean;
  entry_kind?: string;
  entry_target?: string | number;
};

type GatewayRow = {
  id: number;
  name: string;
  profile: string;
  register: boolean;
  username: string | null;
  realm: string | null;
  proxy: string | null;
  enabled: boolean;
  published: boolean;
};

type GatewayForm = {
  name: string;
  proxy?: string;
  username?: string;
  password?: string;
  realm?: string;
  register: boolean;
  enabled: boolean;
};

const ENTRY_KIND_OPTIONS = [
  { value: "extension", label: "分机" },
  { value: "queue", label: "技能组" },
  { value: "ivr", label: "IVR" },
  { value: "robot", label: "机器人" },
  { value: "playback", label: "放音" },
];

type EntryOptionRow = { id: number; ext_number?: string; display_name?: string | null; name?: string };

function entryLabel(
  row: NumberRow,
  lookups: {
    extensions: EntryOptionRow[];
    skillGroups: EntryOptionRow[];
    ivrFlows: EntryOptionRow[];
    aiScenes: EntryOptionRow[];
  },
): string {
  if (!row.entry_kind || !row.entry_target) {
    return "—";
  }
  const kind = ENTRY_KIND_OPTIONS.find((item) => item.value === row.entry_kind)?.label ?? row.entry_kind;
  let target = row.entry_target;
  if (row.entry_kind === "extension") {
    const ext = lookups.extensions.find((item) => item.ext_number === row.entry_target);
    if (ext?.display_name) {
      target = `${ext.ext_number} ${ext.display_name}`;
    }
  } else if (row.entry_kind === "queue") {
    const sg = lookups.skillGroups.find((item) => String(item.id) === row.entry_target);
    if (sg?.name) {
      target = sg.name;
    }
  } else if (row.entry_kind === "ivr") {
    const flow = lookups.ivrFlows.find((item) => String(item.id) === row.entry_target);
    if (flow?.name) {
      target = `${flow.id} ${flow.name}`;
    }
  } else if (row.entry_kind === "robot") {
    const scene = lookups.aiScenes.find((item) => String(item.id) === row.entry_target);
    if (scene?.name) {
      target = scene.name;
    }
  }
  return `${kind} / ${target}`;
}

export function GatewaysNumbersPage() {
  const [numbers, setNumbers] = useState<NumberRow[]>([]);
  const [gateways, setGateways] = useState<GatewayRow[]>([]);
  const [extensions, setExtensions] = useState<EntryOptionRow[]>([]);
  const [skillGroups, setSkillGroups] = useState<EntryOptionRow[]>([]);
  const [ivrFlows, setIvrFlows] = useState<EntryOptionRow[]>([]);
  const [aiScenes, setAiScenes] = useState<EntryOptionRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [numberOpen, setNumberOpen] = useState(false);
  const [gatewayOpen, setGatewayOpen] = useState(false);
  const [editingNumber, setEditingNumber] = useState<NumberRow | null>(null);
  const [editingGateway, setEditingGateway] = useState<GatewayRow | null>(null);
  const [numberForm] = Form.useForm<NumberForm>();
  const [gatewayForm] = Form.useForm<GatewayForm>();

  const load = useCallback(async () => {
    if (!getToken()) {
      setNumbers([]);
      setGateways([]);
      setExtensions([]);
      setSkillGroups([]);
      setIvrFlows([]);
      setAiScenes([]);
      return;
    }
    setLoading(true);
    try {
      const [numberResp, gatewayResp, extResp, sgResp, ivrResp, sceneResp] = await Promise.all([
        api("/numbers"),
        api("/gateways"),
        api("/extensions"),
        api("/skill-groups"),
        api("/ivr"),
        api("/ai-scenes"),
      ]);
      if (numberResp.ok) {
        const body = (await numberResp.json()) as { items: NumberRow[] };
        setNumbers(body.items);
      }
      if (gatewayResp.ok) {
        const body = (await gatewayResp.json()) as { items: GatewayRow[] };
        setGateways(body.items);
      }
      if (extResp.ok) {
        const body = (await extResp.json()) as { items: EntryOptionRow[] };
        setExtensions(body.items ?? []);
      }
      if (sgResp.ok) {
        const body = (await sgResp.json()) as { items: EntryOptionRow[] };
        setSkillGroups(body.items ?? []);
      }
      if (ivrResp.ok) {
        const body = (await ivrResp.json()) as { items: EntryOptionRow[] };
        setIvrFlows(body.items ?? []);
      }
      if (sceneResp.ok) {
        const body = (await sceneResp.json()) as { items: EntryOptionRow[] };
        setAiScenes(body.items ?? []);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function openNumberCreate() {
    setEditingNumber(null);
    numberForm.setFieldsValue({ number: "", label: "", enabled: true, entry_kind: undefined, entry_target: "" });
    setNumberOpen(true);
  }

  function openNumberEdit(row: NumberRow) {
    setEditingNumber(row);
    numberForm.setFieldsValue({
      number: row.number,
      label: row.label ?? "",
      enabled: row.enabled,
      entry_kind: row.entry_kind ?? undefined,
      entry_target: row.entry_target ?? undefined,
    });
    setNumberOpen(true);
  }

  async function onNumberSubmit(values: NumberForm) {
    const target =
      values.entry_kind === "queue" || values.entry_kind === "ivr"
        ? values.entry_target != null && values.entry_target !== ""
          ? String(values.entry_target)
          : null
        : values.entry_target?.toString().trim() || null;
    const body = {
      number: values.number.trim(),
      label: values.label?.trim() || null,
      enabled: values.enabled,
      entry_kind: values.entry_kind || null,
      entry_target: target,
    };
    const resp = editingNumber
      ? await api(`/numbers/${editingNumber.id}`, { method: "PUT", body: JSON.stringify(body) })
      : await api("/numbers", { method: "POST", body: JSON.stringify(body) });
    if (resp.status === 409) {
      message.error("号码已存在");
      return;
    }
    if (!resp.ok) {
      message.error(editingNumber ? "保存失败" : "新增失败");
      return;
    }
    message.success(editingNumber ? "已保存" : "已新增");
    setNumberOpen(false);
    await load();
  }

  async function onNumberDelete(row: NumberRow) {
    const resp = await api(`/numbers/${row.id}`, { method: "DELETE" });
    if (!resp.ok) {
      message.error("删除失败");
      return;
    }
    message.success("已删除");
    await load();
  }

  function openGatewayCreate() {
    setEditingGateway(null);
    gatewayForm.setFieldsValue({
      name: "",
      proxy: "",
      username: "",
      password: "",
      realm: "",
      register: true,
      enabled: true,
    });
    setGatewayOpen(true);
  }

  function openGatewayEdit(row: GatewayRow) {
    setEditingGateway(row);
    gatewayForm.setFieldsValue({
      name: row.name,
      proxy: row.proxy ?? "",
      username: row.username ?? "",
      password: "",
      realm: row.realm ?? "",
      register: row.register,
      enabled: row.enabled,
    });
    setGatewayOpen(true);
  }

  async function onGatewaySubmit(values: GatewayForm) {
    const body: Record<string, unknown> = {
      name: values.name.trim(),
      proxy: values.proxy?.trim() || null,
      username: values.username?.trim() || null,
      realm: values.realm?.trim() || null,
      register: values.register,
      enabled: values.enabled,
      profile: "external",
    };
    if (values.password?.trim()) {
      body.password_encrypted = values.password.trim();
    }
    const resp = editingGateway
      ? await api(`/gateways/${editingGateway.id}`, { method: "PUT", body: JSON.stringify(body) })
      : await api("/gateways", { method: "POST", body: JSON.stringify(body) });
    if (resp.status === 409) {
      message.error("线路名称已存在");
      return;
    }
    if (!resp.ok) {
      message.error(editingGateway ? "保存失败" : "新增失败");
      return;
    }
    message.success(editingGateway ? "已保存" : "已新增");
    setGatewayOpen(false);
    await load();
  }

  async function onGatewayDelete(row: GatewayRow) {
    const resp = await api(`/gateways/${row.id}`, { method: "DELETE" });
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
        线路与号码
      </Typography.Title>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <Typography.Title level={5} style={{ margin: 0 }}>
          号码
        </Typography.Title>
        <Button type="primary" onClick={openNumberCreate}>
          新增号码
        </Button>
      </div>
      <Table<NumberRow>
        style={{ marginTop: 12 }}
        size="small"
        bordered
        rowKey="id"
        loading={loading}
        dataSource={numbers}
        locale={{ emptyText: getToken() ? "暂无数据" : "请先在右上角登录" }}
        pagination={{ pageSize: 8, showTotal: (total) => `共 ${total} 条` }}
        columns={[
          { title: "号码", dataIndex: "number", width: 160 },
          { title: "名称", dataIndex: "label", render: (value: string | null) => value || "—" },
          {
            title: "入口",
            render: (_: unknown, row: NumberRow) =>
              entryLabel(row, { extensions, skillGroups, ivrFlows, aiScenes }),
          },
          {
            title: "启用",
            dataIndex: "enabled",
            width: 90,
            render: (value: boolean) => (value ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>),
          },
          {
            title: "操作",
            width: 140,
            render: (_: unknown, row: NumberRow) => (
              <Space>
                <Button type="link" size="small" onClick={() => openNumberEdit(row)}>
                  编辑
                </Button>
                <Popconfirm title="确认删除该号码？" onConfirm={() => onNumberDelete(row)}>
                  <Button type="link" size="small" danger>
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginTop: 28 }}>
        <div>
          <Typography.Title level={5} style={{ margin: 0 }}>
            线路
          </Typography.Title>
          <Typography.Text type="secondary">
            保存后是草稿。在「配置发布」发布后，FreeSWITCH 才会使用这条线路。
          </Typography.Text>
        </div>
        <Button type="primary" onClick={openGatewayCreate}>
          新增线路
        </Button>
      </div>
      <Table<GatewayRow>
        style={{ marginTop: 12 }}
        size="small"
        bordered
        rowKey="id"
        loading={loading}
        dataSource={gateways}
        locale={{ emptyText: getToken() ? "暂无数据" : "请先在右上角登录" }}
        pagination={{ pageSize: 8, showTotal: (total) => `共 ${total} 条` }}
        columns={[
          { title: "名称", dataIndex: "name", width: 140 },
          { title: "代理", dataIndex: "proxy", render: (value: string | null) => value || "—" },
          { title: "账号", dataIndex: "username", render: (value: string | null) => value || "—" },
          {
            title: "注册",
            dataIndex: "register",
            width: 90,
            render: (value: boolean) => (value ? <Tag color="blue">注册</Tag> : <Tag>不注册</Tag>),
          },
          {
            title: "启用",
            dataIndex: "enabled",
            width: 90,
            render: (value: boolean) => (value ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>),
          },
          {
            title: "网关状态",
            dataIndex: "published",
            width: 110,
            render: (value: boolean) => (value ? <Tag color="green">已发布</Tag> : <Tag>未发布</Tag>),
          },
          {
            title: "操作",
            width: 140,
            render: (_: unknown, row: GatewayRow) => (
              <Space>
                <Button type="link" size="small" onClick={() => openGatewayEdit(row)}>
                  编辑
                </Button>
                <Popconfirm title="确认删除该线路？" onConfirm={() => onGatewayDelete(row)}>
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
        title={editingNumber ? "编辑号码" : "新增号码"}
        open={numberOpen}
        onCancel={() => setNumberOpen(false)}
        onOk={() => numberForm.submit()}
        destroyOnHidden
      >
        <Form form={numberForm} layout="vertical" onFinish={onNumberSubmit}>
          <Form.Item name="number" label="号码" rules={[{ required: true, message: "请输入号码" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="label" label="名称">
            <Input />
          </Form.Item>
          <Form.Item name="entry_kind" label="入口类型">
            <Select
              allowClear
              options={ENTRY_KIND_OPTIONS}
              placeholder="不设置入口"
              onChange={() => numberForm.setFieldsValue({ entry_target: undefined })}
            />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(prev, next) => prev.entry_kind !== next.entry_kind}>
            {() => {
              const kind = numberForm.getFieldValue("entry_kind") as string | undefined;
              if (!kind) {
                return null;
              }
              if (kind === "extension") {
                return (
                  <Form.Item
                    name="entry_target"
                    label="入口目标"
                    rules={[{ required: true, message: "请选择分机" }]}
                  >
                    <Select
                      placeholder="选择分机"
                      options={extensions.map((ext) => ({
                        value: ext.ext_number,
                        label: ext.display_name
                          ? `${ext.ext_number} ${ext.display_name}`
                          : ext.ext_number,
                      }))}
                    />
                  </Form.Item>
                );
              }
              if (kind === "queue") {
  return (
                  <Form.Item
                    name="entry_target"
                    label="入口目标"
                    rules={[{ required: true, message: "请选择技能组" }]}
                  >
                    <Select
                      placeholder="选择技能组"
                      options={skillGroups.map((sg) => ({
                        value: String(sg.id),
                        label: sg.name,
                      }))}
                    />
        </Form.Item>
                );
              }
              if (kind === "ivr") {
                return (
                  <Form.Item
                    name="entry_target"
                    label="入口目标"
                    rules={[{ required: true, message: "请选择 IVR" }]}
                  >
                    <Select
                      placeholder="选择 IVR 流程"
                      options={ivrFlows.map((flow) => ({
                        value: String(flow.id),
                        label: `${flow.id} ${flow.name}`,
                      }))}
                    />
                  </Form.Item>
                );
              }
              if (kind === "playback") {
                return (
                  <Form.Item
                    name="entry_target"
                    label="入口目标"
                    rules={[{ required: true, message: "请上传或填写放音路径" }]}
                    extra="上传后写入 FS 可读目录，也可手填绝对路径"
                  >
                    <SoundPathField />
                  </Form.Item>
                );
              }
              return (
                <Form.Item
                  name="entry_target"
                  label="入口目标"
                  rules={[{ required: true, message: "请选择 AI 场景" }]}
                >
                  <Select
                    placeholder="选择 AI 场景"
                    options={aiScenes.map((scene) => ({
                      value: String(scene.id),
                      label: `${scene.id} ${scene.name}`,
                    }))}
                  />
                </Form.Item>
              );
            }}
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={editingGateway ? "编辑线路" : "新增线路"}
        open={gatewayOpen}
        onCancel={() => setGatewayOpen(false)}
        onOk={() => gatewayForm.submit()}
        destroyOnHidden
      >
        <Form form={gatewayForm} layout="vertical" onFinish={onGatewaySubmit}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入线路名称" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="proxy" label="代理">
            <Input placeholder="运营商 SIP 地址" />
          </Form.Item>
          <Form.Item name="username" label="账号">
            <Input />
          </Form.Item>
          <Form.Item name="password" label="密码">
            <Input.Password placeholder={editingGateway ? "留空则不修改" : ""} />
          </Form.Item>
          <Form.Item name="realm" label="域">
            <Input />
          </Form.Item>
          <Form.Item name="register" label="向运营商注册" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

type SkillGroupRow = {
  id: number;
  name: string;
  members: string[];
  ring_timeout_seconds: number;
  overflow_target: string | null;
};

type SkillGroupForm = {
  name: string;
  members: string[];
  ring_timeout_seconds: number;
};

type ExtensionOption = { id: number; ext_number: string; display_name: string | null };

type IvrKeyRow = {
  digit: string;
  kind: "extension" | "playback" | "hangup" | "robot";
  target?: string;
  skill_group_id?: number;
};

type IvrFlowRow = {
  id: number;
  name: string;
  version: number;
  draft: {
    id?: string;
    kind?: string;
    prompt?: string;
    children?: Array<Record<string, unknown>>;
  };
};

type IvrFlowForm = {
  name: string;
  prompt: string;
  keys: IvrKeyRow[];
};

const IVR_ACTION_OPTIONS = [
  { value: "extension", label: "转分机 / 转人工" },
  { value: "robot", label: "AI 场景" },
  { value: "playback", label: "放音" },
  { value: "hangup", label: "挂机" },
];

const IVR_KEY_KINDS = new Set<IvrKeyRow["kind"]>(["extension", "playback", "hangup", "robot"]);

function draftFromForm(values: IvrFlowForm): Record<string, unknown> {
  return {
    id: "root",
    kind: "menu",
    prompt: values.prompt.trim(),
    children: (values.keys || []).map((key, index) => {
      const child: Record<string, unknown> = {
        digit: String(key.digit).trim(),
        id: `n${index + 1}`,
        kind: key.kind,
      };
      if (key.kind === "extension") {
        child.target = key.target;
        if (key.skill_group_id != null) {
          child.skill_group_id = key.skill_group_id;
        }
      } else if (key.kind === "playback" || key.kind === "robot") {
        child.target = key.target != null ? String(key.target).trim() : undefined;
      }
      return child;
    }),
  };
}

function formFromDraft(row: IvrFlowRow): IvrFlowForm {
  const children = Array.isArray(row.draft?.children) ? row.draft.children : [];
  return {
    name: row.name,
    prompt: String(row.draft?.prompt || ""),
    keys: children.map((child) => {
      const rawKind = String(child.kind || "hangup") as IvrKeyRow["kind"];
      const kind = IVR_KEY_KINDS.has(rawKind) ? rawKind : "hangup";
      return {
        digit: String(child.digit || ""),
        kind,
        target: child.target != null ? String(child.target) : undefined,
        skill_group_id:
          child.skill_group_id != null && child.skill_group_id !== ""
            ? Number(child.skill_group_id)
            : undefined,
      };
    }),
  };
}

export function IvrPage() {
  const [items, setItems] = useState<IvrFlowRow[]>([]);
  const [extensions, setExtensions] = useState<ExtensionOption[]>([]);
  const [skillGroups, setSkillGroups] = useState<SkillGroupRow[]>([]);
  const [aiScenes, setAiScenes] = useState<{ id: number; name: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<IvrFlowRow | null>(null);
  const [form] = Form.useForm<IvrFlowForm>();

  const load = useCallback(async () => {
    if (!getToken()) {
      setItems([]);
      return;
    }
    setLoading(true);
    try {
      const [ivrResp, extResp, sgResp, sceneResp] = await Promise.all([
        api("/ivr"),
        api("/extensions"),
        api("/skill-groups"),
        api("/ai-scenes"),
      ]);
      if (ivrResp.ok) {
        const body = (await ivrResp.json()) as { items: IvrFlowRow[] };
        setItems(body.items ?? []);
      }
      if (extResp.ok) {
        const body = (await extResp.json()) as { items: ExtensionOption[] };
        setExtensions(body.items ?? []);
      }
      if (sgResp.ok) {
        const body = (await sgResp.json()) as { items: SkillGroupRow[] };
        setSkillGroups(body.items ?? []);
      }
      if (sceneResp.ok) {
        const body = (await sceneResp.json()) as { items: { id: number; name: string }[] };
        setAiScenes(body.items ?? []);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function openCreate() {
    setEditing(null);
    form.setFieldsValue({
      name: "",
      prompt: "",
      keys: [{ digit: "0", kind: "extension", target: undefined, skill_group_id: undefined }],
    });
    setOpen(true);
  }

  function openEdit(row: IvrFlowRow) {
    setEditing(row);
    form.setFieldsValue(formFromDraft(row));
    setOpen(true);
  }

  async function onSubmit(values: IvrFlowForm) {
    const body = { name: values.name.trim(), draft: draftFromForm(values) };
    const resp = editing
      ? await api(`/ivr/${editing.id}`, { method: "PUT", body: JSON.stringify(body) })
      : await api("/ivr", { method: "POST", body: JSON.stringify(body) });
    if (!resp.ok) {
      message.error(editing ? "保存失败" : "新增失败");
      return;
    }
    message.success(editing ? "已保存草稿" : "已新增");
    setOpen(false);
    await load();
  }

  async function onPublish(row: IvrFlowRow) {
    const resp = await api(`/ivr/${row.id}/publish`, { method: "POST" });
    if (!resp.ok) {
      const detail = await resp.json().catch(() => null);
      message.error(
        Array.isArray(detail?.detail) ? `发布失败：${detail.detail.join(",")}` : "发布失败",
      );
      return;
    }
    const body = (await resp.json()) as { version: number };
    message.success(`已发布 v${body.version}`);
    await load();
  }

  async function onDelete(row: IvrFlowRow) {
    const resp = await api(`/ivr/${row.id}`, { method: "DELETE" });
    if (!resp.ok) {
      message.error("删除失败");
      return;
    }
    message.success("已删除");
    await load();
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          IVR
        </Typography.Title>
        <Button type="primary" onClick={openCreate}>
          新增流程
        </Button>
      </div>
      <Typography.Text type="secondary">
        保存为草稿后需点「发布」。号码入口选 IVR 时，目标填流程 ID。按键可转分机、接入 AI 场景、放音或挂机；转分机离线时进兜底技能组。
      </Typography.Text>
      <Table<IvrFlowRow>
        style={{ marginTop: 12 }}
        size="small"
        bordered
        rowKey="id"
        loading={loading}
        dataSource={items}
        locale={{ emptyText: getToken() ? "暂无数据" : "请先在右上角登录" }}
        pagination={false}
      columns={[
          { title: "ID", dataIndex: "id", width: 80 },
          { title: "名称", dataIndex: "name" },
          {
            title: "已发布版本",
            dataIndex: "version",
            width: 120,
            render: (value: number) => (value > 0 ? `v${value}` : "未发布"),
          },
          {
            title: "按键数",
            width: 90,
            render: (_: unknown, row: IvrFlowRow) =>
              Array.isArray(row.draft?.children) ? row.draft.children.length : 0,
          },
          {
            title: "操作",
            width: 220,
            render: (_: unknown, row: IvrFlowRow) => (
              <Space>
                <Button type="link" size="small" onClick={() => openEdit(row)}>
                  编辑
                </Button>
                <Button type="link" size="small" onClick={() => void onPublish(row)}>
                  发布
                </Button>
                <Popconfirm title="确认删除该 IVR？" onConfirm={() => onDelete(row)}>
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
        title={editing ? "编辑 IVR" : "新增 IVR"}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        width={720}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={(values) => void onSubmit(values)}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input placeholder="主菜单" />
          </Form.Item>
          <Form.Item
            name="prompt"
            label="欢迎音"
            rules={[{ required: true, message: "请上传或填写欢迎音" }]}
            extra="可上传 wav，或填 FS 路径如 ivr/ivr-welcome"
          >
            <SoundPathField placeholder="ivr/ivr-welcome 或上传后的绝对路径" />
          </Form.Item>
          <Form.List name="keys">
            {(fields, { add, remove }) => (
              <>
                {fields.map((field) => (
                  <Space key={field.key} align="start" style={{ display: "flex", marginBottom: 8 }} wrap>
                    <Form.Item
                      {...field}
                      name={[field.name, "digit"]}
                      label="按键"
                      rules={[{ required: true, message: "按键" }]}
                    >
                      <Input style={{ width: 64 }} placeholder="0" maxLength={1} />
                    </Form.Item>
                    <Form.Item
                      {...field}
                      name={[field.name, "kind"]}
                      label="动作"
                      rules={[{ required: true, message: "动作" }]}
                    >
                      <Select
                        style={{ width: 150 }}
                        options={IVR_ACTION_OPTIONS}
                        onChange={() =>
                          form.setFieldValue(["keys", field.name, "target"], undefined)
                        }
                      />
                    </Form.Item>
                    <Form.Item noStyle shouldUpdate>
                      {() => {
                        const kind = form.getFieldValue(["keys", field.name, "kind"]) as IvrKeyRow["kind"];
                        if (kind === "extension") {
                          return (
                            <>
                              <Form.Item
                                {...field}
                                name={[field.name, "target"]}
                                label="分机"
                                rules={[{ required: true, message: "选择分机" }]}
                              >
                                <Select
                                  style={{ width: 140 }}
                                  placeholder="分机"
                                  options={extensions.map((ext) => ({
                                    value: ext.ext_number,
                                    label: ext.display_name
                                      ? `${ext.ext_number} ${ext.display_name}`
                                      : ext.ext_number,
                                  }))}
                                />
                              </Form.Item>
                              <Form.Item
                                {...field}
                                name={[field.name, "skill_group_id"]}
                                label="兜底技能组"
                                rules={[{ required: true, message: "选择技能组" }]}
                              >
                                <Select
                                  style={{ width: 160 }}
                                  placeholder="技能组"
                                  options={skillGroups.map((sg) => ({
                                    value: sg.id,
                                    label: sg.name,
                                  }))}
                                />
                              </Form.Item>
                            </>
                          );
                        }
                        if (kind === "robot") {
                          return (
                            <Form.Item
                              {...field}
                              name={[field.name, "target"]}
                              label="AI 场景"
                              rules={[{ required: true, message: "请选择 AI 场景" }]}
                            >
                              <Select
                                style={{ width: 200 }}
                                placeholder="选择 AI 场景"
                                options={aiScenes.map((scene) => ({
                                  value: String(scene.id),
                                  label: scene.name,
                                }))}
                              />
                            </Form.Item>
                          );
                        }
                        if (kind === "playback") {
                          return (
                            <Form.Item
                              {...field}
                              name={[field.name, "target"]}
                              label="放音文件"
                              rules={[{ required: true, message: "请上传或填写放音路径" }]}
                              style={{ minWidth: 280 }}
                            >
                              <SoundPathField placeholder="上传或填写路径" />
                            </Form.Item>
                          );
                        }
                        return null;
                      }}
                    </Form.Item>
                    <Button type="link" danger onClick={() => remove(field.name)} style={{ marginTop: 30 }}>
                      删除
                    </Button>
                  </Space>
                ))}
                <Button
                  type="dashed"
                  onClick={() =>
                    add({ digit: "", kind: "extension", target: undefined, skill_group_id: undefined })
                  }
                  block
                >
                  添加按键
                </Button>
              </>
            )}
          </Form.List>
        </Form>
      </Modal>
    </div>
  );
}

export function SkillGroupsPage() {
  const [items, setItems] = useState<SkillGroupRow[]>([]);
  const [extensions, setExtensions] = useState<ExtensionOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<SkillGroupRow | null>(null);
  const [form] = Form.useForm<SkillGroupForm>();

  const load = useCallback(async () => {
    if (!getToken()) {
      setItems([]);
      return;
    }
    setLoading(true);
    try {
      const [sgResp, extResp] = await Promise.all([api("/skill-groups"), api("/extensions")]);
      if (sgResp.ok) {
        const body = (await sgResp.json()) as { items: SkillGroupRow[] };
        setItems(body.items ?? []);
      }
      if (extResp.ok) {
        const body = (await extResp.json()) as { items: ExtensionOption[] };
        setExtensions(body.items ?? []);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function openCreate() {
    setEditing(null);
    form.setFieldsValue({ name: "", members: [], ring_timeout_seconds: 20 });
    setOpen(true);
  }

  function openEdit(row: SkillGroupRow) {
    setEditing(row);
    form.setFieldsValue({
      name: row.name,
      members: row.members ?? [],
      ring_timeout_seconds: row.ring_timeout_seconds,
    });
    setOpen(true);
  }

  async function onSubmit(values: SkillGroupForm) {
    const body = {
      name: values.name.trim(),
      members: values.members ?? [],
      ring_timeout_seconds: Number(values.ring_timeout_seconds) || 20,
    };
    const resp = editing
      ? await api(`/skill-groups/${editing.id}`, { method: "PUT", body: JSON.stringify(body) })
      : await api("/skill-groups", { method: "POST", body: JSON.stringify(body) });
    if (!resp.ok) {
      message.error(editing ? "保存失败" : "新增失败");
      return;
    }
    message.success(editing ? "已保存" : "已新增");
    setOpen(false);
    await load();
  }

  async function onDelete(row: SkillGroupRow) {
    const resp = await api(`/skill-groups/${row.id}`, { method: "DELETE" });
    if (!resp.ok) {
      message.error("删除失败");
      return;
    }
    message.success("已删除");
    await load();
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          技能组
        </Typography.Title>
        <Button type="primary" onClick={openCreate}>
          新增技能组
        </Button>
      </div>
      <Table<SkillGroupRow>
        style={{ marginTop: 12 }}
        size="small"
        bordered
        rowKey="id"
        loading={loading}
        dataSource={items}
        locale={{ emptyText: getToken() ? "暂无数据" : "请先在右上角登录" }}
        pagination={false}
      columns={[
          { title: "名称", dataIndex: "name" },
          {
            title: "成员分机",
            dataIndex: "members",
            render: (members: string[]) => (members?.length ? members.join(", ") : "—"),
          },
          { title: "振铃超时(秒)", dataIndex: "ring_timeout_seconds", width: 120 },
          {
            title: "操作",
            width: 140,
            render: (_: unknown, row: SkillGroupRow) => (
              <Space>
                <Button type="link" size="small" onClick={() => openEdit(row)}>
                  编辑
                </Button>
                <Popconfirm title="确认删除该技能组？" onConfirm={() => onDelete(row)}>
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
        title={editing ? "编辑技能组" : "新增技能组"}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={(values) => void onSubmit(values)}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input placeholder="销售组" />
          </Form.Item>
          <Form.Item name="members" label="成员分机" rules={[{ required: true, message: "请选择分机" }]}>
            <Select
              mode="multiple"
              placeholder="选择分机"
              options={extensions.map((ext) => ({
                value: ext.ext_number,
                label: ext.display_name ? `${ext.ext_number} ${ext.display_name}` : ext.ext_number,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="ring_timeout_seconds"
            label="振铃超时(秒)"
            rules={[{ required: true, message: "请输入超时" }]}
          >
            <Input type="number" min={5} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
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

type ProviderConfig = {
  vendor?: string;
  base_url?: string;
  model?: string;
  engine_model_type?: string;
  voice_type?: string;
  voice_id?: string;
  resource_id?: string;
  region?: string;
  api_key?: string;
  api_key_set?: boolean;
};

type AiSceneRow = {
  id: number;
  name: string;
  opening: string;
  fallback: string;
  ws_url?: string | null;
  system_prompt: string;
  transfer_skill_group_id: number | null;
  asr: ProviderConfig;
  tts: ProviderConfig;
  llm: ProviderConfig;
};

type AiSceneForm = Omit<AiSceneRow, "id" | "transfer_skill_group_id"> & {
  transfer_skill_group_id?: number;
};

const FALLBACK_OPTIONS = [
  { value: "transfer", label: "转人工" },
  { value: "hangup", label: "挂机" },
];

const trimmed = (value?: string) => value?.trim() || undefined;

function asrBody(config: ProviderConfig = {}): ProviderConfig {
  return { vendor: "tencent", engine_model_type: trimmed(config.engine_model_type) };
}

const TTS_VENDOR_OPTIONS = [
  { value: "volcengine", label: "火山引擎" },
  { value: "tencent", label: "腾讯云" },
];

function ttsBody(config: ProviderConfig = {}): ProviderConfig {
  const vendor = config.vendor === "tencent" ? "tencent" : "volcengine";
  if (vendor === "tencent") {
    return {
      vendor,
      voice_id: trimmed(config.voice_id),
      model: trimmed(config.model),
      region: trimmed(config.region),
    };
  }
  return { vendor, voice_type: trimmed(config.voice_type), resource_id: trimmed(config.resource_id) };
}

function llmBody(config: ProviderConfig = {}): ProviderConfig {
  return {
    vendor: "openai",
    base_url: trimmed(config.base_url),
    model: trimmed(config.model),
    api_key: trimmed(config.api_key),
  };
}

function LlmFields({ keySet }: { keySet: boolean }) {
  return (
    <>
      <Typography.Title level={5}>大模型</Typography.Title>
      <Form.Item
        name={["llm", "base_url"]}
        label="接口地址"
        rules={[{ required: true, message: "请填写接口地址" }]}
        extra="OpenAI 兼容格式，例如 https://api.deepseek.com/v1"
      >
        <Input placeholder="https://api.example.com/v1" />
        </Form.Item>
      <Form.Item name={["llm", "api_key"]} label="API Key" extra="保存后不再显示；改了接口地址需要重新填写">
        <Input.Password
          autoComplete="new-password"
          placeholder={keySet ? "已设置，留空则不修改" : "sk-..."}
        />
      </Form.Item>
      <Form.Item name={["llm", "model"]} label="模型" rules={[{ required: true, message: "请填写模型" }]}>
        <Input placeholder="deepseek-chat" />
      </Form.Item>
    </>
  );
}

function TencentAsrFields() {
  return (
    <>
      <Typography.Paragraph type="secondary">
        使用腾讯云实时语音识别。AppId、SecretId、SecretKey 写在服务器 .env 的 TENCENT_APP_ID、TENCENT_SECRET_ID、TENCENT_SECRET_KEY
      </Typography.Paragraph>
      <Form.Item name={["asr", "engine_model_type"]} label="识别模型" extra="电话音频用 8k_zh">
        <Input placeholder="8k_zh" />
      </Form.Item>
    </>
  );
}

function VolcTtsFields() {
  return (
    <>
      <Typography.Paragraph type="secondary">
        使用火山引擎双向流式语音合成。AppId、Access Token 写在服务器 .env 的 VOLC_APP_ID、VOLC_ACCESS_TOKEN
      </Typography.Paragraph>
      <Form.Item name={["tts", "voice_type"]} label="音色" rules={[{ required: true, message: "请填写音色" }]}>
        <Input placeholder="S_BiN8cuJi1" />
      </Form.Item>
      <Form.Item
        name={["tts", "resource_id"]}
        label="资源 ID"
        extra="留空自动选择：S_ 开头的复刻音色用 volc.megatts.default，其他用 volc.service_type.10029"
      >
        <Input placeholder="volc.megatts.default" />
      </Form.Item>
    </>
  );
}

function TencentTtsFields() {
  return (
    <>
      <Typography.Paragraph type="secondary">
        使用腾讯云 TRTC 流式语音合成。SecretId/SecretKey 复用 TENCENT_SECRET_ID、TENCENT_SECRET_KEY；SdkAppId
        写在服务器 .env 的 TENCENT_TRTC_SDK_APP_ID
      </Typography.Paragraph>
      <Form.Item name={["tts", "voice_id"]} label="音色 ID" rules={[{ required: true, message: "请填写音色 ID" }]}>
        <Input placeholder="v-female-R2s4N9qJ" />
      </Form.Item>
      <Form.Item name={["tts", "model"]} label="模型" extra="默认 flow_02_turbo；高阶可选 flow_01_ex">
        <Input placeholder="flow_02_turbo" />
      </Form.Item>
      <Form.Item name={["tts", "region"]} label="地域" extra="默认 ap-guangzhou">
        <Input placeholder="ap-guangzhou" />
      </Form.Item>
    </>
  );
}

function TtsFields() {
  const vendor = Form.useWatch(["tts", "vendor"]);
  return (
    <>
      <Form.Item name={["tts", "vendor"]} label="合成厂商" rules={[{ required: true, message: "请选择厂商" }]}>
        <Select options={TTS_VENDOR_OPTIONS} />
      </Form.Item>
      {vendor === "tencent" ? <TencentTtsFields /> : <VolcTtsFields />}
    </>
  );
}

export function AiScenesPage() {
  const [items, setItems] = useState<AiSceneRow[]>([]);
  const [skillGroups, setSkillGroups] = useState<{ id: number; name: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<AiSceneRow | null>(null);
  const [form] = Form.useForm<AiSceneForm>();
  const fallback = Form.useWatch("fallback", form);

  const load = useCallback(async () => {
    if (!getToken()) {
      setItems([]);
      return;
    }
    setLoading(true);
    try {
      const [sceneResp, sgResp] = await Promise.all([api("/ai-scenes"), api("/skill-groups")]);
      if (sceneResp.ok) {
        const body = (await sceneResp.json()) as { items: AiSceneRow[] };
        setItems(body.items ?? []);
      }
      if (sgResp.ok) {
        const body = (await sgResp.json()) as { items: { id: number; name: string }[] };
        setSkillGroups(body.items ?? []);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function openCreate() {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({
      name: "",
      opening: "",
      fallback: "transfer",
      system_prompt: "",
      transfer_skill_group_id: undefined,
      asr: { vendor: "tencent", engine_model_type: "8k_zh" },
      tts: { vendor: "volcengine", voice_type: "S_BiN8cuJi1", resource_id: "volc.megatts.default" },
      llm: {},
    });
    setOpen(true);
  }

  function openEdit(row: AiSceneRow) {
    setEditing(row);
    form.resetFields();
    form.setFieldsValue({ ...row, transfer_skill_group_id: row.transfer_skill_group_id ?? undefined });
    setOpen(true);
  }

  async function onSubmit(values: AiSceneForm) {
    const body = {
      name: values.name.trim(),
      opening: values.opening?.trim() ?? "",
      fallback: values.fallback,
      ws_url: editing?.ws_url ?? null,
      system_prompt: values.system_prompt?.trim() ?? "",
      transfer_skill_group_id:
        values.fallback === "transfer" ? (values.transfer_skill_group_id ?? null) : null,
      asr: asrBody(values.asr),
      tts: ttsBody(values.tts),
      llm: llmBody(values.llm),
    };
    const resp = editing
      ? await api(`/ai-scenes/${editing.id}`, { method: "PUT", body: JSON.stringify(body) })
      : await api("/ai-scenes", { method: "POST", body: JSON.stringify(body) });
    if (!resp.ok) {
      message.error(editing ? "保存失败" : "新增失败");
      return;
    }
    message.success(editing ? "已保存" : "已新增");
    setOpen(false);
    await load();
  }

  async function onDelete(row: AiSceneRow) {
    const resp = await api(`/ai-scenes/${row.id}`, { method: "DELETE" });
    if (!resp.ok) {
      message.error("删除失败");
      return;
    }
    message.success("已删除");
    await load();
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          AI 场景
        </Typography.Title>
        <Button type="primary" onClick={openCreate}>
          新增场景
        </Button>
      </div>
      <Table<AiSceneRow>
        style={{ marginTop: 12 }}
        size="small"
        bordered
        rowKey="id"
        loading={loading}
        dataSource={items}
        locale={{ emptyText: getToken() ? "暂无数据" : "请先在右上角登录" }}
        pagination={false}
      columns={[
          { title: "ID", dataIndex: "id", width: 60 },
          { title: "名称", dataIndex: "name" },
          { title: "开场白", dataIndex: "opening", ellipsis: true },
          { title: "对话模型", render: (_: unknown, row: AiSceneRow) => row.llm?.model || "—" },
          {
            title: "降级",
            dataIndex: "fallback",
            render: (value: string) => FALLBACK_OPTIONS.find((item) => item.value === value)?.label ?? value,
          },
          {
            title: "转人工技能组",
            dataIndex: "transfer_skill_group_id",
            render: (value: number | null) => skillGroups.find((sg) => sg.id === value)?.name ?? "—",
          },
          {
            title: "操作",
            width: 140,
            render: (_: unknown, row: AiSceneRow) => (
              <Space>
                <Button type="link" size="small" onClick={() => openEdit(row)}>
                  编辑
                </Button>
                <Popconfirm title="确认删除该场景？" onConfirm={() => onDelete(row)}>
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
        title={editing ? "编辑场景" : "新增场景"}
        open={open}
        width={720}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={(values) => void onSubmit(values)}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input placeholder="售后机器人" />
          </Form.Item>
          <Form.Item name="opening" label="开场白" extra="不含「智能助理」时会自动在前面加「我是智能助理。」">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="system_prompt" label="系统提示词">
            <Input.TextArea rows={4} placeholder="你是某某公司的电话客服，回答简短口语化" />
          </Form.Item>
          <Form.Item name="fallback" label="失败降级" rules={[{ required: true, message: "请选择降级方式" }]}>
            <Select options={FALLBACK_OPTIONS} />
          </Form.Item>
          {fallback === "transfer" ? (
            <Form.Item
              name="transfer_skill_group_id"
              label="转人工技能组"
              rules={[{ required: true, message: "失败降级为转人工时请选择技能组" }]}
              extra="机器人失败或主动转人工时，将接入该技能组中在线的分机；未选则会挂断"
            >
              <Select
                placeholder="选择技能组"
                options={skillGroups.map((sg) => ({ value: sg.id, label: sg.name }))}
              />
            </Form.Item>
          ) : null}
          <Typography.Title level={5}>语音识别</Typography.Title>
          <TencentAsrFields />
          <Typography.Title level={5}>语音合成</Typography.Title>
          <TtsFields />
          <LlmFields keySet={Boolean(editing?.llm?.api_key_set)} />
        </Form>
      </Modal>
    </div>
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
