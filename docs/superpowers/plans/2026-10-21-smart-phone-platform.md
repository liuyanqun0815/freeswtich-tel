# 智能电话平台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有单台 FreeSWITCH 上交付浏览器坐席、呼入机器人转人工、通知外呼、树形 IVR、技能组排队和话单管理台。

**Architecture:** 单仓多进程。`config` 只生成 XML；`engine` 独占 ESL；`ai_gw` 把音频转成统一协议；`api` 与 `push` 服务管理台和坐席。进行中的通话在 Redis，话单经 Redis Stream 落 PostgreSQL。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL、Redis、React 18、Ant Design 5、Vite、SIP.js、pytest、vitest。

**Spec:** `docs/superpowers/specs/2026-10-21-smart-phone-platform-design.md`

## Global Constraints

- 只有 `engine` 连接 ESL 并下发通道命令。`ai_gw`、机器人、管理台都不连 ESL。
- `config` 只按库中配置生成 XML，不做路由策略。
- `api` 写配置草稿；生效必须走发布，由 `engine` 对 FS 执行 `sofia rescan`、`killgw`、`xml_flush_cache` 等。
- 全链路使用 `vr_call_id`。
- 业务表带 `tenant_id`，本期只存在默认租户 `t001`，界面不提供切换。
- 话单「通话时长」是客户腿 `billsec`；「持续时长」是 `duration`。详情另展示坐席时长、排队时长、机器人时长。
- 话费未配置费率时汇总显示「—」，不估算。
- 播报下行是分句文件：`streamAudio` 落临时文件后由控制引擎 `uuid_broadcast`。
- 坐席振铃超时 20 秒；业务 WebSocket 断开超过 60 秒自动置忙；临时 SIP 密码 TTL 默认 12 小时。
- `session.start` 后 3 秒内没有 `response.*` 或 `pong`，按场景降级。电话侧采样率固定 8000。
- 外呼默认时间窗 9:00–12:00 与 14:00–20:00；同一号码默认 24 小时内最多 1 通成功外呼。
- voice-robot 转人工约定标记为 `[[transfer]]`。开场白默认包含「我是智能助理」。
- 密码 bcrypt。JWT 访问令牌 + 刷新令牌。初始密码首次登录强制修改。
- 本期不做：Kamailio、多 FS、预测外呼、质检评分、React Flow、多租户切换、开放 API/Webhook 产品、班长监听。

## 范围说明

配置、控制、AI、管理台是一条通话链上的依赖，不是可独立上线的子系统。本计划按依赖排序。每一任务结束时，该任务的测试为绿，且不实现 Global Constraints 里「本期不做」的能力。

## 文件地图

| 路径 | 职责 |
|------|------|
| `packages/common/src/vr_common/models.py` | 全部 ORM 表 |
| `packages/common/src/vr_common/security.py` | bcrypt、JWT、a1-hash |
| `packages/common/src/vr_common/redis_keys.py` | Redis key 与 Stream 名 |
| `packages/common/src/vr_common/xmlbuild.py` | directory / dialplan / not-found XML |
| `packages/common/src/vr_common/protocol.py` | AI 协议消息模型 |
| `packages/common/src/vr_common/dispatch.py` | 事件哈希分桶与可丢事件 |
| `packages/common/src/vr_common/session.py` | 通话 Redis 读写 |
| `packages/common/src/vr_common/inbound.py` | park 后的路由决策，返回命令列表 |
| `packages/common/src/vr_common/acd.py` | 最长空闲、振铃超时、溢出 |
| `packages/common/src/vr_common/ivr.py` | 树形 IVR 步进与发布校验 |
| `packages/common/src/vr_common/dialer.py` | 外呼合规与 originate 字符串 |
| `packages/common/src/vr_common/cdr.py` | Stream 事件折成话单行 |
| `packages/common/src/vr_common/release.py` | 发布快照与回滚选择 |
| `packages/common/src/vr_common/voicerobot.py` | voice-robot 消息映射 |
| `apps/api/src/vr_api/main.py` | 管理台 REST |
| `apps/config/src/vr_config/main.py` | `POST /fs/xml` |
| `apps/engine/src/vr_engine/main.py` | ESL 读循环，只调用 common 决策 |
| `apps/ai_gw/src/vr_ai/main.py` | 音频 WebSocket 与内置话术机器人 |
| `apps/push/src/vr_push/main.py` | 坐席业务 WebSocket |
| `web/src/menu.ts` | 菜单文案，与设计第 6 节一致 |
| `web/src/pages/CdrPage.tsx` | 话单筛选、汇总、分列时长 |
| `deploy/docker-compose.yml` | Postgres、Redis、五个进程、前端 |
| `deploy/freeswitch/xml_curl.conf.xml` | 指向 config 的静态示例 |

决策函数放在 `vr_common`，进程只做 I/O。测试不连接真实 FreeSWITCH。

---

### Task 1: 仓库骨架与测试运行器

**Files:**
- Create: `pyproject.toml`
- Create: `packages/common/src/vr_common/__init__.py`
- Create: `tests/test_smoke.py`
- Create: `deploy/docker-compose.yml`
- Create: `.env.example`

**Interfaces:**
- Consumes: 无
- Produces: 可编辑安装包名 `vr-common`、`vr-api`、`vr-config`、`vr-engine`、`vr-ai`、`vr-push`；`pytest` 从仓库根目录收集 `tests/`

- [ ] **Step 1: Write the failing test**

```python
def test_package_imports():
    import vr_common
    assert vr_common.__doc__
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_smoke.py::test_package_imports -v`  
Expected: FAIL，`vr_common` 无法导入

- [ ] **Step 3: Write minimal implementation**

根 `pyproject.toml` 使用 setuptools，包目录 `packages/common/src`。`deploy/docker-compose.yml` 包含 `postgres:16`、`redis:7`、`minio`，端口 5432、6379、9000。`.env.example` 含：

