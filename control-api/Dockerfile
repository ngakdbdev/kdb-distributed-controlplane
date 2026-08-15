FROM python:3.12-slim

# unixODBC (build + runtime) for pyodbc/SQL Server support. The actual
# Microsoft ODBC driver (msodbcsql18) is NOT installed here since it needs
# Microsoft's own apt repo and a license acceptance step - see
# control-api/README-database.md for the exact lines to add if you need
# SQL Server support in this image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    unixodbc unixodbc-dev gcc && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/app/
COPY migrations/ /app/migrations/
COPY alembic.ini /app/alembic.ini

EXPOSE 8000
# alembic upgrade head before serving - same "migrate before traffic"
# discipline the Helm chart's migrate-job.yaml already enforces for k8s
# deploys (a pre-install/pre-upgrade hook running this exact command),
# now applied to docker-compose too. Previously this path relied on
# app.db.init_db()'s create_all(), which only creates tables for brand-new
# model classes and never alters an existing one - confirmed live this
# session as a real incident (a column added to an existing table needed a
# manual ALTER TABLE because nothing was applying its migration).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*'"]
