import { MENU_LABELS } from "./menu";

const EXPECTED = [
  "概览",
  "坐席工作台",
  "话单",
  "分机",
  "线路与号码",
  "IVR",
  "技能组",
  "外呼任务",
  "黑名单",
  "AI 场景",
  "配置发布",
  "系统",
];

test("MENU_LABELS matches operations console menu", () => {
  expect(MENU_LABELS).toEqual(EXPECTED);
  expect(MENU_LABELS).not.toContain("数据池");
  expect(MENU_LABELS).not.toContain("功能按键");
});
