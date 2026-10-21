import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { MENU_LABELS } from "../menu";
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
} from "./ResourcePages";

const PAGE_BY_TITLE: Record<string, ReactElement> = {
  概览: <OverviewPage />,
  坐席工作台: (
    <AgentWorkbenchPage extension="8001" wssUrl="wss://example/ws" />
  ),
  分机: <ExtensionsPage />,
  线路与号码: <GatewaysNumbersPage />,
  IVR: <IvrPage />,
  技能组: <SkillGroupsPage />,
  外呼任务: <DialTasksPage />,
  黑名单: <BlacklistPage />,
  "AI 场景": <AiScenesPage />,
  配置发布: <PublishPage />,
  系统: <SystemPage />,
};

test("resource page titles match MENU_LABELS and have no tenant switch", () => {
  for (const label of MENU_LABELS) {
    if (label === "话单") {
      continue;
    }
    const page = PAGE_BY_TITLE[label];
    expect(page).toBeTruthy();
    const { unmount } = render(page);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByText("多租户")).not.toBeInTheDocument();
    unmount();
  }
});

test("agent workbench shows extension and WSS only", () => {
  render(
    <AgentWorkbenchPage extension="8001" wssUrl="wss://fs.example/wss" />
  );
  expect(screen.getByText("坐席工作台")).toBeInTheDocument();
  expect(screen.getByText("8001")).toBeInTheDocument();
  expect(screen.getByText("wss://fs.example/wss")).toBeInTheDocument();
  expect(screen.queryByText("多租户")).not.toBeInTheDocument();
});

test("blacklist page uses table and filter form", () => {
  render(<BlacklistPage />);
  expect(screen.getByText("黑名单")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /查\s*询/ })).toBeInTheDocument();
  expect(screen.getAllByText("号码").length).toBeGreaterThanOrEqual(1);
  expect(screen.getByRole("columnheader", { name: "原因" })).toBeInTheDocument();
});
