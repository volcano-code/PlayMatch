# v0.6：来源目录扩充、别名检索与可复核评测

## 交付范围

默认目录从24款扩充至48款，新增24款不同Steam AppID的真实作品。此轮由开发助手逐一阅读官方商店页面，不是使用IGDB凭据批量采集，也不是独立人工玩家评测。中文概述、主题、类型与检索别名是编辑整理，不复制整段商店文案。名称/简介入口、语言表和合作功能分别保留来源与核对时间。

- `data/catalog.json`：保留原24款字节不变，供历史开发评测复现。
- `data/catalog-v06.json`：新增24款，包含编辑别名与字段证据。
- `data/catalog-v06-provenance.json`：逐款来源、日期、审阅方式、事实状态；明确 `independent_human_review=false`。
- `data/catalog-release.json`：两个分片的SHA-256、条数与发行标识；启动时核对摘要、重复身份和字段证据。

首次启动新版本会把新增记录加入数据库。现有收藏引用的老游戏ID不变；不删除操作员导入的其他游戏。显式 `PLAYMATCH_CATALOG_PATH` 是替换整个默认目录，不会暗中拼接发行目录。自定义文件仍须符合来源目录约束。

没有加入实时价格、评分/评价数量、硬件性能、游戏时长、合作人数或“完全不恐怖”等未经验证字段。需要这些硬条件时继续严格返回未知/无结果。

## 新作品来源

以下均是本轮实际读取的官方Steam商店页面；核对时间在JSON中。这里只提供资料入口，不代表当前可售或最低价。

| AppID | 作品 | 官方资料 |
| --- | --- | --- |
|1086940|Baldur's Gate 3|https://store.steampowered.com/app/1086940/|
|1245620|ELDEN RING|https://store.steampowered.com/app/1245620/|
|1446780|MONSTER HUNTER RISE|https://store.steampowered.com/app/1446780/|
|1142710|Total War: WARHAMMER III|https://store.steampowered.com/app/1142710/|
|281990|Stellaris|https://store.steampowered.com/app/281990/|
|289070|Civilization VI|https://store.steampowered.com/app/289070/|
|813780|Age of Empires II: Definitive Edition|https://store.steampowered.com/app/813780/|
|1158310|Crusader Kings III|https://store.steampowered.com/app/1158310/|
|255710|Cities: Skylines|https://store.steampowered.com/app/255710/|
|457140|Oxygen Not Included|https://store.steampowered.com/app/457140/|
|1366540|Dyson Sphere Program|https://store.steampowered.com/app/1366540/|
|1336490|Against the Storm|https://store.steampowered.com/app/1336490/|
|653530|Return of the Obra Dinn|https://store.steampowered.com/app/653530/|
|1677770|The Case of the Golden Idol|https://store.steampowered.com/app/1677770/|
|257510|The Talos Principle|https://store.steampowered.com/app/257510/|
|736260|Baba Is You|https://store.steampowered.com/app/736260/|
|1135690|Unpacking|https://store.steampowered.com/app/1135690/|
|1455840|Dorfromantik|https://store.steampowered.com/app/1455840/|
|1055540|A Short Hike|https://store.steampowered.com/app/1055540/|
|972660|Spiritfarer|https://store.steampowered.com/app/972660/|
|1562430|DREDGE|https://store.steampowered.com/app/1562430/|
|1092790|Inscryption|https://store.steampowered.com/app/1092790/|
|632360|Risk of Rain 2|https://store.steampowered.com/app/632360/|
|322330|Don't Starve Together|https://store.steampowered.com/app/322330/|

特殊判断：Civilization VI的社区合作标签不是官方功能表中的合作证据，故保持unknown；Spiritfarer的同屏合作不是原生在线合作；A Short Hike未核验简体界面；The Case of the Golden Idol此轮页面列出繁体而非简体，因此本应用保守保持unknown；Inscryption开发者简介明确包含心理恐怖，标记yes，其他新增记录不推断“无恐怖”。

`chinese_support=verified`在新目录中特指简体界面，不代表配音、全部DLC语言或特定机器体验。旧记录的核对时间不随本轮升级伪装成重新检查。

## 页面及接口

Game增加可选兼容字段 `aliases:string[]`（默认空，最多20条，每条1..80个可打印字符）。默认BM25文档和目录q查询包含别名。旧关键词ranker保持原实现用于对照；没有借机更换原算法标签。

