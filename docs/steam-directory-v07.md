# v0.7：Steam 自动发现目录，而非另一个手工种子分片

本轮提供可部署的初次全量/增量/周期对账调度器、事务游标、数据库身份目录和前端状态/查询。**交付代码不等于已经连上用户的 Steam 账号或已经导入全库。** 当前开发环境未提供 Steam Key，没有执行真实鉴权接口遍历；生产目录数据不存入 Git，也不以合成容量数据冒充 Steam 游戏数量。

## 官方契约与范围

核对日期：2026-10-21。
- Valve IStoreService：<https://partner.steamgames.com/doc/webapi/IStoreService>。
- 公共 API 主机、Service input_json：<https://partner.steamgames.com/doc/webapi_overview>。
- x-webapi-key 请求头：<https://partner.steamgames.com/doc/webapi_overview/auth>。

只调用 `https://api.steampowered.com/IStoreService/GetAppList/v1/`，不误用发行商专用主机。显式选择 games，关闭 DLC/software/videos/hardware，不限制简介语言。正式结果有多少条取决于当时接口返回，不能预写一个“Steam 全库数量”。`last_modified`、`price_change_number` 是变更线索，不是当前售价、中文支持或完整玩法资料。Key 为 SecretStr，仅放请求头，URL、公有状态及错误不包含凭据。不重定向至其他主机。

**双层数据：** `steam_directory_apps` 是独立、无5,000条总量限制的身份发现表；已有 `games` 是包含推荐资料的小目录。新名称不会自动变成带有编造简介的推荐候选，不会把“已发现”统计当“可推荐”。本轮尚未接通无人审核的自动元数据补全和大规模向量召回；原推荐资料/向量路径仍有其原先上限。48款离线资料继续供演示/回归，不是 Steam 发现接口的上限。

## 启用

先备份运行数据库，保存本地修改，停止旧 API/worker，不删除卷。更新后 API 启动执行迁移0006，建立身份表/游标/回执，并分批回填既有游戏的 SQL 搜索字段。原 JSON、ID和收藏引用保留。

将你自己申请的 Steam Web API Key 配到服务端 `.env`（不要发聊天、不要提交 Git）：

```dotenv
STEAM_WEB_API_KEY=<server-side-key>
STEAM_SYNC_ENABLED=true
STEAM_SYNC_INTERVAL_SECONDS=600
STEAM_FULL_SYNC_SECONDS=604800
STEAM_SYNC_PAGE_SIZE=5000
STEAM_SYNC_DAILY_REQUESTS=1000
```

```sh
docker compose down
docker compose --profile catalog-sync up --build -d --wait
docker compose logs -f catalog-sync
```

独立进程入口为 `python -m playmatch.steam_worker`。`--once` 只处理一页**已到期**任务，不绕过退避、租约或每日额度；`--health` 仅检查数据库共享调度心跳，不证明上游在线，也不是单个副本独有的心跳。正常推荐 worker 与目录 worker 分开，目录网络请求不占用推荐消费者。

关闭：停止 `catalog-sync`，将配置设回 false，再重建 API 使展示与配置一致。历史发现记录不删除。只停止进程而未改开关时，页面会显示缺少心跳，不伪装成运行正常。迁移应由 API/运维单点完成，不同时启动多个迁移器。

## 同步机制

首次自动开始 full；一轮结束后默认每600秒开始 incremental，每7天再完整遍历。间隔不是 Steam 发布后到本应用可见的时效承诺。上游缓存、时钟差和延迟可能导致暂时缺漏，使用300秒重叠窗口和定期 full 缓解，不声称严格事务快照。

- 按 appid 递增分页，单页最多5,000，结果短于页长也不猜测遍历结束：没有显式结束标志时再读到空页。
- 每页身份 UPSERT、游标、累计页数/行数在同一个事务提交；成功后才推进下一页。网络在事务外执行。
- 多个进程共享数据库时钟/单行锁/随机租约令牌。领取时预记一次请求；崩溃或网络超时可能重复 GET，但过期进程不得发布页/完成状态。
- 只有确认遍历终点才推进变更 watermark 和最近成功时间。中途失败保留 cycle start/since/cursor；重启从最后提交页继续。
- 401/403退避；429遵守有界 Retry-After；错误指数退避；跨进程每日请求预算，重启不重置。此预算仅约束本同步器，不能代表同一 Key 的其他调用。
- 异常排序、重复AppID、坏类型、停滞游标、过大响应、首次空全量会报错并保留原状态，不静默丢弃条目。单个持续坏页需要运维修复，不声称自动纠正上游坏数据。
- 原记录的更新时间更晚时，不让陈旧响应回退名称/变更标记。缺失字段仍为 null。
- full 通过世代记录发现情况；最近一轮未出现的身份仍保留，不自动认定下架、删除收藏或触发重新购买。
- 每轮完成保存有界回执：模式、起止、页数、观测行数、最终游标/页摘要。保留最近200轮；摘要只约束最后一页，不冒充完整响应归档或上游签名。

## 读取和界面

GET `/api/v1/catalog/steam/status`：开关/是否配置、近期进程心跳、已发现/已有资料/待补全数量、周期游标、成功时间、失败码、退避和额度。`up_to_date` 意味近期完成接口遍历，不是秒级全库一致性保证。配置存在或有心跳都不足以显示最近同步成功。

GET `/api/v1/catalog/steam/games?q=&after_appid=0&limit=24`：按名称前缀或精确AppID搜索，数据库游标分页，最多100条；不把整个目录加载进 Python。名称 NFKC/casefold，搜索索引截取前512字符以控制数据库索引大小，原名称完整保存；公开查询最多100原始字符。不是中文语义搜索或全字任意子串搜索。读取接口没有同步写入副作用，没有公开写入/注入价格接口。数据在分页中可能变化，游标不是快照令牌。

原 `/games` 的已有资料搜索也改为 SQL 筛选+COUNT+LIMIT/OFFSET；新增派生搜索字段由当前 ORM 插入/更新维护。直接绕过 ORM 的外部 SQL 写入需自行维护字段。任意子串匹配不声称数据库全文索引性能；本轮新增发现目录按名称前缀检索，PostgreSQL `text_pattern_ops` B-tree、AppID主键支撑该路径。现有推荐 workflow 尚未迁移成大目录全文/ANN检索。

前端展示“Steam 自动同步目录”及其真实状态，待补全条目只给官方资料入口；原“目录覆盖”面板仍是推荐资料范围。不会因为开发助手改了配置就出现假的全库计数。

## 验证与遗留

- `uv run pytest services/api/tests/test_steam_sync_v07.py -q`：模拟官方HTTP合同、串行游标、崩溃窗口/迟到owner、退避预算、事务回滚、保守缺失、SQL查询与迁移。
- `uv run python scripts/benchmark_steam_directory.py --output /tmp/steam-scale.json`：100,000条明确合成名称、20页入库、重开数据库、尾部游标/名称/AppID查询；不请求 Steam，不创建推荐元数据，不宣称是真实游戏覆盖率或生产P95。
- 专项 PostgreSQL 使用独立可丢弃数据库和 `PLAYMATCH_ALLOW_DISPOSABLE_DB=yes`；不要拿生产数据库运行这些脚本。

缺少 Key 时，真实请求合同、全量条数、运行时同步新鲜度仍未验收。真实扩容需要部署后完成至少一次 full 与后续 incremental，再记录数量/失败/积压/对账。自动玩法资料补全、全库中文推荐、向量索引扩容、国区与多店价格实时覆盖尚未完成。该阶段不启动自动抓价或外部邮件。
