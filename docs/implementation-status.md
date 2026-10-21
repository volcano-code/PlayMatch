# PlayMatch 阶段记录

更新时间：2026-10-20。首轮目标：一个真实可运行、可验证的端到端版本。

## v0.1 已完成

- Next.js 中文界面、响应式目录、推荐输入、真实流式进度、详情与报价、三款对比。
- FastAPI + SQLAlchemy + Alembic；SQLite 本地启动与 PostgreSQL 部署配置。
- LangGraph 结构化工作流、明确的规则基线、可配置真实模型意图接口、校验失败降级。
- 24 款人工启动目录；10 款中文界面与 9 款在线合作具有逐字段 Steam 证据。
- CheapShark 用户触发的真实观测报价；金额、身份、非本体商品、地区和来源错误处理。
- Argon2id / HttpOnly Cookie、任务与账号权限隔离、CSRF 校验、请求体限制、有限并发与限流。
- 任务与事件入库、幂等键、SSE 重放、重启中断状态处理。
- 收藏目标价、持久化喜好与已拥有反馈、撤销、账户切换清理。
- 依赖锁定、OpenAPI 导出检查、容器定义与 GitHub Actions。

## 已执行的本地验证

| 验证 | 结果 | 覆盖范围 |
| --- | --- | --- |
| Python 测试 | 61 passed | 24 个 API/领域测试 + 37 个供应商与目录测试；含 SSE 鉴权连接释放回归 |
| 前端测试 | 4 passed | SSE 分块解码、金额转换、安全链接 |
| Playwright | 6 passed | 真实 Web + API、认证、推荐、SSE、收藏、对比、CSRF代理、手机布局 |
| 开发评估 | 23/23 | 12 个主题案例 + 11 个约束案例；非独立盲测 |
| Ruff | 通过 | Python 静态检查与格式 |
| TypeScript / Prettier | 通过 | 路由类型生成、类型检查、统一格式 |
| Next.js 生产构建 | 通过 | 不依赖在线 API 或远程字体 |
| OpenAPI 一致性 | 通过 | 17 个路径与仓库导出一致 |
| CheapShark 真实探针 | 成功返回观测报价 | 两款游戏，详见带时间的探针报告 |

本地浏览器使用 Chromium 153 临时运行包，Playwright 为 1.64.1。CI 安装 Playwright 对应的标准 Chromium，再独立重跑浏览器验收。测试使用隔离账号与数据库，不使用真实用户资料。

