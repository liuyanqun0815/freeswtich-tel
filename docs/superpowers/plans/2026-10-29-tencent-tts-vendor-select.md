# 腾讯流式 TTS + 厂商下拉 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 语音合成支持腾讯云 TextToSpeechSSE，管理台用下拉在火山/腾讯间切换。

**Architecture:** `TencentTts` 自签 TC3，经 httpx 读 `trtc.ai.tencentcloudapi.com` 的 SSE，产出 8k PCM；`build_tts` 按 `vendor` 分发；管理台 `tts.vendor` Select 切换字段。

**Tech Stack:** Python 3.12、httpx、FastAPI ai_gw、React Ant Design Form、pytest、vitest

## Global Constraints

- 凭证：复用 `TENCENT_SECRET_ID` / `TENCENT_SECRET_KEY`，新增 `TENCENT_TRTC_SDK_APP_ID`；不把真实密钥写入 `.env.example`
- Host 必须是 `trtc.ai.tencentcloudapi.com`；Action=`TextToSpeechSSE`；Version=`2019-07-22`
- 音频固定 `AudioFormat={Format:pcm, SampleRate:8000}`
- 不引入 `tencentcloud-sdk-python`
- 管理台不展示 OpenAI TTS

---

### Task 1: TencentTts 适配器与注册

**Files:**
- Create: `apps/ai_gw/src/vr_ai/providers/tencent_tts.py`
- Modify: `apps/ai_gw/src/vr_ai/providers/registry.py`
- Modify: `.env.example`（注释占位 `TENCENT_TRTC_SDK_APP_ID`）
- Test: `tests/test_ai_tencent_tts.py`

**Interfaces:**
- Produces: `class TencentTts` with `async def stream(self, text: str) -> AsyncIterator[bytes]`
- Produces: `build_tts({"vendor":"tencent","voice_id":"..."})` → `TencentTts`
- Consumes: env `TENCENT_SECRET_ID`, `TENCENT_SECRET_KEY`, `TENCENT_TRTC_SDK_APP_ID`

- [ ] **Step 1: 写失败单测**（签名头、SSE chunk 拼 PCM、error、缺 voice_id、registry）

- [ ] **Step 2: 实现 `tencent_tts.py`**
  - TC3 签 POST `/`，headers 含 `X-TC-Action/Version/Timestamp/Region/Authorization`
  - body: Text, Voice.VoiceId, SdkAppId(int), AudioFormat pcm/8000, Model（默认 flow_02_turbo）
  - 解析 SSE `data:` 行；chunk 解 base64 Audio；Type=error 或前置 JSON Error → ProviderError
  - 可注入 `client`/`clock` 便于测

- [ ] **Step 3: registry 注册 `tencent`；`.env.example` 加占位**

- [ ] **Step 4: pytest 通过后提交**

---

### Task 2: 管理台 TTS 厂商下拉

**Files:**
- Modify: `web/src/pages/ResourcePages.tsx`
- Modify: `web/src/pages/ResourcePages.test.tsx`

**Interfaces:**
- Consumes: `tts.vendor` ∈ `volcengine` | `tencent`
- Produces: `ttsBody` 按厂商裁剪字段

- [ ] **Step 1: 写/改前端测试**（下拉切换显示腾讯字段；保存 tencent 时 body 含 voice_id 不含 voice_type）

- [ ] **Step 2: UI**
  - 语音合成区增加 Select；watch vendor
  - 火山：VolcTtsFields；腾讯：voice_id / model / region + TENCENT_TRTC_SDK_APP_ID 说明
  - `ttsBody` 分支序列化

- [ ] **Step 3: vitest 通过后提交**

---

### Task 3: 验收自检

- [ ] 跑 `pytest tests/test_ai_tencent_tts.py tests/test_ai_volc_tts.py -q`
- [ ] 跑相关 vitest
- [ ] 确认 `.env` 本地可手填 `TENCENT_TRTC_SDK_APP_ID`（不提交）
