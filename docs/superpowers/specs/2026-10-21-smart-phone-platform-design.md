# 智能电话平台设计（本期）

日期：2026-10-21  
状态：已确认（2026-10-21）  
来源：`智能电话系统平台方案_v2.docx`，以及已确认的范围与选型。

## 1. 目标与范围

基于现有 FreeSWITCH，做一套可运营的智能电话平台：浏览器坐席能打电话，外线呼入可由机器人接听并转人工，可按任务做通知类外呼，话单和录音可在管理台查询。

本期包含：

- 单台已有 FreeSWITCH 联调（示例公网地址 `47.253.82.114`，以实际环境变量为准）
- `mod_xml_curl` 下发分机、拨号计划、网关
- ESL 呼叫控制：统一 park、IVR、转接、录音
- 浏览器软电话（SIP over WSS + SIP.js）与坐席工作台
- 统一 AI 协议 v1，示例接入 voice-robot；密钥未就绪时用内置固定话术机器人跑通转接与话单
- 打断（barge-in）、录音、三层话单
- 树形 IVR 配置与发布
- 应用层 ACD（技能组排队，默认最长空闲）
- 外呼任务（通知：放音或固定话术 + 结果回写）、黑名单、时段与频次拦截
- 配置发布：校验、下发、失败回滚、审计
- 管理台（React + Ant Design），版式参考现有后台截图，菜单按本节第 6 节，不照搬截图菜单名

本期不做：

- Kamailio、多台 FreeSWITCH 集群、跨机房
- 预测外呼
- 质检评分、录音离线转写评分
- 可视化流程图（React Flow）
- 多租户切换界面、按租户独立库
- 开放 API / Webhook 管理页（表结构可预留事件字段，不做对外投递产品）
- 第二家 ASR/TTS/LLM 的完整生产适配（协议与适配器接口留好，示例只接一条机器人链路）
- 坐席辅助实时转写、班长监听/耳语/强插

## 2. 已确认决策

| 项 | 决定 |
|----|------|
| 交付范围 | MVP + 平台化一期（见第 1 节） |
| 后端 | Python FastAPI，单仓多进程 |
| 前端 | React + Ant Design + SIP.js |
| 管理台外观 | 左侧菜单、顶栏页签、筛选区、汇总条、表格；主色与密度对齐所给截图 |
| FreeSWITCH | 使用已有机器，浏览器 WSS 直连其 `:7443` |
| 播报下行 | 分句文件：`streamAudio` 落临时文件后由控制引擎 `uuid_broadcast` |
| 话单「通话时长」 | 客户腿 `billsec`；「持续时长」为 `duration`。详情另展示坐席时长、排队时长、机器人时长 |
| 话费 | 未配置费率时汇总显示「—」，不估算 |
| 数据隔离 | 业务表带 `tenant_id`，本期只存在默认租户 `t001`，界面不提供切换 |
| AI 降级 | 场景可配置：转人工，或放固定提示音后挂机 |

## 3. 仓库与进程

```
freeswitch-tel/
├── apps/
│   ├── api/       # 管理台 REST、登录、话单查询、发布触发
│   ├── config/    # xml_curl：directory / dialplan / configuration
│   ├── engine/    # ESL：通话状态机、IVR、ACD、外呼调度、录音指令
│   ├── ai_gw/     # FS 音频 ↔ 统一协议；voice-robot 转换；内置话术机器人
│   └── push/      # 坐席业务 WebSocket
├── packages/
│   └── common/    # 模型、协议、Redis Key、ESL 客户端
├── web/           # 管理台与坐席工作台
├── deploy/        # docker-compose、FS 静态骨架示例
└── docs/
```

依赖：PostgreSQL、Redis、对象存储（MinIO；本地开发可先落盘）。话单异步走 Redis Stream。

### 3.1 边界

1. 只有 `engine` 连接 ESL 并下发通道命令。`ai_gw`、机器人、管理台都不连 ESL。
2. `config` 只按库中配置生成 XML，不做路由策略。
3. `api` 写配置草稿；生效必须走发布，由 `engine` 对 FS 执行 `sofia rescan`、`killgw`、`xml_flush_cache` 等。
4. 进行中的通话在 Redis，带 TTL。挂机后删键，并异步落库。
5. 全链路使用 `vr_call_id`。它在 dialplan 或 originate 时生成，并带到 ESL、AI 会话、话单、录音文件名。

