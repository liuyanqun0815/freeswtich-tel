# 机器人转人工加固 + 分机通话盲转

日期：2026-10-29  
状态：已确认方向（用户选 C：两块都做，先 A 后 B）

## 1. 目标与范围

两块独立能力，按 **A → B** 交付：

| 阶段 | 能力 | 一句话 |
|------|------|--------|
| **A** | 机器人对话中转人工 | 用户说「转人工」等，稳定转到场景技能组 |
| **B** | 分机通话盲转 | 坐席通话中把当前通话转到另一分机 |

### 本期包含

**A**

- 保留并依赖已有 LLM tool `transfer_to_agent` → `transfer` 指令
- ASR 文本关键词兜底（不依赖模型是否调 tool）
- 关键词优先：命中后本轮直接转，可不再等完整 LLM 回复
- 沿用场景 `transfer_skill_group_id`；未配置时行为与现网一致（忙音 + 挂断）
- 可选：转接前播一句固定提示「正在为您转接人工」（写死文案，不做场景配置）

**B**

- 管理台坐席工作台：通话中「转接」→ 选/填目标分机 → 确认
- API + engine：解析当前坐席侧通道 UUID，对该腿做盲转（`uuid_transfer` + `sofia_contact`，与现有 `_extension_transfer` 一致）
- 鉴权：仅登录用户、仅操作自己绑定分机的通话；目标须已注册且与本机不同

### 本期不做

- 咨询转（两步转）
- 转外线 / 转技能组排队（B 仅转分机）
- 多技能组意图路由、转接话术场景级配置
- 改 FreeSWITCH dialplan XML 以外的复杂排队逻辑

## 2. 已确认决策

| 项 | 决定 |
|----|------|
| 排期 | 先 A 联调通过，再做 B |
| 机器人转人工可靠性 | LLM tool + 关键词兜底（组合） |
| 关键词时机 | 用户一句话说完、进入 `_answer` 后先做关键词检测，命中则直接 `transfer` |
| 分机转接类型 | 仅盲转 |
| 分机转接入口 | 管理台软电话工作台（非实体话机功能键） |

## 3. A：机器人转人工

### 3.1 现状

- `OpenAiLlm` 已注册 tools：`transfer_to_agent`、`hangup`
- `BotSession._reply_to` 收到 `{"action":"transfer"}` 后 `_finish("transfer")`
- `engine` 对 `VR_AI_CMD` type=transfer：停推流 + 按 `skill_group_id` 选在线分机 bridge
- 薄弱点：兼容接口 tool 不稳定；系统提示未强制；用户说转人工但模型只口语答应

### 3.2 流程

```
用户一句结束 (ASR text)
    │
    ├─ 命中转人工关键词？ ──是──► 可选播「正在为您转接人工」
    │                              └─► finish(transfer) → Redis → engine 桥技能组
    │
    └─否──► 照常 LLM stream（仍带 tools）
              └─ tool transfer_to_agent ──► 同上 finish(transfer)
```

### 3.3 关键词

默认规则（代码常量，可后续再做成场景配置）：

- 正则或子串：`转人工|转接人工|找人工|人工客服|转客服|转接客服`
- 匹配对象：本轮 ASR 整句（`strip` 后），大小写不敏感（中文为主）
- 命中后：**取消/跳过**本轮 LLM 调用，直接转（避免双通道竞争）

### 3.4 提示词

- 不强制改库里已有 `system_prompt`
- 在发给 LLM 的 messages 组装处：若场景提示未包含转人工说明，则追加一条固定 system/developer 补充（或拼到 system 尾部）：  
  「用户明确要求人工服务时必须调用 transfer_to_agent，不要只口头答应。」
- tool 描述保持现有即可，可微调为更强调「说出转人工/找客服时必须调用」

### 3.5 错误与边界

| 情况 | 行为 |
|------|------|
| 关键词命中但未配技能组 | 与现网一致：engine 无成员 → 忙音挂断 |
| 关键词命中且技能组无人在线 | 同上 |
| LLM 空回复且无 tool | 仍 `ProviderError` → 场景 fallback |
| 用户说「不要转人工」 | 关键词不匹配「不要…」整句时可先要求正向短语；首版仅正向命中，不做否定语义 |

### 3.6 测试（A）

