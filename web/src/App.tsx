import { useMemo, useState, type ReactNode } from "react";
import { AdminLayout } from "./layout/AdminLayout";
import { SoftphoneProvider } from "./softphone";
import { CdrPage } from "./pages/CdrPage";
import {
  AgentWorkbenchPage,
  AiScenesPage,
  BlacklistPage,
  DialTasksPage,
  ExtensionsPage,
  GatewaysNumbersPage,
  IvrPage,
  OverviewPage,
  PublishPage,
  SkillGroupsPage,
  SystemPage,
} from "./pages/ResourcePages";

const PAGE_BY_MENU: Record<string, () => ReactNode> = {
  概览: () => <OverviewPage />,
  坐席工作台: () => (
    <AgentWorkbenchPage
      extension="—"
      wssUrl={import.meta.env.VITE_WSS_URL ?? "wss://127.0.0.1:7443"}
    />
  ),
  话单: () => <CdrPage />,
  分机: () => <ExtensionsPage />,
  "线路与号码": () => <GatewaysNumbersPage />,
  IVR: () => <IvrPage />,
  技能组: () => <SkillGroupsPage />,
  外呼任务: () => <DialTasksPage />,
  黑名单: () => <BlacklistPage />,
  "AI 场景": () => <AiScenesPage />,
  配置发布: () => <PublishPage />,
  系统: () => <SystemPage />,
};

export function App() {
  const [activeMenu, setActiveMenu] = useState("话单");
  const content = useMemo(() => {
    const render = PAGE_BY_MENU[activeMenu] ?? (() => <CdrPage />);
    return render();
  }, [activeMenu]);

  return (
    <SoftphoneProvider>
      <AdminLayout
        activeMenu={activeMenu}
        onMenuSelect={setActiveMenu}
        tabItems={[{ key: activeMenu, label: activeMenu }]}
      >
        {content}
      </AdminLayout>
    </SoftphoneProvider>
  );
}
