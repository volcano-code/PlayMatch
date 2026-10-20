# v0.3：审核采集与持久向量索引

本阶段保留无密钥可运行的 BM25 默认路径，增加 **IGDB → 审核包 → 事务导入** 和 **离线分批建索引 → 原子激活 → 仅查询向量的在线推荐**。不是自动购买、自动监控、分布式任务系统或全量游戏资料库。

## 1. 真实采集入口及审核边界

在服务端 `.env` 配置 `IGDB_CLIENT_ID` 与 `IGDB_CLIENT_SECRET`。不要把密钥写在命令参数、仓库或聊天里。

```sh
uv run python scripts/collect_igdb.py --live --pages 1 --page-size 50 --output .state/reviews/batch-001.json
uv run python scripts/import_catalog.py .state/reviews/batch-001.json --key igdb-batch-001
# 查看并人工审核该文件的作品、本体/版本、Steam 映射及拒绝原因；再提供精确摘要。
uv run python scripts/import_catalog.py .state/reviews/batch-001.json --key igdb-batch-001 --apply --approve-sha256 <review_sha256>
```

采集本身不写业务数据库。默认最多 50 条，最多四页，每页至多 50 条；查询按递增 IGDB id 分页。`next_after_id` 可用于下一次显式采集的 `--after-id`。达到页数上限不是“源已全部获取”，只在最后一页不足页大小时标记该过滤范围已取完。此游标不是更新订阅：旧 id 的资料变化需要主动重新扫描相应范围，当前没有 webhook、定时增量同步或完整目录保证。

接收 PC 基础作品、remake、remaster、port，并保守排除 DLC、合集及不明确的作品类型。使用新字段 `game_type.type` / `external_games.external_game_source.name`；按已核查的官方接口查询。Steam uid 与商店 URL 不一致、多作品映射同一 app、一个作品映射多个 Steam app 时不任选一个，而是进入拒绝报告。拒绝清单使用 id 和固定原因码，不回显供应商错误正文。

审核包包含规范化 manifest、扫描计数、拒绝清单、分页响应摘要和整个审核包 SHA-256。应用时须显式提供相同摘要，防止“审核后内容被修改却照旧批准”。摘要是完整性约束，不是供应商签名或来源真实性认证；仍需运营者审核。原 v0.2 受信任规范化 manifest 的 CLI 入口兼容，不能据此声称强制全平台人审。

网络失败、认证失败、不连续游标或非法响应不会发布看似完整的审核包；文件发布不覆盖旧审核记录。请求遵守客户端限速、有限重试、总流程超时与响应字节限制；不绕过授权或收集盗版内容。导入的中文支持、合作、恐怖属性仍保持 unknown，没有自动制造中文译名。

## 2. 持久索引配置与操作

```dotenv
RETRIEVAL_MODE=indexed_hybrid
EMBEDDING_BASE_URL=https://your-approved-provider.example/v1
EMBEDDING_MODEL=your-embedding-model
EMBEDDING_API_KEY=your-private-key
EMBEDDING_REVISION=an-immutable-provider-model-revision
```

`EMBEDDING_REVISION` 由运营者核实并声明不可变版本，避免相同模型别名背后的向量空间混用。系统无法侦测供应商在不改版本和维度的情况下替换权重。向量空间身份绑定服务端点（仅存摘要）、模型名、版本和文本转换版本；密钥轮换不触发重建。应使用稳定的服务 URL，变更端点也需要重建。

```sh
# 业务 API 至少启动过一次以初始化默认目录；所有命令使用同一 DATABASE_URL。
uv run python scripts/semantic_index.py status
uv run python scripts/semantic_index.py build --allow-network
uv run python scripts/semantic_index.py list
uv run python scripts/semantic_index.py activate <index_id> --expected-generation 0
# 后续可以在构建成功后自动尝试 CAS 激活；冲突时不会抢占其他发布者。
uv run python scripts/semantic_index.py build --allow-network --activate
# 主动恢复失败或租约过期的构建，只补缺失批次。
uv run python scripts/semantic_index.py build --allow-network --resume <index_id>
# 删除非活跃、非正在构建的旧快照，释放容量；不能删除当前活跃快照。
uv run python scripts/semantic_index.py delete <old_index_id> --confirm
```