```
DATABASE_URL=postgresql+asyncpg://vr:vr@127.0.0.1:5432/vr
REDIS_URL=redis://127.0.0.1:6379/0
JWT_SECRET=change-me-in-env
XML_CURL_USER=fs
XML_CURL_PASSWORD=change-me
FS_HOST=47.253.82.114
FS_ESL_PORT=8021
FS_ESL_PASSWORD=ClueCon
DEFAULT_TENANT=t001
SIP_CRED_TTL_SECONDS=43200
AGENT_WS_TIMEOUT_SECONDS=60
RING_TIMEOUT_SECONDS=20
```

`vr_common/__init__.py` 的 docstring 写「智能电话平台共享库」。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_smoke.py::test_package_imports -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml packages tests deploy .env.example
git commit -m "chore: scaffold monorepo and local dependencies"
```

---

### Task 2: ORM 模型

**Files:**
- Create: `packages/common/src/vr_common/db.py`
- Create: `packages/common/src/vr_common/models.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/20261021_0001_init.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `DEFAULT_TENANT=t001`
- Produces: SQLAlchemy 类 `User`、`Extension`、`Gateway`、`PhoneNumber`、`Entry`、`Schedule`、`IvrFlow`、`IvrPublish`、`SkillGroup`、`AgentStateLog`、`DialTask`、`DialContact`、`Blacklist`、`AiScene`、`FsNode`、`ConfigRelease`、`AuditLog`、`CallRecord`、`CallLeg`、`CallEvent`、`AiTurn`。`CallRecord.vr_call_id` 唯一。每张业务表有 `tenant_id`，默认 `t001`。

- [ ] **Step 1: Write the failing test**

```python
from vr_common.models import CallRecord, User

def test_call_record_unique_and_tenant_default():
    table = CallRecord.__table__
    assert table.c.vr_call_id.unique
    assert table.c.tenant_id.default.arg == "t001"
    assert "billsec" in table.c
    assert "duration" in table.c
    assert "queue_sec" in table.c
    assert "robot_sec" in table.c
    assert "agent_billsec" in table.c
    assert User.__table__.c.password_hash.nullable is False
    assert User.__table__.c.must_change_password.nullable is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py::test_call_record_unique_and_tenant_default -v`  
Expected: FAIL，`CallRecord` 未定义

- [ ] **Step 3: Write minimal implementation**

`models.py` 用 `DeclarativeBase`。`CallRecord` 列至少包括：`vr_call_id`、`tenant_id`、`caller`、`callee`、`direction`、`agent_ext`、`started_at`、`answered_at`、`ended_at`、`duration`、`billsec`、`queue_sec`、`robot_sec`、`agent_billsec`、`hangup_cause`、`record_url`、`restricted`、`restrict_reason`。`User` 含 `username`、`password_hash`、`role`（`admin` 或 `agent`）、`must_change_password`、`extension_id`。`AiScene` 含 `ws_url`（可空，空表示内置话术）、`fallback`（`transfer` 或 `hangup`）、`opening`。`DialTask` 含 `window_json`、`max_success_per_number_per_day` 默认 1。`ConfigRelease` 含 `snapshot_json`、`status`（`draft`、`applied`、`failed`、`rolled_back`）。Alembic 迁移创建这些表。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py::test_call_record_unique_and_tenant_default -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/common/src/vr_common/models.py packages/common/src/vr_common/db.py alembic.ini alembic tests/test_models.py
git commit -m "feat: add tenant-scoped call and config tables"
```

---

### Task 3: 登录与角色

**Files:**
- Create: `packages/common/src/vr_common/security.py`
- Create: `apps/api/src/vr_api/main.py`
- Create: `apps/api/src/vr_api/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `User.password_hash`、`User.must_change_password`、`User.role`
- Produces:
  - `hash_password(password: str) -> str`
  - `verify_password(password: str, hashed: str) -> bool`
  - `create_access_token(user_id: int, role: str, tenant_id: str) -> str`
  - `create_refresh_token(user_id: int) -> str`
  - `decode_token(token: str) -> dict`
  - `POST /auth/login` 返回 `access_token`、`refresh_token`、`must_change_password`
  - `POST /auth/change-password` 成功后 `must_change_password` 为 false
  - 坐席调用 `POST /gateways` 返回 403

- [ ] **Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient
from vr_api.main import app

def test_agent_cannot_create_gateway(monkeypatch):
    client = TestClient(app)
    # login returns tokens for a seeded agent whose must_change_password is false
    login = client.post("/auth/login", json={"username": "agent1", "password": "secret-pass"})
    token = login.json()["access_token"]
    denied = client.post("/gateways", headers={"Authorization": f"Bearer {token}"}, json={"name": "gw"})
    assert denied.status_code == 403
```

测试文件里用 sqlite 内存库和 `seed_user(username, password, role, must_change_password)`，该函数由本任务实现在 `tests/support.py`。

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth.py::test_agent_cannot_create_gateway -v`  
Expected: FAIL，路由不存在

- [ ] **Step 3: Write minimal implementation**

