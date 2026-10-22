import { Button, Form, Input, Layout, Menu, Space, Tabs, Tag, message } from "antd";
import { useState, type ReactNode } from "react";
import { api, clearSession, getToken, getUsername, setSession } from "../auth";
import { MENU_LABELS } from "../menu";
import { usePhoneStatus } from "../phoneStatus";
import { sipProfileFromLogin } from "../sipProfile";
import { useSoftphone } from "../softphone";

const { Header, Sider, Content } = Layout;

type AdminLayoutProps = {
  activeMenu?: string;
  onMenuSelect?: (label: string) => void;
  tabItems?: { key: string; label: string; children?: ReactNode }[];
  children: ReactNode;
};

export function AdminLayout({
  activeMenu = "坐席工作台",
  onMenuSelect,
  tabItems = [{ key: "cdr", label: "话单", children: null }],
  children,
}: AdminLayoutProps) {
  const [username, setUsername] = useState(getUsername);
  const [loggingIn, setLoggingIn] = useState(false);
  const phone = usePhoneStatus();
  const softphone = useSoftphone();
  const headerExtension = softphone.extension || phone.extension;
  const headerRegistered = softphone.status === "registered" || phone.registered;
  const headerStatus =
    softphone.status === "registering"
      ? "注册中"
      : softphone.status === "failed"
        ? "注册失败"
        : headerRegistered
          ? "已注册"
          : "未注册";
  const menuItems = MENU_LABELS.map((label) => ({
    key: label,
    label,
  }));

  async function onLogin(values: { username: string; password: string }) {
    setLoggingIn(true);
    try {
      const resp = await fetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values),
      });
      if (!resp.ok) {
        message.error("登录失败");
        return;
      }
      const data = (await resp.json()) as {
        access_token: string;
        must_change_password: boolean;
        extension?: string | null;
        sip_password?: string | null;
        domain?: string | null;
        wss_url?: string | null;
        ice_servers?: { urls: string }[] | null;
      };
      if (data.must_change_password) {
        message.warning("该账号需要先修改密码");
        return;
      }
      const sip = sipProfileFromLogin(data);
      console.info(
        "[auth] login",
        sip
          ? { extension: sip.extension, domain: sip.domain, wssUrl: sip.wssUrl }
          : { sip: false },
      );
      setSession(data.access_token, values.username, sip);
      setUsername(values.username);
      message.success(sip ? `已登录，正在注册分机 ${sip.extension}` : "已登录");
    } finally {
      setLoggingIn(false);
    }
  }

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider width={200}>
        <Menu
          mode="inline"
          selectedKeys={[activeMenu]}
          items={menuItems}
          style={{ height: "100%" }}
          onClick={({ key }) => onMenuSelect?.(String(key))}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            background: "#fff",
            padding: "0 16px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            height: 56,
          }}
        >
          <Tabs
            style={{ marginBottom: 0 }}
            items={tabItems.map(({ key, label }) => ({ key, label }))}
          />
          {username ? (
            <Space>
              <span>{username}</span>
              {headerExtension ? (
                <Tag color={headerRegistered ? "blue" : "default"}>
                  {headerExtension} {headerStatus}
                </Tag>
              ) : phone.ready ? (
                <Tag>未绑定分机</Tag>
              ) : null}
              <Button
                size="small"
                onClick={() => {
                  void (async () => {
                    await softphone.stop();
                    if (getToken()) {
                      await api("/auth/logout", { method: "POST" }).catch(() => undefined);
                    }
                    clearSession();
                    setUsername("");
                  })();
                }}
              >
                退出
              </Button>
            </Space>
          ) : (
            <Form layout="inline" onFinish={onLogin} style={{ marginTop: 12 }}>
              <Form.Item name="username" style={{ marginBottom: 0 }}>
                <Input size="small" placeholder="用户名" autoComplete="username" />
              </Form.Item>
              <Form.Item name="password" style={{ marginBottom: 0 }}>
                <Input.Password size="small" placeholder="密码" autoComplete="current-password" />
              </Form.Item>
              <Form.Item style={{ marginBottom: 0 }}>
                <Button size="small" type="primary" htmlType="submit" loading={loggingIn}>
                  登录
                </Button>
              </Form.Item>
            </Form>
          )}
        </Header>
        <Content style={{ margin: 12, padding: 12, background: "#fff" }}>
          {children}
        </Content>
      </Layout>
    </Layout>
  );
}
