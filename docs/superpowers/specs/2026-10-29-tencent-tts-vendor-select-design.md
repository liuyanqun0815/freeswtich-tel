# 腾讯流式 TTS + 语音合成厂商下拉

日期：2026-10-29  
状态：待用户确认后实现

## 背景

语音机器人合成目前固定火山引擎双向流式 TTS。需要增加腾讯云 TRTC [流式文本转语音 TextToSpeechSSE](https://cloud.tencent.com/document/product/647/122474)，并在管理台用下拉框选择合成供应商。

## 目标

1. `ai_gw` 支持 `tts.vendor=tencent`，调用 `TextToSpeechSSE`，输出电话可用的 **8k PCM**。
2. 管理台「语音合成」用下拉选择 **火山引擎 / 腾讯云**，表单字段随厂商切换。
3. 凭证：复用 `TENCENT_SECRET_ID` / `TENCENT_SECRET_KEY`，新增 `TENCENT_TRTC_SDK_APP_ID`（与 ASR 的 `TENCENT_APP_ID` 分开）。

非目标：不引入 `tencentcloud-sdk-python`；不接 OpenAI TTS 回管理台；不做发音词典 / 字幕对齐。

## 架构

```
管理台 AI 场景 → tts_json { vendor, ... }
                      ↓
ai_gw build_tts(config)
    ├── volcengine → 现有 VolcTts（WSS bidirection）
    └── tencent    → 新增 TencentTts（HTTP SSE）
                      ↓
                 8k PCM chunks → BotSession 放音
```

## 调用方式（方案已确认）

自签 **TC3-HMAC-SHA256**，用已有依赖 `httpx` 对 SSE 做流式读取。

| 项 | 值 |
|---|---|
| Host | `trtc.ai.tencentcloudapi.com`（文档要求 SSE 必须用此域名） |
| Action | `TextToSpeechSSE` |
| Version | `2019-07-22` |
| Region | 场景可配，默认 `ap-guangzhou` |
| 鉴权 | SecretId / SecretKey（`.env`） |
| SdkAppId | `TENCENT_TRTC_SDK_APP_ID` |
| AudioFormat | `{ "Format": "pcm", "SampleRate": 8000 }` |
| 响应 | SSE：`data: {"Type":"chunk","Audio":"<base64>",...,"IsEnd":bool}`；错误可能是整段 JSON 或 `Type=error` |

不使用官方 SDK：官方 Client 默认 host 为 `trtc.tencentcloudapi.com`，流式域名与解析需额外处理，收益不大。

## 配置

### 环境变量（`.env` / `.env.example` 注释占位，不写真实密钥）

```bash
TENCENT_APP_ID=              # 已有，ASR 用
TENCENT_SECRET_ID=           # 已有，ASR + TTS 共用
TENCENT_SECRET_KEY=          # 已有，ASR + TTS 共用
TENCENT_TRTC_SDK_APP_ID=     # 新增，TRTC 应用 SdkAppId
```

### 场景 `tts_json`

**火山（现有）：**

```json
{
  "vendor": "volcengine",
  "voice_type": "S_BiN8cuJi1",
  "resource_id": "volc.megatts.default"
}
```

**腾讯（新增）：**

```json
{
  "vendor": "tencent",
  "voice_id": "v-female-R2s4N9qJ",
  "model": "flow_02_turbo",
  "region": "ap-guangzhou"
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `vendor` | 是 | `volcengine` \| `tencent` |
| `voice_id` | 腾讯必填 | 对应文档 `Voice.VoiceId` |
| `model` | 否 | 默认 `flow_02_turbo`；可选 `flow_01_ex` |
| `region` | 否 | 默认 `ap-guangzhou`；允许 `ap-beijing` / `ap-shanghai` / `ap-singapore` |

保存时按厂商裁剪字段：选腾讯不保留 `voice_type`/`resource_id`；选火山不保留 `voice_id`/`model`/`region`。

## 适配器

在 `apps/ai_gw/src/vr_ai/providers/tencent.py`（或拆出 `tencent_tts.py` 若文件过长）新增 `TencentTts`，实现现有 `Tts` 协议：

```python
async def stream(self, text: str) -> AsyncIterator[bytes]:
    ...
```

行为：

1. 校验 `voice_id`、读取 SecretId/SecretKey/`TENCENT_TRTC_SDK_APP_ID`。
2. POST SSE，请求体含 `Text`、`Voice.VoiceId`、`SdkAppId`、`AudioFormat(pcm/8000)`、`Model`。
3. 逐条解析 SSE：`Type=chunk` 时 `base64` 解码 `Audio` 并 yield；`IsEnd=true` 结束；`Type=error` 或非 SSE 的业务错误 JSON → `ProviderError`。
4. 空文本、连接失败、超时 → `ProviderError`（会话按场景 `fallback` 降级）。

注册：`registry._TTS["tencent"] = TencentTts`。保留 `_TTS["openai"]` 供测试/兼容，管理台不展示。

## 管理台

`web/src/pages/ResourcePages.tsx`「语音合成」：

1. `Form.Item`：厂商 `Select`，选项：`火山引擎 (volcengine)`、`腾讯云 (tencent)`。
2. `Form.useWatch(["tts","vendor"])` 切换：
   - 火山 → 现有音色 / 资源 ID 字段；
   - 腾讯 → 音色 ID、可选模型、可选地域；文案说明密钥与 `TENCENT_TRTC_SDK_APP_ID` 在服务器 `.env`。
3. `ttsBody` 按 vendor 序列化对应字段。
4. 新建默认：仍为火山 + `S_BiN8cuJi1`（不强制改成腾讯）。

## 错误与边界

- 缺 `TENCENT_TRTC_SDK_APP_ID` / Secret → 构造或首次合成时 `ProviderError`，不拖垮进程。
- 文本超长：接口限制以文档为准；会话层已有分句，单句仍超长则 ProviderError。
- SSE 中途失败：已下发的 PCM 保留，本次合成以错误结束，由会话 fallback 处理。

## 测试

- 单元测试：TC3 签名稳定样例（或固定时间戳 mock）；SSE 解析 chunk / error / 前置 JSON 错误；`build_tts({"vendor":"tencent",...})` 注册。
- 前端测试：切换厂商后字段显示正确；保存 body 只含当前厂商字段。
- 不强制真实联调测试（需账号）；联调靠部署后人工验证。

## 实现顺序

1. `.env.example` 增加 `TENCENT_TRTC_SDK_APP_ID` 注释占位。
2. `TencentTts` + 注册 + 单测。
3. 管理台厂商下拉与字段切换 + 前端测试。
4. 本地/部署：`.env` 填 SdkAppId，场景选腾讯后试听。

## 验收

- 场景选火山：行为与现在一致。
- 场景选腾讯：能合成 8k PCM 并在机器人通话中放音。
- 错误凭证时日志有明确 `ProviderError`，走场景 fallback。
