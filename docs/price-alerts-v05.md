# v0.5 — 主动查价触发的站内提醒

本轮不是持续降价监控或第三方邮件提醒。只交付明确可验证的闭环：用户保存 USD
目标价 → 勾选范围授权 → 开启一次性规则 → 主动点击“查价并检查提醒” → 服务端
从 CheapShark 取观测报价 → 原子写入通知事件 → worker 投递站内信 → 用户读取/删除。
收藏、普通 GET 报价、页面刷新都不会自动开启规则或生成通知。不会分享邮箱给报价源。

## 使用

在“我的收藏”中设置美国地区 USD 目标价，阅读并勾选同意说明后开启。目标价可为零，
但缺失报价永远不视为免费。点击“查价并检查提醒”最多每分钟五次（API 单进程限流）。
本轮采用全部符合输入校验的 US/USD **观测**报价，不保证版本等价、地区激活或实际
结算可购买；通知不是当前价格保证。所有价格比较使用整数最小货币单位。

每次开启最多生成一条站内通知。价格仍高于目标或来源不可用时保持 armed。
命中后的 pending 只意味着事件已持久保存；notified 才意味着站内数据库已提交通知。
修改目标价、地区或币种会关闭旧规则并抑制尚未投递事件，需再次明确授权。
相同收藏内容重存不关闭规则。删除收藏也阻止未投递事件，但不会删除已经投递的历史。
删除历史通知不会自动再次通知；重新开启会创建新的规则版本，仍需要新一次主动查询。

`monitoring_status=not_enabled` 继续描述“定时价格监控”未启用；新 `alert` 字段独立描述
查价规则，不能把二者混为一谈。

## API 与隐私

- `PUT /api/v1/watchlist/{game_id}/alert`：登录、同源校验；提交 enabled、expected_version，
  开启时必须包含 `consent_version=observed-usd-inapp-v1`。CAS 版本冲突返回409。
  同一旧版本命令的精确重放返回当前状态，不会重新开启已结束规则。
- `POST /api/v1/watchlist/{game_id}/check`：仅已开启规则可以查价。价格由服务端适配器获取，
  不接收客户端提供的价格；网络请求前释放数据库事务。返回报价及 `alert_evaluation`。
  请求中注销、关闭、重新开启或修改目标时，不使用旧请求创建新规则通知。
- `GET /api/v1/notifications?limit=20&offset=0`：只返回当前账户的分页历史和未读数。
- `POST /api/v1/notifications/{id}/read`：幂等已读；他人/不存在的ID返回404。
- `DELETE /api/v1/notifications/{id}`：只删除自己历史，不影响去重回执。

上述私人响应为 no-store。前端数据按账户分区，切换/退出账户清理缓存和中止读取。
任何陌生检索方式或提醒结果不会被 UI 当作成功。所有推荐、收藏及价格原有权限保持。
邮件地址不会出现在事件/通知载荷；实际用户数据库与事件不提交到 Git。

## 数据与恢复语义

迁移0005增加规则版本/授权状态，通知事件、站内收件箱与共享写锁。所有修改规则、
创建事件、领取/投递都先取得同一短事务写锁；外部价格HTTP发生在锁外。
`watch_id + alert_version` 保证每次开启最多一个事件，`event_id` 保证最多一个站内通知。

规则状态：off → armed → pending → notified；投递超过时效或重试次数则 failed。
事件状态：pending → sending → delivered；可被抑制 suppressed，或失败 failed。
每次投递有随机 owner token、30秒租约、最多5次尝试，失败使用2^attempt秒退避（上限60秒）。
时钟来自数据库。消费者崩溃后可以重新领取过期租约，旧 token 不能写入。
通知插入、事件 delivered、规则 notified 在同一事务提交；提交失败全部回滚。
这是**站内SQL效果去重**，没有承诺外部邮件 exactly-once，也没有外部邮件投递实现。

查询从开始起最多120秒可以生成事件，事件从查询开始最多600秒可以投递。
这些时限限制本系统观测和队列年龄，不意味着供应商上游商店价格实时更新。
不同机器时间差超过允许范围会保守拒绝报价而非猜测，建议正确同步系统时间。
快照损坏、非法链接、币种/地区不匹配、负数/非整数价格不通知。

embedded 开发模式有独立站内投递线程。Compose external 模式复用现有 worker，
每轮领取推荐之前处理一个通知事件，不新增后台报价调用。单 worker 正在执行长推荐
时，站内投递可能等待当前任务结束；这是当前延迟边界，不声称即时送达或高吞吐。

## 验证与测试数据

`uv run pytest services/api/tests/test_notifications_v05.py -q` 使用合成报价和实际SQL事务，
覆盖阈值、过期、并发、租约、取消、回滚、会话失效、账户隔离与真实独立worker进程。
`ALERT_TEST_DATABASE_URL` 可指定独立 PostgreSQL 测试库，库名需以 `_test` 结尾，并设置
`PLAYMATCH_ALLOW_DISPOSABLE_DB=yes`；不得使用正式数据。测试清理自己的UUID账户记录。

浏览器测试通过 `scripts/e2e_app.py` 注入明确标注的合成报价；这个启动器要求
`PLAYMATCH_E2E_FIXTURES=1` 和专用 `.state/e2e.db`，生产镜像仍启动 `playmatch.main:app`。
浏览器中的新提醒流程是真实认证/API/数据库/消费者，价格为合成fixture，不声称实时优惠。
容器专项 `scripts/smoke_notifications.py` 需要显式测试标志，直接注入一个标注的合成
观测，再通过真实独立worker和已认证HTTP读回；没有对外邮件或价格请求。

## 数据源与未完成边界

核查日期：2026-10-21。官方资料：
- https://apidocs.cheapshark.com/
- https://www.postman.com/cheapshark/cheapshark-s-public-workspace/collection/7h22uhl/cheapshark-api

CheapShark 官方说明定价为USD，接口主要面向用户触发的查询，过量自动采集可能被限流；
购买入口保留 CheapShark redirect，程序不会自动打开购买跳转链接。本次没有调用
第三方 Alerts 写接口，也没有发送验证或营销邮件。

后续持续监控需要允许此用法的报价来源、时效/轮询预算；外部邮件还需要邮箱所有权验证、
明确授权、供应商状态对账和投递可观测。本轮没有这些能力，也没有扩大真实目录、
提高独立推荐质量、提供CN报价或部署公开生产服务。共享事务锁与JSON收件箱未做跨主机
性能验收。通知可手动删除；审计事件保留去重载荷，尚无自动数据保留期/账号删除工作流。
