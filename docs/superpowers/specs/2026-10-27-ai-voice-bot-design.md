# 机器人接听：识别 / 合成 / 大模型抽象与 OpenAI 兼容适配

日期：2026-10-27  
状态：已确认设计方向（2026-10-27），待评审文档  
上位文档：`2026-10-21-smart-phone-platform-design.md` 第 4、5 节

## 1. 目标与范围

外线或分机呼入「机器人」入口后，机器人能接听、播报开场白、多轮对话、被用户打断、转人工、挂机。识别（ASR）、合成（TTS）、大模型（LLM）各自抽象成接口，按厂商注册适配器；本期只实现 OpenAI 兼容接口这一家。

本期包含：

- `ai_gw` 的 WebSocket 服务，对接 FreeSWITCH 已安装的 `mod_audio_stream`
- 识别、合成、大模型三个接口与注册表，OpenAI 兼容适配器
- 对话会话：端点检测、一问一答、按句播报、打断、转人工、挂机、降级
- `ai_gw` → `engine` 的指令通道
- `engine`：机器人入口在通话 ACTIVE 后启动录音与推流；处理放音事件与 `ai_gw` 指令
- AI 场景配置扩展、增删改接口、管理台表单；号码入口「机器人」改为下拉选场景
- 部署：`ai_gw` 默认启动

本期不做：

- 阿里、火山、腾讯、讯飞等其他厂商适配器（接口留好）
- 端到端实时语音（三方 WebSocket 一家包办识别与合成）
- 流式识别（OpenAI 兼容的识别接口是整句提交）
- 密钥加密存储、页面录入密钥
- AI 对话轮次写入话单详情

## 2. 已确认决策

| 项 | 决定 |
|----|------|
| 第一批厂商 | 仅 OpenAI 兼容接口（大模型覆盖 DeepSeek、通义、豆包、硅基流动、本地 Ollama/vLLM 等） |
| 交付范围 | 端到端，能真打电话与机器人对话 |
| 推流模块 | FS 已安装 `mod_audio_stream`，按开源版 `amigniter/mod_audio_stream` 协议对接 |
| 通话控制 | 只有 `engine` 连 ESL；`ai_gw` 的指令经 Redis 交给 `engine` 执行 |
| 播报方式 | `ai_gw` 发 `streamAudio`，模块在 FS 本机落文件并发 `mod_audio_stream::play`，`engine` 用 `uuid_broadcast` 播放 |
| 密钥 | 场景配置只存环境变量名（`api_key_env`），密钥放 `.env`，数据库与页面不出现密钥 |
| 推流时机 | 与 IVR 欢迎音一致，等 `Channel-Call-State=ACTIVE` 后再启动录音和推流 |

## 3. 组件

```
apps/ai_gw/src/vr_ai/
├── providers/
│   ├── base.py           # AsrSession / Asr / Tts / Llm 接口
│   ├── registry.py       # vendor -> 适配器；build_asr / build_tts / build_llm
│   └── openai_compat.py  # OpenAI 兼容适配器
├── audio.py              # 重采样、WAV 封装、端点检测
├── session.py            # 一通电话的对话会话
├── scene.py              # 从数据库读取场景配置
├── commands.py           # 写 Redis 指令
├── server.py             # FastAPI WebSocket：/fs/stream
└── scriptbot.py          # 已有
```

### 3.1 接口

```python
class AsrSession(Protocol):
    def feed(self, pcm: bytes) -> None: ...          # 8k PCM16 单声道
    async def finish(self) -> str: ...               # 一句结束，返回识别文本


class Asr(Protocol):
    def open(self) -> AsrSession: ...


class Tts(Protocol):
    def stream(self, text: str) -> AsyncIterator[bytes]: ...   # 统一输出 8k PCM16


class Llm(Protocol):
    # 产出 {"text": "..."} 文本片段，或 {"action": "transfer"|"hangup"}
    def stream(self, messages: list[dict]) -> AsyncIterator[dict]: ...
```

