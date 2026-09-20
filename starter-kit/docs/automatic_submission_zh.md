# 原始 ICAIF 自动提交流程

本包按原始流程实现：团队注册 → Validation → Official → 明确提交 Final 材料 → 主办方审核、冻结、原 ID 重评分、核验、发布。保留 30 股、每日 7 轮、两个独立 100 万美元账户、0.001 手续费、单股 0.30 上限及原始四指标计算。自动工具只使用主办方实际发放的 Competition 99 profile。

先按根目录 README 安装 Python 依赖，把 `.env.example` 复制为 `.env` 并设为 0600。填写自己的 `CODABENCH_TOKEN`，以及主办方实际 profile 路径 `ICAIF_PROFILE`。不要把密钥放在命令行、profile 或提交包中。

三个上传文件严格保持原名、原 type、原字段，只上传单个原始 UTF-8 JSON 对象：

| 文件 | type | 必须包含 |
|---|---|---|
| `register.json` | `register` | 团队名、全部成员、联系人邮箱 |
| `decision.json` | `decision` | TEAM_ID、TEAM_TOKEN、phase、round_id、全量 30 股 weights |
| `final_submission.json` | `final_submission` | TEAM_ID、TEAM_TOKEN、全部成员、真实 HTTPS 材料地址、实际文件名清单 |

注册回执中的 TEAM_TOKEN 只首次发放。客户端先将它与 TEAM_ID 原子保存到 `.icaif/credentials.json`（0600），再输出脱敏回执。个人平台 Token 只用于平台认证，不能代替 decision/Final 中的团队凭据。已有注册或经主办方审计重置的凭据，可在私有环境设置 TEAM_ID/TEAM_TOKEN 后执行 `import-credentials` 导入；该命令不联网、不打印令牌。

```sh
python tools/auto_submit.py register --file private/register.json
python tools/auto_submit.py schedule
python tools/auto_submit.py decision --file private/decision.json
python tools/auto_submit.py round --round-id validation-2026-10-08-r1
python tools/auto_submit.py portfolio --phase validation
python tools/auto_submit.py decisions --phase validation
python tools/auto_submit.py metrics --phase validation
```

所有查询都是 GET，不上传 status/observation/result 探测文件。首次可归属的轮内上传即使无效也占用该轮，后续上传不能补正。未提交时持仓延续；全零权重按原规则清仓。原始截止时刻 ET 为 09:10、10:25、11:25、12:25、13:25、14:25、15:25；交易轮截止不含等于时刻。以服务器发布的日程与时间为准。旧 round_id 的文件恰在它的截止时刻到达属于 LATE，不占用下一轮；客户端会在上传前拒绝该过期文件。后端明确标记 NOT_ELIGIBLE 的轮外记录不会阻止该轮之后的合法首投。

策略在参赛者本机运行，只接收当前轮、30 股代码、服务器时间和本人仓位/回合状态；合法市场数据由你的策略自行提供。本包不会把合成价格冒充真实行情。示例 `examples/automation/file_strategy.py` 只读取你自己计算、且明确标注当前 round_id 的权重文件：

```sh
python tools/auto_submit.py watch --phase validation --strategy examples.automation.file_strategy:strategy --once
python tools/auto_submit.py watch --phase official --strategy examples.automation.file_strategy:strategy --max-wait-seconds 86400
```

先设置 `ICAIF_WEIGHTS_FILE` 指向自己的权重文件。重启必须使用相同 `.icaif/checkpoint.json`。watch 会先恢复前一轮尚未确认的原提交 ID，再处理当前轮。默认最多运行一小时，可明确增加运行时长；Ctrl-C 会保留恢复状态。

网络中断后重跑相同命令、相同文件，不要删除 checkpoint 强行重投。客户端在 POST 前保存原始文件哈希与状态，不自动重试创建 POST；只有安全 GET、同一个存储对象的原字节 PUT、该对象的完成确认可进行有限重试。丢失创建响应时，客户端只读查询自己该阶段的上传，并核对所有者、原文件名、SHA256 后恢复唯一原 ID。无法唯一确认时会停止，交由主办方核实。

```sh
python tools/auto_submit.py fetch --submission-id 原提交ID
python tools/auto_submit.py resolve --operation registration --submission-id 原注册ID
```

决策 operation 键为 `decision:<phase>:<round_id>`，Final 为 `final_submission`。worker 失败也应恢复同一提交，不需要重新占用一个上传名额。首次注册令牌丢失时应联系主办方审计重置，不能靠新注册获得相同团队的新令牌。

交易结束只提示需要 Final，不会自动创建材料。最后 Official 收盘后，由你填写真实可访问的 HTTPS 材料链接、实际文件清单及全部成员：

```sh
python tools/auto_submit.py final --file private/final_submission.json
python tools/auto_submit.py fetch --submission-id 原Final提交ID
```

一个被接受的 Final 材料记录不能覆盖。原始 Final 截止为 2026-11-03 23:59 ET，等于截止时刻仍可提交。`PENDING_REVIEW` 表示待审，`PUBLISHED_READY` 冻结回执也不能单独证明榜单已公开。正式成绩要等主办方审核、库存封存、冻结、原 ID 重评分、实际平台值核验、后端发布，以及独立的平台榜单公开操作。Official 指标由服务器在发布前限制读取；现金/NAV/盈亏账本可以保持可见。

原本地校验、准备、评估工具与算法保持原字节不变。原示例合成数据只用于离线演示。本地指标、Validation 私有指标均不等于最终公开 Score。全部命令、恢复细节、Python API 见 [英文详细说明](automatic_submission.md)。
