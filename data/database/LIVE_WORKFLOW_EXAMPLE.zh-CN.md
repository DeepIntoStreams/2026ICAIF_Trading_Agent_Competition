# Live Competition 完整实现案例（中文）

本文用一个具体案例说明未来代码应该如何组织、如何读写数据库，以及一份权重如何最终变成交易、现金、持仓和收益记录。本文是实现合同，不代表相关业务代码已经完成。

实现职责边界：Deployment server 只接收并原样保存 participant submission，
以 `RECEIVED` 状态交给 Competition；权重清洗、fallback、execution、估值和
评分均属于 Competition。本案例后续章节描述的是 Competition 如何消费该记录，
不是 server 接收请求时执行的工作。

配套文件：

- `schema.sql`：数据库字段和约束的唯一可执行定义；
- `README.md`：总体设计原则；
- 本文：端到端实现案例和代码接口建议。

## 1. 案例设定

团队 `team_alpha` 在 2026-09-01 收盘后读取 observation，并且只能提交一次目标权重。该权重在 2026-09-02 开盘价上模拟成交。服务器等 2026-09-02 收盘后取得完整日线，再一次性完成开盘成交回放、收盘估值、收益计算和下一份 observation 发布。

时间定义：

```text
Signal day T                 2026-09-01
T market close              2026-09-01T20:00:00Z
Submission window           T close 后至 T+1 open 前
Submission deadline         2026-09-02T13:29:59Z
Execution day T+1           2026-09-02
Economic execution time     2026-09-02T13:30:00Z
Server batch processing     2026-09-02 收盘数据完整到达后
```

`effective_at` 表示模拟交易在经济上发生的时间，`processed_at` 表示服务器实际写入计算结果的时间。

## 2. 建议的代码结构

未来代码可按以下职责拆分，名称可以调整，但边界应保持：

```text
deployment/live_server/
  app.py                    HTTP 路由、认证、请求大小和格式检查
  repositories.py           只负责 SQL 和数据库对象映射
  submission_service.py     一次提交、权重归一化、校验、排队
  daily_service.py          每日盘后总流程和幂等恢复
  observation_service.py    构建并保存每队 observation
  execution_service.py      读取 target、按 open 模拟成交
  valuation_service.py      按 close 生成组合快照和收益
  models.py                 API 请求/响应模型

src/competition_core/
  weight_validator.py       纯函数：raw weights -> sanitized weights + codes
  execution_engine.py       纯函数：portfolio + targets + open -> trades + new portfolio
  valuation_engine.py       纯函数：portfolio + close -> NAV/weights
  metrics_engine.py         纯函数：历史 daily performance -> competition metrics
  observation_builder.py    纯函数：完整 T 数据 + T close portfolio -> payload
```

`competition_core` 不连接数据库、不启动 HTTP，也不保存全局状态。它只进行确定性计算。`deployment` 负责事务、存储、认证和调度。当前 `src/portfolio_agent` 中有可复用逻辑，后续可迁移或改名；它不是额外的业务 Agent。

建议的核心数据对象：

```python
@dataclass(frozen=True)
class Portfolio:
    cash: Decimal
    quantities: dict[int, Decimal]       # instrument_id -> shares

@dataclass(frozen=True)
class TargetWeight:
    instrument_id: int
    sanitized_weight: Decimal

@dataclass(frozen=True)
class SimulatedTrade:
    instrument_id: int
    side: str
    shares_before: Decimal
    target_shares: Decimal
    quantity: Decimal
    price: Decimal
    gross_amount: Decimal
    fee: Decimal
    cash_change: Decimal
```

业务代码应使用 `Decimal`，数据库使用 PostgreSQL `NUMERIC(28, 12)`，避免金额、数量和权重依赖二进制浮点数。

## 3. 初始化比赛静态数据

### 3.1 团队

插入 `teams`：

```text
id              数据库生成主键
team_code       team_alpha，外部稳定标识，唯一
display_name    Team Alpha
api_key_hash    API key 的安全 hash，唯一，不保存明文
status          ACTIVE
created_at      UTC 写入时间
updated_at      UTC 写入时间
```