识别接口按「喂音频、整句结束取结果」设计，流式厂商以后可在 `feed` 里实时上送、在 `finish` 里取最终结果，不改调用方。

### 3.2 OpenAI 兼容适配器

- 识别：`POST {base_url}/audio/transcriptions`，multipart 上传整句 8k WAV，参数 `model`、`language`。
- 合成：`POST {base_url}/audio/speech`，参数 `model`、`voice`、`input`、`response_format`（默认 `pcm`）。按配置的输出采样率（默认 24000）用 `soxr` 重采样到 8k 后分片产出。
- 大模型：`POST {base_url}/chat/completions`，`stream=true`，解析 SSE。声明两个工具 `transfer_to_agent`、`hangup`，模型调用工具即产出对应 `action`。
- 鉴权：`Authorization: Bearer {os.environ[api_key_env]}`。环境变量缺失时，构建适配器即报错，会话走降级。
- HTTP 客户端：`httpx.AsyncClient`，单次调用超时默认 10 秒，可在配置里改。

### 3.3 场景配置

`ai_scene` 新增列（迁移一次）：

| 列 | 类型 | 说明 |
|----|------|------|
| `system_prompt` | Text | 大模型系统提示词 |
| `transfer_skill_group_id` | Integer，可空 | 转人工的技能组 |
| `asr_json` | Text | 识别配置 |
| `tts_json` | Text | 合成配置 |
| `llm_json` | Text | 大模型配置 |

已有列 `name`、`opening`、`fallback`（`transfer` / `hangup`）沿用；`ws_url` 本期不用，保留。

配置示例：

```json
{
  "asr": {"vendor": "openai", "base_url": "https://api.siliconflow.cn/v1", "api_key_env": "SILICONFLOW_API_KEY", "model": "FunAudioLLM/SenseVoiceSmall", "language": "zh"},
  "tts": {"vendor": "openai", "base_url": "https://api.siliconflow.cn/v1", "api_key_env": "SILICONFLOW_API_KEY", "model": "FunAudioLLM/CosyVoice2-0.5B", "voice": "FunAudioLLM/CosyVoice2-0.5B:anna", "sample_rate": 24000},
  "llm": {"vendor": "openai", "base_url": "https://api.deepseek.com/v1", "api_key_env": "DEEPSEEK_API_KEY", "model": "deepseek-chat"}
}
```

## 4. 通话流程

1. `CHANNEL_PARK` 解析到入口 `robot:<场景id>`：`engine` 下发 `uuid_answer`，把录音与推流两条命令挂到「ACTIVE 后执行」队列。
2. `CHANNEL_CALLSTATE` 且 `Channel-Call-State=ACTIVE`：`engine` 下发 `uuid_record` 与
   `uuid_audio_stream <uuid> start {AI_GW_WS_URL} mono 8k {"call_id":..,"uuid":..,"scene_id":..}`。
3. `ai_gw` 收到连接：第一条文本消息是元数据，按 `scene_id` 读场景并构建三个适配器；之后的二进制帧是 8k PCM16。
4. 开场白：`opening` 经合成后发回。
5. 播报：每段音频封成 WAV，发 `{"type":"streamAudio","data":{"audioDataType":"wav","sampleRate":8000,"audioData":"<base64>"}}`。模块在 FS 本机落文件并发 `CUSTOM mod_audio_stream::play`，`engine` 从事件正文取文件路径，执行 `uuid_broadcast <uuid> <file> aleg`。
6. 端点检测：`webrtcvad`，20ms 一帧，激进度 2。连续 200ms 有声判定开口，静音 600ms 判定一句结束，单句最长 15 秒。
7. 一句结束：识别 → 历史消息（系统提示词 + 对话）交给大模型流式生成 → 按 `。！？；` 与换行切句 → 每句合成后立即发回。
8. 打断：机器人播报期间（按已发音频时长估算播报结束时间）检测到开口，`ai_gw` 发 `break` 指令，并取消进行中的大模型与合成任务。`engine` 执行 `uuid_break <uuid> all`。
9. 转人工 / 挂机：大模型产出动作后，`ai_gw` 等已发音频估算播完，再发 `transfer` 或 `hangup` 指令。
   - `transfer`：`engine` 下发 `uuid_audio_stream <uuid> stop`，再用 IVR 现有的选人逻辑在 `transfer_skill_group_id` 的成员中挑在线分机转接；无人在线按现有规则放忙音挂机。
   - `hangup`：`uuid_kill`。