`security.py` 使用 `bcrypt` 与 `PyJWT`，access token 有效期 30 分钟，refresh token 12 小时，载荷含 `sub`、`role`、`tenant_id`、`typ`。`require_admin` 依赖项在 role 不是 `admin` 时抛 403。登录密码错误返回 401。`must_change_password` 为 true 时，除 `POST /auth/change-password` 与 `POST /auth/refresh` 外的业务接口返回 403，body 为 `{"detail":"password_change_required"}`。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auth.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/common/src/vr_common/security.py apps/api tests/test_auth.py tests/support.py
git commit -m "feat: add bcrypt login and admin-only writes"
```

---

### Task 4: xml_curl 配置服务

**Files:**
- Create: `packages/common/src/vr_common/xmlbuild.py`
- Create: `apps/config/src/vr_config/main.py`
- Create: `deploy/freeswitch/xml_curl.conf.xml`
- Test: `tests/test_xmlbuild.py`
- Test: `tests/test_config_app.py`

**Interfaces:**
- Consumes: `Extension`、`Gateway`、`PhoneNumber`、`Entry`；环境变量 `XML_CURL_USER`、`XML_CURL_PASSWORD`
- Produces:
  - `a1_hash(user: str, domain: str, password: str) -> str` 等于 `md5(f"{user}:{domain}:{password}")`
  - `not_found_xml() -> str` 含 `<result status="not found"/>`
  - `directory_xml(domain: str, user: str, a1: str, agent_id: str) -> str`
  - `park_dialplan(tenant: str, entry: str, park_timeout: int) -> str` 含 `vr_call_id=${uuid}` 与 `<action application="park"/>`
  - `gateway_xml(gateways: list[dict]) -> str`
  - `POST /fs/xml` Basic 认证失败返回 401；`section=directory` 查不到用户返回 not found XML

- [ ] **Step 1: Write the failing test**

```python
from vr_common.xmlbuild import a1_hash, park_dialplan, not_found_xml
import hashlib

def test_park_extension_sets_call_id_then_parks():
    xml = park_dialplan("t001", "did:4008001234", 20)
    assert "vr_call_id=${uuid}" in xml
    assert "vr_tenant=t001" in xml
    assert "vr_entry=did:4008001234" in xml
    assert "park_timeout=20" in xml
    assert '<action application="park"/>' in xml
    assert a1_hash("8001", "t001.vr.local", "pw") == hashlib.md5(b"8001:t001.vr.local:pw").hexdigest()
    assert 'status="not found"' in not_found_xml()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_xmlbuild.py::test_park_extension_sets_call_id_then_parks -v`  
Expected: FAIL，模块不存在

- [ ] **Step 3: Write minimal implementation**

XML 文本用 `xml.sax.saxutils.escape` 转义动态字段。`directory_xml` 使用 a1-hash，不出现明文密码。`POST /fs/xml` 读取 form 字段 `section`、`user`、`domain`、`key_value`、`hostname`。`section=dialplan` 时只返回统一 park extension，不按被叫号码分支。`key_value=sofia.conf` 时返回已发布快照里的网关；没有已发布快照时返回空 gateways。`deploy/freeswitch/xml_curl.conf.xml` 的 `gateway-url` 为 `http://127.0.0.1:8081/fs/xml`，`bindings="directory|dialplan|configuration"`，`timeout` 为 5，`auth-scheme` 为 basic。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_xmlbuild.py tests/test_config_app.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/common/src/vr_common/xmlbuild.py apps/config tests/test_xmlbuild.py tests/test_config_app.py deploy/freeswitch/xml_curl.conf.xml
git commit -m "feat: serve park dialplan and directory over xml_curl"
```

---

### Task 5: 事件分桶与通话会话

**Files:**
- Create: `packages/common/src/vr_common/dispatch.py`
- Create: `packages/common/src/vr_common/session.py`
- Test: `tests/test_dispatch.py`
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: 无 FS 连接
- Produces:
  - `worker_index(call_id: str, n: int) -> int`
  - `DROPPABLE = {"HEARTBEAT", "PLAYBACK_START"}`
  - `is_critical(event_name: str) -> bool` 对 `CHANNEL_HANGUP_COMPLETE` 与 `BACKGROUND_JOB` 为 True
  - `offer(queue_size: int, capacity: int, event_name: str) -> str` 返回 `enqueue`、`drop` 或 `block`
  - `CallSession` 字段 `call_id`、`fs_node`、`tenant_id`、`entry`、`state`
  - `session_ttl_seconds() -> int` 返回 14400
  - `keys.call_key(call_id: str) -> str` 形如 `call:{call_id}`
  - `keys.leg_key(uuid: str) -> str`
  - `keys.CDR_STREAM == "cdr.events"`

- [ ] **Step 1: Write the failing test**

```python
from vr_common.dispatch import worker_index, offer

def test_same_call_same_worker_and_hangup_not_dropped():
    assert worker_index("abc", 64) == worker_index("abc", 64)
    assert offer(64, 64, "HEARTBEAT") == "drop"
    assert offer(64, 64, "CHANNEL_HANGUP_COMPLETE") == "block"
    assert offer(1, 64, "CHANNEL_PARK") == "enqueue"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dispatch.py::test_same_call_same_worker_and_hangup_not_dropped -v`  
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

`worker_index` 使用 `zlib.crc32(call_id.encode()) % n`。`session.py` 的 `save`、`load`、`bind_leg`、`delete` 接受一个满足 `get`/`set`/`expire`/`delete` 的对象（测试用 dict 包装）。`save` 写入 Hash 并设置 TTL 14400。`delete` 同时删除 `call:` 与该通话已知的 `leg:`。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dispatch.py tests/test_session.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/common/src/vr_common/dispatch.py packages/common/src/vr_common/session.py packages/common/src/vr_common/redis_keys.py tests/test_dispatch.py tests/test_session.py
git commit -m "feat: hash call events and store sessions with ttl"
```

---

### Task 6: 呼入 park 决策

**Files:**
- Create: `packages/common/src/vr_common/inbound.py`
- Create: `packages/common/src/vr_common/commands.py`
- Test: `tests/test_inbound.py`

**Interfaces:**
- Consumes: `CallSession`、`Entry.kind`（`robot`、`queue`、`ivr`、`extension`、`playback`）
- Produces:
  - `FsCommand(api: str, args: str)`
  - `decide_park(existing: CallSession | None, direction: str, entry: EntryView | None, number_status: str, uuid: str, call_id: str) -> list[FsCommand]`
  - 无入口或号码 `disabled`：一条 `uuid_kill` 之前先有 `playback` 提示音命令
  - `robot`：`uuid_answer`，然后 `uuid_record` 路径含 `vr_call_id` 且参数含 stereo 标记 `RECORD_STEREO=true`，然后 `uuid_audio_stream` 指向 `ws://ai-gw:8040/fs/stream`
  - 已存在会话的 park 不再次 answer