代码操作：

```python
team_id = team_repository.create_team(
    team_code="team_alpha",
    display_name="Team Alpha",
    api_key_hash=hash_api_key(plain_key),
)
```

### 3.2 股票清单

每只允许交易的股票插入 `instruments`：

```text
id              数据库主键，其他业务表使用它做外键
ticker          AAPL，唯一
company_name    Apple Inc.
sector          Technology
exchange        NASDAQ
currency        USD
is_active       1
created_at      UTC 写入时间
```

业务代码不能仅凭客户端传来的 ticker 判断股票是否合法。应从 active instruments 构建映射：

```python
instruments = instrument_repository.list_active()
instrument_by_ticker = {row.ticker: row for row in instruments}
```

## 4. 建立交易日

日历任务提前插入 2026-09-01 和下一个有效交易日 2026-09-02。每个交易日只插入一行，状态随后更新，不为每次状态变化重复插入行。

`trading_days` 字段来源：

```text
id                         数据库主键
trading_date               交易所本地日期，唯一
market_open_at             NYSE 日历转换后的 UTC 开盘时间
market_close_at            NYSE 日历转换后的 UTC 收盘时间
submission_open_at         observation 发布时设置
submission_deadline_at     下一交易日开盘前的固定截止时间
market_status              PENDING -> DATA_IMPORTED/FAILED
execution_status           PENDING -> PROCESSING -> COMPLETED/FAILED
valuation_status           PENDING -> PROCESSING -> COMPLETED/FAILED
observation_status         PENDING -> GENERATING -> PUBLISHED/FAILED
created_at/updated_at      UTC 时间
```

虽然 `observations` 没有模糊的 status，`trading_days.observation_status` 仍用于调度器判断当天整体步骤是否完成。每次状态更新同时追加一条 `audit_logs`。

## 5. Day T 盘后：导入完整市场数据

数据收集器获取 Day T 的完整 OHLCV。每只 active instrument 在 `market_bars` 中一行：

```text
id                  数据库主键
trading_day_id      2026-09-01 对应的 trading_days.id
instrument_id       instruments.id
open/high/low/close 原始日线
adjusted_open       执行使用的复权开盘价
adjusted_close      收盘估值使用的复权收盘价
volume              当日成交量
source              数据提供方名称
received_at         提供方数据到达时间
created_at          数据库写入时间
```

导入必须是一个事务。30 只 active instruments 全部满足质量规则后，才更新：

```text
trading_days.market_status = DATA_IMPORTED
```

失败则整体回滚并记 `market_status = FAILED` 和 `audit_logs`，不能让部分行情进入执行。

基本面原始记录进入 `fundamental_records`：`period_end` 是报告期，`available_at` 是该信息首次允许进入 observation 的时间，`payload_json` 保留完整提供方文档。

## 6. Day T 收盘估值和 Observation

在本案例中，2026-09-01 收盘组合为：

```text
cash                 400,000
AAPL                  2,000 shares × 180 = 360,000
MSFT                    800 shares × 300 = 240,000
positions_value                            600,000
NAV                                      1,000,000
```

写入一个 `portfolio_snapshots` header：

```text
team_id                         team_alpha
trading_day_id                  2026-09-01
execution_id                    当天开盘执行 ID；无则 NULL
snapshot_type                   CLOSE
cash                            400000
positions_value                 600000
nav                             1000000
gross_exposure                  0.60
drawdown                        根据历史高水位计算
effective_at                    2026-09-01 收盘时间
created_at                      实际写入时间
```

对应的 `position_snapshots`：

```text
portfolio_snapshot_id           上述 CLOSE snapshot.id
instrument_id                   AAPL/MSFT ID
quantity                        2000 / 800
reference_price                 180 / 300
market_value                    360000 / 240000
weight                          0.36 / 0.24
created_at                      实际写入时间
```

