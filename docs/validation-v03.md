# v0.3 阶段实际验收记录

## 代码与证据绑定

产品实现提交：`8226598b0d983d260f79cc86c30fa265401f6f1c`。
精确测试 Git tree：`5630bdd5806e8d2ae3df339bf42e6854a54af4c2`。
起点为 main `b1a1dd07c76a793a0ee70fbd6c18d77a0d9a7e15`。

[托管验收 run 39459330192](https://github.com/volcano-code/PlayMatch/actions/runs/39459330192) 的 core 和 real-model 均已成功。工作流先恢复候选补丁，校验补丁 SHA-256 及 Git tree，再测试；导出的 blobs 经逐文件 Git hash 检查。通过连接器重建的正式源码 tree 与测试 tree 完全相同。临时 workbench 的压缩补丁和写权限工作流没有进入产品 tree。随后只追加本记录、模型摘要和环境清单；标准 CI 对正式分支重新执行。

本地为 Python 3.13.5 / Node 22.16；托管为 Ubuntu 24.04 / Python 3.12 / Node 24。基础依赖仍使用项目锁定文件。模型依赖在独立解释器安装，不改变应用环境。

## 执行结果

| 检查 | 结果及口径 |
|---|---|
| Python 全集 | 187 passed，0 skipped；本地与托管均执行 |
| PostgreSQL 目录子集 | 30 passed；复跑，不计作 30 个新增唯一案例 |
| PostgreSQL 索引套件 | 24 passed；参数化存储案例使用独立 PostgreSQL schema，进程强杀专用案例仍使用 SQLite |
| 前端单元 | 12 passed |
| 浏览器 | 10 passed；7 项真实前后端/代理流程，3 项明确合成响应的分页或索引状态 UI 契约 |
| 类型、格式、生产构建 | TypeScript、Ruff、Prettier、Next.js build 通过 |
| OpenAPI | 18 个路径，与导出文件一致 |
| 账户与任务持久化 | PostgreSQL 建库迁移、账户、任务、事件、重启读取通过 |
| 原开发评测 | keyword_baseline、BM25、真实 indexed_hybrid 各通过原有 23 条，标签未改变 |

实现包括采集分页/拒绝码/审核摘要、事务导入、分批落库、文本复用、租约与旧 owner 拒绝、激活版本比较、目录/模型变化降级、损坏向量拒绝、接口状态和 UI 错误状态。

强杀测试确实终止一个独立 Python 构建进程，再恢复缺失批次；为避免等待一分钟，测试主动将租约设为已过期。因此不是“实等 60 秒自然过期”测试，也不是分布式任务执行认证。

超过旧 128 条上限的测试使用 134 条合成目录数据，不代表已有 134 款真实资料。5,000 是输入边界，不是生产性能承诺。

## 真实模型集成

模型为 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，固定 revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`，384 维，CPU，本地临时 HTTP 服务，禁止 remote code，使用 safetensors。

对现有 24 款人工目录实际计算并入库。相同目录重建：**新编码 0，复用 24**；重开数据库读取成功。模型服务统计 26 次请求 / 48 段文本，包含首次建库与后续查询，而非每次推荐重新编码全部文档。

[摘要](../evals/v03-real-model-summary.json) 从原始报告提取，保留原报告 SHA-256；[环境清单](../evals/v03-model-environment.txt) 是该次 pip freeze，不冒充带哈希的完整依赖锁。`cold_build_seconds=0.424` 仅为模型已载入后的本次小目录索引构建，不含安装、下载、模型加载和服务启动，不能用作整体延迟或吞吐指标。128 token 上限会截断长文本。

三组均通过同一探索性开发集合，仅说明这组回归未失败。标签不完整、非独立、非盲测，不证明真实用户满意度或语义推荐优于 BM25。没有进行付费模型调用、线上负载测试或生产模型部署。

## 原始产物

- [core artifact 12281688801](https://github.com/volcano-code/PlayMatch/actions/runs/39459330192/artifacts/12281688801)：SHA-256 `d33fe273bd1fa888b1aa271aafd3350fa1ac912c7f570af897cfa6dc173805ed`。含源码 ZIP、补丁、Git manifest、Python/PostgreSQL JUnit 及词项评测结果。
- [real-model artifact 12280894672](https://github.com/volcano-code/PlayMatch/actions/runs/39459330192/artifacts/12280894672)：SHA-256 `cab76bed8231b0442fd31ccf398ae3de61eed6bdb28f0c45bfe3e37482ce9603`。含完整逐案例模型报告、环境清单和测试 tree。

两份 ZIP 均下载后核对 SHA-256。core 中的 109 个源码文件与本地候选字节一致。Actions artifacts 保留 14 天；本记录及模型摘要持久保留在仓库中。

## 尚未关闭的门槛

真实 IGDB 凭据尚未配置或调用，默认仍为 24 款人工目录；采集实现通过固定 HTTP 契约，不把它宣称为已完成真实批量验真。仍缺独立人工相关性评测、长期运行、CN/CNY 报价、降价通知、分布式任务和生产部署。

持久索引使用 PostgreSQL/SQLite JSON 向量及 Python 精确余弦扫描，不是 pgvector、HNSW 或 ANN。索引状态 ready 只说明元数据匹配，不保证模型服务可达。实际使用和失败行为见 [v0.3 操作说明](catalog-index-v03.md)。