- [ ] **Step 1: Write the failing test**

```python
from vr_common.inbound import decide_park, EntryView

def test_missing_entry_plays_then_hangs_up():
    cmds = decide_park(None, "inbound", None, "active", uuid="u1", call_id="c1")
    assert cmds[0].api == "uuid_broadcast"
    assert cmds[-1].api == "uuid_kill"
    assert "u1" in cmds[-1].args

def test_robot_answers_records_and_streams():
    entry = EntryView(kind="robot", target="scene-1")
    cmds = decide_park(None, "inbound", entry, "active", uuid="u1", call_id="c1")
    apis = [c.api for c in cmds]
    assert apis == ["uuid_answer", "uuid_setvar", "uuid_record", "uuid_audio_stream"]
    assert "RECORD_STEREO=true" in cmds[1].args
    assert "c1" in cmds[2].args
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_inbound.py -v`  
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

`decide_park` 是纯函数，不打开 socket。提示音文件固定为 `ivr/vr_busy.wav`。录音路径为 `/var/lib/vr/recordings/${strftime(%Y%m%d)}/{call_id}.wav`。`uuid_audio_stream` 参数为 `start ws://ai-gw:8040/fs/stream mono 8k`，metadata JSON 含 `call_id`。`number_status=="disabled"` 与 `entry is None` 走同一条提示音+挂机。`direction!="inbound"` 返回空列表（外呼接通后的 park 由 Task 14 处理）。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_inbound.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/common/src/vr_common/inbound.py packages/common/src/vr_common/commands.py tests/test_inbound.py
git commit -m "feat: decide inbound park actions without dialplan branching"
```

---

### Task 7: 话单折算与查询

**Files:**
- Create: `packages/common/src/vr_common/cdr.py`
- Modify: `apps/api/src/vr_api/main.py`
- Test: `tests/test_cdr.py`

**Interfaces:**
- Consumes: `CallRecord` 列名
- Produces:
  - `apply_event(row: dict, event: dict) -> dict` 幂等更新
  - `GET /cdr?call_id=&caller=&callee=&agent=&direction=&answered=&start=&end=`
  - 响应 `items[].billsec` 映射列表列「通话时长」，`items[].duration` 映射「持续时长」
  - `summary.fee_display` 在未传费率时恒为 `"—"`
  - `summary.answer_rate` 为接通数/呼叫数，呼叫数为 0 时为 0
  - `summary.restricted_rate` 为 `restricted==true` 的数量/呼叫数

- [ ] **Step 1: Write the failing test**

```python
from vr_common.cdr import apply_event

def test_hangup_updates_duration_without_duplicating():
    row = apply_event({}, {"type": "start", "vr_call_id": "c1", "caller": "1000", "callee": "1001"})
    row = apply_event(row, {"type": "answer", "billsec": 0})
    row = apply_event(row, {"type": "hangup", "duration": 45, "billsec": 16, "hangup_cause": "NORMAL_CLEARING"})
    row = apply_event(row, {"type": "hangup", "duration": 45, "billsec": 16, "hangup_cause": "NORMAL_CLEARING"})
    assert row["vr_call_id"] == "c1"
    assert row["duration"] == 45
    assert row["billsec"] == 16
```

另写 API 测试：插入一通 `restricted=true` 与一通已接通，`GET /cdr` 的 `summary.fee_display=="—"`，`answer_rate==0.5`，`restricted_rate==0.5`。

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cdr.py -v`  
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

`apply_event` 对同一 `vr_call_id` 覆盖时长而不是累加。`hangup_cause` 只在为空时写入第一条。API 查询强制 `tenant_id=="t001"`。列表不返回话费金额字段。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cdr.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/common/src/vr_common/cdr.py apps/api tests/test_cdr.py
git commit -m "feat: fold call events into idempotent cdr rows"
```

---

### Task 8: 管理台壳与话单页

**Files:**
- Create: `web/package.json`
- Create: `web/src/menu.ts`
- Create: `web/src/pages/CdrPage.tsx`
- Create: `web/src/layout/AdminLayout.tsx`
- Test: `web/src/menu.test.ts`
- Test: `web/src/pages/CdrPage.test.tsx`

**Interfaces:**
- Consumes: `GET /cdr` 的 `items` 与 `summary`
- Produces: 菜单项文本依次为「概览」「坐席工作台」「话单」「分机」「线路与号码」「IVR」「技能组」「外呼任务」「黑名单」「AI 场景」「配置发布」「系统」。话单表有列「通话时长」「持续时长」。汇总在 `fee_display` 为「—」时原样显示。

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from "@testing-library/react";
import { CdrPage } from "./CdrPage";

test("splits talk time and full duration", () => {
  render(<CdrPage summary={{ fee_display: "—", answer_rate: 0.5, restricted_rate: 0, answered: 1, total: 2, talk_sec: 16 }} items={[{ vr_call_id: "c1", billsec: 16, duration: 45, agent_billsec: 10, queue_sec: 8, robot_sec: 6, caller: "1000", callee: "1001", direction: "inbound", hangup_cause: "NORMAL_CLEARING" }]} />);
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
```