建库、读取和切换使用数据库迁移 `0003` 新增表。每批最多 16 条，最多 5,000 款、2,048 维；最多保留 20 个快照。与活跃快照相同文本和身份的向量可复用，变更文本重新计算。网络请求不持有数据库事务。失败保留已提交批次供显式续建，进程死亡留下 building 状态与租约；一分钟租约到期后可接管，所有写入校验 owner 令牌以拒绝旧构建者的迟到写入。

完整构建后仍要核对目录指纹。半成品、目录变更、模型不符、维度异常及校验损坏不能激活。切换活跃指针要求 `expected-generation`，防止并发构建互相覆盖。回滚是重新激活一个与当前目录和模型匹配的 ready 快照；不支持把旧目录的索引强行用于新目录。

在线任务只读取一个固定快照并计算一次查询向量，不在用户请求内重新编码游戏描述。任务记录 `index_id`、`index_generation`、文本指纹和实际检索模式。目录或模型变化会明确降级 BM25，并标记 partial。旧实验性 `hybrid` 仍有 128 款限制；新 `indexed_hybrid` 路径不沿用此限制。

**实现是 PostgreSQL/SQLite 的 JSON 向量持久化 + 精确余弦扫描，不是 pgvector/HNSW/ANN。** 5,000 是输入边界，不是已通过的生产吞吐指标；索引读取与扫描仍为线性成本。下一阶段可在不改变快照/激活语义的前提下替换查询实现。

## 3. 前端、接口和 Docker

`GET /api/v1/retrieval/index` 只报告元数据就绪情况，不发起模型调用；不返回服务端点、密钥或用户查询。ready 表示快照符合当前目录和模型配置，**不表示向量服务在线，也不是全量向量完整性审计**。实际查询及激活还会校验向量。

前端在 indexed_hybrid 模式展示缺失、过期、模型不符、就绪或状态读取失败，并可手动刷新。结果显示实际使用索引；配置目标不会冒充已执行结果。

Docker 已复制 `scripts/` 并透传新配置，可在 API 容器使用 `python scripts/semantic_index.py status` 等命令；不要删数据库卷升级。容器内 `127.0.0.1` 是容器本身，不是宿主机。审核文件应复制到持久存储，默认 API `.state` 不是单独的数据卷。

## 4. 验证与真实模型探针

常规 CI 无需密钥，不下载模型：固定 HTTP fixtures 验证采集协议，合成向量验证存储、恢复、索引切换及损坏拒绝；不能将这些结果称为真实神经模型效果。

```sh
uv run pytest -q
uv run python scripts/check_openapi.py
npm --prefix apps/web test
npm --prefix tests/e2e test
```

`INDEX_TEST_DATABASE_URL` 测试为每个案例建立并删除独立随机 schema，仍要求指向独立可丢弃数据库，并设置 `PLAYMATCH_ALLOW_DISPOSABLE_DB=yes`，不可用于真实数据。

另提供明确授权的真实 CPU 模型探针。模型解释器与应用锁定环境隔离，固定 Hugging Face commit，禁用 remote code，优先 safetensors；模型仅绑定本地临时端口。下载会使用网络和本地磁盘，但不调用付费模型 API。

```sh
python3.12 -m venv /tmp/playmatch-model-env
/tmp/playmatch-model-env/bin/pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
/tmp/playmatch-model-env/bin/pip install -r scripts/model-probe-requirements.txt
uv run python scripts/probe_real_embeddings.py --allow-model-download --model-python /tmp/playmatch-model-env/bin/python --output .state/real-model-report.json
```

模型 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，固定 revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`。探针验证真实 HTTP 嵌入、384 维向量入库、重开数据库读取、未变目录零重复编码，并报告三种检索的原 23 条开发案例结果。其 128 token 上限会截断长文档；模型安装依赖单独记录 freeze，不能把该可选环境冒充应用的 uv.lock。该集合仍非盲测，不能承诺质量提升；无论质量结果好坏均保存。

本阶段未实现生产模型服务部署、真实 IGDB 账号的授权配置、目录批量验真、独立人工评测、通知服务或分布式执行。验收结果以绑定提交的 CI 与报告为准。

## 官方参考

- IGDB API：<https://api-docs.igdb.com/>（认证、速率、Game Type 和 External Game Source）
- SQLAlchemy 事务：<https://docs.sqlalchemy.org/en/21/orm/session_transaction.html>
- SentenceTransformer revision / remote code / model kwargs：<https://www.sbert.net/docs/package_reference/sentence_transformer/model.html>
- 固定模型卡：<https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/tree/e8f8c211226b894fcb81acc59f3b34ba3efd5f42>
