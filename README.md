# PlayMatch

[![CI](https://github.com/volcano-code/PlayMatch/actions/workflows/ci.yml/badge.svg)](https://github.com/volcano-code/PlayMatch/actions/workflows/ci.yml)

**通过主题、偏好和约束发现游戏，再查看有来源、可核验的购买报价。**

PlayMatch 是分阶段实现的全栈 Agent 项目。首轮版本提供完整用户流程：中文主题输入 → LangGraph 推荐任务 → 流式进度 → 有依据的候选结果 → 按需查询报价 → 对比、收藏与偏好反馈。

无密钥即可启动。没有模型配置时，产品明确使用规则与关键词检索；有完整模型配置时，才执行真实的结构化意图解析。当前目录为 24 款人工整理的 PC 游戏，不冒充全量游戏索引。

![PlayMatch 桌面端实际运行界面](docs/screenshots/discover-desktop.jpg)

[查看移动端截图](docs/screenshots/discover-mobile.jpg)

## 当前可用功能

| 能力 | 已实现行为 |
| --- | --- |
| 游戏发现 | 中文界面、主题输入、目录筛选、地区与预算、响应式布局 |
| 推荐工作流 | LangGraph 解析、检索、约束筛选、按需预算报价、解释；规则基线与可配置模型路径 |
| 可信约束 | 区分已核验和未知；负向主题、已拥有排除、平台、中文与合作条件；无法证明的条件明确提示 |
| 任务可靠性 | 数据库持久化 run/event、Idempotency-Key、SSE 与 Last-Event-ID 重放、有界并发与超时 |
| 购买辅助 | CheapShark 真实 USD 观测报价、整数金额、来源与查询时间、版本/激活不确定性 |
| 账号与偏好 | Argon2 哈希、HttpOnly 会话 Cookie、用户数据隔离、目标价、喜好与拥有反馈 |
| 游戏对比 | 最多三款并排比较；未知属性保留为待核验 |
| 工程验证 | 锁定依赖、Alembic、Python/前端单元测试、Playwright、PostgreSQL 与容器 CI |

### 三个使用边界

1. **报价范围是 US / USD。** 中国大陆模式可做主题推荐和收藏，目前没有可核验的国区人民币价格。不会把美元换算成国区售价。
2. **观测低价不等于保证可购最低价。** 供应商未提供足够的版本与激活证据，相关报价保留 `eligible_for_best=false`。预算未核验的候选会明确标记。
3. **目标价可以保存，自动通知未启用。** 当前没有后台价格轮询、邮件投递或购买行为。

## 快速启动

### Docker Compose

需要 Docker Engine 和 Compose v2。

```bash
git clone https://github.com/volcano-code/PlayMatch.git
cd PlayMatch
cp .env.example .env
docker compose up --build -d --wait
```

打开 **http://localhost:3000**。Compose 启动 Web、API 与 PostgreSQL，并执行数据库迁移。默认仅绑定本机端口，数据库不对宿主机公开。

```bash
docker compose logs -f api web
# 停止服务并保留数据库卷
docker compose down
```

### 本地开发

需要 Python 3.12、Node.js 24 和 uv。CI 使用 uv 0.12.23。

```bash
cp .env.example .env
uv sync --frozen --extra dev
npm --prefix apps/web ci
```

终端 A：

```bash
uv run uvicorn playmatch.main:app --host 127.0.0.1 --port 8000
```

终端 B：

```bash
npm --prefix apps/web run dev
```

本地使用 `.state/playmatch.db`，自动执行相同的 Alembic 迁移。Web 通过同源 `/api/` 代理访问 API；服务端地址默认为 `http://127.0.0.1:8000`，可在 `apps/web/.env.local` 设置 `API_INTERNAL_URL`。API 文档位于 http://127.0.0.1:8000/docs。

先试：`想探索太空和科幻世界`、`推荐有中文界面的在线合作游戏`、`想经营农场，节奏轻松一点`。输入 `不要恐怖元素` 或目录没有证据的人数限制时，可能得到明确空结果，这属于严格约束处理。

## 可选模型与数据源

在后端 `.env` 配置三项以启用模型意图解析：

```dotenv
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=your-private-key
LLM_MODEL=your-supported-model
```

服务须支持兼容的 `/chat/completions` 接口与 JSON 对象输出。密钥只保留在后端；超时和结构化输出均有边界。模型失败时返回带提示的规则降级结果，不生成游戏或价格数据。本仓库不含模型密钥，也不声称已在真实模型账户上完成验证。

CheapShark 无需密钥。IGDB 的 client credentials 适配器已实现，尚未自动导入目录；配置凭证不会把人工目录改标为 IGDB 目录。参见 [数据来源说明](docs/data-sources.md)。

## 架构与关键决定

```mermaid
flowchart TD
    Browser[Next.js 中文界面] --> Proxy[同源 API 代理]
    Proxy --> API[FastAPI]
    API --> DB[(SQLite / PostgreSQL)]
    API --> Graph[LangGraph 有界工作流]
    Graph --> Catalog[目录与偏好检索]
    Graph --> LLM[可选模型意图解析]
    Graph --> Prices[CheapShark 报价适配器]
    Catalog --> DB
    Graph --> Events[持久化任务事件]
    Events --> DB
    DB --> Stream[SSE 重放]
    Stream --> Browser
```

- [ADR 0001：模块化单体与执行边界](docs/adr/0001-modular-monolith.md)
- [ADR 0002：价格可比性与证据](docs/adr/0002-price-evidence.md)
- [ADR 0003：推荐模式与硬条件](docs/adr/0003-truthful-agent.md)
- [API 与数据契约](docs/implementation-contract.md)
- [OpenAPI 规范](packages/contracts/openapi.json)

状态与事件持久化不等同于 LangGraph checkpoint 恢复。首轮 API 使用一个进程；中断的任务在下一次启动时变为明确失败，用户可重新提交。Redis/Celery 分布式投递、跨进程恢复和向量检索尚未启用。

## 测试与复现

```bash
# 后端、供应商契约与领域逻辑
uv run --extra dev pytest -q
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run python scripts/check_openapi.py

# 前端类型、流式解析与生产构建
npm --prefix apps/web run typecheck
npm --prefix apps/web test
npm --prefix apps/web run build

# 浏览器流程：自动启动独立测试 API 与 Web
npm --prefix tests/e2e ci
npx --prefix tests/e2e playwright install chromium
npm --prefix tests/e2e test

# 探索性开发评估，非独立盲测
uv run python scripts/evaluate_recommendations.py --check
```

浏览器测试使用独立 `.state/e2e.db`，覆盖真实前后端注册、会话、推荐/SSE、三款对比、国区不支持状态、目标价持久化、代理安全边界与手机布局。供应商契约测试使用确定性 HTTP fixtures；它们不证明上游在部署地始终可达。

小量真实来源验证需要显式执行：

```bash
uv run python scripts/probe_providers.py --live --output data/provider-probe.json
```

[供应商探针报告](data/provider-probe.json) 是记录时间点的状态快照，不代表持续可用性或当前价格。[开发评估说明](docs/evaluation.md) 区分了主题命中与完整推荐质量。PostgreSQL 迁移及跨重启持久化由 CI 独立验证，脚本为 `scripts/smoke_postgres.py`。

## 目录

| 路径 | 内容 |
| --- | --- |
| `apps/web` | Next.js 页面、交互组件、代理与流式解析 |
| `services/api/playmatch` | API、认证、模型、推荐图、任务执行与适配器 |
| `services/api/alembic` | 数据库版本迁移 |
| `data` | 人工目录与带时间的供应商探针 |
| `packages/contracts` | 导出的 OpenAPI |
| `tests/provider` / `tests/e2e` | 供应商契约 / 浏览器验收 |
| `evals` | 人工标注开发评估集 |
| `infra` / `.github` | 容器与 CI |
| `docs` | 来源、设计决策、评估与阶段记录 |

## 后续阶段

按 [阶段记录](docs/implementation-status.md) 推进目录导入、混合检索、持久队列与 checkpoint 恢复、更大规模评估。价格通知需要先确定允许的来源与用户授权流程。实际实现、验证结果与未接通能力分别记录；规划指标不会作为已实现的简历成绩。