`portfolio_snapshots + position_snapshots` 共同组成一份 portfolio。拆表只是因为一个 portfolio 有多只 position。

随后 `observation_builder` 接收：

```python
payload = observation_builder.build(
    signal_date="2026-09-01",
    market_history=market_repository.history_through(day_id),
    fundamentals=fundamental_repository.available_at(market_close_at),
    portfolio=portfolio_repository.load_snapshot(close_snapshot_id),
    constraints=current_competition_constraints,
)
```

payload 不包含新闻，示意：

```json
{
  "type": "decision_request",
  "protocol_version": "1.0",
  "session_date": "2026-09-01",
  "submission_deadline_at": "2026-09-02T13:29:59Z",
  "assets": [{"ticker": "AAPL"}, {"ticker": "MSFT"}, {"ticker": "NVDA"}],
  "market_features": {},
  "fundamental_features": {},
  "portfolio": {
    "cash": 400000,
    "nav": 1000000,
    "weights": {"AAPL": 0.36, "MSFT": 0.24}
  },
  "constraints": {}
}
```

将发送给团队的完整 JSON 原样写入 `observations`：

```text
id                          数据库主键
team_id                     team_alpha
trading_day_id              2026-09-01
close_portfolio_snapshot_id 上述 CLOSE snapshot
payload_json                完整 JSON
payload_hash                canonical JSON 的 SHA-256
generated_at                生成完成时间
published_at                开放读取时间；未发布时 NULL
first_served_at             第一次成功 GET 时设置一次
created_at                  数据库写入时间
```

## 7. 团队读取并提交一次权重

HTTP 层通过 API key 得到权威 `team_id`，读取：

```sql
SELECT * FROM observations
WHERE team_id = ? AND trading_day_id = ? AND published_at IS NOT NULL;
```

客户端提交：

```json
{
  "type": "decision_response",
  "protocol_version": "1.0",
  "session_date": "2026-09-01",
  "target_weights": {
    "AAPL": 0.10,
    "MSFT": 0.08,
    "NVDA": 0.07
  },
  "metadata": {"agent_version": "alpha-3.1"}
}
```

Submission service 使用两个明确事务完成接收与处理：

```python
def receive_decision(authenticated_team_id, body, idempotency_key, server_now):
    # 1. 查 observation 和 signal day；串行化该 team+day。
    # 2. 如果 idempotency_key 已存在，返回已保存的 receipt。
    # 3. 如果该 team+signal_day 已有其他 submission，拒绝并写 audit。
    # 4. INSERT 完整 raw submission，status=RECEIVED，并 RETURNING id。
    # 5. commit receipt；这个数据库生成的 id 就是 submission_id。
    # 6. commit 后返回 RECEIVED receipt。
    # 7. Competition 后续按 submission_id 消费。
```

`decision_submissions` 每个字段：

```text
id                       数据库主键
team_id                  认证得到的 team，不信任客户端 team 字段
observation_id           本次决策使用的确切 observation
signal_day_id            2026-09-01
execution_day_id         从交易日历查到的 2026-09-02
idempotency_key          客户端请求唯一键
received_at              服务端收到时间，决定是否迟到
raw_payload_json         请求原文
payload_hash             canonical JSON SHA-256
source                   PARTICIPANT；缺失提交生成的记录为 FALLBACK
status                   RECEIVED/VALIDATED/REJECTED/QUEUED/EXECUTED
rejection_reason         仅 REJECTED 必填
validator_version        使用的校验器代码版本
validation_policy_json   当时实际使用的完整校验参数
validation_summary_json  组合级校验结果汇总
expected_weight_count    接收时冻结的 active instrument 数量
stored_weight_count      已成功落库的归一化权重行数
sanitized_gross_weight   最终权重之和
weights_processed_at     权重处理完成时间
agent_version            metadata 中的参赛者版本
created_at/updated_at    UTC 时间
```

数据库唯一约束 `(team_id, signal_day_id)` 保证绝不出现第二份 submission。重复请求只可通过相同 idempotency key 取回第一次结果。

