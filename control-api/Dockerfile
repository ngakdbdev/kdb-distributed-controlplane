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
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
