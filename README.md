# 智能电话平台（freeswitch-tel）

基于现有 **FreeSWITCH** 的智能电话平台：浏览器坐席、呼入机器人转人工、通知外呼、树形 IVR、技能组排队、话单与配置管理。

单仓多进程（FastAPI）+ React 管理台。决策逻辑在 `packages/common`，各进程只做 I/O。

## 架构

```
apps/
  api/      # 管理台 REST、登录、话单、发布触发        :8080
  config/  # FreeSWITCH xml_curl                       :8081
  engine/  # 唯一 ESL 控制面
  ai_gw/   # 音频 ↔ AI 协议（运行时占位，compose 默认不启）
  push/    # 坐席业务 WebSocket                        :8082
packages/common/
web/              # 管理台（compose 映射 :80）
deploy/           # FS 示例、仅基础设施 compose
```

**边界：** 只有 `engine` 连 ESL；`config` 只出 XML；路由/分机保存即生效；网关/ACL 经 pending → engine 发布验收；默认租户 `t001`。

## 一键部署（Docker Compose）

仓库根目录：

```bash
cp .env.example .env
# 至少修改 JWT_SECRET、XML_CURL_PASSWORD、FS_HOST / FS_ESL_PASSWORD

docker compose up -d --build
```

将启动：

| 服务 | 说明 | 端口 |
|------|------|------|
| postgres / redis | 依赖 | 5432 / 6379 |
| migrate | 一次性 `alembic upgrade head` | — |
| api / config / push / engine | 平台进程 | 8080 / 8081 / 8082 / — |
| web | nginx 管理台 | **80** |

- 管理台：http://localhost/
- API：http://localhost:8080
- xml_curl：http://localhost:8081/fs/xml（Basic = `XML_CURL_USER` / `XML_CURL_PASSWORD`）
- 坐席 WS：`ws://localhost:8082/ws/agent`（经 web 反代为 `/ws/...`）

`engine` 默认 `FS_HOST=host.docker.internal`（可连本机 FS）；公网 FS 在 `.env` 里改 `FS_HOST`。

可选机器人网关（当前会因未实现而退出）：

```bash
docker compose --profile robot up -d
```

停止：

```bash
docker compose down
```

**不含 FreeSWITCH。** 将现有 FS 的 xml_curl 指到本机 `8081`，示例见 `deploy/freeswitch/xml_curl.conf.xml`。

## 本地开发（不用 Compose 跑应用）

### 仅依赖

```bash
docker compose -f deploy/docker-compose.yml up -d
cp .env.example .env
```

### Python

需要 Python ≥ 3.12。

```bash
python -m pip install -e ".[dev]"
python -m pip install -e packages/common
python -m pip install -e apps/api -e apps/config -e apps/engine -e apps/ai_gw -e apps/push
alembic upgrade head
```

```bash
uvicorn vr_api.main:app --host 0.0.0.0 --port 8080
uvicorn vr_config.main:app --host 0.0.0.0 --port 8081
uvicorn vr_push.main:app --host 0.0.0.0 --port 8082
python -m vr_engine.reader
```

### 前端

```bash
cd web
npm install
npm run dev     # http://127.0.0.1:5173 ，已代理 API
npm test
npm run build
```

## 测试

```bash
pytest tests/ -q
cd web && npm test
```

## 配置生效

| 类型 | 行为 |
|------|------|
| 分机 / 号码 / 呼入入口 | 保存 → 写库 → 下次 REGISTER / 下一通 xml_curl |
| 临时 SIP 密码 | 登录签发到 Redis（默认 12h TTL） |
| 网关 / ACL | `POST /releases` → **pending** → engine ESL/REGED → `applied`/`failed` |
| IVR | 发布不可变版本；通话锁定 `ivr_version` |

## 文档

- [设计说明](docs/superpowers/specs/2026-10-21-smart-phone-platform-design.md)
- [实现计划](docs/superpowers/plans/2026-10-21-smart-phone-platform.md)
- [部署说明](deploy/README.md)

## 联调前已知缺口

ESL Content-Length 分帧、park 写 Redis 会话、CDR Stream→PG、`ai_gw` 可运行 WS、资源页接 API / SIP.js 等。详见设计验收与终审记录。
