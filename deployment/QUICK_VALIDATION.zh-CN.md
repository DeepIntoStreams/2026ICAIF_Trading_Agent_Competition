# Live Server 快速验证与展示

这个 demo 可以直接运行一个 Python 文件，在 `deployment/demo/` 中生成一个能用 VS Code 打开的真实 SQLite 数据库，并留下一个未提交的交易日供 Starter Kit 测试。

## 1. 直接生成数据库

在仓库根目录运行，不需要设置 `PYTHONPATH`：

```bash
python deployment/demo/quick_demo.py --reset
```

如果使用项目 conda 环境：

```bash
/home/hanyueju/miniconda3/envs/icaif2026/bin/python \
  deployment/demo/quick_demo.py --reset
```

运行后生成：

```text
deployment/demo/icaif-demo.sqlite3
deployment/demo/demo-credentials.json
```

脚本只有一个团队 `team_demo`。它调用正式的 `register_team()` 随机生成 API Key；数据库只保存 Key 的 SHA-256 哈希，明文写入 `demo-credentials.json`，方便本地复制。每次 `--reset` 都会生成一个新 Key。

## 2. 在 VS Code 查看数据库

安装 VS Code 扩展 `SQLite Viewer` 或 `SQLite`，然后在 Explorer 中打开：

```text
deployment/demo/icaif-demo.sqlite3
```

数据库有四张表：

| 表 | 本 demo 中的内容 |
| --- | --- |
| `teams` | 1 个团队和 API Key 哈希 |
| `sessions` | 3 个 mock 交易日 |
| `submissions` | 5 条实际写入的提交记录 |
| `states` | 现金、持仓、排队权重、NAV 和费用 |

推荐依次执行：

```sql
SELECT * FROM teams;
SELECT session_date, deadline_utc, settled FROM sessions;
SELECT source_id, session_date, accepted, reason, sanitized_json, violations_json
FROM submissions ORDER BY received_at;
SELECT * FROM states;
```

## 3. 哪些是 mock，哪些是真逻辑

Mock 的只有输入：

- 股票为 `AAPL`、`MSFT`；
- 两只股票开盘价都是 100，收盘价分别为 110 和 102；
- 动量、波动率和利润率是手写示例；
- 请求的服务端接收时间由脚本指定。

以下全部调用正式代码计算，不是脚本手写返回值：

- API Key 生成和哈希：`LiveStore.register_team()`；
- 提交接受或拒绝：`LiveStore.submit()`；
- 权重裁剪：`sanitize_target_weights()`；
- 次日开盘成交、手续费、现金、持仓和 NAV：`LiveStore.settle()`；
- 排行榜 M1-M9：`LiveStore.leaderboard()` 调用 `compute_metrics()`。

脚本只准备 mock observation、价格和提交，再让 Live Server 的实现产生 receipt、账户状态和排行榜。

## 4. 提交 case 的实际顺序

第一天先发送不会占用有效名额的失败请求，再发送成功请求：

| 顺序 | Case | 预期结果 |
| --- | --- | --- |
| 1 | 接收时间晚于 deadline | `late_submission` |
| 2 | body 中 `team_id` 不匹配 | `team_mismatch` |
| 3 | `AAPL=0.05` | `accepted=true` |
| 4 | 重放相同 idempotency ID | `idempotent=true` |
| 5 | 使用新 ID 再提交 | `decision_already_accepted` |

第二天提交：

```json
{"AAPL": 0.25, "MSFT": -0.10, "UNKNOWN": 0.50}
```

正式约束逻辑会得到：

```json
{
  "sanitized_weights": {"AAPL": 0.10, "MSFT": 0.0},
  "violations": ["asset_cap", "short_position", "unknown_asset"]
}
```

第三天只发布 observation，不预先提交，可以继续用真实 HTTP 客户端展示。

## 5. 排行榜从哪里来

第一天的 `AAPL=5%` 先进入 queued target，第二天开盘执行：

```text
买入金额 = 1,000,000 × 5% = 50,000
买入股数 = 50,000 / 100 = 500
手续费 = 50,000 × 0.1% = 50
收盘股票价值 = 500 × 110 = 55,000
第二天 NAV = 949,950 + 55,000 = 1,004,950
```

`leaderboard()` 从 `states.state_json` 读取 NAV、累计交易额、费用和违规日数，再调用项目的 `compute_metrics()` 计算 M1-M9。这里只模拟两个已结算日，因此 Sharpe 等指标没有统计意义，并会显示 `low_sample_warning=true`；这套数据用于验证链路，不代表策略表现。

## 6. 为什么有时需要 PYTHONPATH

项目使用 `src/` 目录布局，`portfolio_agent` 位于 `src/portfolio_agent`。如果项目还没有安装到当前 Python 环境，Python 默认找不到它，因此原来的命令需要 `PYTHONPATH=src`。

`quick_demo.py` 已经自行加入正确目录，所以它可以直接运行。Live Server 可以继续临时指定：

```bash
COMPETITION_ADMIN_TOKEN='local-admin-secret' PYTHONPATH=src \
python -m deployment.live_server.app \
  --db deployment/demo/icaif-demo.sqlite3 \
  --host 127.0.0.1 --port 8080
```

或者在当前虚拟环境一次性安装项目：

```bash
python -m pip install -e '.[server]'
```

安装后不再需要 `PYTHONPATH`：

```bash
COMPETITION_ADMIN_TOKEN='local-admin-secret' \
python -m deployment.live_server.app \
  --db deployment/demo/icaif-demo.sqlite3
```

Swagger 展示地址为 `http://127.0.0.1:8080/docs`。

## 7. 用 Starter Kit 提交第三天

打开 `deployment/demo/demo-credentials.json`，复制随机生成的 `api_key`：

```bash
COMPETITION_BASE_URL=http://127.0.0.1:8080 \
COMPETITION_API_KEY='<复制生成的 api_key>' \
python -m deployment.starter_kit.client
```

第一次应返回 `action=submitted`；第二次应返回 `decision_already_accepted`。

状态和排行榜：

```bash
curl -s http://127.0.0.1:8080/api/v1/me/status \
  -H 'Authorization: Bearer <复制生成的 api_key>'

curl -s http://127.0.0.1:8080/api/v1/leaderboard
```

## 8. 自动测试与 demo 的区别

- `deployment/tests/test_live_store.py` 使用 `unittest` 和临时数据库，逐项断言 deadline、幂等、身份、次日执行和省略资产清仓；结束后临时库自动删除。
- `deployment/tests/test_api.py` 使用 FastAPI `TestClient` 模拟 HTTP 请求，断言 401/422、observation、提交、状态和 Starter Kit 流程。
- `deployment/demo/quick_demo.py` 主要用于展示：它保留一份可打开的数据库，并打印所有中间结果。

底层测试命令：

```bash
PYTHONPATH=src python -m unittest deployment.tests.test_live_store -v
```

重新生成 demo 会替换 SQLite 和凭据文件。不要把生产数据库路径传给 `--db`，也不要使用 demo Key 处理真实数据。
