# Competition server implementation status

PostgreSQL 16 is now the required database. The executable contract is
`data/database/schema.sql`.

`store.py`, `app.py`, and `manage.py` currently preserve the previous SQLite
prototype for reference while the PostgreSQL repository layer is implemented.
They are not included as a runnable service in `deployment/docker-compose.yml`
and must not be used for live competition state.

The replacement must use `COMPETITION_DATABASE_URL`, persist the raw submission
before weight processing, use the database-generated submission ID for all
normalized weights, and pass only complete sanitized vectors to execution.

See the root deployment README and bilingual workflow examples for the exact
contract.
