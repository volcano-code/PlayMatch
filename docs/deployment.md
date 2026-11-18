# 运行与部署说明

本文描述 v0.8 核心运行基线及 v0.9 Steam-first 多数据源 Metadata 增量。历史阶段的运行边界请以各阶段文档为准；不要将 v0.1 初始说明当作当前实现。

## 本地默认

SQLite 与无模型配置适合快速体验。应用首次启动执行 Alembic 迁移并装入启动目录。数据保存在 `.state`；目录内容变更会在下一次启动更新对应记录。现有用户、任务与收藏不会被启动脚本清空。

本地 quickstart 默认 `RUN_EXECUTION_MODE=embedded`：API 进程内线程消费 SQL 持久队列。它适合开发，不提供跨进程监督。需要独立 worker 时，先将 API 与 worker 的 `RUN_EXECUTION_MODE` 设为 `external`，启动 API 并等待迁移完成，再启动 `python -m playmatch.worker`。

## Docker Compose

Compose 使用 PostgreSQL，启动 Web、API、独立推荐 worker 和数据库。只有 Web 端口绑定宿主机 loopback；API 与数据库保持在容器网络内。Steam 身份同步与 Metadata 补全为可选 profile，默认不启动。

```bash
cp .env.example .env
# 修改 POSTGRES_PASSWORD 为随机 URL 安全字符串
docker compose up --build -d --wait
docker compose ps
docker compose logs -f api worker web
```

启用 Steam 身份同步与 Metadata 补全前，先阅读 `steam-directory-v07.md` 和 `multi-source-metadata-v09.md`。Steam 身份同步需要 operator 自己申请的 Steam Web API Key；默认 Metadata provider 使用 Steam Store AppDetails，不需要 IGDB/Twitch 认证，RAWG 是可选补充：

```bash
docker compose --profile catalog-sync --profile metadata-enrichment up --build -d --wait
docker compose logs -f catalog-sync metadata-enrichment
```

两个采集服务默认关闭。配置凭证或启动 profile 不等于真实同步成功；必须通过状态接口确认最近成功批次、游标、待处理任务、失败和实际资料数量。Steam Store AppDetails 是未文档化的 best-effort 商店接口，不是官方 Steam Web API；RAWG Key 仅在后端使用，且必须遵守 RAWG 当前计划与署名/使用条款。不要将密钥提交 Git、发到聊天或放进 URL。

`POSTGRES_PASSWORD` 的默认值仅用于本地开发；可换成随机的 URL 安全字符串，因为同一个值会放入数据库连接 URL。若修改 `WEB_PORT`，同时将 `ALLOWED_ORIGINS` 改成实际端口的 origin；否则跨站请求校验会拒绝注册、推荐等写操作。例如端口 3100 对应 `http://localhost:3100,http://127.0.0.1:3100`。

## 服务边界与任务可靠性

API 必须保持 **一个 uvicorn worker**。内部 `RUN_WORKERS` 限制推荐执行并发，`MAX_PENDING_RUNS` 限制队列容量；它们不等同于无限吞吐或生产级多租户容量。

当前推荐任务使用数据库持久队列。POST `/api/v1/runs` 在一个事务中写入任务和初始事件；Compose 的独立 worker 通过租约、尝试令牌和有限重试领取任务。刷新页面可重新连接原任务，不应再次 POST 新推荐。任务可取消。执行阶段快照是**应用层安全阶段快照**，不是 LangGraph 原生 checkpointer。

恢复边界：已提交的安全阶段可以继续；未提交的只读外部调用可能重放，因此外部读取操作是 at-least-once，不承诺 exactly-once。worker 不在线时任务可能排队，不会被伪装为已完成。Compose 中 worker 健康检查表示最近能到达共享数据库，不证明 LLM、Steam、IGDB 或报价上游可用。

SSE 事件先写数据库，客户端按序读取；代理需要保留流式传输，不能缓冲完整响应。Next.js 代理已设置 `no-store` 与 `X-Accel-Buffering: no`。匿名任务访问凭证位于 `X-Run-Token` header；不要将它改成 URL query，也不要记录 Cookie/Authorization。

## 环境配置