### 7.1 `submission_id` 到底从哪里来

它由数据库生成，不由客户端提供，也不由代码猜测：

```sql
INSERT INTO decision_submissions (
    team_id, observation_id, signal_day_id, execution_day_id,
    idempotency_key, received_at, raw_payload_json, payload_hash,
    source, status, validator_version, validation_policy_json,
    expected_weight_count, stored_weight_count, created_at, updated_at
)
VALUES (
    :team_id, :observation_id, :signal_day_id, :execution_day_id,
    :idempotency_key, :received_at, :raw_json, :payload_hash,
    'PARTICIPANT', 'RECEIVED', :validator_version, :policy_json,
    :active_count, 0, :now, :now
)
RETURNING id;
```

例如数据库返回 `id=200`，后面所有权重行都写 `submission_id=200`。Receipt 先提交事务，因此即使校验器异常退出，原始 submission 仍然存在，并保持 `RECEIVED` 等待恢复。

## 8. 权重归一化和存储

权重在原始 submission 成功入库之后、创建 execution 之前保存。它由
Competition 的 submission processor 处理，不由 Deployment receiver 或
execution engine 处理：

```python
def process_submission_weights(submission_id: int):
    with database.transaction():
        submission = submissions.lock_received(submission_id)
        active = instruments.list_active()
        raw = parse_target_weights(submission.raw_payload_json)
        result = weight_validator.validate(
            raw_weights=raw,
            instruments=active,
            policy=submission.validation_policy_json,
        )
        submission_weights.bulk_insert(
            submission_id=submission.id,
            rows=result.complete_instrument_vector,
        )
        assert submission_weights.count(submission.id) == submission.expected_weight_count
        submissions.mark_queued(
            submission.id,
            stored_weight_count=len(result.complete_instrument_vector),
            sanitized_gross_weight=sum(x.sanitized_weight for x in result.complete_instrument_vector),
            weights_processed_at=now_utc(),
            validation_summary=result.summary,
        )
        executions.create_pending_from_submission(submission)
```

这一整个处理事务要么全部成功，要么全部回滚。回滚后 submission 仍是前一个已提交事务留下的 `RECEIVED`，后台恢复任务可以再次按 ID 处理，不会丢掉实际请求。

`submission_weights` 不只保存客户端写出的三个 ticker，而是为全部 active instruments 写行。以 AAPL、MSFT、NVDA、JPM 为例：

```text
ticker  was_provided  raw_weight  sanitized_weight  validation_codes_json
AAPL    1             0.10        0.10              []
MSFT    1             0.08        0.08              []
NVDA    1             0.07        0.07              []
JPM     0             NULL        0.00              []
```

具体 sanitized 规则可变化，但数据库接口稳定：校验器必须输出每只 active instrument 的最终 numeric target，并冻结 `validator_version` 和 `validation_policy_json`。

执行不是逐行发起查询，而是一次加载集合：

```sql
SELECT instrument_id, sanitized_weight
FROM submission_weights
WHERE submission_id = ?
ORDER BY instrument_id;
```

Target 不能回答“是否买过”。实际是否发生买卖由 `transactions` 回答。`v_submission_weight_details` 和 `v_transaction_history` 已提供跨表查询所需的日期、团队和 ticker。

### 8.1 Engine 的严格输入结构

Execution service 只允许读取 `status=QUEUED` 且 `stored_weight_count=expected_weight_count` 的 submission。它把数据库记录组装成：

```python
ExecutionInput(
    execution_id=300,
    submission_id=200,
    team_id=team_alpha_id,
    signal_day_id=day_2026_09_01_id,
    execution_day_id=day_2026_09_02_id,
    prior_portfolio_snapshot_id=close_snapshot_id,
    targets=[
        TargetWeight(instrument_id=aapl_id, sanitized_weight=Decimal("0.10")),
        TargetWeight(instrument_id=msft_id, sanitized_weight=Decimal("0.08")),
        TargetWeight(instrument_id=nvda_id, sanitized_weight=Decimal("0.07")),
        # 所有其他 active instruments 也存在，权重为 0
    ],
    open_prices={aapl_id: Decimal("182"), msft_id: Decimal("302"), nvda_id: Decimal("120")},
    fee_rate=Decimal("0.001"),
    engine_version="execution-v1",
)
```