10. 挂机：WebSocket 断开，`ai_gw` 取消该会话所有任务。

## 5. 指令通道

- Redis 列表 `vr:ai:cmd`，`ai_gw` `LPUSH`，`engine` 后台线程 `BRPOP`。
- 消息：`{"type": "break"|"transfer"|"hangup", "call_id": "...", "uuid": "...", "skill_group_id": 7}`（`skill_group_id` 仅 `transfer` 带）。
- `engine` 把消息转成内部事件交给 `handle_event`。后台线程与 ESL 读循环共用一把锁，保证状态修改与 ESL 写入串行。

## 6. 错误与降级

| 情况 | 处理 |
|------|------|
| 场景不存在、配置缺字段、密钥环境变量缺失 | 按场景 `fallback`；场景都不存在时挂机 |
| 识别、合成、大模型调用报错或超时 | 按场景 `fallback` |
| 识别结果为空 | 忽略这一句，继续听 |
| `ai_gw` 不可达或中途断开 | 本期不自动处理，记录日志；通话由用户挂断或 park 超时结束 |

`fallback=transfer` 等同第 4 节转人工；`fallback=hangup` 直接挂机。

## 7. 管理台

- AI 场景页：列表 + 新增 / 编辑 / 删除表单。字段：名称、开场白、系统提示词、降级方式、转人工技能组（下拉）、识别 / 合成 / 大模型各自的接口地址、密钥环境变量名、模型，合成另加音色与输出采样率。
- 号码入口类型为「机器人」时，目标改为下拉选择场景。
- 接口：补 `PUT /ai-scenes/{id}`、`DELETE /ai-scenes/{id}`；列表与详情返回新字段。

## 8. 部署

- `docker-compose.yml`：`ai_gw` 去掉 `robot` profile，端口 `127.0.0.1:8040:8040`，依赖 Redis 与迁移；与其他服务一样覆盖 `DATABASE_URL`、`REDIS_URL` 为容器内地址。
- `deploy/docker-entrypoint.sh`：`ai_gw` 启动 `uvicorn vr_ai.server:app --host 0.0.0.0 --port 8040`。
- `engine` 新增环境变量 `AI_GW_WS_URL`，默认 `ws://127.0.0.1:8040/fs/stream`（FS 与 docker 在同一宿主机）。
- `ai_gw` 依赖：`fastapi`、`httpx`、`soxr`、`webrtcvad-wheels`、`redis`、`sqlalchemy`。

## 9. 测试

- 会话：假识别 / 假合成 / 假大模型，覆盖开场白、一问一答、按句播报、打断、转人工、挂机、各类降级。
- 适配器：`httpx.MockTransport` 模拟服务端，覆盖 SSE 解析、工具调用分片拼接、合成重采样、鉴权头、超时。
- 端点检测：合成有声 / 静音帧序列验证开口与一句结束。
- `engine`：机器人入口 ACTIVE 前不推流、ACTIVE 后下发录音与推流；`mod_audio_stream::play` 转 `uuid_broadcast`；三种指令的命令序列。
- 接口：场景增删改与新字段。

## 10. 部署时需要核对

1. 放音事件正文里文件路径的字段名（按开源版协议为 `file`），以实际 FS 日志为准。
2. 连续多次 `uuid_broadcast` 在 FS 上是否按顺序排队播放；若后一条会打断前一条，改为 `ai_gw` 按估算播完时间依次发送。
3. 所选 OpenAI 兼容服务的合成输出采样率，与配置里的 `sample_rate` 一致。
