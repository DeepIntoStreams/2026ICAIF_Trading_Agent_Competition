# Daily Live 可迁移初始化

本目录集中保存正式比赛数据库、股票池和交易日的可重复配置与脚本，方便换机器后快速重建。所有命令都应在仓库根目录运行。

## 初始化内容和边界

- `init_database.py`：应用 PostgreSQL schema、同步 30 只股票、按 XNYS 官方日历创建交易日；可选择回填行情。
- `verify_setup.py`：检查股票池、交易日以及已导入行情是否完整。
- `bootstrap_config.json`：正式股票池、交易日历、初始资金和交易约束配置。
- `deployment/live_server/migrations/`：Deployment 自主管理的版本化生产数据库迁移。
- `run_records/`：每次运行的无密码 JSON 记录；默认不提交生成文件。

## 推荐顺序

```bash
export COMPETITION_DATABASE_URL='postgresql://competition:competition@127.0.0.1:5432/competition'

# 默认初始化“最近一个已收盘 XNYS 交易日所在周”，并导入本周截至该日的行情。
python -m deployment.bootstrap.init_database --fetch-market-data

python -m deployment.bootstrap.verify_setup
```

`init_database` 会先应用基础 schema，再按文件名顺序应用尚未执行的 Deployment migration；
已执行 migration 的 SHA-256 不一致时会立即失败，禁止静默修改历史 migration。

需要固定日期、用于重放或换机器时，显式写日期以避免结果随当天变化：

```bash
python -m deployment.bootstrap.init_database \
  --start-date 2026-08-31 \
  --end-date 2026-09-03 \
  --fetch-market-data
```

上述初始化均为幂等操作，不会清空数据库。已经存在但与配置不一致的初始资金会直接报错，避免静默改写账户历史。行情按“30 只全部成功才提交该日”的原则导入。

## 换机器清单

1. 复制仓库与部署环境变量，但不要把数据库密码写进仓库。
2. 全新数据库按“推荐顺序”执行三条命令。
3. 如果迁移现有数据库，用 PostgreSQL dump/restore 保留全部审计历史，并通过 secret manager 恢复所需凭据。
4. 检查 `verify_setup.py` 返回码为 0，再启动 `daily_live`。`daily_live` 只校验预建日历，不会自动创建或延长正式比赛日期。

## 仍需在正式比赛前确定的初始化项

- 正式队伍名单与生产 API key 发放/轮换方式；
- 正式比赛起止日期、初始资金、手续费和仓位约束的冻结版本；
- 行情源凭据、数据复权规则、失败重试与第二数据源；
- 定时任务、数据库备份/恢复演练、时钟同步和告警；
- 管理员 token、TLS、secret manager、最小权限数据库账号；
- 初始 observation 的生成与发布（当前 `daily_live` 在 Competition 执行/估值模块完成前会停在 `awaiting_competition`）。