### 3.2 部署关系

平台进程与现有 FS 通过内网互通。`config` 的 URL 配进 FS `xml_curl`（Basic 认证，超时 5 秒）。`engine` 连接 FS `8021`，ESL 不对公网开放。浏览器页面走平台 HTTPS；SIP 注册走 FS WSS。

静态 XML 只保留骨架：`vars.xml`、模块列表、profile、`xml_curl`、`event_socket`、配置服务不可用时的兜底 extension（播放系统繁忙后挂机）。分机、网关、业务 dialplan 不写在静态文件里。

## 4. 通话数据流

### 4.1 呼入

1. FS 向 `config` 请求 dialplan。返回统一 extension：设置 `vr_call_id`、`vr_tenant`、`vr_entry`、`park_timeout`，然后 `park`。
2. `engine` 收到 `CHANNEL_PARK`。Redis 中无此 `call_id` 且方向为呼入时，创建通话与腿，建立 `uuid → call_id` 映射。
3. 按被叫号码查号码、入口、日程，得到：机器人、技能组、IVR、指定分机或放音。
4. 查不到、号码停用、目标不存在：放提示音后 `uuid_kill`。
5. 机器人入口：`uuid_answer`，接通后 `uuid_record`（双声道），再 `uuid_audio_stream start` 指向 `ai_gw`。
6. `ai_gw` 将 8k PCM16 转为统一协议，连到场景所配机器人。播报按句回 `streamAudio`，`engine` 收到 `mod_audio_stream::play` 后播放。
7. 打断：机器人发 `response.cancel`，`engine` 执行 `uuid_break <uuid> all`，网关丢弃该 `response_id` 未播放分片。标记为不可打断的话术在播放期间忽略 cancel。
8. 转人工：机器人发 `action.transfer`（意图、槽位、摘要）。`engine` 停止音频流，通话进入技能组队列。`push` 先发 `call.incoming`，再 originate 坐席分机。双方 `uuid_bridge`，并设置 `park_after_bridge=true`。
9. 一方挂断后另一方回到 park，由引擎决定挂机或继续（本期转人工后的满意度调查不做自动流程，回到 park 后若无下一步则挂机）。
10. `CHANNEL_HANGUP_COMPLETE` 收尾：释放坐席、删 Redis、事件进 Stream、消费者写话单；录音异步上传并回写地址。

同一 `vr_call_id` 的事件进入同一个有界队列，保证应答、放音、挂机顺序。心跳、放音开始等可丢事件在队列满时丢弃并计数；挂机完成、后台任务结果等关键事件阻塞等待。

命令一律 `bgapi`。超时则重试一次，仍失败则挂断该通话。

### 4.2 外呼通知

调度器只在任务时间窗内、未超并发、未超频次、未命中黑名单时取名单。预生成 `origination_uuid` 与 `vr_call_id`，先写 Redis，再 `bgapi originate ... &park()`。接通以 `CHANNEL_ANSWER` 为准。接通后放预合成音频或进入固定话术/机器人。结果写回名单：已完成、待重试、失败、已拦截。

### 4.3 坐席身份

1. 坐席用平台账号登录，获得 JWT、分机号、临时 SIP 密码、域、WSS 地址、ICE/TURN（TURN 凭证按次生成，未部署 TURN 时只下发 STUN）。
2. 临时密码的 a1-hash 写入 Redis，TTL 与登录会话相同（默认 12 小时）。
3. FS 注册时 `config` 查 Redis：在线且凭证有效才返回 directory；否则 not found。静态 directory 中删除默认同名分机，避免回退到默认密码。
4. 同一分机只允许一个浏览器在线，新登录踢掉旧会话。
5. 可分配来电的条件：SIP 已注册、业务 WebSocket 在线、状态为空闲。业务 WebSocket 断开超过 60 秒或 SIP 注销，自动置忙。

浏览器外呼：软电话 INVITE 外线号码，仍进统一 park，由引擎做权限、黑名单、主叫选择后再桥接出局。

### 4.4 ACD

每个技能组在 Redis 中有等待队列和空闲坐席集合。本期分配策略只实现最长空闲。排队超时、全忙、无人就绪时，按技能组配置溢出到另一技能组、机器人或直接挂机（放提示音）。坐席振铃 20 秒无应答：该坐席置未就绪，通话回到队首。