`menu.test.ts` 断言 `MENU_LABELS` 与上表完全一致，且不含「数据池」「功能按键」。

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- --run src/menu.test.ts src/pages/CdrPage.test.tsx`  
工作目录：`web`  
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

`AdminLayout` 使用 Ant Design `Layout`、`Menu`、`Tabs`。内容区白底。`CdrPage` 筛选字段：通话 ID、主叫、被叫、坐席、方向、日期、是否接通。主按钮文案「查询」。操作列渲染「详情」「录音」「加入黑名单」三个文字按钮。详情区展示该行的 `agent_billsec`、`queue_sec`、`robot_sec`，标签为「坐席时长」「排队时长」「机器人时长」。不加入质检、多租户、API 密钥菜单。测试数据补上这三个字段，值分别为 10、8、6，并断言这三个数字出现。

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- --run src/menu.test.ts src/pages/CdrPage.test.tsx`  
工作目录：`web`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web
git commit -m "feat: add admin shell and cdr list styled as an operations console"
```

---

### Task 9: 临时 SIP 密码

**Files:**
- Create: `packages/common/src/vr_common/sipcred.py`
- Modify: `apps/api/src/vr_api/main.py`
- Modify: `apps/config/src/vr_config/main.py`
- Test: `tests/test_sipcred.py`

**Interfaces:**
- Consumes: `a1_hash`、`keys.sipcred_key(domain, ext)`
- Produces:
  - `issue_sip_password() -> str` 至少 16 位
  - `POST /auth/login` 当用户绑定分机时额外返回 `extension`、`sip_password`、`domain`、`wss_url`、`ice_servers`
  - directory 查询：Redis 中无凭证返回 not found XML；有凭证返回对应 a1-hash
  - 同一分机第二次登录使第一次的密码失效

- [ ] **Step 1: Write the failing test**

```python
from vr_common.sipcred import store_cred, lookup_a1

def test_second_login_replaces_password():
    store = {}
    store_cred(store, "t001.vr.local", "8001", "first-password-xxxx", "A1", ttl=43200)
    store_cred(store, "t001.vr.local", "8001", "second-password-xxx", "A1", ttl=43200)
    assert lookup_a1(store, "t001.vr.local", "8001", "first-password-xxxx") is None
    assert lookup_a1(store, "t001.vr.local", "8001", "second-password-xxx")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sipcred.py::test_second_login_replaces_password -v`  
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

凭证结构 `{"password": "...", "a1": "...", "agent_id": "..."}`。TTL 读取 `SIP_CRED_TTL_SECONDS`，默认 43200。`wss_url` 来自环境变量 `WSS_URL`，默认 `wss://47.253.82.114:7443`。`ice_servers` 只含 STUN `stun:stun.l.google.com:19302`，本任务不生成 TURN。登出接口 `POST /auth/logout` 删除 `sipcred` key。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sipcred.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/common/src/vr_common/sipcred.py apps/api apps/config tests/test_sipcred.py
git commit -m "feat: bind SIP registration to a single platform login"
```

---

### Task 10: 坐席状态与业务 WebSocket 规则

**Files:**
- Create: `packages/common/src/vr_common/agent.py`
- Create: `apps/push/src/vr_push/main.py`
- Test: `tests/test_agent.py`

**Interfaces:**
- Consumes: 坐席状态枚举 `offline`、`idle`、`ringing`、`talking`、`wrapup`、`break`
- Produces:
  - `can_take_call(sip_registered: bool, ws_online: bool, state: str) -> bool` 仅三者分别为 True、True、`idle` 时为 True
  - `on_ws_silent(silent_seconds: int, state: str) -> str` 当 `silent_seconds>=60` 且 state 为 `idle` 时返回 `break`
  - `pop_before_dial(summary: str, call_id: str) -> dict` 类型为 `call.incoming`，字段含 `call_id`、`summary`
  - 命令列表函数 `dial_agent_after_pop(ext: str, call_id: str) -> list[FsCommand]` 的第一条不是 `originate`；调用方先发送 pop 消息，再执行该列表中的 `originate`

- [ ] **Step 1: Write the failing test**

```python
from vr_common.agent import can_take_call, on_ws_silent, pop_before_dial

def test_available_requires_sip_ws_and_idle():
    assert can_take_call(True, True, "idle") is True
    assert can_take_call(True, False, "idle") is False
    assert on_ws_silent(60, "idle") == "break"
    assert on_ws_silent(59, "idle") == "idle"
    msg = pop_before_dial("要转人工", "c1")
    assert msg["type"] == "call.incoming"
    assert msg["summary"] == "要转人工"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent.py::test_available_requires_sip_ws_and_idle -v`  
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

`apps/push` 提供 `WS /ws/agent`。首帧 `{"type":"auth","token":"..."}`。合法消息类型白名单：`agent.ready`、`agent.not_ready`、`agent.wrapup_done`、`call.hold`、`call.unhold`、`call.transfer`、`call.hangup`、`ping`。`ping` 回 `pong`。状态变更写入 `AgentStateLog`。push 进程源码不得 import ESL 客户端或创建到 8021 的 socket；用测试扫描 `apps/push` 与 `apps/ai_gw` 的源码，断言不含 `8021` 与 `esl`。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/common/src/vr_common/agent.py apps/push tests/test_agent.py
git commit -m "feat: mark agents busy unless phone and websocket are both ready"
```

---

### Task 11: ACD 最长空闲

**Files:**
- Create: `packages/common/src/vr_common/acd.py`
- Test: `tests/test_acd.py`

**Interfaces:**
- Consumes: `can_take_call`、`SkillGroup.overflow_target`、`SkillGroup.ring_timeout_seconds` 默认 20
- Produces:
  - `pick_longest_idle(agents: list[AgentSnapshot]) -> str | None` 返回 `agent_id`
  - `on_ring_timeout(agent_id: str, waited: int, limit: int) -> str` 返回 `requeue` 或 `keep`
  - `overflow(reason: str, target: str) -> str` `target` 为 `queue:{id}`、`robot:{scene}` 或 `hangup`

- [ ] **Step 1: Write the failing test**

```python
from datetime import datetime, timedelta, timezone
from vr_common.acd import AgentSnapshot, pick_longest_idle, on_ring_timeout

def test_longest_idle_and_ring_timeout():
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    agents = [
        AgentSnapshot("a", idle_since=now - timedelta(seconds=10), sip_registered=True, ws_online=True, state="idle"),
        AgentSnapshot("b", idle_since=now - timedelta(seconds=50), sip_registered=True, ws_online=True, state="idle"),
        AgentSnapshot("c", idle_since=now - timedelta(seconds=90), sip_registered=False, ws_online=True, state="idle"),
    ]
    assert pick_longest_idle(agents) == "b"
    assert on_ring_timeout("b", 20, 20) == "requeue"
    assert on_ring_timeout("b", 19, 20) == "keep"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_acd.py::test_longest_idle_and_ring_timeout -v`  
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