| 变量 | 用途 |
| --- | --- |
| `DATABASE_URL` | SQLite 或 `postgresql+psycopg://` 连接 |
| `ALLOWED_ORIGINS` | 实际 Web origin 的逗号分隔列表 |
| `COOKIE_SECURE` | HTTPS 部署时设为 true |
| `RUN_EXECUTION_MODE` | `embedded` 本地内嵌消费者；`external` 使用独立 worker |
| `RUN_WORKERS` / `MAX_PENDING_RUNS` | 推荐并发与等待容量 |
| `RUN_LEASE_SECONDS` / `RUN_MAX_ATTEMPTS` / `RUN_TIMEOUT_SECONDS` | 任务租约、重试上限和总时间预算 |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 可选兼容模型服务 |
| `RETRIEVAL_MODE` | `keyword_baseline`、`bm25`、`hybrid` 或 `indexed_hybrid` |
| `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` / `EMBEDDING_API_KEY` | 可选 embedding 服务 |
| `METADATA_PROVIDER` | `steam_rawg`（默认）、`steam_store` 或可选 legacy `igdb` |
| `RAWG_API_KEY` | 可选 RAWG 资料补充 Key；不配置时 Steam Store 主数据源仍可工作 |
| `IGDB_CLIENT_ID` / `IGDB_CLIENT_SECRET` | 仅当 `METADATA_PROVIDER=igdb` 时需要的 legacy provider 凭证 |
| `STEAM_WEB_API_KEY` / `STEAM_SYNC_ENABLED` | Steam 身份同步凭证和显式开关 |
| `METADATA_ENRICHMENT_ENABLED` | Steam Store / RAWG / IGDB Metadata worker 显式开关 |
| `RETRIEVAL_CANDIDATE_LIMIT` | 大目录 SQL 候选正文上限，默认 256 |
| `API_INTERNAL_URL` | Next.js 服务端到 FastAPI 的固定地址 |
| `PLAYMATCH_CATALOG_PATH` | 显式指定目录文件，供容器或包安装使用 |

## 数据源与能力状态

- `GET /health`：数据库连通性与目录数量。
- `GET /api/v1/capabilities`：实际数据、推荐、价格与执行模式。
- `GET /api/v1/providers/health`：配置状态，不做外部请求，也不把未探测服务标记为健康。
- `GET /api/v1/catalog/steam/status`：Steam 身份同步状态、游标、最近心跳和失败信息。
- `GET /api/v1/catalog/enrichment/status`：IGDB 补全配置、worker 状态、批次、积压和可用资料情况。
- `scripts/probe_providers.py --live`：明确触发少量只读供应商查询；不会订阅提醒或访问购买跳转。

Steam 身份目录与推荐资料目录是两层数据。发现的 AppID/名称不自动成为推荐候选；IGDB 补全只有通过精确身份、PC/基础游戏及证据检查后才会进入推荐。默认离线推荐资料仍为48款。v0.8 的大目录推荐使用 SQL 倒排词项召回有限候选，不是全库 ANN；10,000 条资料和100,000条身份的合成容量测试不是实际商店覆盖率或生产 P95。

价格能力仍是用户触发的 US/USD CheapShark 观测报价与一次性站内提醒，不是后台持续抓价、外部邮件或国区报价。价格/版本/激活不确定性必须保留。

## Steam / Metadata 运行预检

安装依赖后，先做不访问外网的配置检查：

```bash
uv run python scripts/check_ingestion_readiness.py
```

API 完成迁移并运行后，可额外读取实际 worker 和数据状态：

```bash
uv run python scripts/check_ingestion_readiness.py --api-base-url http://127.0.0.1:8000
```

严格部署门禁和报告字段见 [ingestion-readiness-v09.md](ingestion-readiness-v09.md)。该工具不会调用 Steam、RAWG 或 IGDB，也不会执行同步；只有 status 接口报告已有 full-sync / enrichment 记录后，才能认为达到相应最低运行验收门槛。

## 公共部署前的必要工作

公共部署还需要实际 HTTPS 入口、适当的限流层、数据库备份与恢复演练、邮箱验证和账户找回、监控告警、密钥轮换与安全审查。当前注册仅校验邮箱格式和唯一性。仓库提供可运行容器配置和 CI，不等于已完成生产运营或 SLA 验收。

## 诊断与安全

容器启动失败：

```bash
docker compose ps
docker compose logs --tail 100 api worker web db
docker compose config --quiet
```

不要把带凭证的 `.env` 提交到仓库；默认 `.gitignore` 和 `.dockerignore` 已排除本地配置。不要在真实运行数据库上运行专项测试或容量脚本；PostgreSQL 专项测试必须使用空的、可丢弃的测试数据库，并按各脚本的 `PLAYMATCH_ALLOW_DISPOSABLE_DB` 保护要求执行。