坐席状态：离线、空闲、振铃、通话、话后整理、小休。状态变更落库。

### 4.5 IVR

树形节点存 JSON：放音、收号、按键分支、转机器人、转技能组、转分机、挂机。`*` 重听，`#` 在收号节点表示结束输入；未输入数字直接按 `#` 表示返回上级。发布时校验按键不重复、转接目标存在、话术非空，生成不可变快照并递增版本。通话开始时锁定版本。执行器在 `engine` 内解释，不编译成 dialplan XML。

## 5. AI 协议（v1）

一通电话一条 WebSocket。握手带 `Authorization: Bearer` 与 `X-Protocol-Version: 1`。音频为二进制 PCM16 单声道，采样率在 `session.start` 协商，本期电话侧固定 8000，每帧 20ms 的整数倍。文本帧为 JSON，公共字段：`type`、`session_id`（等于 `vr_call_id`）、`seq`、`ts`。

平台发往机器人：`session.start`、`dtmf`、`playback.started`、`playback.finished`、`call.transferred`、`session.end`。

机器人发往平台：`input.speech_started`、`input.speech_stopped`、`transcript.partial`、`transcript.final`、`response.audio`、`response.audio.done`、`response.text`、`response.cancel`、`action.transfer`、`action.hangup`、`action.collect_dtmf`。

双向：`ping` / `pong`、`error`。未知 `type` 忽略。

`session.start` 后 3 秒内没有 `response.*` 或 `pong`，按场景降级。

voice-robot 差异由 `ai_gw` 转换：上行 PCM 打成其 `audio_chunk`；服务端 VAD 生成其 `vad_event` 与 `turn_commit_request`；其 `cancel` 映射为 `response.cancel`。其协议若无转人工消息，转换层识别回复中的约定标记 `[[transfer]]` 生成 `action.transfer`。

内置话术机器人实现同一协议：按场景配置的开场白播放，收到最终识别文本后播放固定回复，或在配置的关键词下转人工。用于无线路密钥时的联调。

## 6. 管理台菜单

外观：Ant Design Layout。左侧深色或浅色菜单（与截图一致的信息密度）、顶栏多页签、内容区白底筛选表单、主按钮、汇总数字、表格操作列。

| 菜单 | 内容 |
|------|------|
| 概览 | 正在通话、排队人数、网关注册状态、今日接通率与通时 |
| 坐席工作台 | SIP.js 软电话；签入签出、就绪/小休、接听挂断、保持、静音、转接；来电弹屏含机器人摘要 |
| 话单 | 筛选：通话 ID、主叫、被叫、坐席、方向、日期、是否接通。汇总：接通数/呼叫数、通时、接通率、受限率；话费无费率时为「—」。列含通话时长、持续时长、挂断原因。操作：详情（时间线、腿、AI 轮次）、录音、加入黑名单 |
| 分机 | 分机号、绑定坐席、启用；浏览器分机不保存长期密码 |
| 线路与号码 | 网关（地址、认证、编码、并发与 CPS）、号码池、呼入入口、工作时间 |
| IVR | 树形编辑与「应用 IVR」 |
| 技能组 | 成员、排队超时、溢出目标、振铃超时 |
| 外呼任务 | 名单导入、时间窗、并发、重试、话术或 IVR、名单状态 |
| 黑名单 | 号码、原因、生效时间 |
| AI 场景 | 机器人 WebSocket 地址或内置话术、失败降级方式、开场白 |
| 配置发布 | 差异预览、下发结果、回滚 |
| 系统 | 用户与角色（管理员、坐席）、FS 节点、审计日志 |

角色：管理员可配置与发布；坐席只能进工作台和与自己相关的话单。密码 bcrypt。JWT 访问令牌 + 刷新令牌。初始密码首次登录强制修改。

## 7. 数据

### 7.1 PostgreSQL

主要实体：`user`、`extension`、`gateway`、`phone_number`、`entry`、`schedule`、`ivr_flow`、`ivr_publish`、`skill_group`、`agent_state_log`、`dial_task`、`dial_contact`、`blacklist`、`ai_scene`、`fs_node`、`config_release`、`audit_log`、`call_record`、`call_leg`、`call_event`、`ai_turn`。

所有业务表含 `tenant_id`，默认 `t001`。`call_record` 以 `vr_call_id` 唯一。挂机原因同时存 FS `Hangup-Cause`。写入幂等：同一通话先插入再按挂机事件更新时长。