只实现最长空闲。不可分配的坐席不参与排序。无人可分配时 `pick_longest_idle` 返回 None，调用方按 `overflow` 处理。`requeue` 表示坐席状态改为 `break`，通话回到队列头部。不实现最少接听、轮询、预测外呼。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_acd.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/common/src/vr_common/acd.py tests/test_acd.py
git commit -m "feat: queue calls to the longest-idle ready agent"
```

---

### Task 12: 转接命令与一方挂断

**Files:**
- Modify: `packages/common/src/vr_common/commands.py`
- Test: `tests/test_bridge.py`

**Interfaces:**
- Consumes: `FsCommand`
- Produces:
  - `bridge(a_uuid: str, b_uuid: str) -> list[FsCommand]` 两条腿都先 `uuid_setvar park_after_bridge=true`，再 `uuid_bridge`
  - `on_leg_hangup(other_uuid: str, park_after_bridge: bool) -> list[FsCommand]` 为 True 时不 `uuid_kill` 对端；为 False 时杀对端
  - `stop_stream(uuid: str) -> FsCommand` 参数为 `uuid_audio_stream {uuid} stop`
  - `finish_if_no_next(uuid: str) -> FsCommand` 的 `api` 为 `uuid_kill`

- [ ] **Step 1: Write the failing test**

```python
from vr_common.commands import bridge, on_leg_hangup

def test_bridge_returns_other_leg_to_park():
    cmds = bridge("ua", "ub")
    assert any(c.api == "uuid_setvar" and "ua" in c.args and "park_after_bridge=true" in c.args for c in cmds)
    assert cmds[-1].api == "uuid_bridge"
    assert on_leg_hangup("ub", True) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bridge.py::test_bridge_returns_other_leg_to_park -v`  
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

`uuid_bridge` 的 args 为 `ua ub`。没有下一步流程时，引擎在对端回到 park 后追加一条 `uuid_kill`，该行为由 `finish_if_no_next(uuid: str) -> FsCommand` 单独提供，测试断言其 api 为 `uuid_kill`。不实现满意度自动流程。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bridge.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/common/src/vr_common/commands.py tests/test_bridge.py
git commit -m "feat: bridge agents without hanging up the other leg"
```

---

### Task 13: AI 协议、内置话术与打断

**Files:**
- Create: `packages/common/src/vr_common/protocol.py`
- Create: `apps/ai_gw/src/vr_ai/scriptbot.py`
- Create: `apps/ai_gw/src/vr_ai/main.py`
- Test: `tests/test_protocol.py`
- Test: `tests/test_scriptbot.py`

**Interfaces:**
- Consumes: `AiScene.opening`、`AiScene.fallback`、`FsCommand`
- Produces:
  - Pydantic 模型，`type` 取值包含 `session.start`、`response.cancel`、`action.transfer`、`action.hangup`
  - `parse_message(raw: str) -> dict | None` 未知 type 返回 None
  - `ScriptBot.reply(final_text: str) -> dict` 文本含「人工」时 `type=="action.transfer"`
  - `opening_text(scene_opening: str) -> str` 结果包含「我是智能助理」
  - `commands_for_cancel(uuid: str) -> list[FsCommand]` 含 `uuid_break` args `{uuid} all`
  - `commands_for_transfer(uuid: str) -> list[FsCommand]` 第一条为 `uuid_audio_stream ... stop`
  - `degraded(elapsed_ms: int, saw_response: bool) -> bool` 在 `elapsed_ms>=3000` 且 `saw_response` 为 False 时为 True
  - `reject_keyword_hit(text: str, keywords: list[str]) -> bool`

- [ ] **Step 1: Write the failing test**

```python
from vr_common.protocol import parse_message
from vr_ai.scriptbot import ScriptBot, opening_text

def test_unknown_type_ignored_and_transfer_keyword():
    assert parse_message('{"type":"nope","session_id":"c","seq":1,"ts":1}') is None
    bot = ScriptBot(keywords=["人工"])
    assert bot.reply("转人工")["type"] == "action.transfer"
    assert "我是智能助理" in opening_text("您好")

def test_silence_degrades_and_reject_keyword_hits():
    from vr_common.protocol import degraded, reject_keyword_hit
    assert degraded(3000, False) is True
    assert degraded(2999, False) is False
    assert degraded(3000, True) is False
    assert reject_keyword_hit("不要再打了", ["不要再打"]) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_protocol.py tests/test_scriptbot.py -v`  
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

`session.start` 必填 `session_id`、`seq`、`ts`、`sample_rate`。`sample_rate` 不是 8000 时 `parse_message` 抛 `ValueError`。`ScriptBot` 开场白把场景 opening 接在「我是智能助理。」之后。`degraded` 与 `reject_keyword_hit` 放在 `protocol.py`。命中拒呼词时 engine 把主叫写入 `Blacklist`，原因 `customer_reject`。打断命令不在 `ai_gw` 里执行，只返回 `FsCommand` 给 engine。把 `apps/ai_gw/src` 加入包搜索路径，模块名 `vr_ai`。`apps/ai_gw` 源码不得包含 `8021`。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_protocol.py tests/test_scriptbot.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/common/src/vr_common/protocol.py apps/ai_gw tests/test_protocol.py tests/test_scriptbot.py
git commit -m "feat: add scripted bot and barge-in command mapping"
```

---

### Task 14: IVR 解释与发布锁定

**Files:**
- Create: `packages/common/src/vr_common/ivr.py`
- Modify: `apps/api/src/vr_api/main.py`
- Test: `tests/test_ivr.py`

**Interfaces:**
- Consumes: `IvrFlow.draft_json`、`IvrPublish.version`、`IvrPublish.snapshot_json`
- Produces:
  - `validate(flow: dict) -> list[str]` 同一父节点按键重复时含 `duplicate_digit`
  - `publish(draft: dict, previous_version: int) -> dict` 返回 `version=previous_version+1` 与深拷贝快照
  - `step(snapshot: dict, node_id: str, digit: str | None) -> dict` 键 `*` 留在当前放音节点；收号节点空输入后的 `#` 返回父节点
  - 通话锁定 `locked_version` 后，`step` 只读该快照

