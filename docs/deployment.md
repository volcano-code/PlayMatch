# 运行与部署说明

## 本地默认

SQLite 与无模型配置适合快速体验。应用首次启动执行 Alembic 迁移并装入启动目录。数据保存在 `.state`；目录内容变更会在下一次启动更新对应记录。现有用户、任务与收藏不会被启动脚本清空。

Compose 使用 PostgreSQL。只有 Web 端口绑定宿主机的 loopback，API 与数据库保持在容器网络内。`POSTGRES_PASSWORD` 的默认值仅用于本地开发；可换成随机的 URL 安全字符串，因为同一个值会放入数据库连接 URL。

如果修改 `WEB_PORT`，同时将 `ALLOWED_ORIGINS` 改成实际端口的 origin；否则跨站请求校验会拒绝注册、推荐等写操作。例如端口 3100 对应 `http://localhost:3100,http://127.0.0.1:3100`。

## 服务边界

API 必须保持 **一个 uvicorn worker**。内部 `RUN_WORKERS` 限制并发推荐线程，`MAX_PENDING_RUNS` 限制队列容量。它们不提供跨进程的分布式保证。

SSE 事件先写数据库，客户端按序读取；代理需要保留流式传输，不能缓冲完整响应。Next.js 代理已设置 `no-store` 与 `X-Accel-Buffering: no`。匿名任务访问凭证位于 `X-Run-Token` header；不要将它改成 URL query，也不要记录 Cookie/Authorization。

进程中断后，原 queued/running 任务在下一次启动时转成 failed。已经完成的任务和事件仍可读取。当前没有任务续跑，也没有后台价格轮询。

## 环境配置

| 变量 | 用途 |
| --- | --- |
| `DATABASE_URL` | SQLite 或 `postgresql+psycopg://` 连接 |
| `ALLOWED_ORIGINS` | 实际 Web origin 的逗号分隔列表 |
| `COOKIE_SECURE` | HTTPS 部署时设为 true |
| `RUN_WORKERS` / `MAX_PENDING_RUNS` | 单进程并发与等待容量 |
| `RUN_TIMEOUT_SECONDS` | 推荐阶段的时间预算 |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 可选兼容模型服务 |
| `IGDB_CLIENT_ID` / `IGDB_CLIENT_SECRET` | 可选 IGDB 适配器凭证，不会自动启用目录导入 |
| `API_INTERNAL_URL` | Next.js 服务端到 FastAPI 的固定地址 |
| `PLAYMATCH_CATALOG_PATH` | 显式指定目录文件，供容器或包安装使用 |

公共部署还需要实际 HTTPS 入口、合适的限流层、数据库备份与恢复、邮箱身份验证和账户找回。当前注册仅校验邮箱格式和唯一性。此版本不宣称已经完成这些运营能力。

## 诊断

- `GET /health`：数据库连通性与目录数量。
- `GET /api/v1/capabilities`：实际数据、推荐、价格与执行模式。
- `GET /api/v1/providers/health`：配置状态，不做外部请求，也不把未探测服务标记为健康。
- `scripts/probe_providers.py --live`：明确触发少量只读查询；不会订阅提醒或访问购买跳转。

容器启动失败可查看 `docker compose logs --tail 100 api web db`。不要将带有凭证的环境文件提交到仓库；默认 `.gitignore` 和 `.dockerignore` 已排除本地配置。