PostgreSQL 和 Docker Compose 不在当前本地环境内运行；它们有独立 CI 门禁。实际远端结果见 [GitHub Actions](https://github.com/volcano-code/PlayMatch/actions/workflows/ci.yml)，以具体提交的运行状态为准。

## 没有声称完成的能力

| 后续能力 | 当前状态与接续验收 |
| --- | --- |
| 大规模目录与 IGDB 导入 | 适配器已实现；需凭证、去重映射、字段级证据与增量同步验收 |
| 向量 + 词法混合检索 | 目前为关键词基线；需加入语义向量、融合排序与对照评估 |
| 列表整体推荐质量 | 开发集只验证主题命中与约束；尾部候选仍可能偏离组合偏好，需要更难查询与人工盲评 |
| 图 checkpoint 与持久队列 | 目前单进程有界执行；需任务幂等、投递可靠性、进程崩溃和恢复测试 |
| 中国区报价 | 未接通；需允许使用且实际覆盖地区、币种与版本的来源 |
| 可确认最低可购价 | 当前资格信息不足；需要购买地区、激活平台、版本等价性证据 |
| 自动价格提醒 | 未启用；先确定允许的来源与用户明确授权，再验证订阅/退订/去重和实际投递 |
| 真实模型质量评估 | HTTP 适配器有确定性测试；未调用用户真实模型账户，不把规则结果归因于 LLM |
| 公开生产部署 | 仓库提供本地与容器启动；尚无公网域名、HTTPS、邮箱验证/恢复、备份演练与持续运行记录 |

## 下一里程碑的建议顺序

1. 将真实 IGDB 导入做到至少 100 款有可靠身份和关键属性的目录，保留现有人工数据的来源关系。
2. 加入可离线复现的向量检索与融合排序，对比纯关键词基线；先解决组合偏好与尾部相关性。
3. 将 run 执行迁移到有投递保证的队列，并接入持久 checkpoint；以故障注入验证恢复和幂等。
4. 扩充独立评估集与压力测试，报告实际数据规模、延迟、成本和失败边界。
5. 在来源使用范围明确后接入新的地区报价与用户主动订阅的价格提醒。

这是完整规划的第一轮工作版本，不是所有后续里程碑已结束的声明。


## v0.2 recovery increment

Implemented: BM25 default and preserved lexical baseline; opt-in bounded embedding/RRF
with explicit degradation; atomic operator-reviewed normalized IGDB import, receipts,
curated preservation and stale/conflicting identity rejection; UI page navigation,
per-record metadata source and actual retrieval/fingerprint display; Docker option
passthrough. Original v0.1 sections above are historical, not the current capability list.

The original interrupted v0.2 was not recovered. Do not reuse its reported 132 Python /
11 web / 7 browser counts or model results. This increment has its own CI evidence.
No bulk live IGDB import, independent semantic model evaluation, notifications, distributed
execution or production readiness is claimed. Main changes only after final acceptance.


## v0.3 increment (acceptance recorded separately)

Added bounded real IGDB collection code producing review bundles; exact-hash approval before
applying collected bundles. No live authorization or production directory expansion is implied.
Added immutable database vector snapshots, incremental reuse, explicit resume, lease fencing,
CAS activation/compatible rollback and protected deletion. New indexed query path sends only
one query to the configured model; current default remains BM25. Indexed UI/status API and
Docker CLI are wired. Optional pinned-model integration probe reports real inference separately
from synthetic regression tests. See catalog-index-v03.md; test counts and execution evidence
are recorded against the delivered commit, not inferred from earlier stages.

## v0.4 — durable runs (supersedes earlier single-process execution notes)

Persisted queue admission, shared capacity, fenced claim/renew/progress/finalization,
bounded crash retries, explicit cancellation, safe-stage snapshots and independent
supervised worker implemented. Compose now runs a worker service. Browser offers
same-task reconnect across refresh and separate cancellation. Regression and cloud
acceptance evidence for this increment are recorded in its PR and validation-v04.md.
No new provider credentials, catalog expansion, notification service, paid generation
call, ANN index or public production deployment is implied by this stage.

## v0.5 increment — own-query price alerts

Implemented explicit one-shot USD observation consent, CAS/idempotent commands,
fresh server-side quote evaluation via an explicit POST, transactional outbox,
fenced leases/retries and owner-scoped in-app notification history/read/delete.
UI makes background polling and external email absence explicit. Same-origin/auth,
existing worker, migration0005 and full source/test/config/docs delivery retained.
No new genuine catalog import, independent recommendation-quality result, CN price,
continuous price monitoring, external email or public production deployment.
See docs/price-alerts-v05.md and this increment's recorded validation, not prior counts.


## v0.6: source-linked catalog expansion and reproducible measurement

- 24 new distinct official Steam product records; default release 48. Sources,
  checked times and coding-agent provenance committed; no IGDB credentials used.
- Chinese interface, online/local co-op and unknown horror remain separate facts.
- Optional editorial aliases, actual coverage endpoint/panel and true second page.
- Original 24-game snapshot and 23-case labels frozen. Separate 40-case development
  challenge, same-catalog alias ablation, exact report and pending review templates.
- Two-reviewer pooled metric workflow implemented; no human grades fabricated.
- Full checks/results for this increment belong in docs/validation-v06.md.
- No new live prices, paid model/embedding run, background polling or deployment.