- 单测：关键词命中 → `finish(transfer)` 且不调 LLM
- 单测：未命中 → 仍走 LLM；LLM 返回 action=transfer 仍生效
- 单测：transfer 指令带上 `skill_group_id`

## 4. B：分机通话盲转

### 4.1 现状

- 坐席工作台：`useSoftphone` 呼叫 / 挂断 / DTMF，无转接
- 软电话经 SIP/WebRTC 直连 FS，不一定经过 `/calls` 验证呼叫状态机
- engine 已有 `_extension_transfer(uuid, ext)`：`uuid_transfer … bridge:${sofia_contact(ext@domain)} inline`

### 4.2 流程

```
坐席（分机 E1）通话中
  → 工作台点「转接」→ 输入/选择目标分机 E2
  → POST /agent/transfer { target: "E2" }  （名称可微调，需鉴权）
  → API：校验用户绑定分机 == E1、E2 已注册、E1≠E2
  → 入队 ESL 任务或发 engine 事件：blind_transfer
  → engine：按 E1 查找当前桥接中的通道 UUID（坐席腿）
  → uuid_transfer 该 UUID → sofia_contact(E2)
  → 成功：主叫与 E2 通话，E1 退出；失败：返回错误，保持原通话
```

### 4.3 通道定位

软电话侧通常拿不到 FS UUID，由服务端解析：

1. 用坐席分机号在 ESL/`show channels`（或项目已有注册/通道缓存）中找 **Caller-Caller-ID-Number / Caller-Callee-ID-Number / presence** 匹配该分机、且状态为 answered/bridged 的通道
2. 若多条：取最近建立的一条；若零条：409「当前无通话」
3. 实现落点优先复用 engine 已有 ESL 连接，避免 API 进程直连 ESL（与现有 `enqueue_command` / AI 指令模式一致）

命令形态与现网一致，例如：

```text
uuid_transfer <agent-leg-uuid> bridge:${sofia_contact(<target>@<domain>)} inline
```

### 4.4 API 草图

```http
POST /agent/transfer
Authorization: Bearer …
Content-Type: application/json

{ "target": "1002" }
```

响应：

- `200` `{ "ok": true }`（已入队）
- `400` 目标非法
- `409` 无通话 / 目标未注册 / 目标是自己
- `503` 引擎队列不可用

（若已有对称的 agent 路由模块则挂到其下；否则新建 `vr_api` 路由。）

### 4.5 管理台 UI

- 仅 `callState === "in-call"` 显示「转接」
- 弹层：分机下拉（可复用 `/extensions` + 注册状态）或手输
- 确认后调 API；成功提示「已发起转接」；失败展示 detail
- 不在 UI 做咨询转两步状态机

### 4.6 测试（B）

- API：未注册目标 → 409；无绑定分机 → 403/400
- engine：给定 fake 通道表 + 分机，生成正确的 `uuid_transfer` 参数
- 前端：非通话中无转接按钮；通话中可选目标并 POST

## 5. 实现顺序

1. **Task A1** 关键词检测 + session 短路转人工 + 单测  
2. **Task A2** LLM 转人工提示补充（messages 组装）+ 单测  
3. **Task A3**（可选）转接前提示音一句  
4. 联调 A  
5. **Task B1** engine 按分机解析 UUID + 盲转命令 + 单测  
6. **Task B2** API `POST /agent/transfer`  
7. **Task B3** 坐席工作台转接 UI + 前端测试  
8. 联调 B  

## 6. 验收

**A**

- [ ] 用户说「转人工」，技能组有人在线 → 坐席振铃/接通
- [ ] 故意让模型不调 tool（或 mock 无 tool）时，关键词仍能转
- [ ] 未配技能组 → 忙音挂断（与现逻辑一致）

**B**

- [ ] E1 与主叫通话中，工作台盲转到 E2 → 主叫与 E2 通话，E1 退出
- [ ] E2 未注册或 E1 无通话 → 明确错误，原通话保持

## 7. 风险

| 风险 | 缓解 |
|------|------|
| 部分模型不发 tool_calls | 关键词兜底 |
| 关键词误触发（如「不转人工」） | 首版仅正向短语；后续可加否定过滤 |
| 坐席腿 UUID 认错 | 严格匹配分机 + answered；单测覆盖多通道 |
| 盲转目标早挂 | FS 行为如实返回；UI 提示失败 |
