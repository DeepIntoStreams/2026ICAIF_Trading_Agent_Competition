# PostgreSQL 数据库快速验证

SQLite demo 已停止作为正式开发基线。现在所有开发者使用 PostgreSQL 16。

## Docker 启动

```bash
docker compose -f deployment/docker-compose.yml up -d postgres
docker compose -f deployment/docker-compose.yml run --rm schema-init
```

默认本地连接地址：

```text
postgresql://competition:competition@127.0.0.1:5432/competition
```

如需自定义，在未提交的 `.env` 中设置：

```text
POSTGRES_DB=competition
POSTGRES_USER=competition
POSTGRES_PASSWORD=<local-password>
POSTGRES_PORT=5432
```

## 不使用 Docker

准备 PostgreSQL 16 数据库后：

```bash
export COMPETITION_DATABASE_URL='postgresql://user:password@host:5432/database'
python data/database/init_db.py
```

初始化是幂等的，可以重复运行。应看到 16 张业务表和 3 个查询视图。

## 当前边界

Participant receiver 已切换到 PostgreSQL，可设置
`COMPETITION_ADMIN_TOKEN` 后通过 Compose 启动 `live-server`。Receiver
只负责认证、observation 读取、calendar/cutoff、一天一次和 raw submission
追踪；权重清洗与 execution 由 Competition 消费 `RECEIVED` 记录后完成。
