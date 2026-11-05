# 技术设计与运行边界

## 服务组成

Python标准库HTTP服务承载静态页面及JSON API，SQLite WAL保存状态。两个后台作业线程分别执行采集/历史补采和导入/研究/备份；一个调度线程定时检查采集、日级历史复核和模拟结算。SQLite事务与订单唯一键保证幂等；较长研究不会锁住页面读取。模拟账户修改受线程锁和BEGIN IMMEDIATE双重保护。网络请求有超时，每30秒触发官方优先采集，失败在该轮切换pc28.world；下一轮重试官方。队列上限10，防止重复点击制造无限任务。

## 数据与审计

- draws：原始号码、换算列、首见时间、源时间、UTC时间、哈希、版本。
- jobs：导入、采集和研究日志；页面任务队列是进程内状态，服务重启后不恢复未执行研究任务。
- conflicts / revisions：同一期差异、核验处理与变更前后快照。替换不会改写已经结算的订单，而会暂停账户、作废旧候选，要求人工核对影响。
- evaluations / selections：完整研究结果与按月冻结的候选。
- accounts / orders / ledger：可用余额、待结算订单、下注扣款、结算返还。实际净资产=可用余额+待结算已扣本金；默认页面同时显示两者。
- decisions / switches：每期决策、数据截止期号、跳过原因、候选切换原因。
- settings_audit：配置快照，代理凭据脱敏。

原始文件不可变，以SHA256命名；不解压到任意文件路径，ZIP仅内存读取CSV并限制解压总大小。批次异常整批回滚。

## 决策时序

收到N期开奖→校验入库→结算目标N的已有订单→检查当前已知最新期N→读取截至N的窗口→选择策略→检查账户与数据时效→生成目标N+1的pending订单→原子扣款。

补采包含多期时，只能在最新一期上产生下一期决策，不会重放遗漏期来制造收益。目标期缺失时保留pending，阻止追加订单；补齐后恰好一次结算。请求失败、进程重启、重复轮询不会重复扣款。

目前目标期=N+1，使用源时间与首见时间做保守决策窗口，没有接入交易平台截止时刻；这是本地模拟假设，不是可用于真实交易的时效保证。

## API

GET /api/session 获取页面令牌
GET /api/status 统计、账户、订单、候选、任务与日志
GET /api/config 当前本机配置
GET /api/draws?limit=100&before=期号 分页开奖
GET /api/conflicts 未解决冲突
GET /api/equity 最近500笔结算曲线
POST /api/config 保存配置
POST /api/pause {paused: true/false}
POST /api/resolve-conflict {id, choice: keep/replace, reason}
POST /api/task {kind, args} 加入后台队列
GET /exports/文件名 结果下载

POST必须Content-Type application/json及X-PC28-Token。Host只能是127.0.0.1/localhost和服务端口；Origin必须本机同源。所有静态输出设置禁止嵌套、脚本同源和MIME嗅探限制。此防护不替代多用户身份认证。

## 上线前尚需现场确认

1. 网络能否访问PlayNow官方端点；官方历史ZIP已经成功下载；最新JSON服务返回HTTP403。
2. 最新接口实际响应字段、时间格式、号码顺序、可抓取数量、更新延迟及服务允许的频率；适配器是可配置的，当前没有成功取得逐期实时JSON样本。
3. 源CSV与实时接口时区含义，夏令时折叠小时处理。
4. 模拟赔率、费用、封盘时刻。当前赔率是历史研究假设。
5. 断网补采窗口是否覆盖最长预期离线时长，跨年文件需单独导入。

已完成应用代码和本地历史数据端到端验证，不应把“采集程序已写好”描述为“官方实时接入已验证成功”。

备用源适配器位于ingest.world_records；源切换日志位于latest_official/latest_fallback/latest，source_observations记录跨源时间和哈希。备用实时JSON已成功联调；官方最新端点仍403。