Engine 不读取 `raw_payload_json`，不理解 HTTP envelope，也不再执行 sanitize。它只计算 `sanitized target + prior portfolio + execution-day open prices`。这就是数据库与 engine 之间唯一稳定的 weight 合同。

## 9. 创建待执行任务

权重处理事务在完整向量落库并验证数量后插入 `executions`：

```text
id                    数据库主键
team_id               team_alpha
submission_id         当前 submission.id，唯一
trading_day_id        2026-09-02
status                PENDING
engine_version        将执行该权重的 engine 版本
scheduled_at          计划经济执行时间，即 T+1 open
effective_at          未执行时 NULL；完成后为 T+1 open
processed_at          未处理时 NULL；批处理完成后为服务器时间
nav_before            执行前开盘 NAV
cash_before/after     执行前后现金
total_buy_value       买入总额
total_sell_value      卖出总额
total_fee             总手续费
error_code/message    失败时填写
created_at/updated_at UTC 时间
```

唯一约束确保一个 submission 只有一个 execution、一个团队一天只有一个 execution。

## 10. Day T+1 盘后批量执行

2026-09-02 收盘数据导入后，调度器查出：

```sql
SELECT * FROM executions
WHERE trading_day_id = :day_id AND status = 'PENDING';
```

对 team_alpha：

1. 加载最新有效 portfolio（2026-09-01 CLOSE）；
2. 加载 submission 的完整 sanitized target；
3. 加载 2026-09-02 adjusted open；
4. 将 execution 更新为 `PROCESSING`；
5. 调用纯 `execution_engine`；
6. 原子写入 transactions、cash ledger 和 POST_OPEN portfolio；
7. 将 execution 更新为 `COMPLETED`。

示例开盘价：

```text
AAPL 182
MSFT 302
NVDA 120
```

执行前开盘 NAV：

```text
400,000 + 2,000×182 + 800×302 = 1,005,600
```

目标数量：

```text
AAPL = 1,005,600 × 0.10 / 182
MSFT = 1,005,600 × 0.08 / 302
NVDA = 1,005,600 × 0.07 / 120
```

每只产生实际变化的股票写一条 `transactions`：

```text
id                 数据库主键
execution_id       本次批量 execution
instrument_id      股票
side               BUY/SELL
shares_before      成交前数量
target_shares      目标数量
quantity           abs(target_shares - shares_before)
price              T+1 adjusted_open
gross_amount       quantity × price
fee                本笔手续费
cash_change        对 cash 的净影响；买入为负、卖出为正
status             COMPLETED/SKIPPED/FAILED
effective_at       T+1 open
processed_at       实际批处理时间
created_at         数据库写入时间
```

当前模型是一只股票一个确定性模拟成交，所以一张 `transactions` 足够，不拆 order/fill。一次 execution 通过 `execution_id` 自然关联多条 transactions。

## 11. Cash ledger 和 POST_OPEN portfolio

每次现金变化追加 `cash_ledger`：

```text
id                 数据库主键
team_id            team_alpha
trading_day_id     2026-09-02
execution_id       当前 execution；初始资金等可为 NULL
transaction_id     对应交易；初始资金/调整可为 NULL
event_type         INITIAL_CAPITAL/TRADE/FEE/DIVIDEND/ADJUSTMENT
amount             本次现金变化
balance_before     变化前余额
balance_after      变化后余额
effective_at       经济发生时间
created_at         写入时间
```

`transactions.cash_change` 用于解释一笔交易的净现金效果；`cash_ledger` 用于证明完整现金余额连续性，并覆盖没有 transaction 的初始资金或调整。

