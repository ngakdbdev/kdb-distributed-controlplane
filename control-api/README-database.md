# Control-plane database backends

The control API's state (tenants, topology config, connectors, subscribers, audit log) lives in
whatever database `DATABASE_URL` points at. Four dialects are supported via SQLAlchemy/SQLModel:

| Dialect    | DATABASE_URL example                                                         | Driver          | Tested here? |
|------------|-------------------------------------------------------------------------------|-----------------|--------------|
| SQLite     | `sqlite:///./control_plane.db`                                                | built-in        | Yes (original demo) |
| PostgreSQL | `postgresql+psycopg2://user:pass@host:5432/kdb_control_plane`                 | psycopg2-binary | **Yes** - ran a full end-to-end test (login, subscriber CRUD, audit trail) against a live local Postgres 16, including the Alembic migration |
| MySQL/MariaDB | `mysql+pymysql://user:pass@host:3306/kdb_control_plane`                   | pymysql         | **Yes** - same full test suite against a live local MariaDB 10.11, including the migration |
| SQL Server | `mssql+pyodbc://user:pass@host:1433/kdb_control_plane?driver=ODBC+Driver+18+for+SQL+Server` | pyodbc | **No** - could not install `msodbcsql18` in this sandbox (needs Microsoft's apt repo, which this environment can't reach). The connection string and SQLAlchemy dialect are standard/well-documented, but treat this path as "should work, verify yourself" rather than "verified," and test it against a real SQL Server instance before you put a bank in front of it. |

## Recommended default for a real SaaS deployment

SQLite is fine for the local demo and nothing else - it has no concurrent-writer safety across
replicas and no HA story. For anything you'd show a bank, point `DATABASE_URL` at whichever of
Postgres/MySQL/SQL Server that customer's environment already runs:

- **AWS**: Amazon RDS for PostgreSQL / MySQL / SQL Server
- **Azure**: Azure Database for PostgreSQL / MySQL, or Azure SQL Database
- **GCP**: Cloud SQL for PostgreSQL / MySQL / SQL Server
- **On-prem**: whatever the bank's DBA team already operates - this is very often the actual
  requirement, since many banks have change-management policies that forbid a vendor from standing
  up a new, unapproved database engine inside their perimeter.

## Schema migrations (Alembic)

Table creation used to be a bare `SQLModel.metadata.create_all()` call - fine for a demo, not fine
for a SaaS product that needs to evolve its schema across many customer deployments without ad-hoc
manual `ALTER TABLE`s. Migrations now live in `migrations/`, driven by Alembic:

```bash
# generate a new migration after changing app/models.py
alembic revision --autogenerate -m "add tenant_id to subscriber"

# apply migrations - run this as a deploy step, before starting the app,
# against whichever DATABASE_URL the target environment uses
alembic upgrade head
```

`migrations/env.py` reads `DATABASE_URL` from the same `app.config.settings` object the app itself
uses, specifically so a migration can never accidentally target a different database than the one
the app will actually connect to.

**Known gotcha already fixed here**: Alembic's autogenerate emits `sqlmodel.sql.sqltypes.AutoString()`
for SQLModel string columns but does not import the `sqlmodel` module by default, which crashes every
migration with `NameError: name 'sqlmodel' is not defined`. The migration template
(`migrations/script.py.mako`) has been patched to always import it, so this won't bite future
migrations - it bit the very first one generated for this project, and is worth knowing about if you
ever regenerate `env.py`/`script.py.mako` from scratch.

## Local testing against a real database

If you want to test against Postgres or MySQL locally rather than SQLite:

```bash
# Postgres via Docker
docker run -d --name kdb-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=kdb_control_plane -p 5432:5432 postgres:16
DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/kdb_control_plane" alembic upgrade head
DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/kdb_control_plane" uvicorn app.main:app

# MySQL via Docker
docker run -d --name kdb-mysql -e MYSQL_ROOT_PASSWORD=root -e MYSQL_DATABASE=kdb_control_plane -p 3306:3306 mysql:8
DATABASE_URL="mysql+pymysql://root:root@localhost:3306/kdb_control_plane" alembic upgrade head
DATABASE_URL="mysql+pymysql://root:root@localhost:3306/kdb_control_plane" uvicorn app.main:app
```

## Adding SQL Server driver support to the image

The control-api image installs `unixodbc`/`unixodbc-dev` so `pyodbc` builds correctly, but does
**not** install Microsoft's actual ODBC driver (`msodbcsql18`), since that requires adding Microsoft's
package repo and accepting their EULA non-interactively. To add it:

```dockerfile
RUN apt-get update && apt-get install -y curl gnupg && \
    curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - && \
    curl https://packages.microsoft.com/config/debian/12/prod.list > /etc/apt/sources.list.d/mssql-release.list && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y msodbcsql18
```

Add this to `control-api/Dockerfile` before the `pip install` step if a customer needs SQL Server,
then verify against a real instance - this exact snippet was not run in this sandbox.