- [ ] **Step 1: Write the failing test**

```python
from vr_common.ivr import validate, publish, step

def test_publish_locks_old_call_to_previous_snapshot():
    draft = {"id": "root", "kind": "menu", "children": [
        {"digit": "1", "id": "n1", "kind": "hangup"},
        {"digit": "1", "id": "n2", "kind": "hangup"},
    ]}
    assert "duplicate_digit" in validate(draft)
    ok = {"id": "root", "kind": "menu", "children": [{"digit": "1", "id": "n1", "kind": "queue", "target": "g1"}]}
    pub = publish(ok, 3)
    assert pub["version"] == 4
    assert step(pub["snapshot"], "root", "1")["target"] == "g1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ivr.py::test_publish_locks_old_call_to_previous_snapshot -v`  
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

`POST /ivr/{id}/publish` 仅管理员。新快照写入 `IvrPublish`。进行中的通话在会话里保存 `ivr_version`，不读取草稿。`*` 的 `step` 结果 `action=="replay"`。收号节点 `terminators` 含 `#`，且 `digit=="#"` 且缓冲为空时 `action=="parent"`。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ivr.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/common/src/vr_common/ivr.py apps/api tests/test_ivr.py
git commit -m "feat: publish immutable ivr snapshots for in-flight calls"
```

---

### Task 15: 外呼合规与 originate

**Files:**
- Create: `packages/common/src/vr_common/dialer.py`
- Test: `tests/test_dialer.py`

**Interfaces:**
- Consumes: `Blacklist`、`DialTask.window_json`、`DialTask.max_success_per_number_per_day`
- Produces:
  - `block_reason(now, task, number, blacklist, success_count_24h) -> str | None` 返回 `blacklist`、`outside_window`、`frequency` 或 None
  - `originate(gateway: str, caller: str, callee: str, call_id: str, uuid: str) -> str` 含 `origination_uuid`、`vr_call_id`、`&park()`
  - `next_action(...) -> dict` 在 `block_reason` 非空时为 `{"status":"blocked","reason":...}` 且没有 `originate` 键
  - 被拦截时不调用 `originate`

- [ ] **Step 1: Write the failing test**

```python
from datetime import datetime
from vr_common.dialer import block_reason, originate, TaskWindow

def test_blacklist_does_not_originate():
    now = datetime(2026, 10, 21, 10, 0)
    task = TaskWindow(morning=(9, 12), afternoon=(14, 20), max_success=1)
    assert block_reason(now, task, "13800000000", {"13800000000"}, 0) == "blacklist"
    cmd = originate("gw1", "0210000", "13800000000", "c1", "u1")
    assert "origination_uuid=u1" in cmd
    assert "vr_call_id=c1" in cmd
    assert cmd.endswith("&park()")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dialer.py::test_blacklist_does_not_originate -v`  
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

默认窗口外（例如 13:00、21:00）返回 `outside_window`。`success_count_24h >= max_success` 返回 `frequency`。`originate` 使用 `bgapi` 参数字符串：`originate {origination_uuid=u1,vr_call_id=c1,origination_caller_id_number=0210000}sofia/gateway/gw1/13800000000 &park()`。调度函数 `next_action(...)` 在 `block_reason` 非空时返回 `{"status":"blocked","reason":...}` 且不含 originate 字符串。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dialer.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/common/src/vr_common/dialer.py tests/test_dialer.py
git commit -m "feat: block outbound calls that fail compliance checks"
```

---

### Task 16: 配置发布回滚、审计与对账

**Files:**
- Create: `packages/common/src/vr_common/release.py`
- Create: `packages/common/src/vr_common/reconcile.py`
- Modify: `apps/api/src/vr_api/main.py`
- Test: `tests/test_release.py`
- Test: `tests/test_reconcile.py`

**Interfaces:**
- Consumes: `ConfigRelease.snapshot_json`、`AuditLog`
- Produces:
  - `plan_apply(kind: str, name: str) -> list[str]` 网关修改返回 `["sofia profile external killgw {name}", "sofia profile external rescan"]`；新增网关只有 rescan；分机变更返回 `["xml_flush_cache {name}"]`
  - `rollback(history: list[dict]) -> dict` 返回最近一条 `status=="applied"` 的快照
  - `reconcile(redis_ids: set[str], fs_ids: set[str]) -> dict` 键 `missing_on_fs`、`missing_in_redis`
  - `POST /releases` 写 `AuditLog`，字段 `actor`、`action`、`before`、`after`
  - 任一步命令失败时新行 `status=="failed"`，当前生效版本仍是回滚选中的上一版

- [ ] **Step 1: Write the failing test**

```python
from vr_common.release import plan_apply, rollback
from vr_common.reconcile import reconcile

def test_failed_release_keeps_previous_applied_snapshot():
    assert plan_apply("gateway_update", "gw1")[0] == "sofia profile external killgw gw1"
    history = [
        {"id": 1, "status": "applied", "snapshot": {"gw": "old"}},
        {"id": 2, "status": "failed", "snapshot": {"gw": "new"}},
    ]
    assert rollback(history)["snapshot"]["gw"] == "old"
    assert reconcile({"c1", "c2"}, {"c2"})["missing_on_fs"] == {"c1"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_release.py tests/test_reconcile.py -v`  
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