成交完成后写 `portfolio_snapshots(snapshot_type=POST_OPEN)`，其中 cash 是 ledger 最后一笔的 `balance_after`，positions value 使用开盘价。再为所有非零持仓写 `position_snapshots`。Execution、transactions、cash ledger、POST_OPEN snapshot 必须在同一事务内提交；失败全部回滚。

## 12. Day T+1 收盘估值和收益

示例收盘价：

```text
AAPL 184
MSFT 305
NVDA 123
```

估值引擎读取 POST_OPEN quantities 和 close prices：

```python
close_result = valuation_engine.value(
    portfolio=post_open_portfolio,
    close_prices=market_repository.close_prices(day_id),
)
```

写入 `CLOSE` portfolio snapshot 和对应 positions，然后写 `daily_performance`：

```text
id                          数据库主键
team_id                     team_alpha
trading_day_id              2026-09-02
close_portfolio_snapshot_id 当日 CLOSE snapshot，唯一
previous_close_nav          2026-09-01 CLOSE NAV
current_close_nav           2026-09-02 CLOSE NAV
daily_return                current / previous - 1
cumulative_return           current / initial capital - 1
transaction_cost            当天 executions.total_fee
turnover                    当天 traded notional / 约定 NAV denominator
drawdown                    current / historical peak - 1
calculated_at               计算时间
created_at                  写入时间
```

`daily_performance` 是汇总，不是账本。它可由 snapshots、transactions 和 cash ledger 重算。Leaderboard 和 M1-M9 应读取这些日汇总或底层事实，并保存/返回计算版本。

估值完成后，系统基于 2026-09-02 完整数据和 CLOSE portfolio 生成下一份 observation，进入下一轮。

## 13. 状态和幂等性

事实表采用追加写：market bars、observations、submissions、weights、transactions、ledger、snapshots 和 performance 不应静默覆盖。协调状态允许更新：trading day、submission、execution。

每个关键事务成功后，追加 `audit_logs`：

```text
id                 数据库主键
team_id/day_id     事件相关对象，可为空
actor_type         SYSTEM/TEAM/ADMIN
actor_id           调用者标识
event_type         例如 SUBMISSION_ACCEPTED、EXECUTION_COMPLETED
entity_type/id     指向逻辑业务对象
request_id         HTTP/任务关联 ID
details_json       结构化补充信息
created_at         UTC 时间
```

重试盘后任务时只选择 `PENDING` 或明确可恢复的 `PROCESSING` 项；`COMPLETED` execution 不能再次生成 transaction 或重复扣费。

## 14. 缺失和失败行为

当前数据库直接支持：

- 重复 HTTP：相同 idempotency key 返回原结果；
- 第二份不同提交：拒绝并写 audit，不创建新 submission；
- 没有提交：截止后创建 `source=FALLBACK` 的唯一决策和 execution；
- 权重问题：raw JSON 永久保留，normalized rows 保存处理结果；
- 单股票交易跳过/失败：transaction status 和 execution error 记录；
- 整批失败：执行事务回滚，execution 保持可恢复状态；
- 防重复结算：submission/execution/snapshot/performance 唯一约束；
- 状态追踪：当前状态在业务表，所有转换追加到 audit。

具体权重校验、缺价、停牌、公司行动和 fallback 策略属于业务规则；数据库保留版本、状态、错误和原始输入，使规则可以迭代而不破坏历史解释。

## 15. 最终端到端链路

```text
Import full T bars
  -> execute T-1 submission at T open (simulated after close)
  -> transactions + cash ledger
  -> POST_OPEN portfolio + positions
  -> value at T close
  -> CLOSE portfolio + positions
  -> daily performance
  -> build/store/publish T observation (no news)
  -> participant submits once
  -> raw submission + normalized/sanitized weights
  -> create PENDING T+1 execution
  -> repeat after T+1 close
```

这条链路中，任何 transaction 都可反查 execution、submission、observation、signal day、execution day 和团队；任何 daily performance 都可反查 CLOSE portfolio、positions、market bars、transactions 和 cash movements。
