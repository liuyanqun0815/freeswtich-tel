# 部署

## 一键（推荐）

在**仓库根目录**：

```bash
cp .env.example .env
docker compose up -d --build
```

见根目录 `docker-compose.yml`、`Dockerfile`、`README.md`。

## 仅依赖

```bash
docker compose -f deploy/docker-compose.yml up -d
```

## FreeSWITCH

示例 xml_curl：`deploy/freeswitch/xml_curl.conf.xml`，将 `gateway-url` 指到可达的 config 服务（宿主机映射端口 `8081`）。
