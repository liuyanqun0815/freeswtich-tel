import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  expect(screen.getByText("未注册")).toBeInTheDocument();
  expect(screen.getByText("wss://fs.example/wss")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "呼 叫" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "挂 断" })).toBeInTheDocument();
  expect(screen.getByText("IVR 按键（通话中可用）")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "0" })).toBeDisabled();
  expect(screen.queryByText("多租户")).not.toBeInTheDocument();
});

test("blacklist page uses table and filter form", () => {
  render(<BlacklistPage />);
  expect(screen.getByText("黑名单")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /查\s*询/ })).toBeInTheDocument();
  expect(screen.getAllByText("号码").length).toBeGreaterThanOrEqual(1);
  expect(screen.getByRole("columnheader", { name: "原因" })).toBeInTheDocument();
});

test("ai scenes page opens a form with recognition, synthesis and llm sections", async () => {
  render(<AiScenesPage />);
  fireEvent.click(screen.getByRole("button", { name: "新增场景" }));
  expect(await screen.findByText("语音识别")).toBeInTheDocument();
  expect(screen.getByText("语音合成")).toBeInTheDocument();
  expect(screen.getByText("大模型")).toBeInTheDocument();
  expect(screen.queryByText("密钥环境变量名")).not.toBeInTheDocument();
  expect(screen.getByText(/VOLC_APP_ID/)).toBeInTheDocument();
  expect(screen.getByText("API Key")).toBeInTheDocument();
  expect(screen.queryByText("OpenAI 兼容")).not.toBeInTheDocument();
  expect(screen.getByText(/TENCENT_APP_ID/)).toBeInTheDocument();
  expect(screen.getByPlaceholderText("8k_zh")).toHaveValue("8k_zh");
});

test("ai scenes form sends a typed llm api key and keeps it hidden otherwise", async () => {
  const scene = {
    id: 3,
    name: "大模型场景",
    opening: "",
    fallback: "hangup",
    ws_url: null,
    system_prompt: "",
    transfer_skill_group_id: null,
    asr: { vendor: "openai", base_url: "https://asr.example/v1", model: "asr-1" },
    tts: { vendor: "volcengine", voice_type: "zh_female_shuangkuaisisi_moon_bigtts" },
    llm: { vendor: "openai", base_url: "https://api.deepseek.com/v1", model: "deepseek-chat", api_key_set: true },
  };
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => ({
    ok: true,
    status: 200,
    json: async () =>
      init?.method === "PUT" ? scene : { items: String(url).startsWith("/ai-scenes") ? [scene] : [] },
  }));
  vi.stubGlobal("fetch", fetchMock);
  sessionStorage.setItem("vr_access_token", "test-token");
  try {
    render(<AiScenesPage />);
    fireEvent.click(await screen.findByRole("button", { name: /编\s*辑/ }));
    const keyInput = await screen.findByPlaceholderText(/已设置/);
    expect(keyInput).toHaveValue("");
    fireEvent.change(keyInput, { target: { value: "sk-new" } });
    fireEvent.click(screen.getByRole("button", { name: /^(确\s*定|OK)$/ }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "PUT")).toBe(true),
    );
    const put = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PUT");
    const body = JSON.parse(String((put?.[1] as RequestInit).body));
    expect(body.llm).toEqual({
      vendor: "openai",
      base_url: "https://api.deepseek.com/v1",
      model: "deepseek-chat",
      api_key: "sk-new",
    });
  } finally {
    sessionStorage.removeItem("vr_access_token");
    vi.unstubAllGlobals();
  }
});

test("ai scenes create form does not keep values from a previously edited scene", async () => {
  const scene = {
    id: 1,
    name: "售后机器人",
    opening: "",
    fallback: "transfer",
    ws_url: null,
    system_prompt: "",
    transfer_skill_group_id: null,
    asr: { base_url: "https://asr.example/v1", model: "asr-1", language: "zh" },
    tts: { vendor: "volcengine", voice_type: "anna" },
    llm: { base_url: "https://llm.example/v1", model: "llm-1" },
  };
  const fetchMock = vi.fn(async (url: string) => ({
    ok: true,
    status: 200,
    json: async () => ({ items: String(url).startsWith("/ai-scenes") ? [scene] : [] }),
  }));
  vi.stubGlobal("fetch", fetchMock);
  sessionStorage.setItem("vr_access_token", "test-token");
  try {
    render(<AiScenesPage />);
    fireEvent.click(await screen.findByRole("button", { name: /编\s*辑/ }));
    expect(await screen.findByPlaceholderText("zh_female_shuangkuaisisi_moon_bigtts")).toHaveValue("anna");
    fireEvent.click(screen.getByRole("button", { name: /^(取\s*消|Cancel)$/ }));
    fireEvent.click(screen.getByRole("button", { name: "新增场景" }));
    await waitFor(() => expect(screen.getByPlaceholderText("zh_female_shuangkuaisisi_moon_bigtts")).toHaveValue(""));
  } finally {
    sessionStorage.removeItem("vr_access_token");
    vi.unstubAllGlobals();
  }
});

test("ai scenes form shows tencent asr fields and keeps the vendor on save", async () => {
  const scene = {
    id: 2,
    name: "腾讯识别",
    opening: "",
    fallback: "hangup",
    ws_url: null,
    system_prompt: "",
    transfer_skill_group_id: null,
    asr: { vendor: "tencent", engine_model_type: "16k_zh" },
    tts: { vendor: "volcengine", voice_type: "S_my_voice", resource_id: "volc.megatts.default" },
    llm: { vendor: "openai", base_url: "https://llm.example/v1", model: "llm-1" },
  };
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => ({
    ok: true,
    status: 200,
    json: async () =>
      init?.method === "PUT" ? scene : { items: String(url).startsWith("/ai-scenes") ? [scene] : [] },
  }));
  vi.stubGlobal("fetch", fetchMock);
  sessionStorage.setItem("vr_access_token", "test-token");
  try {
    render(<AiScenesPage />);
    fireEvent.click(await screen.findByRole("button", { name: /编\s*辑/ }));
    expect(await screen.findByPlaceholderText("8k_zh")).toHaveValue("16k_zh");
    expect(screen.getByText(/TENCENT_APP_ID/)).toBeInTheDocument();
    expect(screen.queryByText("密钥环境变量名")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^(确\s*定|OK)$/ }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "PUT")).toBe(true),
    );
    const put = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PUT");
    const body = JSON.parse(String((put?.[1] as RequestInit).body));
    expect(body.asr).toEqual(scene.asr);
    expect(body.tts).toEqual(scene.tts);
  } finally {
    sessionStorage.removeItem("vr_access_token");
    vi.unstubAllGlobals();
  }
});
