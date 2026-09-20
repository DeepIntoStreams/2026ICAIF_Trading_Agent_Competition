# Receiver 手动验收案例

这组案例让你逐步观察 Participant API 响应和 PostgreSQL 中的实际记录。
初始化脚本会清空测试库，因此它强制要求数据库名以 `_test` 结尾。

## 1. 启动 PostgreSQL 并创建测试库

```bash
docker compose -f deployment/docker-compose.yml up -d postgres
docker compose -f deployment/docker-compose.yml exec postgres \
  createdb -U competition competition_test

export TEST_COMPETITION_DATABASE_URL='postgresql://competition:competition@127.0.0.1:5432/competition_test'
```

如果测试库已经存在，`createdb` 报错可以忽略。

## 2. 初始化测试场景

```bash
PYTHONPATH=src /home/hanyueju/miniconda3/envs/icaif2026/bin/python \
  deployment/test_cases/seed_receiver_case.py
```

它会创建：

- `manual_team` 和固定测试 API key；
- 当前 UTC 日期及下一个显式交易日；
- 当前时间前后一小时的开放提交窗口；
- 一份含 AAPL/MSFT 的已发布 team observation。

每次重新测试前可再次运行 seed，恢复干净状态。

## 3. 启动 Receiver

另开一个终端：

```bash
export COMPETITION_DATABASE_URL="$TEST_COMPETITION_DATABASE_URL"
export COMPETITION_ADMIN_TOKEN='manual-admin-token'
PYTHONPATH=src /home/hanyueju/miniconda3/envs/icaif2026/bin/python \
  -m deployment.live_server.app
```

## 4. 调用并检查完整案例

```bash
export COMPETITION_API_KEY='receiver-manual-test-api-key'
export COMPETITION_BASE_URL='http://127.0.0.1:8080'
PYTHONPATH=src /home/hanyueju/miniconda3/envs/icaif2026/bin/python \
  deployment/test_cases/run_receiver_cases.py
```

Runner 会依次验证：

1. status 和 observation 可读取；
2. 非法 envelope 被拒绝但不占用当天名额；
3. 包含负权重和未知 ticker 的合法 envelope 原样进入 `RECEIVED`；
4. 相同 idempotency key 和相同 body 返回同一 receipt；
5. 相同 key 不同 body 被拒绝；
6. 当天第二份合法 submission 被拒绝；
7. attempts 中保留完整状态轨迹；
8. Server 没有写入 `submission_weights` 或 `executions`。

所有断言成功时最后输出 `All receiver cases passed.`。

## 5. 手动查看数据库

```sql
SELECT id, outcome, rejection_reason, received_at
FROM submission_attempts ORDER BY id;

SELECT id, status, raw_payload_json, expected_weight_count, stored_weight_count
FROM decision_submissions;

SELECT event_type, entity_type, entity_id, details_json
FROM audit_logs ORDER BY id;
```