`missing_on_fs` 的通话要生成一条 `hangup_cause="RECONCILE"` 的话单事件，并调用 `session.delete`。`missing_in_redis` 且事件带 `vr_call_id` 时生成 `uuid_kill`。录音播放 `GET /cdr/{call_id}/recording` 返回短期 token 路径 `/media/{token}`，并插入 `AuditLog.action=="play_recording"`。无 `record_url` 时返回 404。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_release.py tests/test_reconcile.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/common/src/vr_common/release.py packages/common/src/vr_common/reconcile.py apps/api tests/test_release.py tests/test_reconcile.py
git commit -m "feat: roll back failed config publishes and reconcile stale calls"
```

---

### Task 17: voice-robot 映射与管理端资源

**Files:**
- Create: `packages/common/src/vr_common/voicerobot.py`
- Create: `apps/api/src/vr_api/resources.py`
- Create: `web/src/pages/ResourcePages.tsx`
- Test: `tests/test_voicerobot.py`
- Test: `tests/test_resources.py`
- Test: `web/src/pages/ResourcePages.test.tsx`

**Interfaces:**
- Consumes: `parse_message`、管理员 JWT、菜单路由
- Produces:
  - `to_voice_robot(pcm: bytes, sample_rate: int) -> dict` 字段 `type=="audio_chunk"`，`audio` 为 base64
  - `from_voice_robot(msg: dict) -> dict | None` 当文本含 `[[transfer]]` 时得到 `action.transfer`，且输出文本去掉该标记
  - 管理员 CRUD：`/extensions`、`/gateways`、`/numbers`、`/entries`、`/skill-groups`、`/dial-tasks`、`/blacklist`、`/ai-scenes`、`/fs-nodes`、`/users`
  - 前端页面标题与 `MENU_LABELS` 对应；这些页面不出现「多租户」切换控件

- [ ] **Step 1: Write the failing test**

```python
from vr_common.voicerobot import from_voice_robot

def test_transfer_marker_becomes_action():
    out = from_voice_robot({"type": "assistant_text", "text": "好的[[transfer]]"})
    assert out["type"] == "action.transfer"
    assert "[[transfer]]" not in out.get("text", "")
```

资源测试对 `/blacklist` 做一次管理员 POST 与坐席 POST，期望 200 与 403。

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_voicerobot.py tests/test_resources.py -v`  
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

`to_voice_robot` 使用标准 base64。`from_voice_robot` 对未知类型返回 None。CRUD 写入时 `tenant_id` 固定 `t001`，请求体里的 `tenant_id` 被忽略。`AiScene.fallback` 只接受 `transfer` 与 `hangup`。前端资源页用 Ant Design `Table` 与 `Form`，筛选区与话单页同一布局密度。坐席工作台页只放 SIP.js 注册所需字段展示（分机、WSS），本任务不在单测里拨打真实 WSS。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_voicerobot.py tests/test_resources.py -v`  
工作目录 `web`：`npm test -- --run src/pages/ResourcePages.test.tsx`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/common/src/vr_common/voicerobot.py apps/api/src/vr_api/resources.py web/src/pages/ResourcePages.tsx tests/test_voicerobot.py tests/test_resources.py web/src/pages/ResourcePages.test.tsx
git commit -m "feat: map voice-robot events and expose admin resources"
```

---

### Task 18: engine 进程把决策接到 ESL 读循环

**Files:**
- Create: `apps/engine/src/vr_engine/main.py`
- Create: `apps/engine/src/vr_engine/reader.py`
- Test: `tests/test_engine_loop.py`

**Interfaces:**
- Consumes: `offer`、`decide_park`、`bridge`、`commands_for_cancel`、`next_action`、`reconcile`、`apply_event`
- Produces:
  - `handle_event(event: dict, ctx: EngineCtx) -> list[FsCommand]`
  - `retry_or_kill(attempts: int) -> str`：`attempts==0` 返回 `retry`，否则返回 `kill`
  - `CHANNEL_PARK` 且无会话时调用 `decide_park`
  - `ctx.transport` 是测试桩；生产入口 `connect(host, port, password)` 只在 `vr_engine.reader` 中出现
  - 其他 app 目录不定义 `connect`

- [ ] **Step 1: Write the failing test**

```python
from vr_engine.main import handle_event, EngineCtx

def test_park_without_entry_emits_hangup():
    from vr_engine.main import retry_or_kill
    ctx = EngineCtx(entries={}, numbers={"10086": "disabled"})
    cmds = handle_event({"Event-Name": "CHANNEL_PARK", "Unique-ID": "u1", "Caller-Direction": "inbound", "Caller-Destination-Number": "10086", "variable_vr_call_id": "c1"}, ctx)
    assert cmds[-1].api == "uuid_kill"
    assert retry_or_kill(0) == "retry"
    assert retry_or_kill(1) == "kill"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_engine_loop.py::test_park_without_entry_emits_hangup -v`  
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

`handle_event` 用 `variable_vr_call_id` 作为哈希键，没有时用 `Unique-ID`。命令写入 `ctx.sent`。同时把 `apply_event` 的结果 `xadd` 到名为 `cdr.events` 的测试列表。`connect` 使用 inbound ESL：连接 `FS_HOST`:`FS_ESL_PORT`，发送 `auth {password}` 与 `event plain` 加设计文档中的事件名。读循环里的业务判断只调用 `handle_event`。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_engine_loop.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/engine tests/test_engine_loop.py
git commit -m "feat: run call decisions from the single ESL owner"
```

---

## 自检记录

- 设计第 4 节呼入、外呼、坐席、ACD、IVR：Task 6、10、11、12、14、15、18。
- 设计第 5 节协议与 `[[transfer]]`：Task 13、17。
- 设计第 6 节菜单与话单口径：Task 7、8、17。
- 设计第 7、8 节 Redis、录音审计、回滚、对账：Task 5、7、16。
- 设计第 9 节真实 FS 通话不在单测里拨号。Task 18 留下 `connect`，联调按设计第 9 节的 7 条用例人工执行。
- 未纳入任务：Kamailio、预测外呼、质检、React Flow、多租户切换、Webhook 投递、班长监听。
