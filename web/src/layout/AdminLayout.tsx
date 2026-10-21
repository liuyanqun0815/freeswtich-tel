import { Layout, Menu, Tabs } from "antd";
import type { ReactNode } from "react";
import { MENU_LABELS } from "../menu";

const { Header, Sider, Content } = Layout;

type AdminLayoutProps = {
  activeMenu?: string;
  tabItems?: { key: string; label: string; children?: ReactNode }[];
  children: ReactNode;
};

export function AdminLayout({
  activeMenu = "话单",
  tabItems = [{ key: "cdr", label: "话单", children: null }],
  children,
}: AdminLayoutProps) {
  const menuItems = MENU_LABELS.map((label) => ({
    key: label,
    label,
  }));

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider width={200}>
        <Menu
          mode="inline"
          selectedKeys={[activeMenu]}
          items={menuItems}
          style={{ height: "100%" }}
        />
      </Sider>
      <Layout>
        <Header style={{ background: "#fff", padding: "0 16px" }}>
          <Tabs items={tabItems.map(({ key, label }) => ({ key, label }))} />
        </Header>
        <Content style={{ margin: 16, padding: 16, background: "#fff" }}>
          {children}
        </Content>
      </Layout>
    </Layout>
  );
}
