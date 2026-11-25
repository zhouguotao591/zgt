# 平台净额发送

同一会员绑定、同一期的原始推送快照保持不变。冻结合并批次时按整数分抵消大/小和单/双；组合、a/b/c方向不抵消。净额计划保存在 robot_batches.request.netting，重试读取原计划。

发生对冲的批次走 payload.text 普通消息转发，不附带 orders 或 execution_callback。HTTP 接收成功只代表接口接收，不代表外部平台成交。未发生对冲及历史批次沿用原协议。

member_orders.stake 为分配到该原始订单的实际执行金额，original_stake/original_message 保留原单。完全抵消的订单标记 void/offset，不产生现金流水。原策略模拟订单不修改。平台资金和机器人风险占用使用实际执行金额；同方向同赔率的批次结算按合并金额计算后分配，避免逐笔四舍五入差额。

0/27命中只退本金、未命中亏本金，按冻结赔率结算。接口明确拒绝不入账；重试沿用批次 event_id，接收工具需按 event_id 去重。

盈利金额暂停使用已结算净收益，每次触发记录累计收益基准；暂停期满不重复触发同一笔利润。金额或期数为0关闭。

验证：test_order_netting.py、test_netting_delivery.py、test_merged_robot_risk.py。测试使用隔离数据库和模拟HTTP接收，不向真实机器人下注。
