import { render, screen } from "@testing-library/react";
import { CdrPage } from "./CdrPage";

test("splits talk time and full duration", () => {
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
          hangup_cause: "NORMAL_CLEARING",
        },
      ]}
    />
  );
  expect(screen.getByText("通话时长")).toBeInTheDocument();
  expect(screen.getByText("持续时长")).toBeInTheDocument();
  expect(screen.getByText("16")).toBeInTheDocument();
  expect(screen.getByText("45")).toBeInTheDocument();
  expect(screen.getByText("—")).toBeInTheDocument();
  expect(screen.getByText("坐席时长")).toBeInTheDocument();
  expect(screen.getByText("排队时长")).toBeInTheDocument();
  expect(screen.getByText("机器人时长")).toBeInTheDocument();
  expect(screen.getByText("10")).toBeInTheDocument();
  expect(screen.getByText("8")).toBeInTheDocument();
  expect(screen.getByText("6")).toBeInTheDocument();
});