`call_record` 至少包含：主被叫、方向、坐席、开始/接通/结束时间、`duration`、`billsec`、排队秒数、机器人秒数、坐席腿 `billsec`、挂断原因、录音地址、是否受限及原因。

### 7.2 Redis

- `call:{call_id}` 会话 Hash
- `leg:{uuid}` → `call_id`
- `node:{fs_node}:calls` 该节点通话集合
- `sipcred:{domain}:{ext}` 临时 SIP 凭证
- `queue:{group_id}` 等待通话
- `agents:{group_id}` 空闲坐席
- Stream `cdr.events` 话单与 AI 轮次

TTL 为最大通话时长加缓冲，默认 4 小时，收到该通话事件时续期。ESL 重连后以及每 60 秒：Redis 有而 FS 无的会话补「对账结束」话单并释放坐席；FS 有而 Redis 无、且带 `vr_call_id` 的通道按策略挂断。

### 7.3 录音

接通后开始录音，`RECORD_STEREO=true`。路径含日期与 `vr_call_id`。挂机后异步上传。播放走平台签发的短期链接，并写审计。

## 8. 出错处理

| 情况 | 行为 |
|------|------|
| 无入口、号码停用、目标不存在 | 提示音后挂机 |
| 机器人或 `ai_gw` 断开 | 按场景转人工或提示音后挂机 |
| `bgapi` 无回执 | 重试一次，仍失败则挂断 |
| 坐席振铃超时 | 坐席未就绪，通话回队首 |
| 外呼命中黑名单、时段外、超频次、超日上限 | 不 originate，名单记已拦截 |
| 放音文件缺失 | 挂机 |
| 配置下发任一步失败 | 回滚上一快照并重新下发，写审计 |
| 引擎漏掉挂机事件 | 对账补话单，不留永久 Redis 键 |
| `park` 超过 `park_timeout` | FS 释放通道；引擎对账清理 |

外呼合规底线：名单需有同意来源与时间；任务默认时间窗为 9:00–12:00 与 14:00–20:00（可改）；同一号码默认 24 小时内最多 1 通成功外呼（可按任务改）；客户通话中说出配置的拒呼关键词则写入黑名单。机器人开场白默认包含「我是智能助理」。

## 9. 验收

每条用例记录 `vr_call_id`，在话单页能打开时间线；接通的用例能播放录音。

1. 浏览器登录两个分机，注册成功，互打、挂断。
2. 外线或分机呼入机器人，多轮对话，用户说话能打断播报。
3. 转人工：弹屏先于振铃；桥接后通话；一方挂断另一方不被连带挂死（回到 park 后引擎挂机，话单完整）。
4. IVR 按键进入机器人或技能组；发布新版本后仅新通话使用新版本。
5. 外呼任务在时间窗内拨出；黑名单号码状态为已拦截且 FS 上无该呼叫。
6. 话单筛选命中上述通话；通话时长与持续时长分列；受限呼叫计入受限率。
7. 修改网关或分机并发布后下一通生效；下发失败时配置回到上一版。

线路或 voice-robot 未就绪时，第 2、5 条用内置话术机器人和分机互拨代替，并在验收记录中注明。全部 7 条在真实线路上通过后，本期才算完成。

## 10. 安全

- ESL、xml_curl、明文 SIP `5066` 不对公网。
- xml_curl 使用 Basic 认证，日志不打印密码和 Authorization。
- 网关密码、机器人令牌加密存储，只在运行时解密。
- 删除或停用 FS 自带 1000–1019 演示分机的静态密码入口。
- 日志与 AI 文本中的手机号中间四位脱敏。
- 管理台写操作与录音播放进入 `audit_log`。

## 11. 构建顺序

按依赖从底向上，前一步可独立运行后再做下一步：

1. 公共模型、数据库迁移、登录与管理台空壳（菜单可点、话单页空表）。
2. `config` 的 xml_curl 与 `engine` 的 ESL 连接、统一 park、分机互打。
3. 话单 Stream 落库、录音、话单页可查询。
4. 坐席登录、临时 SIP 密码、SIP.js 工作台。
5. 内置话术机器人与 `ai_gw`、打断、转技能组。
6. 树形 IVR、外呼任务与黑名单。
7. 配置发布与回滚、审计。
8. 真实线路与 voice-robot 联调，补齐第 9 节验收。
