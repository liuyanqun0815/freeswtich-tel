import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { buildCdrPath, CdrPage } from "./CdrPage";

test("splits talk time and full duration", async () => {
  render(
    <CdrPage
      summary={{
        fee_display: "—",
        answer_rate: 0.5,
        restricted_rate: 0,
        answered: 1,
        total: 2,
        talk_sec: 16,
      }}
      items={[
        {
          vr_call_id: "c1",
          billsec: 16,
          duration: 45,
          agent_billsec: 10,
          queue_sec: 8,
          robot_sec: 6,
          caller: "1000",
          callee: "1001",
          direction: "inbound",
          started_at: "2026-10-26T09:53:19+00:00",
          answered_at: "2026-10-26T09:53:20+00:00",
          ended_at: "2026-10-26T09:53:39+00:00",
          hangup_cause: "NORMAL_CLEARING",
        },
      ]}
    />
  );
  expect(screen.getAllByText("开始时间").length).toBeGreaterThan(0);
  expect(screen.getAllByText("接通时间").length).toBeGreaterThan(0);
  expect(screen.getAllByText("结束时间").length).toBeGreaterThan(0);
  expect(screen.getAllByText("通话时长").length).toBeGreaterThan(0);
  expect(screen.getAllByText("持续时长").length).toBeGreaterThan(0);
  expect(screen.getByText("16")).toBeInTheDocument();
  expect(screen.getByText("45")).toBeInTheDocument();
  expect(screen.getByText("—")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "详情" }));
  await waitFor(() => expect(screen.getByText("话单详情")).toBeInTheDocument());
  expect(screen.getByText("坐席时长")).toBeInTheDocument();
  expect(screen.getByText("排队时长")).toBeInTheDocument();
  expect(screen.getByText("机器人时长")).toBeInTheDocument();
  expect(screen.getByText("10")).toBeInTheDocument();
  expect(screen.getByText("8")).toBeInTheDocument();
  expect(screen.getByText("6")).toBeInTheDocument();
});

test("query button requests the cdr api", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({
      items: [
        {
          vr_call_id: "c9",
          billsec: 3,
          duration: 4,
          agent_billsec: 1,
          queue_sec: 0,
          robot_sec: 0,
          caller: "1001",
          callee: "1000",
          direction: "inbound",
          hangup_cause: "NORMAL_CLEARING",
        },
      ],
      summary: {
        fee_display: "—",
        answer_rate: 1,
        restricted_rate: 0,
        answered: 1,
        total: 1,
        talk_sec: 3,
      },
    }),
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<CdrPage />);
  const submit = () => document.querySelector('button[type="submit"]') as HTMLButtonElement;
  await waitFor(() => expect(submit().className).not.toContain("ant-btn-loading"));
  fireEvent.change(screen.getByPlaceholderText("主叫"), { target: { value: "1001" } });
  fireEvent.click(submit());

  await waitFor(() => expect(screen.getByText("c9")).toBeInTheDocument());
  const urls = fetchMock.mock.calls.map((call) => String(call[0]));
  expect(urls.some((url) => url.startsWith("/cdr") && url.includes("caller=1001"))).toBe(true);
});

test("date filter becomes a full-day range", () => {
  expect(buildCdrPath({ caller: "1000", date: "2026-10-23" })).toBe(
    "/cdr?caller=1000&start=2026-10-23T00%3A00%3A00&end=2026-10-23T23%3A59%3A59"
  );
  expect(buildCdrPath({ date: "10-23" })).toBeNull();
});

test("action buttons open detail and call recording api", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ path: "/media/rec-c1.wav" }),
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <CdrPage
      summary={{
        fee_display: "—",
        answer_rate: 1,
        restricted_rate: 0,
        answered: 1,
        total: 1,
        talk_sec: 16,
      }}
      items={[
        {
          vr_call_id: "c1",
          billsec: 16,
          duration: 45,
          agent_billsec: 10,
          queue_sec: 8,
          robot_sec: 6,
          caller: "1000",
          callee: "1001",
          direction: "inbound",
          hangup_cause: "NORMAL_CLEARING",
        },
      ]}
    />
  );

  fireEvent.click(screen.getByRole("button", { name: "详情" }));
  await waitFor(() => expect(screen.getByText("话单详情")).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: "录音" }));
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some((call) => String(call[0]).includes("/cdr/c1/recording"))
    ).toBe(true)
  );

  expect(screen.getByRole("button", { name: "加入黑名单" })).toBeInTheDocument();
});