GET `/api/v1/catalog/coverage` 统计当前数据库实际内容，返回总数、来源链接、带日期的简介、已核验简体界面/合作、恐怖未知、别名覆盖与范围提示。不是写死默认48，也不是模型/价格健康探针。新前端面板显示范围和失败态；未将curated标签宣传为独立人工审阅。

目录扩充或别名变化会改变检索指纹，旧活跃向量索引可能被判过期。显式重建后再启用indexed_hybrid；默认BM25不需要向量服务。此轮没有执行新的真实向量模型评测，v0.3结果不能迁移当作48款的效果证据。

## 固定快照开发挑战

`evals/catalog-challenge-v06.json` 有40条案例：24条作品描述、8条别名、8条硬条件。32个主题家族；别名与描述属于相同家族，不当成独立统计样本。查询、暂定正例和理由由同一开发助手整理，与官方描述存在接近表达，因此是**开发挑战/功能回归，不是盲测或推荐满意度**。

快照摘要覆盖完整游戏记录，包括语言等硬条件；不仅是向量检索文本。报告记录数据集摘要、完整目录摘要及检索文本指纹。数据与快照不匹配就拒绝执行，不悄悄重映射标签。

在相同48款目录、相同规则意图解析器、相同40条请求上对照：
1. 原关键词基线；
2. BM25去除别名字段；
3. BM25包含别名。

只检查已知正例Hit@1/5和MRR@5、显式硬约束违规数；正例不穷举，因此不把未标注游戏当负例，也不声称precision、全库recall或nDCG。CI对硬条件零违规和可复现报告做门禁，不追逐一个修改标签后的“高准确率”。别名8/8是刻意设计的功能测试，不是模型泛化提升。

```sh
uv run python scripts/benchmark_catalog.py evaluate --check --output /tmp/catalog-v06-results.json
# 原历史23案例固定在原24款目录，原JSON标签和目录字节未改。
uv run python scripts/evaluate_recommendations.py --check --output /tmp/legacy.json
```

正式接口和评测均不配置付费模型；挑战评测封禁socket连接。三组都是词项算法；不称为向量/LLM对比。

## 给独立评审者的交接

已经生成 `evals/review-v06/review-template.json`（全是空grade）和 `scoring-key.json`。交给两位不同评审者时**只发模板，不发评分key或开发结果**；不要让两人先看彼此的判断。候选集合是三组top5加开发正例的并集，顺序打乱，不显示ranker、排名、分数或正例标记。此候选池仍有选择偏差，不是全库人工标注。

评分指南：0=不相关，1=部分匹配且取舍大，2=较符合但有明显取舍，3=强匹配请求。阅读证据并考虑否定要求；不要只看名称。为每个候选填整数0..3，填写不同的reviewer_id，provenance改为`self_attested_human`；保留问题、说明、顺序、来源和候选ID不变。

```sh
# 生成新副本；已有目录会拒绝覆盖，防止丢失未完成标注。
uv run python scripts/benchmark_catalog.py prepare --report evals/catalog-v06-results.json --directory /tmp/review-round-1
# 获得两份真正完成的独立评审文件后才能运行；模板本身会被拒绝。
uv run python scripts/benchmark_catalog.py score --key evals/review-v06/scoring-key.json --reviews /path/reviewer-a.json /path/reviewer-b.json --output /tmp/review-report.json
```

工具拒绝缺项、重复ID、相同评审者、错误快照、改动问题/证据、非法分数及自动模型来源。只有两份完整自我声明人工表才计算均分后的**候选池内nDCG@5**、一致率和分歧数量；全零相关性的池不计为满分。CLI无法认证人的身份或真正独立性，输出明确为self-attested，不升级为外部审计结论。

**本轮没有任何完成的独立人工评审，nDCG仍未发布。** 测试里的合成打分只证明评分代码，不写入正式标注数据。

## 未完成

48款不是1000款或全量目录；IGDB授权批量同步、持续更新、独立人工评测、国区报价、自动盯价、外部邮件、公开部署和生产规模性能仍待实施。代码单元/浏览器通过不等于推荐满意度。后续应先收集新用户自然查询和评审，不根据这40条挑战持续调参冒充新结论。
