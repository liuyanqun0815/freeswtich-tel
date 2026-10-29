# 机器人转人工加固 + 分机盲转 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用户对机器人说转人工能稳定进技能组；坐席通话中可盲转到另一分机。

**Architecture:** A 在 `BotSession._answer` 对 ASR 文本做关键词短路，并给 LLM 补转人工 system 提示；B 新增 `POST /agent/transfer`，engine 按坐席分机找腿执行已有 `_extension_transfer`。

**Tech Stack:** Python ai_gw/engine/api、Redis/ESL 指令队列、React 坐席工作台、pytest/vitest

## Global Constraints

- 先完成并提交 A，再做 B
- 转人工目标只用场景 `transfer_skill_group_id`
- 关键词：`转人工|转接人工|找人工|人工客服|转客服|转接客服`
- 盲转复用 `uuid_transfer` + `sofia_contact`；不做咨询转
- 中文回复与提交说明

**Spec:** `docs/superpowers/specs/2026-10-29-transfer-agent-and-blind-xfer-design.md`

---

### Task A1: 关键词短路转人工

**Files:**
- Modify: `apps/ai_gw/src/vr_ai/session.py`
- Test: `tests/test_ai_session.py`

**Interfaces:**
- Produces: `def wants_human_agent(text: str) -> bool`
- Modifies: `_answer` — 命中则跳过 LLM，`await self._finish("transfer")`（可先短播提示）

- [ ] **Step 1:** 单测：命中「转人工」不调用 LLM，发出 transfer
- [ ] **Step 2:** 实现 `wants_human_agent` + `_answer` 短路
- [ ] **Step 3:** pytest 通过并提交

---

### Task A2: 转人工 system 提示补充

**Files:**
- Modify: `apps/ai_gw/src/vr_ai/session.py`（构造 history 时）
- Optionally tweak tool description in `openai_compat.py`
- Test: `tests/test_ai_session.py`

**Interfaces:**
- Produces: 固定补充文案常量；若已有 system 则拼接，否则新增 system 消息

- [ ] **Step 1:** 单测：无 system_prompt 时 history 含转人工说明；有 system 时被追加
- [ ] **Step 2:** 实现并提交

---

### Task B1–B3: 分机盲转（A 完成后再做）

**Files:**
- Modify: `apps/engine/src/vr_engine/main.py`（按分机解析 UUID + 处理 blind_transfer）
- Create/Modify: `apps/api/src/vr_api/` agent transfer 路由
- Modify: `web/src/pages/ResourcePages.tsx` 坐席工作台 + `softphone` 若需
- Tests: engine / api / frontend

- [ ] B1 engine 定位通道 + 盲转命令单测
- [ ] B2 `POST /agent/transfer`
- [ ] B3 工作台「转接」UI

详细步骤实现 B 时再按 TDD 展开；通道定位：ESL `show channels` / 现有缓存，匹配坐席分机且 answered。
